# -*- coding: utf-8 -*-
"""
重新索引指定文件夹的音频文件
"""

import sys
import os
import sqlite3
from pathlib import Path

# 添加 backend 到路径
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

from core.indexer import get_indexer
from core.scanner import AudioScanner
import config


def save_folder_mapping(folder_path, file_count):
    """保存导入文件夹信息到数据库"""
    db_path = config.BASE_DIR / 'db' / 'soundmind.db'
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # 确保表存在
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS imported_folder_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            folder_path TEXT NOT NULL,
            user_folder_id TEXT,
            folder_name TEXT,
            file_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, folder_path)
        )
    ''')

    # 插入或更新导入记录
    folder_name = os.path.basename(folder_path)
    cursor.execute('''
        INSERT OR REPLACE INTO imported_folder_mappings
        (project_id, folder_path, folder_name, file_count)
        VALUES (?, ?, ?, ?)
    ''', ('default', folder_path, folder_name, file_count))

    conn.commit()
    conn.close()
    print(f'✓ 已保存导入文件夹信息到数据库')


def main():
    folder_path = '/Volumes/Studio Hub/音效设计工程/比赛参加/SDBC2361/太空猫传奇-音效设计比赛参加/Media'

    if not os.path.exists(folder_path):
        print(f'✗ 文件夹不存在: {folder_path}')
        return

    print('=' * 60)
    print('音频文件索引工具')
    print('=' * 60)
    print(f'\n目标文件夹: {folder_path}')

    # 扫描文件
    print('\n1. 扫描音频文件...')
    scanner = AudioScanner()
    audio_files = scanner.scan(folder_path, recursive=True)
    print(f'   找到 {len(audio_files)} 个音频文件')

    if not audio_files:
        print('✗ 没有找到音频文件')
        return

    # 索引
    print('\n2. 开始索引（生成向量嵌入）...')
    print('   这可能需要一些时间，请耐心等待...')
    indexer = get_indexer()
    result = indexer.index_audio_files(folder_path, recursive=True, force_reindex=True)

    print(f'\n   索引完成:')
    print(f'   - 新增: {result["added"]} 个文件')
    print(f'   - 更新: {result["updated"]} 个文件')
    print(f'   - 总计索引: {result["total_indexed"]} 个文件')

    # 保存导入信息
    print('\n3. 保存导入信息...')
    save_folder_mapping(folder_path, len(audio_files))

    # 验证
    print('\n4. 验证索引结果...')
    from core.searcher import get_searcher
    searcher = get_searcher()
    stats = searcher.get_collection_stats()
    print(f'   向量数据库中共有 {stats.get("total_count", 0)} 个文件')

    # 测试搜索
    print('\n5. 测试搜索...')
    test_queries = ['音乐', '音效', '声音']
    for query in test_queries:
        results = searcher.search(query, top_k=3)
        print(f'   "{query}": 找到 {len(results)} 个结果')
        for r in results[:2]:
            print(f'     - {r.filename} (相似度: {r.similarity:.3f})')

    print('\n' + '=' * 60)
    print('✅ 索引完成！')
    print('=' * 60)


if __name__ == '__main__':
    main()
