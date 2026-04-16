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

"""FastAPI 后端服务，用于音效管理器的 AI 语义搜索功能。"""

import os
import re
import time
import asyncio
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor


def validate_path(path: str, allow_absolute: bool = True) -> bool:
    """
    验证路径是否安全，防止路径遍历攻击
    
    Args:
        path: 要验证的路径
        allow_absolute: 是否允许绝对路径
        
    Returns:
        bool: 路径是否安全
    """
    if not path:
        return False
    
    # 解码 URL 编码
    import urllib.parse
    decoded_path = urllib.parse.unquote(path)
    
    # 规范化路径
    normalized = os.path.normpath(decoded_path)
    
    # 检查是否包含路径遍历字符
    if '..' in normalized:
        return False
    
    # 检查是否包含空字节（Null byte）
    if '\x00' in decoded_path:
        return False
    
    # 检查是否包含危险的特殊字符
    dangerous_patterns = [
        r'\.{2,}',  # 多个点
        r'[~`]',     # 波浪号和反引号
    ]
    for pattern in dangerous_patterns:
        if re.search(pattern, decoded_path):
            return False
    
    # 如果不允许绝对路径，检查是否是相对路径
    if not allow_absolute and os.path.isabs(normalized):
        return False
    
    return True

from fastapi import FastAPI, HTTPException, Query, Path as PathParam, BackgroundTasks, WebSocket, WebSocketDisconnect, Body
from fastapi.responses import FileResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

import config
from models import schemas
from core.indexer import get_indexer, AudioIndexer, reset_indexer
from core.searcher import get_searcher, AudioSearcher, reset_searcher
from core.embedder import get_embedder, reset_embedder, is_embedder_available
from core.database import get_db_manager, reset_db_manager, AudioFileRecord
from core.websocket_manager import (
    get_ws_manager,
    reset_ws_manager,
    register_playback_client,
    unregister_playback_client
)
from core.audio_cache import get_audio_cache, reset_audio_cache
from core.playback_manager import get_playback_manager, reset_playback_manager
from utils.logger import logger

# 线程池用于 CPU 密集型任务
_executor = ThreadPoolExecutor(max_workers=4)


def cleanup_old_clips(max_keep=100):
    """
    清理多余的临时文件，只保留最新的max_keep个
    
    注意：此功能已禁用，临时文件由用户自行管理
    保留函数是为了向后兼容，但不再执行实际清理操作
    """
    # 临时文件由用户自行管理，不再自动清理
    logger.info("临时文件自动清理已禁用，由用户自行管理")
    return


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info(f"SoundBot API 启动中...")
    logger.info(f"设备: {config.get_device()}")
    logger.info(f"数据库路径: {config.get_db_path()}")

    # 初始化 SQLite 数据库
    db_manager = get_db_manager()
    file_count = db_manager.get_file_count()
    logger.info(f"SQLite 数据库已加载，当前文件数: {file_count}")

    # 使用动态获取的临时文件目录
    temp_dir = Path(config.get_temp_clip_dir())
    temp_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"临时文件目录: {temp_dir}")

    logger.info("临时文件由用户自行管理，不执行自动清理")

    # 初始化 LRU 音频缓存
    cache = get_audio_cache()
    logger.info(f"LRU 音频缓存已初始化，最大容量: {cache._max_size} 个文件")

    # 启动模型预加载（后台异步）
    from core.model_preloader import preload_models_on_startup
    await preload_models_on_startup()

    yield

    logger.info("SoundBot API 关闭中...")
    logger.info("临时文件由用户自行管理，不执行自动清理")
    reset_playback_manager()  # 关闭播放管理器
    reset_audio_cache()  # 清理 LRU 缓存
    reset_embedder()
    reset_indexer()
    reset_searcher()
    reset_db_manager()
    reset_ws_manager()
    logger.info("全局单例状态已清理")


# 创建 FastAPI 应用
app = FastAPI(
    title="SoundBot API",
    description="AI 音效管理器的语义搜索后端",
    version=config.APP_VERSION,
    lifespan=lifespan
)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# ==================== WebSocket 端点 ====================

@app.websocket("/ws/scan/{client_id}")
async def websocket_scan_progress(websocket: WebSocket, client_id: str):
    """
    WebSocket 端点：接收扫描进度推送

    前端通过此 WebSocket 接收实时扫描进度。

    接收消息格式:
    - {"type": "cancel", "task_id": "xxx"} - 取消扫描任务
    - {"type": "ping"} - 心跳检测

    发送消息格式:
    - scan_progress: {"type": "scan_progress", "task_id": "xxx", "data": {...}}
    - scan_complete: {"type": "scan_complete", "task_id": "xxx", "data": {...}}
    - scan_error: {"type": "scan_error", "task_id": "xxx", "data": {...}}
    """
    logger.info(f"[WS] WebSocket 连接尝试: client_id={client_id}, origin={websocket.headers.get('origin')}")
    ws_manager = get_ws_manager()
    await ws_manager.connect(websocket, client_id)
    logger.info(f"[WS] WebSocket 连接成功: client_id={client_id}")
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get("type", "")

            if msg_type == "cancel":
                task_id = message.get("task_id")
                if task_id:
                    ws_manager.cancel_task(task_id)
                    logger.info(f"任务已取消: {task_id}")

            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"[WS] WebSocket 断开: client_id={client_id}")
        ws_manager.disconnect(websocket, client_id)
    except json.JSONDecodeError:
        logger.warning(f"[WS] 无效的 JSON 消息 from {client_id}")
    except Exception as e:
        logger.error(f"[WS] WebSocket 错误: {e}")
        ws_manager.disconnect(websocket, client_id)


@app.websocket("/ws/playback/{client_id}")
async def websocket_playback_state(websocket: WebSocket, client_id: str):
    """
    WebSocket 端点：接收播放状态实时推送

    前端通过此 WebSocket 接收播放状态更新。

    发送消息格式:
    - playback_state: {"position": 3.2, "duration": 12.4, "is_playing": true}
    """
    logger.info(f"[WS Playback] WebSocket 连接尝试: client_id={client_id}")
    await websocket.accept()
    logger.info(f"[WS Playback] WebSocket 连接成功: client_id={client_id}")

    # 注册到广播列表
    await register_playback_client(client_id, websocket)

    # 设置播放管理器的状态回调
    playback = get_playback_manager()

    # 使用队列实现线程安全的状态传递
    state_queue = asyncio.Queue()

    def state_callback(state: dict):
        """播放状态回调（同步函数，从音频线程调用）"""
        try:
            # 将状态放入队列，由主循环处理
            state_queue.put_nowait(state)
        except Exception:
            pass

    playback.set_state_callback(state_callback)

    # 启动状态转发任务
    async def forward_state():
        """从队列转发状态到 WebSocket"""
        while True:
            try:
                state = await asyncio.wait_for(state_queue.get(), timeout=1.0)
                try:
                    await websocket.send_json(state)
                except Exception:
                    pass
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

    forward_task = asyncio.create_task(forward_state())

    # 发送当前状态
    status = playback.get_status()
    await websocket.send_json({
        "position": status["position"],
        "duration": status["duration"],
        "is_playing": status["is_playing"]
    })

    try:
        while True:
            # 接收客户端消息（心跳等）
            data = await websocket.receive_text()
            message = json.loads(data)
            msg_type = message.get("type", "")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "get_status":
                status = playback.get_status()
                await websocket.send_json({
                    "position": status["position"],
                    "duration": status["duration"],
                    "is_playing": status["is_playing"]
                })

    except WebSocketDisconnect:
        logger.info(f"[WS Playback] WebSocket 断开: {client_id}")
        await unregister_playback_client(client_id, websocket)
    except json.JSONDecodeError:
        logger.warning(f"[WS Playback] 无效的 JSON 消息 from {client_id}")
    except Exception as e:
        logger.error(f"[WS Playback] WebSocket 错误: {e}")
        await unregister_playback_client(client_id, websocket)
    finally:
        # 取消状态转发任务
        if forward_task and not forward_task.done():
            forward_task.cancel()
            try:
                await forward_task
            except asyncio.CancelledError:
                pass
        # 清除播放管理器的回调
        playback.set_state_callback(None)


# ==================== 健康检查 ====================

@app.get("/api/v1/health", response_model=schemas.HealthResponse)
async def health_check():
    """
    健康检查接口

    返回服务状态、版本号和当前设备信息
    """
    return schemas.HealthResponse(
        status="healthy",
        version=config.APP_VERSION,
        device=config.get_device()
    )


@app.get("/api/v1/model/status")
async def get_model_status():
    """
    获取 AI 模型加载状态

    返回模型是否已预加载到内存，以及加载进度
    """
    from core.model_preloader import get_preloader
    preloader = get_preloader()

    return {
        "status": "success",
        "model_status": preloader.get_status(),
        "embedder_available": preloader.get_embedder() is not None
    }


# ==================== LRU 音频缓存管理 ====================

@app.get("/api/v1/cache/stats")
async def get_cache_stats():
    """
    获取 LRU 音频缓存统计信息

    返回格式：
    {
        "size": 50,                    # 当前缓存文件数
        "max_size": 100,               # 最大容量
        "hits": 1234,                  # 命中次数
        "misses": 567,                 # 未命中次数
        "total_requests": 1801,       # 总请求数
        "hit_rate": 0.685,             # 命中率
        "total_evictions": 0,          # 总踢出次数
        "total_memory_bytes": 52428800, # 总内存占用（字节）
        "total_memory_mb": 50.0        # 总内存占用（MB）
    }
    """
    cache = get_audio_cache()
    return cache.get_stats()


@app.post("/api/v1/cache/clear")
async def clear_cache():
    """
    清空 LRU 音频缓存

    返回格式：
    {
        "success": true,
        "cleared_count": 50,
        "message": "缓存已清空，共 50 个条目"
    }
    """
    cache = get_audio_cache()
    cleared_count = cache.clear()
    cache.reset_stats()
    return {
        "success": True,
        "cleared_count": cleared_count,
        "message": f"缓存已清空，共 {cleared_count} 个条目"
    }


# ==================== 流式播放控制 ====================

