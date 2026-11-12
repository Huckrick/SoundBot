# -*- coding: utf-8 -*-
# SoundBot - AI 音效管理器
# Copyright (C) 2026 Huckrick and SoundBot contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
SoundBot 启动引导模块
=====================
检查应用目录、音频解码和 SQLite FTS5 支持。
"""

import sys
import sqlite3
from contextlib import closing


def check_environment():
    """
    检查运行环境
    
    Returns:
        dict: 包含检查结果的字典
        {
            'ok': bool,           # 是否所有检查都通过
            'errors': list,       # 错误列表
            'warnings': list,     # 警告列表
            'paths': dict         # 各路径信息
        }
    """
    errors = []
    warnings = []
    
    # 延迟导入 config，避免循环依赖
    from config import (
        get_executable_dir,
        get_user_data_dir,
        get_db_path,
        get_temp_dir
    )
    
    # 收集路径信息
    paths = {
        'executable': str(get_executable_dir()),
        'user_data': str(get_user_data_dir()),
        'database': str(get_db_path()),
        'temp': str(get_temp_dir()),
    }
    
    from core.audio_service import AudioService

    if not AudioService.runtime_status()["available"]:
        errors.append({"type": "audio_decoder", "message": "内置音频解码器不可用",
                       "solution": "检查 PyAV 安装或应用包完整性"})
    try:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE VIRTUAL TABLE test_fts USING fts5(value)")
    except sqlite3.Error:
        errors.append({"type": "fts5", "message": "SQLite 缺少 FTS5",
                       "solution": "检查 Python SQLite 运行时或应用包完整性"})

    # 检查磁盘空间 (至少 1GB 可用)
    try:
        import shutil
        user_data = get_user_data_dir()
        stat = shutil.disk_usage(user_data)
        free_gb = stat.free / (1024**3)
        if free_gb < 1:
            warnings.append({
                'type': 'low_disk_space',
                'message': f'磁盘空间不足: 仅剩 {free_gb:.1f} GB',
                'solution': '请清理磁盘空间，确保至少有 1GB 可用空间'
            })
    except Exception:
        pass  # 忽略磁盘空间检查错误
    
    return {
        'ok': len(errors) == 0,
        'errors': errors,
        'warnings': warnings,
        'paths': paths
    }


def print_check_result(result: dict):
    """打印检查结果"""
    print("=" * 60)
    print("SoundBot 环境检查")
    print("=" * 60)
    
    # 打印路径信息
    print("\n📁 路径信息:")
    for key, value in result['paths'].items():
        print(f"  {key:12s}: {value}")
    
    # 打印警告
    if result['warnings']:
        print("\n⚠️  警告:")
        for warning in result['warnings']:
            print(f"  - {warning['message']}")
            print(f"    解决: {warning['solution']}")
    
    # 打印错误
    if result['errors']:
        print("\n❌ 错误:")
        for error in result['errors']:
            print(f"\n  [{error['type']}]")
            print(f"  问题: {error['message']}")
            print(f"  解决: {error['solution']}")
            if 'possible_locations' in error:
                print("  可能的位置:")
                for loc in error['possible_locations']:
                    print(f"    - {loc}")
        print("\n" + "=" * 60)
        return False
    
    print("\n✅ 环境检查通过")
    print("=" * 60)
    return True


def main():
    """主函数 - 用于命令行检查"""
    result = check_environment()
    success = print_check_result(result)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
