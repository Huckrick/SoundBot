# -*- coding: utf-8 -*-
"""
调试向量搜索功能
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from core.searcher import get_searcher
from core.embedder import get_embedder
from core.indexer import get_chroma_client
import numpy as np

print("=" * 60)
print("调试向量搜索")
print("=" * 60)

# 1. 检查 ChromaDB 客户端
print("\n1. 检查 ChromaDB 客户端...")
client = get_chroma_client()
print(f"   客户端类型: {type(client)}")

# 2. 检查 collection
print("\n2. 检查 collection...")
collection = client.get_collection("audio_embeddings")
count = collection.count()
print(f"   Collection 名称: audio_embeddings")
print(f"   文档数量: {count}")

# 3. 获取一些样本数据
print("\n3. 获取样本数据...")
if count > 0:
    sample = collection.get(limit=3)
    print(f"   样本 IDs: {sample['ids'][:3]}")
    print(f"   样本元数据: {[m.get('filename') for m in sample['metadatas'][:3]]}")
else:
    print("   Collection 为空！")

# 4. 生成查询 embedding
print("\n4. 生成查询 embedding...")
embedder = get_embedder()
query_text = "music"
query_embedding = embedder.text_to_embedding(query_text)
print(f"   查询文本: {query_text}")
print(f"   Embedding 维度: {len(query_embedding)}")
print(f"   Embedding 前5个值: {query_embedding[:5]}")
print(f"   Embedding 范数: {np.linalg.norm(query_embedding):.4f}")

# 5. 直接查询 ChromaDB
print("\n5. 直接查询 ChromaDB...")
try:
    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=5
    )
    print(f"   查询结果 IDs: {results.get('ids', [])}")
    print(f"   查询结果 Distances: {results.get('distances', [])}")
    if results.get('ids') and len(results['ids'][0]) > 0:
        print(f"   找到 {len(results['ids'][0])} 个结果")
        for i, id_ in enumerate(results['ids'][0]):
            dist = results['distances'][0][i]
            meta = results['metadatas'][0][i]
            print(f"     - {meta.get('filename', 'unknown')}: distance={dist:.4f}")
    else:
        print("   没有找到结果！")
except Exception as e:
    print(f"   查询出错: {e}")
    import traceback
    traceback.print_exc()

# 6. 使用 searcher 搜索
print("\n6. 使用 searcher 搜索...")
searcher = get_searcher()
search_results = searcher.search("music", top_k=5, min_similarity=0.0)
print(f"   搜索结果数量: {len(search_results)}")
for r in search_results:
    print(f"     - {r.filename}: {r.similarity:.4f}")

print("\n" + "=" * 60)
print("调试完成")
print("=" * 60)