@app.post("/api/play", response_model=schemas.PlaybackResponse)
async def play_audio(request: schemas.PlayRequest):
    """
    开始播放音频文件

    使用 sounddevice callback 模式实现真正的流式播放，不占用大量内存。

    - **path**: 音频文件路径
    - **start**: 起始位置（秒），默认为 0

    返回格式：
    {
        "success": true,
        "state": "playing",
        "file_path": "/path/to/file.wav",
        "position": 0.0,
        "duration": 12.4,
        "is_playing": true,
        "message": "开始播放"
    }
    """
    try:
        # 验证路径
        path = config.validate_audio_path(request.path)

        # 开始播放
        playback = get_playback_manager()
        status = playback.play(str(path), start=request.start)

        return schemas.PlaybackResponse(
            success=True,
            state=status["state"],
            file_path=status["file_path"],
            position=status["position"],
            duration=status["duration"],
            is_playing=status["is_playing"],
            message="开始播放"
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"播放失败: {e}")
        raise HTTPException(status_code=500, detail=f"播放失败: {e}")


@app.post("/api/pause", response_model=schemas.PlaybackResponse)
async def pause_audio():
    """
    暂停播放

    返回格式：
    {
        "success": true,
        "state": "paused",
        "file_path": "/path/to/file.wav",
        "position": 5.2,
        "duration": 12.4,
        "is_playing": false,
        "message": "已暂停"
    }
    """
    playback = get_playback_manager()
    status = playback.pause()

    return schemas.PlaybackResponse(
        success=True,
        state=status["state"],
        file_path=status["file_path"],
        position=status["position"],
        duration=status["duration"],
        is_playing=status["is_playing"],
        message="已暂停" if status["is_playing"] else "播放已暂停"
    )


@app.post("/api/resume", response_model=schemas.PlaybackResponse)
async def resume_audio():
    """
    继续播放（从暂停状态恢复）

    返回格式：
    {
        "success": true,
        "state": "playing",
        "file_path": "/path/to/file.wav",
        "position": 5.2,
        "duration": 12.4,
        "is_playing": true,
        "message": "继续播放"
    }
    """
    playback = get_playback_manager()
    status = playback.resume()

    return schemas.PlaybackResponse(
        success=True,
        state=status["state"],
        file_path=status["file_path"],
        position=status["position"],
        duration=status["duration"],
        is_playing=status["is_playing"],
        message="继续播放" if status["is_playing"] else "继续播放失败"
    )


@app.post("/api/stop", response_model=schemas.PlaybackResponse)
async def stop_audio():
    """
    停止播放

    返回格式：
    {
        "success": true,
        "state": "idle",
        "file_path": "",
        "position": 0.0,
        "duration": 0.0,
        "is_playing": false,
        "message": "已停止"
    }
    """
    playback = get_playback_manager()
    status = playback.stop()

    return schemas.PlaybackResponse(
        success=True,
        state=status["state"],
        file_path=status["file_path"],
        position=status["position"],
        duration=status["duration"],
        is_playing=status["is_playing"],
        message="已停止"
    )


