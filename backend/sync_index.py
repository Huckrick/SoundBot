# -*- coding: utf-8 -*-
"""
同步 SQLite 和 ChromaDB 索引

将 SQLite 中已有的音频文件重新索引到 ChromaDB 向量数据库
"""

import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from core.database import get_db_manager
from core.indexer import get_indexer
from core.embedder import get_embedder, is_embedder_available
import config


async def sync_index():
    """同步索引"""
    print("=" * 60)
    print("同步 SQLite 和 ChromaDB 索引")
    print("=" * 60)

    db_manager = get_db_manager()
    indexer = get_indexer()

    # 获取 SQLite 中的所有文件
    all_files = db_manager.get_all_files()
    print(f"\nSQLite 数据库中有 {len(all_files)} 个文件")

    # 获取已索引的文件数
    indexed_count = indexer.get_indexed_count()
    print(f"ChromaDB 中已索引 {indexed_count} 个文件")

    if indexed_count >= len(all_files):
        print("\n✅ 所有文件已索引，无需同步")
        return

    # 检查 embedder 是否可用
    if not is_embedder_available():
        print("\n❌ Embedder 不可用，无法建立向量索引")
        print("   请检查模型是否正确加载")
        return

    print(f"\n需要索引 {len(all_files) - indexed_count} 个文件")
    print("\n开始索引...")

    indexed = 0
    failed = 0

    for i, record in enumerate(all_files):
        file_path = record.path
        print(f"\n[{i+1}/{len(all_files)}] {record.filename}")

        try:
            # 检查文件是否存在
            if not Path(file_path).exists():
                print(f"   ⚠️ 文件不存在，跳过")
                failed += 1
                continue

            # 索引文件
            success = indexer.index_audio_file(file_path)
            if success:
                print(f"   ✅ 索引成功")
                indexed += 1
            else:
                print(f"   ❌ 索引失败")
                failed += 1

        except Exception as e:
            print(f"   ❌ 错误: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print("同步完成")
    print("=" * 60)
    print(f"成功索引: {indexed} 个文件")
    print(f"失败: {failed} 个文件")

    # 最终统计
    final_count = indexer.get_indexed_count()
    print(f"\nChromaDB 当前索引数: {final_count}")


if __name__ == "__main__":
    asyncio.run(sync_index())
