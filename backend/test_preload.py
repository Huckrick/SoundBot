# -*- coding: utf-8 -*-
"""
测试模型预加载功能
"""

import sys
import asyncio
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from core.model_preloader import get_preloader, preload_models_on_startup
from core.search_engine import get_optimized_searcher


async def test_preload():
    """测试预加载功能"""
    print("=" * 60)
    print("测试模型预加载功能")
    print("=" * 60)

    preloader = get_preloader()

    # 检查初始状态
    print("\n1. 初始状态:")
    print(f"   已加载: {preloader.is_loaded()}")
    print(f"   加载中: {preloader.is_loading()}")

    # 启动预加载
    print("\n2. 启动预加载...")
    start_time = time.time()

    # 添加进度回调
    def progress_callback(stage, progress):
        print(f"   进度: {stage} - {progress * 100:.1f}%")

    preloader.add_progress_callback(progress_callback)

    # 开始预加载
    await preloader.preload_models()

    preload_time = time.time() - start_time
    print(f"\n   预加载耗时: {preload_time:.2f} 秒")

    # 检查加载后状态
    print("\n3. 加载后状态:")
    print(f"   已加载: {preloader.is_loaded()}")
    print(f"   Embedder 可用: {preloader.get_embedder() is not None}")

    # 测试搜索（应该很快，因为模型已预加载）
    print("\n4. 测试搜索（模型已预加载）:")
    searcher = get_optimized_searcher()

    search_start = time.time()
    results, stats = await searcher.search_async(
        query="cat",
        top_k=5,
        min_similarity=0.0
    )
    search_time = time.time() - search_start

    print(f"   搜索耗时: {search_time:.3f} 秒")
    print(f"   找到 {len(results)} 个结果")
    print(f"   缓存命中: {stats.get('cache_hit', False)}")

    # 对比：清除缓存后再搜索
    print("\n5. 清除缓存后再次搜索:")
    await searcher.clear_cache()

    search_start2 = time.time()
    results2, stats2 = await searcher.search_async(
        query="cat",
        top_k=5,
        min_similarity=0.0
    )
    search_time2 = time.time() - search_start2

    print(f"   搜索耗时: {search_time2:.3f} 秒")
    print(f"   找到 {len(results2)} 个结果")
    print(f"   缓存命中: {stats2.get('cache_hit', False)}")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
    print(f"\n预加载耗时: {preload_time:.2f} 秒")
    print(f"首次搜索耗时: {search_time:.3f} 秒")
    print(f"二次搜索耗时: {search_time2:.3f} 秒")


if __name__ == "__main__":
    asyncio.run(test_preload())
