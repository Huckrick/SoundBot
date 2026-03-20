# -*- coding: utf-8 -*-
"""
测试优化后的搜索功能
"""

import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from core.search_engine import get_optimized_searcher, ChineseTextProcessor


async def test_search():
    """测试搜索功能"""
    print("=" * 60)
    print("测试优化后的搜索功能")
    print("=" * 60)

    searcher = get_optimized_searcher()

    # 测试中文查询扩展
    print("\n1. 测试中文查询扩展:")
    print("-" * 60)
    test_queries = [
        "猫叫声",
        "火焰音效",
        "门开关",
        "点击声",
        "音乐",
        "机械声"
    ]
    for query in test_queries:
        expanded = ChineseTextProcessor.expand_query(query)
        print(f"  '{query}' -> {expanded}")

    # 测试搜索（带缓存）
    print("\n2. 测试搜索（第一次，无缓存）:")
    print("-" * 60)

    async def progress_callback(stage, progress):
        print(f"  进度: {stage} - {progress * 100:.1f}%")

    results, stats = await searcher.search_async(
        query="cat",
        top_k=5,
        min_similarity=0.0,
        progress_callback=progress_callback
    )

    print(f"\n  结果: 找到 {len(results)} 个文件")
    print(f"  统计: {stats}")
    for r in results[:3]:
        print(f"    - {r.filename}: {r.similarity:.4f}")

    # 测试缓存命中
    print("\n3. 测试搜索（第二次，有缓存）:")
    print("-" * 60)

    results2, stats2 = await searcher.search_async(
        query="cat",
        top_k=5,
        min_similarity=0.0
    )

    print(f"\n  结果: 找到 {len(results2)} 个文件")
    print(f"  统计: {stats2}")

    # 测试中文搜索
    print("\n4. 测试中文搜索:")
    print("-" * 60)

    results3, stats3 = await searcher.search_async(
        query="猫",  # 中文查询
        top_k=5,
        min_similarity=0.0
    )

    print(f"\n  结果: 找到 {len(results3)} 个文件")
    print(f"  统计: {stats3}")
    for r in results3[:3]:
        print(f"    - {r.filename}: {r.similarity:.4f}")

    # 测试缓存统计
    print("\n5. 缓存统计:")
    print("-" * 60)
    cache_stats = searcher.get_cache_stats()
    print(f"  {cache_stats}")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_search())
