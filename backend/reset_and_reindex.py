# -*- coding: utf-8 -*-
"""
重置所有缓存并重新索引音频文件

功能：
1. 清除 SQLite 数据库中的文件记录
2. 清除 ChromaDB 向量数据库
3. 清除音频内存缓存
4. 重新扫描并索引所有音频文件
"""

import os
import sys
import shutil
import sqlite3
from pathlib import Path

# 添加 backend 到路径
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

import config
from core.database import get_db_manager, reset_db_manager
from core.indexer import get_indexer, reset_indexer, reset_chroma_client
from core.searcher import reset_searcher
from core.embedder import reset_embedder
from core.audio_cache import reset_audio_cache

def reset_sqlite_db():
    """清空 SQLite 数据库中的文件记录"""
    print("=" * 50)
    print("1. 重置 SQLite 数据库...")
    print("=" * 50)
    
    db_path = config.BASE_DIR / "db" / "soundmind.db"
    
    if not db_path.exists():
        print(f"数据库文件不存在: {db_path}")
        return
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # 获取重置前的记录数
        cursor.execute("SELECT COUNT(*) FROM files")
        count_before = cursor.fetchone()[0]
        
        # 清空文件表
        cursor.execute("DELETE FROM files")
        
        # 清空导入文件夹映射表
        cursor.execute("DELETE FROM imported_folder_mappings")
        
        conn.commit()
        conn.close()
        
        print(f"✓ 已清空 SQLite 数据库")
        print(f"  - 删除前记录数: {count_before}")
        print(f"  - 删除后记录数: 0")
        
    except Exception as e:
        print(f"✗ 重置 SQLite 数据库失败: {e}")
        raise

def reset_chroma_db():
    """清除 ChromaDB 向量数据库"""
    print("\n" + "=" * 50)
    print("2. 重置 ChromaDB 向量数据库...")
    print("=" * 50)
    
    chroma_path = config.get_chroma_db_path()
    
    if chroma_path.exists():
        try:
            # 删除整个 ChromaDB 目录
            shutil.rmtree(chroma_path)
            print(f"✓ 已删除 ChromaDB 目录: {chroma_path}")
        except Exception as e:
            print(f"✗ 删除 ChromaDB 目录失败: {e}")
            raise
    else:
        print(f"ChromaDB 目录不存在: {chroma_path}")
    
    # 同时检查旧的 chroma_store 目录
    old_chroma_path = config.BASE_DIR / "db" / "chroma_store"
    if old_chroma_path.exists():
        try:
            shutil.rmtree(old_chroma_path)
            print(f"✓ 已删除旧版 ChromaDB 目录: {old_chroma_path}")
        except Exception as e:
            print(f"✗ 删除旧版 ChromaDB 目录失败: {e}")

def reset_memory_cache():
    """重置内存缓存"""
    print("\n" + "=" * 50)
    print("3. 重置内存缓存...")
    print("=" * 50)
    
    try:
        reset_audio_cache()
        print("✓ 音频内存缓存已重置")
    except Exception as e:
        print(f"✗ 重置音频内存缓存失败: {e}")
    
    try:
        reset_embedder()
        print("✓ Embedder 已重置")
    except Exception as e:
        print(f"✗ 重置 Embedder 失败: {e}")
    
    try:
        reset_searcher()
        print("✓ Searcher 已重置")
    except Exception as e:
        print(f"✗ 重置 Searcher 失败: {e}")
    
    try:
        reset_indexer()
        print("✓ Indexer 已重置")
    except Exception as e:
        print(f"✗ 重置 Indexer 失败: {e}")
    
    try:
        reset_chroma_client()
        print("✓ ChromaDB 客户端已重置")
    except Exception as e:
        print(f"✗ 重置 ChromaDB 客户端失败: {e}")
    
    try:
        reset_db_manager()
        print("✓ DatabaseManager 已重置")
    except Exception as e:
        print(f"✗ 重置 DatabaseManager 失败: {e}")

