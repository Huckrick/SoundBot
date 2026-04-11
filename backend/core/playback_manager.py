# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Nagisa_Huckrick (胡杨)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
流式音频播放管理器

使用 sounddevice 的 callback 模式实现真正的流式播放：
- 不需要整个文件在内存中
- callback 每次只取约 0.1 秒的数据块填充声卡缓冲区
- 播放状态通过 WebSocket 实时推送到前端
"""

import os
import time
import asyncio
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import soundfile as sf

try:
    import sounddevice as sd
    _SOUNDDEVICE_AVAILABLE = True
except Exception as _sd_import_error:
    sd = None
    _SOUNDDEVICE_AVAILABLE = False
    import logging
    logging.getLogger(__name__).warning(
        f"sounddevice not available (audio playback disabled): {_sd_import_error}"
    )

from utils.logger import get_logger

logger = get_logger()


class PlaybackState(Enum):
    """播放状态枚举"""
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass
class PlaybackInfo:
    """播放信息"""
    state: PlaybackState = PlaybackState.IDLE
    file_path: str = ""
    duration: float = 0.0
    sample_rate: int = 44100
    channels: int = 2
    current_frame: int = 0
    start_frame: int = 0
    end_frame: Optional[int] = None


@dataclass
class PlaybackStats:
    """播放统计信息"""
    total_calls: int = 0
    underflow_count: int = 0
    last_position: float = 0.0
    memory_usage_mb: float = 0.0


class PlaybackManager:
    """
    流式音频播放管理器

    使用 sounddevice 的 callback 模式实现真正的流式播放：
    - 不需要整个文件在内存中
    - callback 每次只取约 0.1 秒的数据块填充声卡缓冲区
    - 播放状态通过回调函数实时通知
    """

    # 默认缓冲区参数
    DEFAULT_BLOCK_SIZE = 4096  # 每次回调的样本数
    DEFAULT_LATENCY = 0.05    # 50ms 延迟（平衡延迟和稳定性）

    def __init__(self):
        self._lock = threading.RLock()
        self._stream: Optional[Any] = None
        self._playback_info = PlaybackInfo()
        self._stats = PlaybackStats()
        self._file_handle = None
        self._state_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._position_update_task: Optional[asyncio.Task] = None
        self._running = False
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None  # 存储事件循环引用
        self._pending_state_updates: list = []  # 待处理的状态更新

        # 注册音频设备
        self._register_device_callback()

    def _register_device_callback(self):
        """注册音频设备回调（用于监控）"""
        if not _SOUNDDEVICE_AVAILABLE:
            logger.warning("sounddevice 不可用，音频播放功能已禁用")
            return
        try:
            devices = sd.query_devices()
            logger.info(f"音频设备信息: {devices}")
        except Exception as e:
            logger.warning(f"无法查询音频设备: {e}")

    def set_state_callback(self, callback: Callable[[Dict[str, Any]], None]):
        """
        设置状态更新回调函数

        Args:
            callback: 回调函数，接收包含 position, duration, is_playing 的字典
        """
        self._state_callback = callback

    def _notify_state(self, position: float, duration: float, is_playing: bool):
        """通知状态更新（线程安全）"""
        if self._state_callback:
            try:
                state = {
                    "position": round(position, 3),
                    "duration": round(duration, 3),
                    "is_playing": is_playing
                }
                event_loop = self._event_loop
                if event_loop is not None and event_loop.is_running():
                    event_loop.call_soon_threadsafe(self._state_callback, state)
                else:
                    try:
                        self._state_callback(state)
                    except Exception as callback_err:
                        logger.error(f"状态回调执行失败: {callback_err}")
            except Exception as e:
                logger.error(f"状态通知失败: {e}")

    def _audio_callback(self, outdata, frames, time_info, status):
        """
        sounddevice 的音频回调函数

        此函数由 sounddevice 在需要填充音频数据时调用（约每 0.1 秒一次）
        注意：此函数在单独的音频线程中运行，必须是线程安全的

        Args:
            outdata: 输出缓冲区
            frames: 请求的帧数
            time_info: 时间信息
            status: 状态标志
        """
        if status:
            if status.input_overflow:
                self._stats.underflow_count += 1
                logger.debug(f"音频缓冲区下溢 (underflow): {self._stats.underflow_count}")

        # 检查是否暂停
        if not self._pause_event.is_set():
            outdata.fill(0)
            return

        with self._lock:
            # 检查是否在播放
            if self._playback_info.state != PlaybackState.PLAYING:
                outdata.fill(0)
                return

            # 检查是否到达结束位置
            if (self._playback_info.end_frame is not None and
                self._playback_info.current_frame >= self._playback_info.end_frame):
                outdata.fill(0)
                self._stop_internal()
                return

            # 计算剩余帧数
            remaining_frames = 0
            if self._playback_info.end_frame is not None:
                remaining_frames = self._playback_info.end_frame - self._playback_info.current_frame
            else:
                remaining_frames = int(self._playback_info.duration * self._playback_info.sample_rate) - self._playback_info.current_frame

            # 如果需要更多数据但文件已结束，填充静音
            if remaining_frames <= 0:
                outdata.fill(0)
                self._stop_internal()
                return

            # 读取音频数据
            frames_to_read = min(frames, remaining_frames)

            try:
                if self._file_handle is not None:
                    data = self._file_handle.read(frames_to_read)
                    if isinstance(data, tuple):
                        audio_data = data[0]
                    else:
                        audio_data = data

                    # 确保数据形状正确
                    if audio_data.ndim == 1:
                        # 单声道 - 复制到所有声道
                        audio_data = np.tile(audio_data.reshape(-1, 1), (1, self._playback_info.channels))

                    # 截取到需要的帧数
                    actual_frames = len(audio_data)
                    if actual_frames < frames:
                        # 填充静音
                        silence = np.zeros((frames - actual_frames, self._playback_info.channels), dtype=np.float32)
                        audio_data = np.vstack([audio_data, silence])

                    # 确保数据类型为 float32 并归一化
                    if audio_data.dtype != np.float32:
                        # 根据原始数据类型进行归一化
                        if audio_data.dtype == np.int16:
                            audio_data = audio_data.astype(np.float32) / 32768.0
                        elif audio_data.dtype == np.int32:
                            audio_data = audio_data.astype(np.float32) / 2147483648.0
                        else:
                            audio_data = audio_data.astype(np.float32)

                    # 写入输出缓冲区
                    outdata[:] = audio_data

                    # 更新当前位置
                    self._playback_info.current_frame += actual_frames
                    self._stats.total_calls += 1
                else:
                    outdata.fill(0)

            except Exception as e:
                logger.error(f"音频回调读取数据失败: {e}")
                outdata.fill(0)
                self._stop_internal()

    def _stop_internal(self):
        """内部停止方法（不释放锁）"""
        self._playback_info.state = PlaybackState.IDLE
        self._running = False
        self._notify_state(0, self._playback_info.duration, False)

    async def _position_updater(self, interval_ms: int = 100):
        """
        定期更新播放位置的异步任务

        通过 WebSocket 推送当前播放位置到前端

        Args:
            interval_ms: 更新间隔（毫秒）
        """
        while self._running:
            await asyncio.sleep(interval_ms / 1000.0)

            with self._lock:
                if self._playback_info.state == PlaybackState.PLAYING:
                    position = self._playback_info.current_frame / self._playback_info.sample_rate
                    self._stats.last_position = position
                    self._notify_state(
                        position,
                        self._playback_info.duration,
                        True
                    )

    def _get_memory_usage(self) -> float:
        """获取当前进程的内存使用量（MB）"""
        try:
            # 尝试使用 resource 模块（POSIX 标准库）
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # macOS 返回的是字节，Linux 返回的是 KB
            import sys
            if sys.platform == 'darwin':
                return usage / 1024 / 1024
            else:
                return usage / 1024
        except (ImportError, AttributeError):
            return 0.0

    def play(self, file_path: str, start: float = 0.0) -> Dict[str, Any]:
        """
        开始播放音频文件

        Args:
            file_path: 音频文件路径
            start: 起始位置（秒）

        Returns:
            包含播放信息的字典

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 参数无效
            RuntimeError: 播放启动失败
        """
        with self._lock:
            # 如果已在播放同一文件，可以继续
            if self._playback_info.state == PlaybackState.PLAYING and self._playback_info.file_path == file_path:
                return self._get_status()

            # 停止当前播放
            self._stop_unsafe()

            # 验证文件
            path = Path(file_path)
            if not path.exists():
                raise FileNotFoundError(f"音频文件不存在: {file_path}")

            try:
                # 获取音频信息
                info = sf.info(file_path)
                duration = info.duration
                sample_rate = int(info.samplerate)
                channels = info.channels

                # 验证起始位置
                if start < 0:
                    start = 0.0
                elif start >= duration:
                    start = duration - 0.1

                # 计算起始帧
                start_frame = int(start * sample_rate)

                # 打开文件准备流式读取
                self._file_handle = sf.SoundFile(file_path, 'r')
                self._file_handle.seek(start_frame)

                # 更新播放信息
                self._playback_info = PlaybackInfo(
                    state=PlaybackState.PLAYING,
                    file_path=file_path,
                    duration=duration,
                    sample_rate=sample_rate,
                    channels=channels,
                    current_frame=start_frame,
                    start_frame=start_frame,
                    end_frame=None
                )

                # 打开音频流
                if not _SOUNDDEVICE_AVAILABLE:
                    raise RuntimeError("sounddevice 不可用，无法播放音频（PortAudio 库缺失）")
                self._stream = sd.OutputStream(
                    samplerate=sample_rate,
                    channels=channels,
                    callback=self._audio_callback,
                    blocksize=self.DEFAULT_BLOCK_SIZE,
                    latency=self.DEFAULT_LATENCY,
                    dtype=np.float32
                )

                # 启动流
                self._stream.start()
                self._running = True
                self._pause_event.set()

                # 存储当前事件循环引用（用于线程安全回调）
                try:
                    self._event_loop = asyncio.get_running_loop()
                except RuntimeError:
                    self._event_loop = None

                # 取消旧的位置更新任务（如果存在）
                if self._position_update_task is not None and not self._position_update_task.done():
                    self._position_update_task.cancel()

                # 启动位置更新任务
                try:
                    loop = asyncio.get_running_loop()
                    self._position_update_task = loop.create_task(self._position_updater())
                except RuntimeError:
                    # 没有运行的事件循环，不启动位置更新任务
                    logger.warning("没有运行的事件循环，位置更新任务未启动")
                    self._position_update_task = None

                # 发送初始状态
                self._notify_state(start, duration, True)

                logger.info(f"开始播放: {file_path}, 起始位置: {start:.2f}s")

                return self._get_status()

            except Exception as e:
                self._stop_unsafe()
                raise RuntimeError(f"播放启动失败: {e}")

    def pause(self) -> Dict[str, Any]:
        """
        暂停播放

        Returns:
            包含播放信息的字典
        """
        with self._lock:
            if self._playback_info.state != PlaybackState.PLAYING:
                return self._get_status()

            self._pause_event.clear()
            self._playback_info.state = PlaybackState.PAUSED

            current_position = self._playback_info.current_frame / self._playback_info.sample_rate
            self._notify_state(current_position, self._playback_info.duration, False)

            logger.info(f"暂停播放: {self._playback_info.file_path}")

            return self._get_status()

    def resume(self) -> Dict[str, Any]:
        """
        继续播放

        Returns:
            包含播放信息的字典
        """
        with self._lock:
            if self._playback_info.state != PlaybackState.PAUSED:
                return self._get_status()

            self._pause_event.set()
            self._playback_info.state = PlaybackState.PLAYING

            current_position = self._playback_info.current_frame / self._playback_info.sample_rate
            self._notify_state(current_position, self._playback_info.duration, True)

            logger.info(f"继续播放: {self._playback_info.file_path}")

            return self._get_status()

    def stop(self) -> Dict[str, Any]:
        """
        停止播放

        Returns:
            包含播放信息的字典
        """
        with self._lock:
            self._stop_unsafe()
            return self._get_status()

    def seek(self, position: float) -> Dict[str, Any]:
        """
        跳转到指定位置

        Args:
            position: 目标位置（秒）

        Returns:
            包含播放信息的字典

        Raises:
            ValueError: 位置无效
        """
        with self._lock:
            if self._playback_info.state == PlaybackState.IDLE:
                raise ValueError("当前没有播放任何文件")

            if position < 0:
                position = 0.0
            elif position > self._playback_info.duration:
                position = self._playback_info.duration - 0.1

            # 计算目标帧
            target_frame = int(position * self._playback_info.sample_rate)

            # 在文件中定位
            if self._file_handle is not None:
                self._file_handle.seek(target_frame)

            # 更新播放信息
            self._playback_info.current_frame = target_frame

            # 发送状态更新
            self._notify_state(position, self._playback_info.duration, self._playback_info.state == PlaybackState.PLAYING)

            logger.info(f"跳转播放位置: {position:.3f}s")

            return self._get_status()

    def get_status(self) -> Dict[str, Any]:
        """
        获取当前播放状态

        Returns:
            包含播放信息的字典
        """
        with self._lock:
            return self._get_status()

    def _get_status(self) -> Dict[str, Any]:
        """内部方法：获取当前播放状态（需要先获取锁）"""
        position = self._playback_info.current_frame / self._playback_info.sample_rate

        return {
            "state": self._playback_info.state.value,
            "file_path": self._playback_info.file_path,
            "position": round(position, 3),
            "duration": round(self._playback_info.duration, 3),
            "sample_rate": self._playback_info.sample_rate,
            "channels": self._playback_info.channels,
            "is_playing": self._playback_info.state == PlaybackState.PLAYING,
            "memory_usage_mb": self._get_memory_usage(),
            "stats": {
                "total_callbacks": self._stats.total_calls,
                "underflow_count": self._stats.underflow_count
            }
        }

    def _stop_unsafe(self):
        """
        内部停止方法（假设已经获取锁）
        """
        self._running = False
        self._pause_event.set()

        # 取消位置更新任务
        if self._position_update_task is not None and not self._position_update_task.done():
            try:
                self._position_update_task.cancel()
            except Exception as e:
                logger.warning(f"取消位置更新任务时出错: {e}")
            finally:
                self._position_update_task = None

        # 停止并关闭音频流
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.warning(f"关闭音频流时出错: {e}")
            finally:
                self._stream = None

        # 关闭文件
        if self._file_handle is not None:
            try:
                self._file_handle.close()
            except Exception as e:
                logger.warning(f"关闭文件时出错: {e}")
            finally:
                self._file_handle = None

        # 重置播放信息
        self._playback_info = PlaybackInfo()
        self._notify_state(0, 0, False)

    def is_busy(self) -> bool:
        """
        检查是否正在播放

        Returns:
            是否正在播放或已暂停
        """
        with self._lock:
            return self._playback_info.state != PlaybackState.IDLE

    def get_current_position(self) -> float:
        """
        获取当前播放位置

        Returns:
            当前播放位置（秒）
        """
        with self._lock:
            if self._playback_info.state == PlaybackState.IDLE:
                return 0.0
            return self._playback_info.current_frame / self._playback_info.sample_rate

    def cleanup(self):
        """清理资源"""
        with self._lock:
            self._stop_unsafe()


# ========== 全局单例 ==========

_playback_manager: Optional[PlaybackManager] = None


def get_playback_manager() -> PlaybackManager:
    """
    获取播放管理器单例

    Returns:
        PlaybackManager 实例
    """
    global _playback_manager
    if _playback_manager is None:
        _playback_manager = PlaybackManager()
    return _playback_manager


def reset_playback_manager():
    """重置播放管理器"""
    global _playback_manager
    if _playback_manager is not None:
        _playback_manager.cleanup()
        _playback_manager = None
