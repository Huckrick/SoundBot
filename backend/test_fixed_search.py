# -*- coding: utf-8 -*-
"""
测试修复后的向量搜索功能
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from core.searcher import get_searcher

print("=" * 60)
print("测试修复后的向量搜索")
print("=" * 60)

searcher = get_searcher()

# 测试不同的搜索词
test_queries = [
    ('cat', '猫叫声'),
    ('meow', '猫叫'),
    ('fire', '火焰'),
    ('door', '门'),
    ('impact', '撞击'),
    ('whoosh', '呼啸声'),
    ('drill', '电钻'),
    ('bell', '铃声'),
    ('ui', 'UI音效'),
]

print("\n使用默认阈值 0.15 搜索:")
print("-" * 60)

for query, desc in test_queries:
    results = searcher.search(query, top_k=3)
    if results:
        print(f"\n'{query}' ({desc}): 找到 {len(results)} 个结果")
        for r in results[:2]:
            print(f"  - {r.filename}: {r.similarity:.4f}")
    else:
        print(f"\n'{query}' ({desc}): 无结果")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
