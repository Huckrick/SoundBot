#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyInstaller 打包测试脚本
用于验证修复后的打包是否正常工作
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def check_imports():
    """检查关键依赖是否可以导入"""
    print("=" * 60)
    print("检查关键依赖导入...")
    print("=" * 60)
    
    critical_imports = [
        ('fastapi', 'FastAPI'),
        ('uvicorn', 'Uvicorn'),
        ('chromadb', 'ChromaDB'),
        ('transformers', 'Transformers'),
        ('torch', 'PyTorch'),
        ('librosa', 'Librosa'),
        ('soundfile', 'SoundFile'),
        ('numpy', 'NumPy'),
        ('pydantic', 'Pydantic'),
    ]
    
    all_ok = True
    for module, name in critical_imports:
        try:
            __import__(module)
            print(f"✅ {name:20s} - 导入成功")
        except ImportError as e:
            print(f"❌ {name:20s} - 导入失败: {e}")
            all_ok = False
    
    return all_ok

def check_soundfile_library():
    """检查 soundfile 的二进制库"""
    print("\n" + "=" * 60)
    print("检查 soundfile 二进制库...")
    print("=" * 60)
    
    try:
        import soundfile
        soundfile_dir = Path(soundfile.__file__).parent
        
        # 检查不同平台的库文件
        if sys.platform == 'win32':
            lib_file = soundfile_dir / '_soundfile_data' / 'libsndfile-1.dll'
        elif sys.platform == 'darwin':
            lib_file = soundfile_dir / '_soundfile_data' / 'libsndfile.dylib'
        else:
            lib_file = soundfile_dir / '_soundfile_data' / 'libsndfile.so'
        
        if lib_file.exists():
            print(f"✅ 找到 soundfile 库: {lib_file}")
            return True
        else:
            print(f"⚠️  未找到 soundfile 库: {lib_file}")
            print("   这可能导致打包后的应用无法播放音频")
            return False
    except Exception as e:
        print(f"❌ 检查 soundfile 失败: {e}")
        return False

def check_file_structure():
    """检查后端文件结构"""
    print("\n" + "=" * 60)
    print("检查后端文件结构...")
    print("=" * 60)
    
    backend_dir = Path(__file__).parent.parent / "backend"
    
    required_files = [
        'main.py',
        'config.py',
        'bootstrap.py',
        'main.spec',
        'core/__init__.py',
        'utils/__init__.py',
        'models/__init__.py',
        'core/embedder.py',
        'core/indexer.py',
        'core/database.py',
    ]
    
    all_ok = True
    for file in required_files:
        file_path = backend_dir / file
        if file_path.exists():
            print(f"✅ {file:30s} - 存在")
        else:
            print(f"❌ {file:30s} - 缺失")
            all_ok = False
    
    return all_ok

def check_multiprocessing_fix():
    """检查 multiprocessing.freeze_support() 是否已添加"""
    print("\n" + "=" * 60)
    print("检查 multiprocessing.freeze_support()...")
    print("=" * 60)
    
    backend_dir = Path(__file__).parent.parent / "backend"
    main_py = backend_dir / 'main.py'
    
    content = main_py.read_text()
    
    if 'multiprocessing.freeze_support()' in content:
        print("✅ main.py 已包含 multiprocessing.freeze_support()")
        return True
    else:
        print("❌ main.py 缺少 multiprocessing.freeze_support()")
        print("   这可能导致 Windows 下打包后的多进程问题")
        return False

def test_build():
    """测试构建过程"""
    print("\n" + "=" * 60)
    print("测试 PyInstaller 构建...")
    print("=" * 60)
    
    backend_dir = Path(__file__).parent.parent / "backend"
    spec_file = backend_dir / 'main.spec'
    
    # 检查 spec 文件是否存在
    if not spec_file.exists():
        print(f"❌ Spec 文件不存在: {spec_file}")
        return False
    
    print(f"✅ 找到 spec 文件: {spec_file}")
    
    # 尝试构建（仅分析阶段）
    print("\n执行 PyInstaller 分析阶段...")
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        str(spec_file),
        '--distpath', str(backend_dir / 'dist' / 'test'),
        '--workpath', str(backend_dir / 'dist' / 'build'),
        '--noconfirm',
        '--clean',
    ]
    
    print(f"命令: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            print("✅ PyInstaller 分析阶段成功")
            
            # 检查输出目录
            output_dir = backend_dir / 'dist' / 'test' / 'soundbot-backend'
            if output_dir.exists():
                print(f"✅ 输出目录创建成功: {output_dir}")
                
                # 检查关键文件
                exe_name = 'soundbot-backend.exe' if sys.platform == 'win32' else 'soundbot-backend'
                exe_path = output_dir / exe_name
                
                if exe_path.exists():
                    size_mb = exe_path.stat().st_size / 1024 / 1024
                    print(f"✅ 可执行文件创建成功: {exe_path} ({size_mb:.1f} MB)")
                else:
                    print(f"⚠️  可执行文件路径异常: {exe_path}")
                    # 列出目录内容
                    print("   目录内容:")
                    for item in output_dir.iterdir():
                        print(f"     - {item.name}")
                
                # 清理测试构建
                shutil.rmtree(backend_dir / 'dist' / 'test', ignore_errors=True)
                shutil.rmtree(backend_dir / 'dist' / 'build', ignore_errors=True)
                
                return True
            else:
                print(f"❌ 输出目录未创建: {output_dir}")
                return False
        else:
            print("❌ PyInstaller 构建失败")
            print("\nSTDOUT:")
            print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
            print("\nSTDERR:")
            print(result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr)
            return False
            
    except subprocess.TimeoutExpired:
        print("❌ PyInstaller 构建超时")
        return False
    except Exception as e:
        print(f"❌ PyInstaller 构建异常: {e}")
        return False

def main():
    """主函数"""
    print("=" * 60)
    print("SoundBot PyInstaller 打包环境检查")
    print("=" * 60)
    
    results = []
    
    # 运行所有检查
    results.append(("依赖导入", check_imports()))
    results.append(("soundfile 库", check_soundfile_library()))
    results.append(("文件结构", check_file_structure()))
    results.append(("multiprocessing 修复", check_multiprocessing_fix()))
    
    # 询问是否测试构建
    print("\n" + "=" * 60)
    response = input("是否测试 PyInstaller 构建？(这可能需要几分钟) [y/N]: ")
    if response.lower() == 'y':
        results.append(("PyInstaller 构建", test_build()))
    
    # 汇总结果
    print("\n" + "=" * 60)
    print("检查结果汇总")
    print("=" * 60)
    
    for name, result in results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{name:25s} - {status}")
    
    all_passed = all(r[1] for r in results)
    
    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 所有检查通过！可以尝试构建了。")
        print("\n构建命令:")
        print("  python scripts/build.py")
    else:
        print("⚠️  部分检查未通过，请修复上述问题后再尝试构建。")
        print("\n参考文档:")
        print("  BUILD_FIX_README.md")
    print("=" * 60)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
