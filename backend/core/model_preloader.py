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
模型预加载器

在应用启动时预加载 CLAP 模型到内存，避免首次搜索时的加载延迟。
"""

import logging
import asyncio
import threading
from pathlib import Path
from typing import Optional, Callable
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class ModelPreloader:
    """模型预加载管理器"""
    
    def __init__(self):
        self._embedder = None
        self._loading = False
        self._loaded = False
        self._error = None
        self._progress_callbacks = []
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._state_lock = threading.RLock()
        self._task: Optional[asyncio.Task] = None
        self._attempt_source_signature: Optional[tuple] = None
    
    def add_progress_callback(self, callback: Callable[[str, float], None]):
        """添加进度回调"""
        with self._state_lock:
            if callback not in self._progress_callbacks:
                self._progress_callbacks.append(callback)

    def remove_progress_callback(self, callback: Callable[[str, float], None]):
        """Remove a lifecycle callback without affecting other consumers."""
        with self._state_lock:
            if callback in self._progress_callbacks:
                self._progress_callbacks.remove(callback)
    
    def _notify_progress(self, stage: str, progress: float):
        """通知进度更新"""
        with self._state_lock:
            callbacks = tuple(self._progress_callbacks)
        for callback in callbacks:
            try:
                callback(stage, progress)
            except Exception as e:
                logger.warning(f"进度回调失败: {e}")
    
    async def preload_models(self):
        """
        异步预加载所有模型
        
        在后台线程中加载模型，不阻塞主线程。
        """
        with self._state_lock:
            if self._loading or self._loaded:
                return
            # Failed loads may be retried explicitly without recreating the
            # entire process.
            self._loading = True
            self._error = None
            self._attempt_source_signature = self._source_signature()
        self._notify_progress("starting", 0.0)
        
        try:
            # 在线程池中执行模型加载
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self._executor,
                self._load_models_sync
            )
            with self._state_lock:
                self._loaded = True
            self._notify_progress("complete", 1.0)
            logger.info("✅ 模型预加载完成")
            
        except Exception as e:
            with self._state_lock:
                self._error = e
                self._loaded = False
            logger.error(f"❌ 模型预加载失败: {e}")
            self._notify_progress("error", 0.0)
        finally:
            with self._state_lock:
                self._loading = False
    
    def _load_models_sync(self):
        """同步加载模型（在线程中执行）"""
        from core.embedder import CLIPEmbedder
        
        self._notify_progress("loading_embedder", 0.2)
        logger.info("🔄 正在预加载 CLAP 模型...")
        
        # 这会触发模型加载
        self._embedder = CLIPEmbedder()
        
        self._notify_progress("model_ready", 0.8)
        
        # 预热 - 执行一次前向传播
        logger.info("🔄 正在预热模型...")
        try:
            _ = self._embedder.text_to_embedding("test")
            self._notify_progress("warmup_complete", 0.95)
        except Exception as e:
            logger.warning(f"模型预热失败: {e}")
    
    def get_embedder(self):
        """获取预加载的 embedder"""
        if self._embedder is not None:
            return self._embedder
        
        # 如果还没加载，返回 None
        return None
    
    def is_loaded(self) -> bool:
        """检查模型是否已加载"""
        return self._loaded
    
    def is_loading(self) -> bool:
        """检查是否正在加载"""
        return self._loading
    
    def get_error(self) -> Optional[Exception]:
        """获取加载错误"""
        return self._error
    
    def get_status(self) -> dict:
        """获取加载状态"""
        return {
            "loaded": self._loaded,
            "loading": self._loading,
            "error": str(self._error) if self._error else None,
            "fingerprint": getattr(self._embedder, "fingerprint", None),
        }

    def start(self) -> Optional[asyncio.Task]:
        """Start one background preload task and return it."""
        with self._state_lock:
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self.preload_models())
            return self._task

    @staticmethod
    def _source_signature() -> tuple:
        """Cheaply identify a newly installed/replaced local model package."""
        import config

        root = Path(config.get_clap_model_name())
        names = (
            "config.json",
            "preprocessor_config.json",
            "tokenizer_config.json",
            "model.safetensors",
            "pytorch_model.bin",
        )
        signature = [str(root)]
        for name in names:
            candidate = root / name
            try:
                stat = candidate.stat()
                signature.append((name, int(stat.st_size), int(stat.st_mtime_ns)))
            except OSError:
                signature.append((name, None, None))
        manifest = root.parent / "model-manifest.json"
        try:
            stat = manifest.stat()
            signature.append((
                "../model-manifest.json", int(stat.st_size), int(stat.st_mtime_ns)
            ))
        except OSError:
            signature.append(("../model-manifest.json", None, None))
        return tuple(signature)

    def retry_if_source_changed(self) -> Optional[asyncio.Task]:
        """Retry a failed preload only after the local model package changes."""
        with self._state_lock:
            should_retry = bool(
                self._error is not None
                and not self._loading
                and not self._loaded
                and self._source_signature() != self._attempt_source_signature
            )
        return self.start() if should_retry else None

    async def close(self) -> None:
        """Cancel background work and release the private executor."""
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)

    def reset(self) -> None:
        """Reset load state; the next preload can retry a failed model."""
        with self._state_lock:
            self._embedder = None
            self._loading = False
            self._loaded = False
            self._error = None
            self._attempt_source_signature = None
        from core.embedder import reset_embedder

        reset_embedder()


# 全局预加载器实例
_preloader: Optional[ModelPreloader] = None


def get_preloader() -> ModelPreloader:
    """获取模型预加载器单例"""
    global _preloader
    if _preloader is None:
        _preloader = ModelPreloader()
    return _preloader


def reset_preloader() -> None:
    """Drop the preloader singleton (primarily for project shutdown/tests)."""
    global _preloader
    if _preloader is not None:
        _preloader.reset()
    _preloader = None


async def preload_models_on_startup():
    """
    应用启动时预加载模型
    
    用法:
        @app.on_event("startup")
        async def startup_event():
            await preload_models_on_startup()
    """
    import config
    
    if not config.ENABLE_MODEL_PRELOAD:
        logger.info("⏭️  模型预加载已禁用（ENABLE_MODEL_PRELOAD=false）")
        return
    
    preloader = get_preloader()
    
    # 在后台启动预加载
    preloader.start()
    
    logger.info(f"🚀 模型预加载任务已启动（后台运行，使用模型: {config.get_clap_model_name()}）")
