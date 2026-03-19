"""
音频文件扫描模块

扫描指定文件夹及其子文件夹，识别音频文件并提取元数据。
"""

import logging
import os
from pathlib import Path
from typing import List, Optional
import librosa
import soundfile as sf
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# 支持的音频格式
SUPPORTED_AUDIO_FORMATS = {'.wav', '.mp3', '.aac', '.flac', '.aiff', '.ogg', '.m4a'}


class AudioFile(BaseModel):
    """音频文件信息模型"""
    path: str
    filename: str
    duration: float
    sample_rate: int
    channels: int
    format: str
    size: int


class AudioScanner:
    """音频文件扫描器"""

    def __init__(self):
        self.supported_formats = SUPPORTED_AUDIO_FORMATS

    def scan(self, folder_path: str, recursive: bool = True) -> List[AudioFile]:
        """
        扫描指定文件夹中的音频文件

        Args:
            folder_path: 要扫描的文件夹路径
            recursive: 是否递归扫描子文件夹

        Returns:
            音频文件列表，包含完整路径和元数据
        """
        folder = Path(folder_path)
        
        if not folder.exists():
            raise FileNotFoundError(f"文件夹不存在: {folder_path}")
        
        if not folder.is_dir():
            raise NotADirectoryError(f"路径不是文件夹: {folder_path}")

        audio_files = []

        if recursive:
            # 递归扫描所有子文件夹
            for root, dirs, files in os.walk(folder):
                for filename in files:
                    file_path = Path(root) / filename
                    audio_file = self._process_file(file_path)
                    if audio_file:
                        audio_files.append(audio_file)
        else:
            # 只扫描当前文件夹
            for file_path in folder.iterdir():
                if file_path.is_file():
                    audio_file = self._process_file(file_path)
                    if audio_file:
                        audio_files.append(audio_file)

        return audio_files

    def _process_file(self, file_path: Path) -> Optional[AudioFile]:
        """
        处理单个音频文件，提取元数据

        Args:
            file_path: 文件路径

        Returns:
            音频文件信息，如果不支持则返回 None
        """
        # 检查文件格式
        if file_path.suffix.lower() not in self.supported_formats:
            return None

        try:
            # 获取文件基本信息
            stat = file_path.stat()
            
            # 使用 soundfile 获取音频信息（更高效）
            info = sf.info(str(file_path))
            
            return AudioFile(
                path=str(file_path.absolute()),
                filename=file_path.name,
                duration=info.duration,
                sample_rate=info.samplerate,
                channels=info.channels,
                format=info.format,
                size=stat.st_size
            )
        except Exception as e:
            # 如果 soundfile 失败，尝试使用 librosa
            try:
                stat = file_path.stat()
                y, sr = librosa.load(str(file_path), sr=None, mono=False)
                
                # 计算时长
                duration = librosa.get_duration(y=y, sr=sr)
                
                # 确定声道数
                channels = 1 if y.ndim == 1 else y.shape[0]
                
                return AudioFile(
                    path=str(file_path.absolute()),
                    filename=file_path.name,
                    duration=duration,
                    sample_rate=sr,
                    channels=channels,
                    format=file_path.suffix.lower()[1:],
                    size=stat.st_size
                )
            except Exception as e:
                # 跳过无法读取的文件
                logger.warning(f"无法读取音频文件 {file_path}: {e}")
                return None

    def is_audio_file(self, file_path: str) -> bool:
        """
        检查文件是否为支持的音频格式

        Args:
            file_path: 文件路径

        Returns:
            是否为支持的音频文件
        """
        return Path(file_path).suffix.lower() in self.supported_formats


# 便捷函数
_scanner = None


def scan_directory(folder_path: str, recursive: bool = True) -> List[AudioFile]:
    """
    扫描目录的便捷函数

    Args:
        folder_path: 要扫描的文件夹路径
        recursive: 是否递归扫描子文件夹

    Returns:
        音频文件列表
    """
    global _scanner
    if _scanner is None:
        _scanner = AudioScanner()
    return _scanner.scan(folder_path, recursive)