def get_imported_folders():
    """从 SQLite 获取已导入的文件夹列表"""
    db_path = config.BASE_DIR / "db" / "soundmind.db"
    
    if not db_path.exists():
        return []
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        cursor.execute("SELECT DISTINCT folder_path FROM imported_folder_mappings")
        folders = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        return folders
    except Exception as e:
        print(f"获取导入文件夹列表失败: {e}")
        return []

def reindex_all(folders=None):
    """重新索引所有音频文件"""
    print("\n" + "=" * 50)
    print("4. 重新索引音频文件...")
    print("=" * 50)

    # 获取已导入的文件夹
    if folders is None:
        folders = get_imported_folders()

    if not folders:
        print("没有找到已导入的文件夹。请先通过前端导入音频文件夹。")
        return

    print(f"发现 {len(folders)} 个已导入的文件夹:")
    for folder in folders:
        print(f"  - {folder}")
    
    # 重新初始化 indexer
    indexer = get_indexer()
    
    total_added = 0
    total_updated = 0
    
    for folder in folders:
        print(f"\n正在索引: {folder}")
        
        if not os.path.exists(folder):
            print(f"  ⚠ 文件夹不存在，跳过: {folder}")
            continue
        
        try:
            result = indexer.index_audio_files(
                folder_path=folder,
                recursive=True,
                force_reindex=True  # 强制重新索引
            )
            
            print(f"  ✓ 完成: 新增 {result['added']}, 更新 {result['updated']}, 总计 {result['total_indexed']}")
            total_added += result['added']
            total_updated += result['updated']
            
        except Exception as e:
            print(f"  ✗ 索引失败: {e}")
    
    print(f"\n{'=' * 50}")
    print("索引完成统计:")
    print(f"  - 总计新增: {total_added}")
    print(f"  - 总计更新: {total_updated}")
    print(f"  - 索引文件总数: {total_added + total_updated}")

def verify_index():
    """验证索引结果"""
    print("\n" + "=" * 50)
    print("5. 验证索引结果...")
    print("=" * 50)
    
    try:
        from core.searcher import get_searcher
        searcher = get_searcher()
        stats = searcher.get_collection_stats()
        
        print(f"✓ 向量数据库统计:")
        print(f"  - Collection 名称: {stats.get('collection_name', 'N/A')}")
        print(f"  - 索引文件数量: {stats.get('total_count', 0)}")
        
        # 测试搜索
        print(f"\n测试搜索 '音乐':")
        results = searcher.search("音乐", top_k=5)
        if results:
            print(f"  ✓ 搜索成功，找到 {len(results)} 个结果")
            for r in results[:3]:
                print(f"    - {r.filename} (相似度: {r.similarity:.3f})")
        else:
            print(f"  ⚠ 搜索返回空结果（可能是正常情况，如果没有匹配的音频）")
            
    except Exception as e:
        print(f"✗ 验证失败: {e}")

def main():
    """主函数"""
    print("=" * 50)
    print("SoundMind 缓存重置与重新索引工具")
    print("=" * 50)
    
    # 确认操作
    print("\n⚠️  警告: 此操作将清除所有缓存数据并重新索引！")
    print("   包括:")
    print("   - SQLite 数据库中的文件记录")
    print("   - ChromaDB 向量数据库")
    print("   - 内存中的音频缓存")
    print()
    
    response = input("确认继续? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("操作已取消")
        return
    
    try:
        # 0. 先获取已导入的文件夹列表（在清空数据库之前）
        folders = get_imported_folders()
        print(f"\n已保存 {len(folders)} 个导入文件夹信息")
        
        # 1. 重置 SQLite
        reset_sqlite_db()
        
        # 2. 重置 ChromaDB
        reset_chroma_db()
        
        # 3. 重置内存缓存
        reset_memory_cache()
        
        # 4. 重新索引（使用之前保存的文件夹列表）
        reindex_all(folders)
        
        # 5. 验证
        verify_index()
        
        print("\n" + "=" * 50)
        print("✅ 所有操作已完成！")
        print("=" * 50)
        print("\n提示: 请重启后端服务以确保所有更改生效")
        
    except Exception as e:
        print(f"\n✗ 操作失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
