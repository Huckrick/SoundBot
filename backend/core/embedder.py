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
CLAP 音频-文本嵌入模型封装

使用 LAION 的 CLAP 模型进行音频和文本的特征提取。
支持音频-文本对齐搜索功能。

模型引用 / Model Citation:
    laion/larger_clap_general
    
    Wu, Y., Chen, K., Zhang, T., Hui, Y., Berg-Kirkpatrick, T., & Dubnov, S. (2022).
    Large-scale Contrastive Language-Audio Pretraining with Feature Fusion and Keyword-to-Caption Augmentation.
    arXiv preprint arXiv:2211.06687.
    
    @misc{wu2022large,
      doi = {10.48550/ARXIV.2211.06687},
      url = {https://arxiv.org/abs/2211.06687},
      author = {Wu, Yusong and Chen, Ke and Zhang, Tianyu and Hui, Yuchen and Berg-Kirkpatrick, Taylor and Dubnov, Shlomo},
      title = {Large-scale Contrastive Language-Audio Pretraining with Feature Fusion and Keyword-to-Caption Augmentation},
      publisher = {arXiv},
      year = {2022}
    }
    
    HuggingFace: https://huggingface.co/laion/larger_clap_general
    
许可证 / License:
    MIT License - 允许商业使用，需保留版权声明
    详见: https://huggingface.co/laion/larger_clap_general
"""

import logging
import os
import socket
import threading
import torch
import numpy as np
from typing import Optional
from pathlib import Path
from contextlib import contextmanager

import librosa
import config
from utils.audio_utils import load_audio

logger = logging.getLogger(__name__)

# 设置 HuggingFace 镜像（国内加速）
if hasattr(config, 'HF_ENDPOINT'):
    os.environ["HF_ENDPOINT"] = config.HF_ENDPOINT

os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
os.environ["REQUESTS_CA_BUNDLE"] = ""


class CLIPEmbedder:
    """CLAP 音频-文本嵌入模型封装"""
    
    _instance = None  # 单例模式，避免重复加载模型
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    @contextmanager
    def _timeout_context(self, seconds: int = 30):
        """设置模型加载超时（跨平台兼容）"""
        def timeout_handler():
            raise TimeoutError(f"模型加载超时 ({seconds}秒)")

        timer = threading.Timer(seconds, timeout_handler)
        timer.start()
        try:
            yield
        finally:
            timer.cancel()

    def __init__(self):
        if self._initialized:
            return

        self.device = self._get_device()
        logger.info(f"加载 CLAP 模型到 {self.device}...")

        try:
            from transformers import ClapModel, ClapProcessor

            # 使用运行时动态获取的模型路径（支持 PyInstaller 打包后的环境）
            model_path = config.get_clap_model_name()
            logger.info(f"正在加载模型: {model_path}")

            self.model = ClapModel.from_pretrained(model_path, low_cpu_mem_usage=True)
            self.model.to(self.device)
            self.processor = ClapProcessor.from_pretrained(model_path)

            self.model.eval()
            self._initialized = True
            logger.info("CLAP 模型加载完成")
        except TimeoutError as e:
            logger.error(f"模型加载超时: {e}")
            raise
        except Exception as e:
            logger.error(f"模型加载失败: {e}")
            raise
    
    def _get_device(self) -> torch.device:
        """自动检测可用的计算设备"""
        if torch.cuda.is_available():
            return torch.device("cuda")
        
        # 尝试 MPS（Apple Silicon）
        try:
            if torch.backends.mps.is_available():
                return torch.device("mps")
        except AttributeError:
            pass
        
        return torch.device("cpu")
    
    def audio_to_embedding(self, audio_path: str) -> np.ndarray:
        """
        将音频文件转换为 embedding 向量
        
        支持任意时长音频：
        - 短音频 (< 30s): 直接处理
        - 长音频 (>= 30s): 使用滑动窗口提取多个片段，然后聚合
        
        Args:
            audio_path: 音频文件路径
            
        Returns:
            归一化的 embedding 向量
        """
        try:
            # 加载音频（支持各种格式）
            audio, sr = librosa.load(audio_path, sr=48000, mono=True)
            
            # 音频时长（秒）
            duration = len(audio) / 48000
            
            # 短音频：直接处理
            if duration <= 30:
                return self._process_audio_segment(audio)
            
            # 长音频：使用滑动窗口
            logger.info(f"[Embedder] 长音频 ({duration:.1f}s)，使用滑动窗口处理")
            return self._process_long_audio(audio)
            
        except Exception as e:
            raise RuntimeError(f"[Embedder] 处理 {audio_path} 失败: {e}")
    
    def _process_audio_segment(self, audio: np.ndarray) -> np.ndarray:
        """
        处理单个音频片段，生成 embedding
        
        Args:
            audio: 音频数据（48kHz 采样率）
            
        Returns:
            归一化的 embedding 向量
        """
        # 限制最大长度 30 秒
        max_samples = 30 * 48000
        if len(audio) > max_samples:
            audio = audio[:max_samples]
        
        # 使用 processor 处理（新版本使用 audio 而不是 audios）
        try:
            inputs = self.processor(
                audio=[audio],
                sampling_rate=48000,
                return_tensors="pt"
            ).to(self.device)
        except TypeError:
            # 旧版本使用 audios
            inputs = self.processor(
                audios=[audio],
                sampling_rate=48000,
                return_tensors="pt"
            ).to(self.device)

        with torch.no_grad():
            outputs = self.model.get_audio_features(**inputs)
            # 处理不同版本的输出格式
            if hasattr(outputs, 'pooler_output'):
                # 新版本返回 BaseModelOutputWithPooling 对象
                embedding = outputs.pooler_output.cpu().numpy()[0]
            else:
                # 旧版本直接返回张量
                embedding = outputs.cpu().numpy()[0]

        # 归一化
        embedding = embedding / np.linalg.norm(embedding)

        return embedding
    
    def _process_long_audio(self, audio: np.ndarray, window_size: int = 30, hop_size: int = 10) -> np.ndarray:
        """
        使用滑动窗口处理长音频
        
        Args:
            audio: 音频数据（48kHz 采样率）
            window_size: 窗口大小（秒），默认 30
            hop_size: 滑动步长（秒），默认 10
            
        Returns:
            聚合后的 embedding 向量
        """
        window_samples = window_size * 48000
        hop_samples = hop_size * 48000
        
        embeddings = []
        total_samples = len(audio)
        
        # 滑动窗口提取 embedding
        start = 0
        segment_count = 0
        while start < total_samples:
            end = min(start + window_samples, total_samples)
            segment = audio[start:end]
            
            # 如果片段太短（< 5秒），跳过
            if len(segment) < 5 * 48000:
                break
            
            # 处理片段
            emb = self._process_audio_segment(segment)
            embeddings.append(emb)
            segment_count += 1
            
            start += hop_samples
        
        logger.info(f"[Embedder] 提取了 {segment_count} 个片段的 embedding")
        
        if not embeddings:
            raise RuntimeError("无法提取有效的音频片段")
        
        # 聚合：使用平均池化（保留整体特征）
        aggregated = np.mean(embeddings, axis=0)
        
        # 再次归一化
        aggregated = aggregated / np.linalg.norm(aggregated)
        
        return aggregated
    
    def text_to_embedding(self, text: str) -> np.ndarray:
        """
        将文本查询转换为 embedding 向量
        
        Args:
            text: 文本查询
            
        Returns:
            归一化的 embedding 向量
        """
        try:
            inputs = self.processor(
                text=[text],
                return_tensors="pt"
            ).to(self.device)

            with torch.no_grad():
                outputs = self.model.get_text_features(**inputs)
                # 处理不同版本的输出格式
                if hasattr(outputs, 'pooler_output'):
                    # 新版本返回 BaseModelOutputWithPooling 对象
                    embedding = outputs.pooler_output.cpu().numpy()[0]
                else:
                    # 旧版本直接返回张量
                    embedding = outputs.cpu().numpy()[0]

            # 归一化
            embedding = embedding / np.linalg.norm(embedding)

            return embedding

        except Exception as e:
            raise RuntimeError(f"[Embedder] 文本嵌入失败: {e}")
    
    def get_embedding_dim(self) -> int:
        """获取 embedding 向量的维度"""
        # CLAP 模型固定维度为 512
        return 512


# 全局单例
_embedder: Optional[CLIPEmbedder] = None
_embedder_loading_failed: bool = False


def get_embedder() -> Optional[CLIPEmbedder]:
    """获取 Embedder 单例（优先使用预加载的模型）"""
    global _embedder, _embedder_loading_failed

    # 首先检查是否有预加载的模型
    try:
        from core.model_preloader import get_preloader
        preloader = get_preloader()
        preloaded_embedder = preloader.get_embedder()
        if preloaded_embedder is not None:
            return preloaded_embedder
    except ImportError:
        pass

    # 如果没有预加载，使用延迟加载
    if _embedder is None and not _embedder_loading_failed:
        try:
            _embedder = CLIPEmbedder()
        except Exception as e:
            logger.error(f"无法加载模型: {e}")
            _embedder_loading_failed = True
            _embedder = None
    return _embedder


def is_embedder_available() -> bool:
    """检查 Embedder 是否可用"""
    return get_embedder() is not None


def reset_embedder() -> None:
    """重置 Embedder 单例（用于测试或重新加载模型）"""
    global _embedder, _embedder_loading_failed
    _embedder = None
    _embedder_loading_failed = False
