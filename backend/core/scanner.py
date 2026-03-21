"""
音频文件扫描模块

扫描指定文件夹及其子文件夹，识别音频文件并提取元数据。
"""

import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
import librosa
import soundfile as sf
from pydantic import BaseModel
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

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
    folder_path: str = ""  # 文件所在文件夹路径（相对于导入根目录）
    # 文件名解析的元数据
    parsed_name: str = ""  # 解析后的文件名（去除扩展名和分隔符）
    name_tokens: List[str] = []  # 文件名分词
    name_description: str = ""  # 从文件名生成的描述
    # 音频元数据标签
    metadata_tags: Dict[str, Any] = {}  # 音频文件内置元数据标签


class FolderNode(BaseModel):
    """文件夹节点模型（用于构建树形结构）"""
    name: str
    path: str  # 完整路径
    relative_path: str  # 相对于导入根目录的路径
    children: List['FolderNode'] = []  # 子文件夹
    file_count: int = 0  # 该文件夹下的文件数量（包含子文件夹）

    class Config:
        arbitrary_types_allowed = True


class AudioScanner:
    """音频文件扫描器"""

    def __init__(self):
        self.supported_formats = SUPPORTED_AUDIO_FORMATS

    def scan(self, folder_path: str, recursive: bool = True, max_workers: int = 8) -> List[AudioFile]:
        """
        扫描指定文件夹中的音频文件（优化版本，支持并行处理）

        Args:
            folder_path: 要扫描的文件夹路径
            recursive: 是否递归扫描子文件夹
            max_workers: 并行处理线程数

        Returns:
            音频文件列表，包含完整路径和元数据
        """
        import time
        start_time = time.time()
        
        folder = Path(folder_path)

        if not folder.exists():
            raise FileNotFoundError(f"文件夹不存在: {folder_path}")

        if not folder.is_dir():
            raise NotADirectoryError(f"路径不是文件夹: {folder_path}")

        logger.info(f"[SCANNER] 开始扫描文件夹: {folder_path}, 递归: {recursive}, 并行线程: {max_workers}")
        print(f"[SCANNER] 开始扫描文件夹: {folder_path}, 递归: {recursive}, 并行线程: {max_workers}", flush=True)

        # 第一步：快速收集所有音频文件路径
        audio_file_paths = []
        scanned_dirs = []
        
        if recursive:
            logger.info(f"[SCANNER] 快速收集文件路径...")
            for root, dirs, files in os.walk(folder):
                scanned_dirs.append(root)
                for filename in files:
                    file_path = Path(root) / filename
                    if file_path.suffix.lower() in self.supported_formats:
                        audio_file_paths.append(file_path)
        else:
            for file_path in folder.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in self.supported_formats:
                    audio_file_paths.append(file_path)
        
        total_files = len(audio_file_paths)
        logger.info(f"[SCANNER] 找到 {total_files} 个音频文件，开始并行处理...")
        print(f"[SCANNER] 找到 {total_files} 个音频文件，开始并行处理...", flush=True)
        
        # 第二步：并行处理音频文件
        audio_files = []
        processed_count = 0
        error_count = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_path = {executor.submit(self._process_file, path): path for path in audio_file_paths}
            
            # 处理结果
            for future in as_completed(future_to_path):
                file_path = future_to_path[future]
                try:
                    audio_file = future.result(timeout=30)  # 单个文件处理超时30秒
                    if audio_file:
                        audio_files.append(audio_file)
                        processed_count += 1
                        if processed_count % 50 == 0:
                            logger.info(f"[SCANNER] 已处理 {processed_count}/{total_files} 个文件...")
                            print(f"[SCANNER] 已处理 {processed_count}/{total_files} 个文件...", flush=True)
                    else:
                        error_count += 1
                except Exception as e:
                    error_count += 1
                    logger.debug(f"[SCANNER] 处理文件失败 {file_path}: {e}")
        
        duration = time.time() - start_time
        logger.info(f"[SCANNER] 扫描完成: {processed_count} 个成功, {error_count} 个失败, 耗时 {duration:.2f} 秒")
        print(f"[SCANNER] 扫描完成: {processed_count} 个成功, {error_count} 个失败, 耗时 {duration:.2f} 秒", flush=True)
        
        return audio_files

    def scan_with_structure(self, folder_path: str, recursive: bool = True) -> tuple[List[AudioFile], FolderNode]:
        """
        扫描文件夹并返回文件列表和文件夹结构

        Args:
            folder_path: 要扫描的文件夹路径
            recursive: 是否递归扫描子文件夹

        Returns:
            (音频文件列表, 文件夹树形结构根节点)
        """
        folder = Path(folder_path)
        root_name = folder.name or folder_path

        # 先扫描所有文件
        audio_files = self.scan(folder_path, recursive)

        # 构建文件夹树形结构
        root_node = FolderNode(
            name=root_name,
            path=str(folder.absolute()),
            relative_path="",
            children=[],
            file_count=len(audio_files)
        )

        # 按文件夹路径分组文件
        folder_files = {}
        for audio_file in audio_files:
            file_path = Path(audio_file.path)
            parent_path = str(file_path.parent)
            if parent_path not in folder_files:
                folder_files[parent_path] = []
            folder_files[parent_path].append(audio_file)

        # 构建文件夹层级结构
        folder_nodes = {str(folder.absolute()): root_node}

        for parent_path, files in folder_files.items():
            parent = Path(parent_path)

            # 确保父文件夹节点存在
            current_path = str(parent.absolute())
            if current_path not in folder_nodes:
                # 创建从根到当前文件夹的路径
                relative = parent.relative_to(folder)
                parts = list(relative.parts) if relative.parts else []

                current_node = root_node
                current_build_path = str(folder.absolute())

                for part in parts:
                    current_build_path = os.path.join(current_build_path, part)

                    if current_build_path not in folder_nodes:
                        new_node = FolderNode(
                            name=part,
                            path=current_build_path,
                            relative_path=str(Path(current_build_path).relative_to(folder)),
                            children=[],
                            file_count=0
                        )
                        folder_nodes[current_build_path] = new_node
                        current_node.children.append(new_node)

                    current_node = folder_nodes[current_build_path]

            # 更新文件计数
            node = folder_nodes.get(current_path, root_node)
            node.file_count = len(files)

            # 为每个文件设置 folder_path
            for audio_file in files:
                try:
                    file_parent = Path(audio_file.path).parent
                    relative_folder = str(file_parent.relative_to(folder))
                    audio_file.folder_path = relative_folder if relative_folder != "." else ""
                except ValueError:
                    audio_file.folder_path = ""

        # 递归计算每个节点的总文件数（包含子文件夹）
        def calc_file_count(node: FolderNode) -> int:
            total = node.file_count
            for child in node.children:
                total += calc_file_count(child)
            node.file_count = total
            return total

        calc_file_count(root_node)

        logger.info(f"[SCANNER] 文件夹结构构建完成: {root_name}, 共 {len(audio_files)} 个文件")
        return audio_files, root_node

    def _parse_filename(self, filename: str) -> tuple[str, List[str], str]:
        """
        解析文件名，提取有意义的词汇和描述

        Args:
            filename: 文件名（不含路径）

        Returns:
            (解析后的名称, 分词列表, 生成的描述)
        """
        # 去除扩展名
        name_without_ext = os.path.splitext(filename)[0]

        # 替换常见分隔符为空格
        separators = r'[_\-\s\.]+'
        parsed = re.sub(separators, ' ', name_without_ext)

        # 分词
        tokens = [t.strip() for t in parsed.split() if t.strip()]

        # 过滤掉纯数字和过短的词
        meaningful_tokens = []
        for token in tokens:
            # 保留长度大于1的词，或者包含字母的词
            if len(token) > 1 or any(c.isalpha() for c in token):
                # 去除常见无意义后缀
                if token.lower() not in ['wav', 'mp3', 'flac', 'aif', 'aiff', 'm4a', 'ogg']:
                    meaningful_tokens.append(token)

        # 生成描述
        description = ' '.join(meaningful_tokens)

        return parsed, meaningful_tokens, description

    def _extract_wav_metadata(self, file_path: Path) -> Dict[str, Any]:
        """
        提取 WAV 文件的 BWF/iXML 元数据

        Args:
            file_path: WAV 文件路径

        Returns:
            元数据字典
        """
        metadata = {}
        try:
            # 使用 wave 模块读取基本 RIFF 信息
            import wave
            with wave.open(str(file_path), 'rb') as wav_file:
                # 尝试读取 INFO 块
                # 注意：标准 wave 模块不支持扩展块，需要手动解析
                pass

            # 使用 soundfile 读取更多元数据
            info = sf.info(str(file_path))

            # 尝试读取 BWF 元数据 (Broadcast Wave Format)
            if hasattr(info, 'comment') and info.comment:
                metadata['comment'] = info.comment
                metadata['description'] = info.comment

            # 使用 mutagen 读取 WAV 的 BWF 标签
            try:
                from mutagen.wave import WAVE
                audio = WAVE(str(file_path))

                # 读取 BWF 特有的标签
                if hasattr(audio, 'tags') and audio.tags:
                    for key, value in audio.tags.items():
                        if value:
                            metadata[key] = str(value)

                # 尝试读取 BWF 的 iXML 块
                if hasattr(audio, 'info') and hasattr(audio.info, 'xml_data'):
                    xml_data = audio.info.xml_data
                    if xml_data:
                        metadata['ixml'] = xml_data.decode('utf-8', errors='ignore')[:1000]  # 限制长度

            except ImportError:
                pass
            except Exception as e:
                logger.debug(f"读取 WAV BWF 标签失败 {file_path}: {e}")

            # 尝试使用 tinytag 读取更多元数据
            try:
                from tinytag import TinyTag
                tag = TinyTag.get(str(file_path))

                if tag.title:
                    metadata['title'] = tag.title
                if tag.artist:
                    metadata['artist'] = tag.artist
                if tag.album:
                    metadata['album'] = tag.album
                if tag.comment:
                    metadata['comment'] = tag.comment
                if tag.track:
                    metadata['track'] = str(tag.track)
                if tag.year:
                    metadata['year'] = str(tag.year)
                if tag.genre:
                    metadata['genre'] = tag.genre

            except ImportError:
                pass
            except Exception as e:
                logger.debug(f"读取 WAV tinytag 失败 {file_path}: {e}")

        except Exception as e:
            logger.debug(f"提取 WAV 元数据失败 {file_path}: {e}")

        return metadata

    def _extract_audio_metadata(self, file_path: Path) -> Dict[str, Any]:
        """
        提取音频文件的元数据标签

        Args:
            file_path: 文件路径

        Returns:
            元数据字典
        """
        metadata = {}
        suffix = file_path.suffix.lower()

        try:
            # 使用 soundfile 读取元数据
            info = sf.info(str(file_path))
            if hasattr(info, 'comment') and info.comment:
                metadata['comment'] = info.comment

            # WAV 文件特殊处理（BWF/iXML）
            if suffix == '.wav':
                wav_metadata = self._extract_wav_metadata(file_path)
                metadata.update(wav_metadata)

            # MP3 文件
            elif suffix == '.mp3':
                try:
                    from mutagen.mp3 import MP3
                    audio = MP3(str(file_path))
                    if audio.tags:
                        for key, value in audio.tags.items():
                            if value:
                                metadata[key] = str(value)
                except ImportError:
                    pass

            # FLAC/OGG 文件
            elif suffix in ['.flac', '.ogg']:
                try:
                    from mutagen.flac import FLAC
                    from mutagen.oggvorbis import OggVorbis
                    if suffix == '.flac':
                        audio = FLAC(str(file_path))
                    else:
                        audio = OggVorbis(str(file_path))
                    if audio.tags:
                        for key, value in audio.tags.items():
                            if value:
                                metadata[key] = str(value[0]) if isinstance(value, list) else str(value)
                except ImportError:
                    pass

            # AIFF 文件
            elif suffix in ['.aiff', '.aif']:
                try:
                    from mutagen.aiff import AIFF
                    audio = AIFF(str(file_path))
                    if audio.tags:
                        for key, value in audio.tags.items():
                            if value:
                                metadata[key] = str(value)
                except ImportError:
                    pass

            # M4A/AAC 文件
            elif suffix in ['.m4a', '.aac']:
                try:
                    from mutagen.mp4 import MP4
                    audio = MP4(str(file_path))
                    if audio.tags:
                        for key, value in audio.tags.items():
                            if value:
                                metadata[key] = str(value[0]) if isinstance(value, list) else str(value)
                except ImportError:
                    pass

        except Exception as e:
            logger.debug(f"提取音频元数据失败 {file_path}: {e}")

        return metadata

    def _process_file(self, file_path: Path) -> Optional[AudioFile]:
        """
        处理单个音频文件，提取元数据（优化版本）

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

            # 解析文件名（这个很快，先做）
            parsed_name, name_tokens, name_description = self._parse_filename(file_path.name)

            # 使用 soundfile 获取音频信息（只读文件头，不加载音频数据）
            info = sf.info(str(file_path))

            # 简化元数据提取 - 只提取基本信息，跳过耗时的 mutagen 解析
            metadata_tags = {}
            if hasattr(info, 'comment') and info.comment:
                metadata_tags['comment'] = info.comment

            return AudioFile(
                path=str(file_path.absolute()),
                filename=file_path.name,
                duration=info.duration,
                sample_rate=info.samplerate,
                channels=info.channels,
                format=info.format,
                size=stat.st_size,
                parsed_name=parsed_name,
                name_tokens=name_tokens,
                name_description=name_description,
                metadata_tags=metadata_tags
            )
        except Exception as e:
            # 如果 soundfile 失败，尝试使用 librosa（但只读取信息，不加载整个文件）
            try:
                stat = file_path.stat()
                
                # 使用 librosa 的 get_duration 直接获取时长，不加载音频数据
                duration = librosa.get_duration(path=str(file_path))
                
                # 使用 soundfile 获取其他信息（如果可能）
                try:
                    info = sf.info(str(file_path))
                    sr = info.samplerate
                    channels = info.channels
                except:
                    # 如果 soundfile 也失败，使用默认值
                    sr = 44100
                    channels = 2

                # 解析文件名
                parsed_name, name_tokens, name_description = self._parse_filename(file_path.name)

                return AudioFile(
                    path=str(file_path.absolute()),
                    filename=file_path.name,
                    duration=duration,
                    sample_rate=sr,
                    channels=channels,
                    format=file_path.suffix.lower()[1:],
                    size=stat.st_size,
                    parsed_name=parsed_name,
                    name_tokens=name_tokens,
                    name_description=name_description,
                    metadata_tags={}
                )
            except Exception as e2:
                # 跳过无法读取的文件
                logger.debug(f"无法读取音频文件 {file_path}: {e2}")
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
