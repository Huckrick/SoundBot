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
        scanned_dirs = []
        skipped_files = []
        error_files = []

        logger.info(f"[SCANNER] 开始扫描文件夹: {folder_path}, 递归: {recursive}")
        print(f"[SCANNER] 开始扫描文件夹: {folder_path}, 递归: {recursive}", flush=True)

        if recursive:
            # 递归扫描所有子文件夹
            logger.info(f"[SCANNER] 使用 os.walk 开始递归扫描...")
            print(f"[SCANNER] 使用 os.walk 开始递归扫描...", flush=True)
            print(f"[SCANNER] 文件夹对象类型: {type(folder)}, 路径: {folder}", flush=True)
            print(f"[SCANNER] 文件夹是否存在: {folder.exists()}", flush=True)
            print(f"[SCANNER] 文件夹是否可读: {os.access(folder, os.R_OK)}", flush=True)
            
            # 尝试列出根目录内容
            try:
                root_contents = list(folder.iterdir())
                print(f"[SCANNER] 根目录内容数量: {len(root_contents)}", flush=True)
                for item in root_contents[:10]:  # 只显示前10个
                    item_type = "文件夹" if item.is_dir() else "文件"
                    print(f"[SCANNER]   - {item.name} ({item_type})", flush=True)
            except Exception as e:
                print(f"[SCANNER] ❌ 无法列出根目录内容: {e}", flush=True)
            
            walk_count = 0
            for root, dirs, files in os.walk(folder):
                walk_count += 1
                scanned_dirs.append(root)
                logger.info(f"[SCANNER] 扫描子文件夹: {root}, 文件数: {len(files)}, 子目录数: {len(dirs)}")
                print(f"[SCANNER] [{walk_count}] 扫描: {root}", flush=True)
                print(f"[SCANNER]      文件: {len(files)} 个, 子目录: {len(dirs)} 个", flush=True)
                if dirs:
                    print(f"[SCANNER]      子目录列表: {dirs}", flush=True)
                
                # 检查每个子目录是否可访问
                for d in dirs:
                    subdir_path = Path(root) / d
                    try:
                        is_accessible = subdir_path.exists() and subdir_path.is_dir() and os.access(subdir_path, os.R_OK)
                        print(f"[SCANNER]      检查子目录 '{d}': 可访问={is_accessible}", flush=True)
                        if not is_accessible:
                            logger.warning(f"[SCANNER] 子目录可能无法访问: {subdir_path}")
                            print(f"[SCANNER]      ⚠️ 子目录可能无法访问: {subdir_path}", flush=True)
                    except Exception as e:
                        logger.error(f"[SCANNER] 检查子目录权限失败 {subdir_path}: {e}")
                        print(f"[SCANNER]      ❌ 检查子目录权限失败: {e}", flush=True)
                
                for filename in files:
                    file_path = Path(root) / filename
                    file_ext = file_path.suffix.lower()
                    
                    # 记录所有被检查的文件
                    logger.debug(f"[SCANNER] 检查文件: {file_path}, 扩展名: {file_ext}")
                    
                    # 检查格式是否支持
                    if file_ext not in self.supported_formats:
                        skipped_files.append(f"{file_path} (不支持格式: {file_ext})")
                        logger.debug(f"[SCANNER] 跳过不支持的格式: {file_path}")
                        continue
                    
                    audio_file = self._process_file(file_path)
                    if audio_file:
                        audio_files.append(audio_file)
                        logger.info(f"[SCANNER] 成功处理音频文件: {file_path}")
                        print(f"[SCANNER] ✓ 成功处理: {file_path.name}", flush=True)
                    else:
                        error_files.append(str(file_path))
                        logger.warning(f"[SCANNER] 处理文件失败: {file_path}")
                        print(f"[SCANNER] ✗ 处理失败: {file_path.name}", flush=True)
        else:
            # 只扫描当前文件夹
            logger.info(f"[SCANNER] 非递归模式，仅扫描当前文件夹")
            for file_path in folder.iterdir():
                if file_path.is_file():
                    file_ext = file_path.suffix.lower()
                    logger.debug(f"[SCANNER] 检查文件: {file_path}, 扩展名: {file_ext}")
                    
                    if file_ext not in self.supported_formats:
                        skipped_files.append(f"{file_path} (不支持格式: {file_ext})")
                        continue
                    
                    audio_file = self._process_file(file_path)
                    if audio_file:
                        audio_files.append(audio_file)
                        logger.info(f"[SCANNER] 成功处理音频文件: {file_path}")

        # 输出扫描统计
        logger.info(f"[SCANNER] 扫描完成统计:")
        logger.info(f"  - 扫描的文件夹数: {len(scanned_dirs)}")
        logger.info(f"  - 跳过的文件数: {len(skipped_files)}")
        logger.info(f"  - 处理失败的文件数: {len(error_files)}")
        logger.info(f"  - 成功处理的音频文件数: {len(audio_files)}")
        logger.info(f"  - 扫描的文件夹列表: {scanned_dirs}")
        
        print(f"[SCANNER] ===== 扫描完成统计 =====", flush=True)
        print(f"[SCANNER] 扫描的文件夹数: {len(scanned_dirs)}", flush=True)
        print(f"[SCANNER] 跳过的文件数: {len(skipped_files)}", flush=True)
        print(f"[SCANNER] 处理失败的文件数: {len(error_files)}", flush=True)
        print(f"[SCANNER] 成功处理的音频文件数: {len(audio_files)}", flush=True)
        print(f"[SCANNER] 扫描的文件夹列表:", flush=True)
        for d in scanned_dirs:
            print(f"  - {d}", flush=True)
        
        if skipped_files:
            print(f"[SCANNER] 跳过的文件 (前10个):", flush=True)
            for f in skipped_files[:10]:
                print(f"  - {f}", flush=True)
        
        if error_files:
            print(f"[SCANNER] 处理失败的文件 (前10个):", flush=True)
            for f in error_files[:10]:
                print(f"  - {f}", flush=True)

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
