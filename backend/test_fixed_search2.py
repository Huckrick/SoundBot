# -*- coding: utf-8 -*-
"""
测试修复后的向量搜索功能 - 不同阈值
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from core.searcher import get_searcher

print("=" * 60)
print("测试修复后的向量搜索（不同阈值）")
print("=" * 60)

searcher = get_searcher()

# 测试不同的搜索词
test_queries = [
    'cat', 'meow', 'fire', 'door', 'impact',
    'whoosh', 'drill', 'bell', 'ui', 'click'
]

# 测试不同阈值
thresholds = [0.0, 0.05, 0.10, 0.15]

for threshold in thresholds:
    print(f"\n{'=' * 60}")
    print(f"阈值: {threshold}")
    print('=' * 60)

    for query in test_queries:
        results = searcher.search(query, top_k=3, min_similarity=threshold)
        if results:
            print(f"  '{query}': {len(results)} 个结果")
            for r in results[:1]:
                print(f"    - {r.filename}: {r.similarity:.4f}")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
