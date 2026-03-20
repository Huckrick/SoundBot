# -*- coding: utf-8 -*-
"""
调试向量搜索功能 - 使用正确的音效关键词
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from core.searcher import get_searcher
from core.embedder import get_embedder
from core.indexer import get_chroma_client
import numpy as np

print("=" * 60)
print("调试向量搜索（音效关键词）")
print("=" * 60)

# 获取 collection
client = get_chroma_client()
collection = client.get_collection("audio_embeddings")
count = collection.count()
print(f"\nCollection 中有 {count} 个文件")

# 获取所有文件名
all_files = collection.get()
filenames = [m.get('filename', '') for m in all_files['metadatas']]
print(f"\n样本文件名:")
for f in filenames[:15]:
    print(f"  - {f}")

# 测试不同的搜索词
test_queries = [
    'cat', 'meow', 'animal',  # 猫叫声相关
    'fire', 'burning', 'flame',  # 火焰相关
    'door', 'open', 'close',  # 门相关
    'impact', 'hit', 'crash',  # 撞击相关
    'whoosh', 'swoosh', 'swish',  # 呼啸声
    'drill', 'machine', 'mechanical',  # 机械相关
    'bell', 'ring', 'chime',  # 铃声
    'ui', 'interface', 'click',  # UI音效
]

print("\n" + "=" * 60)
print("搜索测试（不设置阈值）:")
print("=" * 60)

searcher = get_searcher()

for query in test_queries:
    results = searcher.search(query, top_k=3, min_similarity=0.0)
    if results:
        print(f"\n'{query}': 找到 {len(results)} 个结果")
        for r in results[:2]:
            print(f"  - {r.filename}: {r.similarity:.4f}")

# 直接查询查看原始距离
print("\n" + "=" * 60)
print("直接查询 ChromaDB（查看原始距离）:")
print("=" * 60)

embedder = get_embedder()
for query in ['cat', 'fire', 'door']:
    query_embedding = embedder.text_to_embedding(query)
    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=3
    )
    print(f"\n'{query}':")
    if results.get('ids') and len(results['ids'][0]) > 0:
        for i, id_ in enumerate(results['ids'][0]):
            dist = results['distances'][0][i]
            meta = results['metadatas'][0][i]
            # 正确的余弦相似度转换
            cosine_sim = 1 - (dist ** 2) / 2  # 余弦距离转余弦相似度
            print(f"  - {meta.get('filename', 'unknown')}")
            print(f"    原始距离: {dist:.4f}, 余弦相似度: {cosine_sim:.4f}")

print("\n" + "=" * 60)
print("调试完成")
print("=" * 60)
