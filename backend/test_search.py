# -*- coding: utf-8 -*-
"""
测试向量搜索功能
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from core.searcher import get_searcher

searcher = get_searcher()

# 测试搜索，不设置相似度阈值
print('测试搜索（不设置阈值）:')
results = searcher.search('music', top_k=5, min_similarity=0.0)
print(f'找到 {len(results)} 个结果')
for r in results:
    print(f'  - {r.filename}: {r.similarity:.4f}')

print()
print('测试搜索（中文 - 音乐）:')
results = searcher.search('音乐', top_k=5, min_similarity=0.0)
print(f'找到 {len(results)} 个结果')
for r in results:
    print(f'  - {r.filename}: {r.similarity:.4f}')

print()
print('测试搜索（cat）:')
results = searcher.search('cat', top_k=5, min_similarity=0.0)
print(f'找到 {len(results)} 个结果')
for r in results:
    print(f'  - {r.filename}: {r.similarity:.4f}')

print()
print('测试搜索（bell）:')
results = searcher.search('bell', top_k=5, min_similarity=0.0)
print(f'找到 {len(results)} 个结果')
for r in results:
    print(f'  - {r.filename}: {r.similarity:.4f}')

print()
print('测试搜索（fire）:')
results = searcher.search('fire', top_k=5, min_similarity=0.0)
print(f'找到 {len(results)} 个结果')
for r in results:
    print(f'  - {r.filename}: {r.similarity:.4f}')

print()
print('使用默认阈值 0.15 搜索:')
results = searcher.search('music', top_k=5)
print(f'找到 {len(results)} 个结果')
for r in results:
    print(f'  - {r.filename}: {r.similarity:.4f}')