@app.post("/api/seek", response_model=schemas.PlaybackResponse)
async def seek_audio(request: schemas.SeekRequest):
    """
    跳转到指定位置

    - **position**: 目标位置（秒）

    返回格式：
    {
        "success": true,
        "state": "playing",
        "file_path": "/path/to/file.wav",
        "position": 5.5,
        "duration": 12.4,
        "is_playing": true,
        "message": "已跳转"
    }
    """
    try:
        playback = get_playback_manager()
        status = playback.seek(request.position)

        return schemas.PlaybackResponse(
            success=True,
            state=status["state"],
            file_path=status["file_path"],
            position=status["position"],
            duration=status["duration"],
            is_playing=status["is_playing"],
            message="已跳转"
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/playback/status", response_model=schemas.PlaybackResponse)
async def get_playback_status():
    """
    获取当前播放状态

    返回格式：
    {
        "success": true,
        "state": "playing",
        "file_path": "/path/to/file.wav",
        "position": 3.2,
        "duration": 12.4,
        "is_playing": true,
        "message": null
    }
    """
    playback = get_playback_manager()
    status = playback.get_status()

    return schemas.PlaybackResponse(
        success=True,
        state=status["state"],
        file_path=status["file_path"],
        position=status["position"],
        duration=status["duration"],
        is_playing=status["is_playing"],
        message=None
    )


@app.get("/api/v1/cache/check/{file_path:path}")
async def check_cache(file_path: str):
    """
    检查指定文件是否在缓存中

    - **file_path**: 文件路径（URL编码）

    返回格式：
    {
        "cached": true,
        "file_path": "/path/to/file.wav",
        "memory_mb": 12.5,
        "last_access": 1701234567.890
    }
    """
    import urllib.parse
    decoded_path = urllib.parse.unquote(file_path)

    cache = get_audio_cache()

    if decoded_path in cache:
        entry = cache.get(decoded_path)
        return {
            "cached": True,
            "file_path": decoded_path,
            "memory_mb": round(entry.memory_size / (1024 * 1024), 2) if entry else 0,
            "last_access": entry.last_access if entry else 0,
            "sample_rate": entry.sample_rate if entry else 0,
            "duration": entry.duration if entry else 0,
            "channels": entry.channels if entry else 0
        }
    else:
        return {
            "cached": False,
            "file_path": decoded_path
        }


# ==================== 扫描与索引 ====================

@app.post("/api/v1/scan", response_model=schemas.IndexResponse)
async def scan_and_index(request: schemas.ScanRequest):
    """
    扫描音频文件并建立索引
    
    - **folder_path**: 要扫描的文件夹路径
    - **recursive**: 是否递归扫描子文件夹
    """
    # 验证路径安全性，防止路径遍历攻击
    if not validate_path(request.folder_path):
        raise HTTPException(status_code=400, detail="路径包含非法字符")
    
    folder = Path(request.folder_path)
    
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {request.folder_path}")
    
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是文件夹: {request.folder_path}")
    
    try:
        start_time = time.time()
        
        logger.info(f"[SCAN_API] 开始扫描和索引: {folder}")
        
        # 获取或创建索引器
        indexer = get_indexer()
        logger.info(f"[SCAN_API] 获取 indexer 成功: {indexer}")
        
        # 执行索引
        logger.info(f"[SCAN_API] 调用 index_audio_files...")
        result = indexer.index_audio_files(
            folder_path=str(folder),
            recursive=request.recursive
        )
        logger.info(f"[SCAN_API] index_audio_files 返回: {result}")
        
        duration = time.time() - start_time
        
        # 返回扫描到的文件数量
        return schemas.IndexResponse(
            indexed=result.get("added", 0) + result.get("updated", 0),
            skipped=result.get("skipped", 0),
            duration=duration
        )
        
    except Exception as e:
        logger.error(f"扫描索引失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 仅扫描文件（不建索引）====================
@app.post("/api/v1/scan-only", response_model=schemas.ScanResponse)
async def scan_only(request: schemas.ScanRequest):
    """
    仅扫描音频文件，不建立索引（用于没有模型的情况）
    """
    # 验证路径安全性，防止路径遍历攻击
    if not validate_path(request.folder_path):
        raise HTTPException(status_code=400, detail="路径包含非法字符")
    
    folder = Path(request.folder_path)
    
    if not folder.exists():
        raise HTTPException(status_code=404, detail=f"文件夹不存在: {request.folder_path}")
    
    if not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是文件夹: {request.folder_path}")
    
    try:
        from core.scanner import AudioScanner
        scanner = AudioScanner()
        audio_files = scanner.scan(str(folder), request.recursive)
        
        audio_file_list = []
        for f in audio_files:
            audio_file_list.append(schemas.AudioFile(
                path=f.path,
                filename=f.filename,
                duration=f.duration,
                sample_rate=f.sample_rate,
                channels=f.channels,
                format=f.format,
                size=f.size
            ))
        
        return schemas.ScanResponse(
            total=len(audio_file_list),
            files=audio_file_list
        )
        
    except Exception as e:
        logger.error(f"扫描失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 异步导入（带进度推送） ====================

@app.post("/api/v1/import/async")
async def import_folder_async(
    request: schemas.ScanRequest,
    background_tasks: BackgroundTasks,
    client_id: str = Query(default="default")
):
    """
    异步导入文件夹（带进度推送）

    - **folder_path**: 要导入的文件夹路径
    - **recursive**: 是否递归扫描子文件夹
    - **client_id**: WebSocket 客户端标识（前端生成）

    后台执行扫描和导入，通过 WebSocket 推送进度。
    """
    # 验证路径安全性，防止路径遍历攻击
    if not validate_path(request.folder_path):
        raise HTTPException(status_code=400, detail="路径包含非法字符")
    
    folder = Path(request.folder_path)

    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail="无效的文件夹路径")

    task_id = f"scan_{int(time.time() * 1000)}"
    ws_manager = get_ws_manager()
    ws_manager.register_task(task_id, client_id)

    # 后台执行扫描
    background_tasks.add_task(
        _scan_and_import_task,
        task_id=task_id,
        folder_path=str(folder),
        recursive=request.recursive,
        client_id=client_id
    )

    return {"task_id": task_id, "message": "扫描任务已启动"}


async def _scan_and_import_task(
    task_id: str,
    folder_path: str,
    recursive: bool,
    client_id: str
):
    """
    后台扫描导入任务

    执行流程：
    1. 扫描所有音频文件
    2. 检查文件是否已存在于数据库
    3. 对新文件计算波形峰值
    4. 写入 SQLite 数据库
    5. 通过 WebSocket 推送进度
    """
    from core.scanner import AudioScanner
    import librosa
    import numpy as np

    ws_manager = get_ws_manager()
    db_manager = get_db_manager()
    scanner = AudioScanner()

    # 确保 CURRENT_PROJECT_ID 已初始化（防止竞态）
    if not getattr(config, 'CURRENT_PROJECT_ID', None):
        config.CURRENT_PROJECT_ID = 'default'
        logger.warning("[SCAN_TASK] CURRENT_PROJECT_ID 未初始化，已设为 default")

    logger.info(f"[SCAN_TASK] 任务ID: {task_id}, 文件夹: {folder_path}, 递归: {recursive}, 客户端: {client_id}, 工程: {config.CURRENT_PROJECT_ID}")
    logger.info(f"[SCAN_TASK] ws_manager 连接数: {ws_manager.get_connection_count()}")
    logger.info(f"[SCAN_TASK] 活跃连接: {list(ws_manager.active_connections.keys())}")

    try:
        # 第一步：扫描所有文件
        logger.info(f"[SCAN_TASK] 发送扫描状态: 正在扫描文件...")
        await ws_manager.send_scan_status(
            client_id, task_id, "scanning", "正在扫描文件..."
        )
        logger.info(f"[SCAN_TASK] 扫描状态已发送，当前连接数: {ws_manager.get_connection_count()}")

        # 使用 scan_with_structure 获取文件列表和文件夹结构
        audio_files, folder_structure = scanner.scan_with_structure(folder_path, recursive)
        total = len(audio_files)
        logger.info(f"[SCAN_TASK] 扫描完成，找到 {total} 个音频文件")

        # 发送扫描统计日志到前端
        await ws_manager.send_scan_log(
            client_id, task_id, 'info',
            f"扫描完成统计: 找到 {total} 个音频文件",
            {'total': total, 'folder_path': folder_path}
        )

        # 发送文件夹结构到前端
        await ws_manager.send_folder_structure(
            client_id, task_id, folder_structure.dict()
        )

        # 创建导入文件夹映射记录（未分类）
        try:
            from core.scanner import FolderNode

            def collect_folder_paths(node: FolderNode, paths: list):
                """递归收集所有文件夹路径"""
                if node.path:
                    paths.append(node.path)
                for child in node.children:
                    collect_folder_paths(child, paths)

            folder_paths = []
            collect_folder_paths(folder_structure, folder_paths)

            # 为每个文件夹创建映射记录（未分类，user_folder_id 为 None）
            for folder_path in folder_paths:
                db_manager.add_imported_folder_mapping(
                    project_id=config.CURRENT_PROJECT_ID,
                    folder_path=folder_path,
                    user_folder_id=None,
                    folder_name=Path(folder_path).name
                )

            logger.info(f"[SCAN_TASK] 创建了 {len(folder_paths)} 个文件夹映射记录")
        except Exception as e:
            logger.warning(f"[SCAN_TASK] 创建文件夹映射记录失败: {e}")

        if total == 0:
            await ws_manager.send_scan_complete(
                client_id, task_id, 0, 0, 0, "未找到音频文件"
            )
            ws_manager.unregister_task(task_id)
            return

        logger.info(f"开始导入 {total} 个文件到 SQLite")

        # 第二步：逐个处理文件
        added = 0
        skipped = 0
        indexed = 0

        for i, audio_file in enumerate(audio_files):
            # 检查是否取消
            if ws_manager.is_task_cancelled(task_id):
                await ws_manager.send_scan_complete(
                    client_id, task_id, total, added, i - added,
                    "用户取消"
                )
                ws_manager.unregister_task(task_id)
                return

            current_file = Path(audio_file.path).name
            progress_pct = int((i / total) * 100)

            # 发送进度 - 扫描阶段 (0-40%)
            await ws_manager.send_scan_progress(
                client_id, task_id, i, total, current_file, "scanning",
                progress=int(progress_pct * 0.4)
            )

            # 检查是否已存在（按当前工程过滤）
            if db_manager.file_exists(audio_file.path, project_id=config.CURRENT_PROJECT_ID):
                skipped += 1
                continue

            # 发送进度 - 分析阶段 (40-70%)
            await ws_manager.send_scan_progress(
                client_id, task_id, i, total, current_file, "analyzing",
                progress=int(40 + progress_pct * 0.3)
            )

            # 计算波形峰值
            peaks_json = None
            try:
                # 使用 librosa 加载并计算峰值
                y, sr = librosa.load(audio_file.path, sr=None, mono=True)

                # 降采样到 2000 个点
                target_points = 2000
                samples_per_point = max(1, len(y) // target_points)
                peaks = []
                for j in range(target_points):
                    start = j * samples_per_point
                    end = min((j + 1) * samples_per_point, len(y))
                    if end > start:
                        peak = float(np.max(np.abs(y[start:end])))
                        peaks.append(peak)

                peaks_json = json.dumps(peaks)
            except Exception as e:
                logger.warning(f"计算波形失败 {audio_file.path}: {e}")

            # 发送进度 - 保存到数据库阶段 (70-85%)
            await ws_manager.send_scan_progress(
                client_id, task_id, i, total, current_file, "saving",
                progress=int(70 + progress_pct * 0.15)
            )

            # 写入数据库（使用当前工程ID）
            record = AudioFileRecord(
                path=audio_file.path,
                filename=audio_file.filename,
                duration=audio_file.duration,
                sample_rate=audio_file.sample_rate,
                channels=audio_file.channels,
                file_size=audio_file.size,
                peaks_json=peaks_json,
                tags='[]'
            )
            success = db_manager.add_file(record, config.CURRENT_PROJECT_ID)
            if success:
                added += 1
                if added % 10 == 0:
                    logger.info(f"[SCAN_TASK] 已添加 {added} 个文件，当前: {current_file}")

                # 发送进度 - 向量索引阶段 (85-100%)
                await ws_manager.send_scan_progress(
                    client_id, task_id, i, total, current_file, "indexing",
                    progress=int(85 + progress_pct * 0.15)
                )

                try:
                    if is_embedder_available():
                        indexer = get_indexer()
                        indexer.add_single_audio(audio_file.path, metadata={
                            "filename": audio_file.filename,
                            "duration": audio_file.duration,
                            "sample_rate": audio_file.sample_rate,
                            "channels": audio_file.channels,
                            "format": audio_file.format,
                            "size": audio_file.size
                        })
                        indexed += 1
                except Exception as e:
                    logger.warning(f"生成语义索引失败 {audio_file.path}: {e}")

        await ws_manager.send_scan_complete(
            client_id, task_id, total, added, skipped
        )
        ws_manager.unregister_task(task_id)
        logger.info(f"导入完成: 新增 {added} 个文件，跳过 {skipped} 个")

    except Exception as e:
        logger.error(f"扫描导入失败: {e}")
        await ws_manager.send_scan_error(client_id, task_id, str(e))
        ws_manager.unregister_task(task_id)


# ==================== SQLite 数据库 API ====================

@app.get("/api/v1/db/files")
async def get_all_db_files():
    """
    从 SQLite 获取当前工程的所有文件列表（启动时加载）

    返回格式：
    {
        "total": 1000,
        "files": [
            {
                "path": "/path/to/file.wav",
                "filename": "file.wav",
                "duration": 12.5,
                "sample_rate": 48000,
                "channels": 2,
                "size": 1234567,
                "peaks": [0.1, 0.2, ...],  # 波形峰值数组
                "tags": ["标签1", "标签2"],
                "created_at": "2024-01-01T00:00:00"
            }
        ]
    }
    """
    import time
    start_time = time.time()

    logger.info(f"[DB_FILES] 开始获取文件列表")

    try:
        db_manager = get_db_manager()
        current_project_id = getattr(config, 'CURRENT_PROJECT_ID', 'default')
        files = db_manager.get_files_by_project(current_project_id)
        logger.info(f"[DB_FILES] 从数据库读取 {len(files)} 个文件, 工程: {current_project_id}")

        # 构建返回数据
        result_files = []
        total_peaks_size = 0
        long_durations = []

        for f in files:
            peaks = f.get_peaks()
            peaks_len = len(peaks) if peaks else 0
            total_peaks_size += peaks_len

            if f.duration > 60:
                long_durations.append({'filename': f.filename, 'duration': f.duration, 'peaks_len': peaks_len})

            result_files.append({
                "path": f.path,
                "filename": f.filename,
                "duration": f.duration,
                "sample_rate": f.sample_rate,
                "channels": f.channels,
                "size": f.file_size,
                "peaks": peaks,
                "tags": f.get_tags(),
                "created_at": f.created_at
            })

        elapsed = time.time() - start_time
        logger.info(f"[DB_FILES] 构建返回数据完成: {len(files)} 个文件, 耗时 {elapsed:.3f}秒")

        return {
            "total": len(files),
            "files": result_files
        }
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/db/file/{path:path}")
async def get_db_file(path: str):
    """
    获取单个文件详情

    - **path**: URL 编码的文件路径
    """
    import urllib.parse
    file_path = urllib.parse.unquote(path)

    db_manager = get_db_manager()
    record = db_manager.get_file(file_path)

    if not record:
        raise HTTPException(status_code=404, detail="文件不存在")

    return {
        "path": record.path,
        "filename": record.filename,
        "duration": record.duration,
        "sample_rate": record.sample_rate,
        "channels": record.channels,
        "size": record.file_size,
        "peaks": record.get_peaks(),
        "tags": record.get_tags(),
        "created_at": record.created_at
    }


@app.put("/api/v1/db/file/{path:path}/tags")
async def update_file_tags(path: str, tags: List[str] = Body(...)):
    """
    更新文件标签

    - **path**: URL 编码的文件路径
    - **tags**: 新的标签列表
    """
    import urllib.parse
    file_path = urllib.parse.unquote(path)

    db_manager = get_db_manager()
    success = db_manager.update_tags(file_path, tags)

    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")

    return {"success": True, "message": "标签已更新"}


@app.delete("/api/v1/db/file/{path:path}")
async def delete_db_file(path: str):
    """
    从数据库删除文件记录

    - **path**: URL 编码的文件路径
    """
    import urllib.parse
    file_path = urllib.parse.unquote(path)

    db_manager = get_db_manager()
    success = db_manager.delete_file(file_path)

    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")

    return {"success": True, "message": "文件已删除"}


@app.get("/api/v1/db/stats")
async def get_db_stats():
    """
    获取数据库统计信息
    """
    try:
        db_manager = get_db_manager()
        return {
            "total_files": db_manager.get_file_count(),
            "total_duration": db_manager.get_total_duration()
        }
    except Exception as e:
        logger.error(f"获取统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 语义搜索 ====================

@app.post("/api/v1/search", response_model=schemas.SearchResponse)
async def search_audio(request: schemas.SearchRequest):
    """
    语义搜索音频（同步版本，保持向后兼容）

    - **query**: 自然语言查询（如"清脆的铃铛声"）
    - **top_k**: 返回结果数量（默认 20）
    - **threshold**: 相似度阈值（默认 0.15）
    """
    try:
        from core.search_engine import get_optimized_searcher_sync

        searcher = get_optimized_searcher_sync()

        filters = {}
        if request.min_duration is not None:
            filters["duration"] = {"$gte": request.min_duration}
        if request.max_duration is not None:
            if "duration" in filters:
                filters["duration"]["$lte"] = request.max_duration
            else:
                filters["duration"] = {"$lte": request.max_duration}
        if request.sample_rate is not None:
            filters["sample_rate"] = request.sample_rate
        if request.channels is not None:
            filters["channels"] = request.channels
        if request.format is not None:
            filters["format"] = request.format

        # 使用异步搜索但不推送进度
        results, stats = await searcher.search_async(
            query=request.query,
            top_k=request.top_k,
            min_similarity=request.threshold,
            filters=filters if filters else None,
            use_cache=True
        )

        logger.info(f"搜索 '{request.query}': 找到 {len(results)} 个结果, 耗时 {stats.get('duration', 0):.3f}s, 缓存命中: {stats.get('cache_hit', False)}")

        # 转换为响应格式
        search_results = []
        for r in results:
            # 使用 getattr 避免与 Python 内置 format 函数冲突
            file_format = getattr(r, 'format', '') or ''
            audio_file = schemas.AudioFile(
                path=r.file_path,
                filename=r.filename,
                duration=r.duration,
                sample_rate=r.metadata.get("sample_rate", 0),
                channels=r.metadata.get("channels", 0),
                format=file_format,
                size=r.metadata.get("size", 0)
            )
            search_results.append(schemas.SearchResult(
                audio_file=audio_file,
                score=r.similarity,
                distance=1.0 - r.similarity,
                metadata={
                    "semantic_score": r.metadata.get("semantic_score", r.similarity),
                    "keyword_score": r.metadata.get("keyword_score", 0.0),
                    "keyword_only": r.metadata.get("keyword_score", 0.0) > r.metadata.get("semantic_score", r.similarity)
                }
            ))

        # 分页处理
        total = len(search_results)
        page = request.page
        page_size = request.page_size
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_results = search_results[start_idx:end_idx]
        total_pages = (total + page_size - 1) // page_size

        return schemas.SearchResponse(
            query=request.query,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            results=paginated_results
        )

    except Exception as e:
        import traceback
        logger.error(f"搜索失败: {e}")
        logger.error(f"搜索失败详细堆栈: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/search/async")
async def search_audio_async(
    request: schemas.SearchRequest,
    background_tasks: BackgroundTasks,
    client_id: str = Query(default="default")
):
    """
    异步语义搜索（带 WebSocket 进度推送）

    - **query**: 自然语言查询
    - **top_k**: 返回结果数量
    - **threshold**: 相似度阈值
    - **client_id**: WebSocket 客户端标识

    返回搜索任务ID，通过 WebSocket 接收进度和结果
    """
    try:
        search_id = f"search_{int(time.time() * 1000)}"

        # 后台执行搜索
        background_tasks.add_task(
            _search_task,
            search_id=search_id,
            query=request.query,
            top_k=request.top_k,
            min_similarity=request.threshold,
            filters={
                k: v for k, v in {
                    "min_duration": request.min_duration,
                    "max_duration": request.max_duration,
                    "sample_rate": request.sample_rate,
                    "channels": request.channels,
                    "format": request.format
                }.items() if v is not None
            },
            client_id=client_id
        )

        return {"search_id": search_id, "message": "搜索任务已启动"}

    except Exception as e:
        logger.error(f"启动搜索任务失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _search_task(
    search_id: str,
    query: str,
    top_k: Optional[int],
    min_similarity: Optional[float],
    filters: Dict[str, Any],
    client_id: str
):
    """
    后台搜索任务（带 WebSocket 进度推送）
    """
    from core.search_engine import get_optimized_searcher_sync

    ws_manager = get_ws_manager()
    searcher = get_optimized_searcher_sync()

    # 构建过滤条件
    where_clause = {}
    if filters.get("min_duration") is not None:
        where_clause["duration"] = {"$gte": filters["min_duration"]}
    if filters.get("max_duration") is not None:
        if "duration" in where_clause:
            where_clause["duration"]["$lte"] = filters["max_duration"]
        else:
            where_clause["duration"] = {"$lte": filters["max_duration"]}
    if filters.get("sample_rate") is not None:
        where_clause["sample_rate"] = filters["sample_rate"]
    if filters.get("channels") is not None:
        where_clause["channels"] = filters["channels"]
    if filters.get("format") is not None:
        where_clause["format"] = filters["format"]

    try:
        # 定义进度回调
        async def progress_callback(stage: str, progress: float):
            await ws_manager.send_search_progress(
                client_id=client_id,
                search_id=search_id,
                stage=stage,
                progress=progress,
                message=f"搜索阶段: {stage}"
            )

        # 执行搜索
        results, stats = await searcher.search_async(
            query=query,
            top_k=top_k,
            min_similarity=min_similarity,
            filters=where_clause if where_clause else None,
            use_cache=True,
            progress_callback=progress_callback
        )

        # 发送完成消息
        await ws_manager.send_search_complete(
            client_id=client_id,
            search_id=search_id,
            results_count=len(results),
            duration=stats.get("duration", 0),
            cache_hit=stats.get("cache_hit", False)
        )

        # 发送搜索结果
        search_results_data = []
        for r in results:
            search_results_data.append({
                "path": r.file_path,
                "filename": r.filename,
                "duration": r.duration,
                "format": r.format,
                "similarity": r.similarity,
                "metadata": r.metadata
            })

        await ws_manager.broadcast({
            "type": "search_results",
            "search_id": search_id,
            "data": {
                "query": query,
                "total": len(results),
                "results": search_results_data,
                "stats": stats
            }
        }, client_id)

        logger.info(f"异步搜索 '{query}' 完成: 找到 {len(results)} 个结果, 耗时 {stats.get('duration', 0):.3f}s")

    except Exception as e:
        logger.error(f"异步搜索失败: {e}")
        await ws_manager.send_search_error(
            client_id=client_id,
            search_id=search_id,
            error=str(e)
        )


@app.get("/api/v1/search/cache/stats")
async def get_search_cache_stats():
    """
    获取搜索缓存统计信息
    """
    try:
        from core.search_engine import get_optimized_searcher_sync
        searcher = get_optimized_searcher_sync()
        stats = searcher.get_cache_stats()
        return {
            "status": "success",
            "cache_stats": stats
        }
    except Exception as e:
        logger.error(f"获取缓存统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/search/cache/clear")
async def clear_search_cache():
    """
    清空搜索缓存
    """
    try:
        from core.search_engine import get_optimized_searcher_sync
        searcher = get_optimized_searcher_sync()
        await searcher.clear_cache()
        return {
            "status": "success",
            "message": "搜索缓存已清空"
        }
    except Exception as e:
        logger.error(f"清空缓存失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 音频波形 ====================

@app.get("/api/waveform")
async def get_waveform(path: str = Query(..., description="音频文件路径")):
    """
    获取音频波形数据

    优先从 SQLite 数据库读取缓存的波形数据，如果没有则实时计算并缓存。
    将原始波形降采样到 2000 个峰值点，用于前端波形显示。

    返回格式：
    {
        "peaks": [0.1, 0.4, -0.3, ...],  # 降采样后的峰值数组
        "duration": 12.4,                  # 时长（秒）
        "sample_rate": 48000,               # 采样率
        "channels": 2,                       # 声道数
        "cached": true                       # 是否从缓存读取
    }
    """
    import urllib.parse
    import librosa
    import numpy as np
    import soundfile as sf

    file_path = urllib.parse.unquote(path)
    audio_file = config.validate_audio_path(file_path)

    try:
        # 首先尝试从数据库获取缓存的波形数据
        db_manager = get_db_manager()
        record = db_manager.get_file(file_path)

        if record and record.peaks_json:
            # 使用缓存的波形数据
            logger.debug(f"波形数据从缓存读取: {file_path}")
            return {
                "peaks": record.get_peaks(),
                "duration": record.duration,
                "sample_rate": record.sample_rate,
                "channels": record.channels,
                "cached": True
            }

        # 没有缓存，实时计算
        logger.info(f"波形数据未缓存，实时计算: {file_path}")

        # 使用 soundfile 获取基本信息（比 librosa 更快）
        info = sf.info(str(audio_file))
        duration = info.duration
        sr = info.samplerate
        channels = info.channels

        # 加载音频计算波形
        y, sr = librosa.load(str(audio_file), sr=None, mono=True)  # 直接转单声道，节省内存

        # 降采样到 2000 个点
        target_points = 2000
        samples_per_point = len(y) // target_points

        if samples_per_point > 0:
            # 计算每个区间的峰值（绝对值最大）
            peaks = []
            for i in range(target_points):
                start = i * samples_per_point
                end = min((i + 1) * samples_per_point, len(y))
                segment = y[start:end]
                if len(segment) > 0:
                    peak = float(np.max(np.abs(segment)))
                    peaks.append(peak)
                else:
                    peaks.append(0.0)
        else:
            # 如果音频太短，直接返回全部数据
            peaks = y.tolist()[:target_points]

        # 保存到数据库缓存
        if record:
            db_manager.update_peaks(file_path, peaks)
            logger.debug(f"波形数据已缓存到数据库: {file_path}")

        return {
            "peaks": peaks,
            "duration": duration,
            "sample_rate": sr,
            "channels": channels,
            "cached": False
        }

    except Exception as e:
        logger.error(f"获取波形失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 音频文件服务 ====================
# 注意：具体子路由必须在通用路由 /api/v1/audio/{file_path} 之前定义
# 否则 FastAPI 会错误匹配，例如 /api/v1/audio/stream/test.wav
# 会被匹配为 file_path="stream/test.wav" 而不是调用 stream 端点

@app.get("/api/v1/audio/decoded/{file_path:path}")
async def get_decoded_audio(file_path: str):
    """
    获取已解码的音频数据（使用 LRU 缓存）

    第二次点击同一文件时可以享受缓存加速

    返回格式：
    {
        "path": "/path/to/file.wav",
        "filename": "file.wav",
        "cached": true,                    # 是否从缓存获取
        "sample_rate": 48000,               # 采样率
        "channels": 2,                       # 声道数
        "duration": 12.4,                   # 时长（秒）
        "size": 1234567,                    # 原始文件大小
        "memory_size": 4243456,             # 内存占用（字节）
        "waveform_peaks": [0.1, 0.4, ...]  # 波形峰值（2000点）
    }
    """
    import urllib.parse
    import time
    import numpy as np
    import soundfile as sf

    file_path = urllib.parse.unquote(file_path)
    audio_file = config.validate_audio_path(file_path)

    start_time = time.time()
    cache = get_audio_cache()

    # 尝试从缓存获取
    cached_entry = cache.get(file_path)

    if cached_entry:
        # 命中缓存
        cached_flag = True
        audio_data = cached_entry.audio_data
        sample_rate = cached_entry.sample_rate
        channels = cached_entry.channels
        duration = cached_entry.duration
    else:
        # 未命中，从磁盘加载
        cached_flag = False
        try:
            audio_data, sample_rate = librosa.load(str(audio_file), sr=None, mono=False)
            info = sf.info(str(audio_file))
            channels = info.channels
            duration = info.duration

            # 放入缓存
            from core.audio_cache import AudioCacheEntry
            entry = AudioCacheEntry(
                audio_data=audio_data,
                sample_rate=sample_rate,
                duration=duration,
                last_access=time.time(),
                file_size=audio_file.stat().st_size,
                channels=channels
            )
            cache.put(file_path, entry)
        except Exception as e:
            logger.error(f"解码音频失败 {file_path}: {e}")
            raise HTTPException(status_code=500, detail=f"解码失败: {str(e)}")

    # 生成波形峰值
    if audio_data.ndim > 1:
        audio_mono = np.mean(audio_data, axis=0)
    else:
        audio_mono = audio_data

    target_points = 2000
    samples_per_point = max(1, len(audio_mono) // target_points)
    peaks = []
    for i in range(target_points):
        start = i * samples_per_point
        end = min((i + 1) * samples_per_point, len(audio_mono))
        if end > start:
            peak = float(np.max(np.abs(audio_mono[start:end])))
            peaks.append(peak)
        else:
            peaks.append(0.0)

    load_time_ms = round((time.time() - start_time) * 1000, 2)

    return {
        "path": file_path,
        "filename": audio_file.name,
        "cached": cached_flag,
        "load_time_ms": load_time_ms,
        "sample_rate": sample_rate,
        "channels": channels,
        "duration": duration,
        "size": audio_file.stat().st_size,
        "memory_size": audio_data.nbytes,
        "waveform_peaks": peaks
    }


@app.post("/api/v1/audio/preload/{file_path:path}")
async def preload_audio_to_cache(file_path: str):
    """
    预加载音频到 LRU 缓存

    用于提前缓存即将播放的文件

    返回格式：
    {
        "success": true,
        "path": "/path/to/file.wav",
        "cached": true,
        "memory_mb": 12.5
    }
    """
    import urllib.parse
    import time
    import soundfile as sf

    file_path = urllib.parse.unquote(file_path)
    audio_file = config.validate_audio_path(file_path)

    cache = get_audio_cache()

    # 检查是否已在缓存中
    if file_path in cache:
        entry = cache.get(file_path)
        return {
            "success": True,
            "path": file_path,
            "cached": True,
            "memory_mb": round(entry.memory_size / (1024 * 1024), 2) if entry else 0
        }

    # 从磁盘加载并缓存
    try:
        start_time = time.time()
        audio_data, sample_rate = librosa.load(str(audio_file), sr=None, mono=False)
        info = sf.info(str(audio_file))

        from core.audio_cache import AudioCacheEntry
        entry = AudioCacheEntry(
            audio_data=audio_data,
            sample_rate=sample_rate,
            duration=info.duration,
            last_access=time.time(),
            file_size=audio_file.stat().st_size,
            channels=info.channels
        )

        cache.put(file_path, entry)
        load_time_ms = round((time.time() - start_time) * 1000, 2)

        return {
            "success": True,
            "path": file_path,
            "cached": False,
            "memory_mb": round(entry.memory_size / (1024 * 1024), 2),
            "load_time_ms": load_time_ms
        }

    except Exception as e:
        logger.error(f"预加载失败 {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"预加载失败: {str(e)}")


@app.get("/api/v1/audio/stream/{file_path:path}")
async def stream_audio_from_cache(file_path: str):
    """
    从 LRU 缓存流式获取音频数据（返回 WAV bytes）

    如果缓存命中则直接返回缓存数据（已解码的 numpy 数组转 WAV）
    如果缓存未命中则先加载到缓存再返回

    用于前端播放，实现真正的内存缓存加速

    返回：audio/wav 格式的二进制数据
    """
    import urllib.parse
    import io
    import librosa
    import soundfile as sf
    import numpy as np

    file_path = urllib.parse.unquote(file_path)
    audio_file = config.validate_audio_path(file_path)

    cache = get_audio_cache()

    cached_entry = cache.get(file_path)

    if cached_entry:
        audio_data = cached_entry.audio_data
        sample_rate = cached_entry.sample_rate
    else:
        try:
            audio_data, sample_rate = librosa.load(str(audio_file), sr=None, mono=False)
            info = sf.info(str(audio_file))

            from core.audio_cache import AudioCacheEntry
            entry = AudioCacheEntry(
                audio_data=audio_data,
                sample_rate=sample_rate,
                duration=info.duration,
                last_access=time.time(),
                file_size=audio_file.stat().st_size,
                channels=info.channels
            )
            cache.put(file_path, entry)
        except Exception as e:
            logger.error(f"加载音频失败 {file_path}: {e}")
            raise HTTPException(status_code=500, detail=f"加载失败: {str(e)}")

    try:
        buffer = io.BytesIO()
        sf.write(buffer, audio_data.T if audio_data.ndim > 1 else audio_data, sample_rate, format='WAV')
        buffer.seek(0)
        wav_bytes = buffer.read()

        # 处理中文文件名：使用 ASCII 文件名避免编码问题
        filename = audio_file.name
        # 将中文文件名转为 ASCII 表示（使用 URL 编码或替换）
        try:
            # 尝试使用 RFC 5987 编码
            from urllib.parse import quote
            encoded_filename = quote(filename, safe='')
            content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"
        except:
            # 回退：使用纯 ASCII 文件名
            safe_filename = os.path.basename(file_path)
            safe_filename = ''.join(c if c.isalnum() or c in '._-' else '_' for c in safe_filename)
            if not safe_filename.endswith('.wav'):
                safe_filename += '.wav'
            content_disposition = f'attachment; filename="{safe_filename}"'

        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers={
                "Content-Disposition": content_disposition,
                "X-Cached": "true" if cached_entry else "false",
                "X-Duration": str(audio_data.shape[-1] / sample_rate if audio_data.ndim > 1 else len(audio_data) / sample_rate),
            }
        )
    except Exception as e:
        logger.error(f"生成 WAV 失败 {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"生成音频失败: {str(e)}")


# ==================== 通用音频文件服务（必须放在子路由之后）====================

@app.get("/api/v1/audio/{file_path:path}")
async def get_audio(file_path: str = PathParam(..., description="音频文件路径")):
    """
    提供音频文件播放服务（通用路由，放在子路由之后）

    支持范围请求（用于前端波形显示和流式播放）
    """
    import urllib.parse
    file_path = urllib.parse.unquote(file_path)

    audio_file = config.validate_audio_path(file_path)

    # 获取文件大小
    file_size = audio_file.stat().st_size

    # 获取 MIME 类型
    mime_types = {
        '.wav': 'audio/wav',
        '.mp3': 'audio/mpeg',
        '.flac': 'audio/flac',
        '.aiff': 'audio/aiff',
        '.aif': 'audio/aiff',
        '.ogg': 'audio/ogg',
        '.m4a': 'audio/mp4',
        '.aac': 'audio/aac'
    }
    mime_type = mime_types.get(audio_file.suffix.lower(), 'application/octet-stream')

    # 创建文件响应，支持范围请求
    return FileResponse(
        path=str(audio_file),
        media_type=mime_type,
        filename=audio_file.name
    )


# ==================== 索引状态 ====================

@app.get("/api/v1/index/status", response_model=schemas.IndexStatus)
async def get_index_status():
    """获取当前索引状态"""
    try:
        from core.search_engine import get_optimized_searcher_sync
        searcher = get_optimized_searcher_sync()
        stats = searcher.get_collection_stats()

        return schemas.IndexStatus(
            total_files=stats.get("total_count", 0),
            indexed_files=stats.get("total_count", 0)
        )
    except Exception as e:
        logger.error(f"获取索引状态失败: {e}")
        return schemas.IndexStatus(total_files=0, indexed_files=0)


# ==================== 文件列表 ====================

@app.get("/api/v1/files", response_model=schemas.ScanResponse)
async def get_indexed_files():
    """获取所有已索引的文件列表"""
    try:
        from core.search_engine import get_optimized_searcher_sync
        searcher = get_optimized_searcher_sync()
        files = searcher.get_all_indexed_files()

        audio_files = []
        for f in files:
            audio_files.append(schemas.AudioFile(
                path=f.get("file_path", ""),
                filename=f.get("filename", ""),
                duration=f.get("duration", 0.0),
                sample_rate=0,
                channels=0,
                format=f.get("format", ""),
                size=f.get("size", 0)
            ))

        return schemas.ScanResponse(
            total=len(audio_files),
            files=audio_files
        )
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 音频裁切 ====================

@app.post("/api/export/clip", response_model=schemas.ClipResponse)
async def export_clip(request: schemas.ClipRequest):
    """
    裁切音频片段

    - **path**: 源音频文件路径
    - **start**: 裁切起始时间（秒）
    - **end**: 裁切结束时间（秒）
    - **output**: 输出文件路径（可选，默认在原文件同目录添加 _clip 后缀）
    - **temp_file**: 是否创建临时文件（用于拖拽导出，会在系统临时目录创建）
    """
    import soundfile as sf
    import numpy as np
    import os

    logger.info(f"[裁切请求] path={request.path}, start={request.start}, end={request.end}, temp_file={request.temp_file}")

    source_file = config.validate_audio_path(request.path)

    if request.start >= request.end:
        raise HTTPException(status_code=400, detail="起始时间必须小于结束时间")

    try:
        # 读取音频，获取原始采样率
        audio, sr = sf.read(str(source_file))

        # 获取原始音频的格式信息
        subtype = None
        if hasattr(source_file, 'suffix'):
            # 根据文件格式确定 subtype
            suffix = source_file.suffix.lower()
            if suffix in ['.wav']:
                # WAV 文件保留原始格式
                try:
                    info = sf.info(str(source_file))
                    subtype = info.subtype if hasattr(info, 'subtype') else 'PCM_16'
                except:
                    subtype = 'PCM_16'

        # 计算裁切的样本位置
        start_sample = int(request.start * sr)
        end_sample = int(request.end * sr)

        # 边界检查
        if start_sample >= len(audio):
            raise HTTPException(status_code=400, detail="起始时间超出音频时长")

        end_sample = min(end_sample, len(audio))

        # 裁切
        clipped_audio = audio[start_sample:end_sample]

        # 生成输出路径
        if request.temp_file:
            import uuid
            temp_name = f"clip_{int(time.time())}_{uuid.uuid4().hex[:8]}{source_file.suffix}"
            output_path = Path(config.get_temp_clip_dir()) / temp_name
            logger.info(f"[裁切] 使用临时目录: {output_path}")
        elif request.output:
            # 验证输出路径安全性，防止路径遍历攻击
            if not validate_path(request.output):
                raise HTTPException(status_code=400, detail="输出路径包含非法字符")
            output_path = Path(request.output)
            logger.info(f"[裁切] 使用指定输出路径: {output_path}")
        else:
            output_path = source_file.parent / f"{source_file.stem}_clip{source_file.suffix}"
            logger.info(f"[裁切] 使用原目录: {output_path}")

        # 确保输出目录存在
        output_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"[裁切] 保存文件到: {output_path}")

        # 保存 - 保留原始采样率和格式
        if subtype:
            sf.write(str(output_path), clipped_audio, sr, subtype=subtype)
        else:
            sf.write(str(output_path), clipped_audio, sr)

        # 不再自动清理临时文件，由用户自行管理
        # 保留所有裁切后的文件供用户使用

        duration = len(clipped_audio) / sr

        return schemas.ClipResponse(
            success=True,
            output_path=str(output_path),
            duration=duration,
            message=f"成功裁切 {request.start:.2f}s - {request.end:.2f}s"
        )

    except Exception as e:
        logger.error(f"裁切失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 删除临时文件 ====================

@app.delete("/api/temp/{file_path:path}")
async def delete_temp_file(file_path: str):
    """
    删除临时文件

    - **file_path**: 临时文件路径（URL编码）
    """
    import os

    try:
        # 解码URL编码的路径
        from urllib.parse import unquote
        file_path = unquote(file_path)

        # 安全检查：确保文件在临时目录内
        abs_path = Path(file_path).resolve()
        temp_dir_path = Path(config.get_temp_clip_dir()).resolve()
        try:
            abs_path.relative_to(temp_dir_path)
        except ValueError:
            raise HTTPException(status_code=400, detail="只能删除临时目录中的文件")

        # 删除文件
        if abs_path.exists():
            abs_path.unlink()
            return {"success": True, "message": f"已删除临时文件: {abs_path.name}"}
        else:
            return {"success": True, "message": "文件不存在"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除临时文件失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 音频淡入淡出 ====================

@app.post("/api/audio/fade", response_model=schemas.FadeResponse)
async def audio_fade(request: schemas.FadeRequest):
    """
    音频淡入淡出
    
    - **path**: 音频文件路径
    - **fade_in**: 淡入时长（秒）
    - **fade_out**: 淡出时长（秒）
    - **output**: 输出文件路径（可选，默认在原文件同目录添加 _fade 后缀）
    """
    import soundfile as sf
    import numpy as np

    source_file = config.validate_audio_path(request.path)
    
    try:
        # 读取音频
        audio, sr = sf.read(str(source_file))
        duration_samples = len(audio)
        duration_seconds = duration_samples / sr
        
        # 确保 fade 时间不超过音频时长
        fade_in_samples = min(int(request.fade_in * sr), duration_samples)
        fade_out_samples = min(int(request.fade_out * sr), duration_samples)
        
        # 淡入：线性增益从 0 到 1
        if fade_in_samples > 0:
            fade_in_curve = np.linspace(0, 1, fade_in_samples)
            if audio.ndim == 1:
                audio[:fade_in_samples] = audio[:fade_in_samples] * fade_in_curve
            else:
                for ch in range(audio.shape[1]):
                    audio[:fade_in_samples, ch] = audio[:fade_in_samples, ch] * fade_in_curve.reshape(-1, 1)
        
        # 淡出：线性增益从 1 到 0
        if fade_out_samples > 0:
            fade_out_curve = np.linspace(1, 0, fade_out_samples)
            start_idx = duration_samples - fade_out_samples
            if audio.ndim == 1:
                audio[start_idx:] = audio[start_idx:] * fade_out_curve
            else:
                for ch in range(audio.shape[1]):
                    audio[start_idx:, ch] = audio[start_idx:, ch] * fade_out_curve.reshape(-1, 1)
        
        # 生成输出路径
        if request.output:
            # 验证输出路径安全性，防止路径遍历攻击
            if not validate_path(request.output):
                raise HTTPException(status_code=400, detail="输出路径包含非法字符")
            output_path = Path(request.output)
        else:
            output_path = source_file.parent / f"{source_file.stem}_fade{source_file.suffix}"
        
        # 保存
        sf.write(str(output_path), audio, sr)
        
        return schemas.FadeResponse(
            success=True,
            output_path=str(output_path),
            message=f"淡入: {request.fade_in}s, 淡出: {request.fade_out}s"
        )
        
    except Exception as e:
        logger.error(f"淡入淡出处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 裁切并淡入淡出 ====================

@app.post("/api/export/clip_with_fade", response_model=schemas.ClipResponse)
async def export_clip_with_fade(request: schemas.ClipWithFadeRequest):
    """
    裁切音频片段并应用淡入淡出

    - **path**: 源音频文件路径
    - **start**: 裁切起始时间（秒）
    - **end**: 裁切结束时间（秒）
    - **fade_in**: 淡入时长（秒）
    - **fade_out**: 淡出时长（秒）
    - **temp_file**: 是否创建临时文件
    """
    import soundfile as sf
    import numpy as np
    import uuid

    source_file = config.validate_audio_path(request.path)

    if request.start >= request.end:
        raise HTTPException(status_code=400, detail="起始时间必须小于结束时间")

    try:
        info = sf.info(str(source_file))
        sr = info.samplerate
        channels = info.channels
        subtype = info.subtype if hasattr(info, 'subtype') else 'PCM_16'

        start_sample = int(request.start * sr)
        end_sample = int(request.end * sr)

        if start_sample >= info.frames:
            raise HTTPException(status_code=400, detail="起始时间超出音频时长")

        end_sample = min(end_sample, info.frames)

        with sf.SoundFile(str(source_file)) as f:
            f.seek(start_sample)
            clipped_audio = f.read(frames=end_sample - start_sample, dtype='float32')

        if clipped_audio.ndim > 1 and clipped_audio.shape[1] != channels:
            channels = clipped_audio.shape[1]

        duration_samples = len(clipped_audio)
        fade_in_samples = min(int(request.fade_in * sr), duration_samples)
        fade_out_samples = min(int(request.fade_out * sr), duration_samples)

        if fade_in_samples > 0:
            fade_in_curve = np.linspace(0, 1, fade_in_samples)
            if clipped_audio.ndim == 1:
                clipped_audio[:fade_in_samples] = clipped_audio[:fade_in_samples] * fade_in_curve
            else:
                for ch in range(clipped_audio.shape[1]):
                    clipped_audio[:fade_in_samples, ch] = clipped_audio[:fade_in_samples, ch] * fade_in_curve.reshape(-1, 1)

        if fade_out_samples > 0:
            fade_out_curve = np.linspace(1, 0, fade_out_samples)
            start_idx = duration_samples - fade_out_samples
            if clipped_audio.ndim == 1:
                clipped_audio[start_idx:] = clipped_audio[start_idx:] * fade_out_curve
            else:
                for ch in range(clipped_audio.shape[1]):
                    clipped_audio[start_idx:, ch] = clipped_audio[start_idx:, ch] * fade_out_curve.reshape(-1, 1)

        if request.temp_file:
            temp_name = f"clip_fade_{int(time.time())}_{uuid.uuid4().hex[:8]}{source_file.suffix}"
            output_path = Path(config.get_temp_clip_dir()) / temp_name
        else:
            output_path = source_file.parent / f"{source_file.stem}_clip_fade{source_file.suffix}"

        sf.write(str(output_path), clipped_audio, sr, subtype=subtype)

        duration = len(clipped_audio) / sr

        return schemas.ClipResponse(
            success=True,
            output_path=str(output_path),
            duration=duration,
            message=f"裁切 {request.start:.2f}s - {request.end:.2f}s, 淡入 {request.fade_in}s, 淡出 {request.fade_out}s"
        )

    except Exception as e:
        logger.error(f"裁切并淡入淡出失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 验证临时文件 ====================

@app.get("/api/clip/verify")
async def verify_clip(file_path: str = Query(..., description="临时文件路径")):
    """
    验证临时文件是否存在

    - **file_path**: 临时文件路径（URL编码）
    """
    import urllib.parse

    decoded_path = urllib.parse.unquote(file_path)
    abs_path = Path(decoded_path).resolve()
    temp_dir_path = Path(config.get_temp_clip_dir()).resolve()
    try:
        abs_path.relative_to(temp_dir_path)
    except ValueError:
        raise HTTPException(status_code=400, detail="只能验证临时目录中的文件")

    exists = abs_path.exists() and abs_path.is_file()

    return {
        "exists": exists,
        "path": str(abs_path),
        "size": abs_path.stat().st_size if exists else 0
    }


# ==================== 临时文件路径配置 ====================

@app.get("/api/v1/config/temp-dir", response_model=schemas.TempDirResponse)
async def get_temp_dir():
    """
    获取当前临时文件存放目录
    """
    return schemas.TempDirResponse(
        temp_dir=config.get_temp_clip_dir(),
        default_dir=config.DEFAULT_TEMP_CLIP_DIR
    )


@app.post("/api/v1/config/temp-dir", response_model=schemas.TempDirResponse)
async def set_temp_dir(request: schemas.TempDirRequest):
    """
    设置临时文件存放目录
    
    - **temp_dir**: 新的临时文件目录路径
    """
    import json
    
    new_dir = request.temp_dir
    
    # 验证路径安全性，防止路径遍历攻击
    if not validate_path(new_dir):
        raise HTTPException(status_code=400, detail="路径包含非法字符")
    
    # 验证路径是否存在
    if not os.path.exists(new_dir):
        raise HTTPException(status_code=400, detail="指定的目录不存在")
    
    if not os.path.isdir(new_dir):
        raise HTTPException(status_code=400, detail="指定的路径不是目录")
    
    # 保存到配置文件（使用应用目录而不是用户主目录）
    config_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config')
    config_path = os.path.join(config_dir, "user_config.json")
    
    try:
        # 确保配置目录存在
        os.makedirs(config_dir, exist_ok=True)
        
        # 读取现有配置或创建新配置
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                current_config = json.load(f)
        else:
            current_config = {}
        
        # 更新临时文件目录
        current_config['tempClipDir'] = new_dir
        
        # 保存配置
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, ensure_ascii=False, indent=2)
        
        logger.info(f"临时文件目录已更新: {new_dir}")
        
        return schemas.TempDirResponse(
            temp_dir=new_dir,
            default_dir=config.DEFAULT_TEMP_CLIP_DIR,
            message="临时文件目录设置成功"
        )
        
    except Exception as e:
        logger.error(f"设置临时文件目录失败: {e}")
        raise HTTPException(status_code=500, detail=f"设置失败: {str(e)}")


@app.get("/api/v1/disk-space")
async def get_disk_space():
    """
    获取临时文件目录所在磁盘的空间信息
    """
    try:
        import shutil
        temp_dir = config.get_temp_clip_dir()
        
        # 获取磁盘使用情况
        usage = shutil.disk_usage(temp_dir)
        
        return {
            "success": True,
            "path": temp_dir,
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free
        }
    except Exception as e:
        logger.error(f"获取磁盘空间失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取磁盘空间失败: {str(e)}")


@app.post("/api/v1/temp-clips/clear")
async def clear_temp_clips():
    """
    清理所有临时裁切文件
    """
    try:
        temp_dir = config.get_temp_clip_dir()
        
        if not os.path.exists(temp_dir):
            return {
                "success": True,
                "deleted_count": 0,
                "freed_space": 0,
                "message": "临时文件目录不存在"
            }
        
        deleted_count = 0
        freed_space = 0
        
        # 遍历并删除所有文件
        for item in os.listdir(temp_dir):
            item_path = os.path.join(temp_dir, item)
            try:
                if os.path.isfile(item_path):
                    file_size = os.path.getsize(item_path)
                    os.remove(item_path)
                    deleted_count += 1
                    freed_space += file_size
                elif os.path.isdir(item_path):
                    # 递归删除子目录
                    import shutil
                    dir_size = sum(os.path.getsize(os.path.join(dirpath, filename)) 
                                  for dirpath, dirnames, filenames in os.walk(item_path) 
                                  for filename in filenames)
                    shutil.rmtree(item_path)
                    deleted_count += 1
                    freed_space += dir_size
            except Exception as e:
                logger.warning(f"删除文件失败 {item_path}: {e}")
        
        logger.info(f"清理临时文件完成: 删除 {deleted_count} 项, 释放 {freed_space} 字节")
        
        return {
            "success": True,
            "deleted_count": deleted_count,
            "freed_space": freed_space,
            "message": f"已清理 {deleted_count} 个文件，释放 {freed_space / (1024*1024):.2f} MB"
        }
    except Exception as e:
        logger.error(f"清理临时文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"清理失败: {str(e)}")


# ==================== 工程管理 API ====================

@app.post("/api/v1/projects")
async def create_project(request: schemas.CreateProjectRequest):
    """
    创建新工程

    - **id**: 工程唯一标识（可选，不传则自动生成）
    - **name**: 工程名称
    - **description**: 工程描述
    - **temp_dir**: 工程特定的临时文件目录
    """
    try:
        import uuid
        project_id = request.id or f"proj_{uuid.uuid4().hex[:8]}"

        db_manager = get_db_manager()
        success = db_manager.create_project(
            project_id=project_id,
            name=request.name,
            description=request.description,
            temp_dir=request.temp_dir
        )

        if not success:
            raise HTTPException(status_code=400, detail="创建工程失败，可能ID已存在")

        return {
            "success": True,
            "project_id": project_id,
            "message": f"工程 '{request.name}' 创建成功"
        }
    except Exception as e:
        logger.error(f"创建工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/projects")
async def get_all_projects():
    """
    获取所有工程列表
    """
    try:
        db_manager = get_db_manager()
        projects = db_manager.get_all_projects()

        # 添加每个工程的文件数量
        for project in projects:
            project['file_count'] = db_manager.get_project_file_count(project['id'])

        return {
            "total": len(projects),
            "projects": projects
        }
    except Exception as e:
        logger.error(f"获取工程列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 注意：/api/v1/projects/recent 必须在 /api/v1/projects/{project_id} 之前定义
# 否则 FastAPI 会将 "recent" 匹配为 project_id 参数
@app.get("/api/v1/projects/recent")
async def get_recent_projects(limit: int = 10):
    """
    获取最近使用的工程列表
    """
    try:
        db_manager = get_db_manager()
        projects = db_manager.get_recent_projects(limit)

        # 添加每个工程的文件数量
        for project in projects:
            project['file_count'] = db_manager.get_project_file_count(project['id'])

        return {
            "total": len(projects),
            "projects": projects
        }
    except Exception as e:
        logger.error(f"获取最近工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/projects/{project_id}")
async def get_project(project_id: str):
    """
    获取工程详情
    """
    try:
        db_manager = get_db_manager()
        project = db_manager.get_project(project_id)

        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        # 添加文件数量
        project['file_count'] = db_manager.get_project_file_count(project_id)

        return project
    except Exception as e:
        logger.error(f"获取工程详情失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/v1/projects/{project_id}")
async def update_project(project_id: str, request: schemas.UpdateProjectRequest):
    """
    更新工程信息
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        existing = db_manager.get_project(project_id)
        if not existing:
            raise HTTPException(status_code=404, detail="工程不存在")

        success = db_manager.update_project(
            project_id=project_id,
            name=request.name,
            description=request.description,
            temp_dir=request.temp_dir,
            settings=request.settings
        )

        if not success:
            raise HTTPException(status_code=400, detail="更新工程失败")

        return {
            "success": True,
            "message": "工程更新成功"
        }
    except Exception as e:
        logger.error(f"更新工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/projects/{project_id}")
async def delete_project(project_id: str):
    """
    删除工程（会级联删除所有相关文件、向量数据库和缓存）
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        existing = db_manager.get_project(project_id)
        if not existing:
            raise HTTPException(status_code=404, detail="工程不存在")

        # 检查是否是当前工程
        current_project_id = getattr(config, 'CURRENT_PROJECT_ID', None)
        is_current_project = (current_project_id == project_id)

        # 删除工程的向量数据库
        from core.indexer import delete_project_index
        index_deleted = delete_project_index(project_id)
        if not index_deleted:
            logger.warning(f"删除工程 {project_id} 的向量数据库失败，继续删除工程数据")

        # 删除工程数据
        success = db_manager.delete_project(project_id)

        if not success:
            raise HTTPException(status_code=400, detail="删除工程失败")

        # 如果删除的是当前工程，清理缓存并切换到默认工程
        if is_current_project:
            logger.info(f"删除的是当前工程 {project_id}，清理缓存并切换到默认工程")

            # 先切换到默认工程
            config.CURRENT_PROJECT_ID = 'default'
            logger.info("已切换到默认工程")

            # 清理音频缓存
            from core.audio_cache import reset_audio_cache
            reset_audio_cache()

            # 重置 Searcher
            from core.search_engine import reset_optimized_searcher
            reset_optimized_searcher()
            logger.info("Searcher 已重置")

            # 重置 ChromaDB 客户端
            from core.indexer import reset_chroma_client
            reset_chroma_client()
            logger.info("ChromaDB 客户端已重置")

        return {
            "success": True,
            "message": "工程已删除",
            "index_deleted": index_deleted,
            "was_current_project": is_current_project,
            "switched_to_default": is_current_project
        }
    except Exception as e:
        logger.error(f"删除工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/projects/{project_id}/switch")
async def switch_project(project_id: str):
    """
    切换到指定工程

    会将工程添加到最近工程列表，同时切换向量数据库和清理缓存
    """
    try:
        # 获取当前工程ID（用于判断是否真的切换了）
        old_project_id = getattr(config, 'CURRENT_PROJECT_ID', None)

        # 如果确实切换了工程，先更新当前工程ID，然后清理缓存
        if old_project_id and old_project_id != project_id:
            logger.info(f"切换工程: {old_project_id} -> {project_id}，开始清理缓存")

            # 先更新当前工程ID（确保后续创建的 searcher 使用正确的路径）
            config.CURRENT_PROJECT_ID = project_id
            logger.info(f"当前工程ID已更新为: {project_id}")

            # 清理音频缓存
            from core.audio_cache import reset_audio_cache
            reset_audio_cache()
            logger.info("音频缓存已清理")

            # 重置 Searcher（强制使用新工程的 ChromaDB）- 必须在重置 ChromaDB 客户端之前
            from core.search_engine import reset_optimized_searcher
            reset_optimized_searcher()
            logger.info("Searcher 已重置")

            # 重置 ChromaDB 客户端（避免跨工程缓存问题）
            from core.indexer import reset_chroma_client
            reset_chroma_client()
            logger.info("ChromaDB 客户端已重置")

            # 重置数据库连接
            from core.database import reset_db_manager
            reset_db_manager()
            logger.info("数据库连接已重置")

        # 获取数据库管理器（可能是新的连接）
        db_manager = get_db_manager()

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        # 添加到最近工程
        db_manager.add_to_recent_projects(project_id)

        # 更新全局配置中的当前工程
        config.CURRENT_PROJECT_ID = project_id

        # 如果有工程特定的临时目录，更新配置
        if project.get('temp_dir'):
            config.TEMP_CLIP_DIR = project['temp_dir']

        # 获取该工程的向量数据库信息
        from core.indexer import get_indexer
        from core.embedder import is_embedder_available

        indexer = get_indexer(project_id)
        indexed_count = indexer.get_indexed_count()

        embedder_available = is_embedder_available()

        return {
            "success": True,
            "project_id": project_id,
            "project_name": project['name'],
            "message": f"已切换到工程 '{project['name']}'",
            "cache_cleared": old_project_id != project_id,
            "vector_db": {
                "indexed_count": indexed_count,
                "embedder_available": embedder_available
            }
        }
    except Exception as e:
        logger.error(f"切换工程失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 工程文件和文件夹管理 ====================

@app.get("/api/v1/projects/{project_id}/files")
async def get_project_files(project_id: str):
    """
    获取指定工程的所有文件
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        files = db_manager.get_files_by_project(project_id)

        return {
            "project_id": project_id,
            "project_name": project['name'],
            "total": len(files),
            "files": [f.to_dict() for f in files]
        }
    except Exception as e:
        logger.error(f"获取工程文件失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 用户自定义文件夹 API ====================

class CreateFolderRequest(BaseModel):
    name: str
    description: Optional[str] = None
    color: Optional[str] = '#3b82f6'


class UpdateFolderRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    sort_order: Optional[int] = None


@app.get("/api/v1/projects/{project_id}/folders")
async def get_user_folders(project_id: str):
    """
    获取指定工程的所有用户自定义文件夹
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        folders = db_manager.get_user_folders(project_id)

        # 获取每个文件夹下的导入文件夹数量
        for folder in folders:
            mappings = db_manager.get_imported_folder_mappings(project_id, folder['id'])
            folder['imported_folder_count'] = len(mappings)
            folder['total_file_count'] = sum(m['file_count'] for m in mappings)

        return {
            "project_id": project_id,
            "total": len(folders),
            "folders": folders
        }
    except Exception as e:
        logger.error(f"获取用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/projects/{project_id}/folders")
async def create_user_folder(project_id: str, request: CreateFolderRequest):
    """
    创建用户自定义文件夹
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        # 生成文件夹ID
        import uuid
        folder_id = f"folder_{uuid.uuid4().hex[:8]}"

        # 获取当前最大排序号
        existing_folders = db_manager.get_user_folders(project_id)
        sort_order = len(existing_folders)

        success = db_manager.create_user_folder(
            folder_id=folder_id,
            project_id=project_id,
            name=request.name,
            description=request.description,
            color=request.color,
            sort_order=sort_order
        )

        if not success:
            raise HTTPException(status_code=400, detail="创建文件夹失败")

        return {
            "success": True,
            "folder_id": folder_id,
            "name": request.name,
            "message": f"文件夹 '{request.name}' 创建成功"
        }
    except Exception as e:
        logger.error(f"创建用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/v1/projects/{project_id}/folders/{folder_id}")
async def update_user_folder(project_id: str, folder_id: str, request: UpdateFolderRequest):
    """
    更新用户自定义文件夹
    """
    try:
        db_manager = get_db_manager()

        # 检查文件夹是否存在
        folder = db_manager.get_user_folder(folder_id)
        if not folder or folder['project_id'] != project_id:
            raise HTTPException(status_code=404, detail="文件夹不存在")

        success = db_manager.update_user_folder(
            folder_id=folder_id,
            name=request.name,
            description=request.description,
            color=request.color,
            sort_order=request.sort_order
        )

        if not success:
            raise HTTPException(status_code=400, detail="更新文件夹失败")

        return {
            "success": True,
            "message": "文件夹更新成功"
        }
    except Exception as e:
        logger.error(f"更新用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/v1/projects/{project_id}/folders/{folder_id}")
async def delete_user_folder(project_id: str, folder_id: str):
    """
    删除用户自定义文件夹

    删除后，该文件夹下的导入文件夹将变为未分类状态
    """
    try:
        db_manager = get_db_manager()

        # 检查文件夹是否存在
        folder = db_manager.get_user_folder(folder_id)
        if not folder or folder['project_id'] != project_id:
            raise HTTPException(status_code=404, detail="文件夹不存在")

        success = db_manager.delete_user_folder(folder_id)

        if not success:
            raise HTTPException(status_code=400, detail="删除文件夹失败")

        return {
            "success": True,
            "message": f"文件夹 '{folder['name']}' 已删除"
        }
    except Exception as e:
        logger.error(f"删除用户文件夹失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/projects/{project_id}/folder-mappings")
async def get_imported_folder_mappings(project_id: str, user_folder_id: Optional[str] = None):
    """
    获取导入文件夹的映射关系
    """
    try:
        db_manager = get_db_manager()

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        mappings = db_manager.get_imported_folder_mappings(project_id, user_folder_id)

        return {
            "project_id": project_id,
            "total": len(mappings),
            "mappings": mappings
        }
    except Exception as e:
        logger.error(f"获取导入文件夹映射失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/projects/{project_id}/folder-mappings/{folder_path:path}")
async def update_folder_mapping(project_id: str, folder_path: str, user_folder_id: Optional[str] = None):
    """
    更新导入文件夹的用户文件夹关联

    - **user_folder_id**: 用户文件夹ID，为空表示取消关联（变为未分类）
    """
    try:
        db_manager = get_db_manager()
        import urllib.parse

        decoded_path = urllib.parse.unquote(folder_path)

        # 检查工程是否存在
        project = db_manager.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="工程不存在")

        success = db_manager.update_imported_folder_mapping(project_id, decoded_path, user_folder_id)

        if not success:
            raise HTTPException(status_code=400, detail="更新文件夹映射失败")

        return {
            "success": True,
            "message": "文件夹分类已更新"
        }
    except Exception as e:
        logger.error(f"更新文件夹映射失败: {e}")
        raise HTTPException(status_code=500, detail="更新文件夹映射失败，请检查日志")


# ==================== AI Chat API ====================

@app.post("/api/v1/ai/chat")
async def ai_chat(request: schemas.AIChatRequest):
    """
    AI 对话 - 自然语言搜索
    
    支持流式响应，前端需要使用 EventSource 接收。
    
    请求格式：
    - **message**: 用户消息
    - **history**: 对话历史（可选）
    - **top_k**: 返回结果数量（默认 20）
    - **threshold**: 相似度阈值（默认 0.1）
    
    SSE 流式响应：
    - thinking: 正在分析
    - analyzing: 分析完成
    - searching: 正在搜索
    - results: 搜索结果
    - error: 错误
    - done: 完成
    """
    from core.ai_chat_service import get_ai_chat_service, stream_to_sse
    from core.llm_client import get_llm_client
    
    try:
        # 检查 LLM 服务是否可用
        llm_client = get_llm_client()
        if not llm_client.is_available:
            logger.warning("AI Chat 请求时 LLM 服务不可用，将使用降级模式")
        
        chat_service = get_ai_chat_service()
        
        return StreamingResponse(
            stream_to_sse(chat_service.chat(
                message=request.message,
                conversation_history=request.history,
                top_k=request.top_k,
                threshold=request.threshold
            )),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    except Exception as e:
        logger.error(f"AI Chat 请求失败: {e}")
        import traceback
        logger.error(f"详细错误: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"AI 服务暂时不可用: {str(e)}")


@app.get("/api/v1/ai/config")
async def get_ai_config():
    """
    获取 AI 配置
    
    返回当前 LLM 和 Embedding 的配置
    """
    try:
        from core.llm_config_manager import get_llm_config_manager
        
        config_manager = get_llm_config_manager()
        
        return {
            "success": True,
            "llm": {
                "provider": config_manager.get_llm_provider(),
                "config": config_manager.get_llm_config(),
                "available_services": config_manager.detect_available_local_services()
            },
            "embedding": {
                "provider": config_manager.get_embedding_provider(),
                "config": config_manager.get_embedding_config()
            }
        }
    except Exception as e:
        logger.error(f"获取 AI 配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/ai/config")
async def save_ai_config(request: schemas.AIConfigRequest):
    """
    保存 AI 配置
    
    - **llm_provider**: LLM 提供者 (lm_studio/ollama/external)
    - **llm_config**: LLM 提供者配置
    - **embedding_provider**: Embedding 提供者 (default/local/external)
    - **embedding_config**: Embedding 提供者配置
    """
    try:
        from core.llm_config_manager import get_llm_config_manager
        from core.llm_client import reset_llm_client
        from core.ai_chat_service import reset_ai_chat_service
        
        config_manager = get_llm_config_manager()
        
        config_manager.save_full_config(
            llm_provider=request.llm_provider,
            llm_config=request.llm_config,
            embedding_provider=request.embedding_provider,
            embedding_config=request.embedding_config
        )
        
        # 重置 LLM 客户端和 AI Chat 服务以应用新配置
        reset_llm_client()
        reset_ai_chat_service()
        
        return {
            "success": True,
            "message": "配置已保存"
        }
    except Exception as e:
        logger.error(f"保存 AI 配置失败: {e}")
        raise HTTPException(status_code=500, detail="保存配置失败，请检查日志")


@app.post("/api/v1/ai/config/test")
async def test_ai_config(request: schemas.AIConfigRequest):
    """
    测试 AI 配置连接
    
    - **llm_provider**: LLM 提供者
    - **llm_config**: LLM 提供者配置
    - **embedding_provider**: Embedding 提供者
    - **embedding_config**: Embedding 提供者配置
    """
    try:
        from core.llm_config_manager import get_llm_config_manager
        
        config_manager = get_llm_config_manager()
        
        # 测试 LLM 连接
        llm_result = await config_manager.test_llm_connection(
            provider=request.llm_provider,
            provider_config=request.llm_config
        )
        
        # 测试 Embedding 连接
        embedding_result = await config_manager.test_embedding_connection(
            provider=request.embedding_provider,
            provider_config=request.embedding_config
        )
        
        return {
            "success": llm_result.get("success", False) and embedding_result.get("success", False),
            "llm": llm_result,
            "embedding": embedding_result
        }
    except Exception as e:
        logger.error(f"测试 AI 配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/ai/status")
async def get_ai_status():
    """
    获取 AI 服务状态
    
    返回 LLM 和 Embedding 的可用状态
    """
    try:
        from core.llm_config_manager import get_llm_config_manager
        from core.llm_client import get_llm_client
        from core.embedder import is_embedder_available
        
        config_manager = get_llm_config_manager()
        
        # 检查 LLM 可用性
        llm_available = False
        llm_provider = config_manager.get_llm_provider()
        available_services = config_manager.detect_available_local_services()
        
        if llm_provider == "lm_studio":
            llm_available = available_services.get("lm_studio", False)
        elif llm_provider == "ollama":
            llm_available = available_services.get("ollama", False)
        else:
            # 外部 API，尝试连接
            try:
                llm_client = get_llm_client()
                llm_available = llm_client.is_available
            except:
                llm_available = False
        
        # 检查 Embedding 可用性
        embedding_available = is_embedder_available()
        
        return {
            "success": True,
            "llm": {
                "available": llm_available,
                "provider": llm_provider
            },
            "embedding": {
                "available": embedding_available,
                "provider": config_manager.get_embedding_provider()
            }
        }
    except Exception as e:
        logger.error(f"获取 AI 状态失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 主入口 ====================

if __name__ == "__main__":
    # PyInstaller 打包后的 Windows 多进程支持
    import multiprocessing
    multiprocessing.freeze_support()

    # 必须传 app 对象而非字符串 "main:app"
    # 字符串形式会让 uvicorn 尝试 importlib.import_module("main")，
    # 在 PyInstaller 冻结环境中会失败：Could not import module "main"
    uvicorn.run(
        app,
        host=config.HOST,
        port=config.PORT,
        log_level="info"
    )