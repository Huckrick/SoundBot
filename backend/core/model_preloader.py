# -*- coding: utf-8 -*-
"""
模型预加载器

在应用启动时预加载 CLAP 模型到内存，避免首次搜索时的加载延迟。
"""

import logging
import asyncio
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
    
    def add_progress_callback(self, callback: Callable[[str, float], None]):
        """添加进度回调"""
        self._progress_callbacks.append(callback)
    
    def _notify_progress(self, stage: str, progress: float):
        """通知进度更新"""
        for callback in self._progress_callbacks:
            try:
                callback(stage, progress)
            except Exception as e:
                logger.warning(f"进度回调失败: {e}")
    
    async def preload_models(self):
        """
        异步预加载所有模型
        
        在后台线程中加载模型，不阻塞主线程。
        """
        if self._loading or self._loaded:
            return
        
        self._loading = True
        self._notify_progress("starting", 0.0)
        
        try:
            # 在线程池中执行模型加载
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                self._executor,
                self._load_models_sync
            )
            self._loaded = True
            self._notify_progress("complete", 1.0)
            logger.info("✅ 模型预加载完成")
            
        except Exception as e:
            self._error = e
            logger.error(f"❌ 模型预加载失败: {e}")
            self._notify_progress("error", 0.0)
        finally:
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
            "error": str(self._error) if self._error else None
        }


# 全局预加载器实例
_preloader: Optional[ModelPreloader] = None


def get_preloader() -> ModelPreloader:
    """获取模型预加载器单例"""
    global _preloader
    if _preloader is None:
        _preloader = ModelPreloader()
    return _preloader


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
    asyncio.create_task(preloader.preload_models())
    
    logger.info(f"🚀 模型预加载任务已启动（后台运行，使用模型: {config.CLAP_MODEL_NAME}）")
