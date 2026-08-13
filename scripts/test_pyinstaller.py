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
import argparse
import platform
from importlib.metadata import distribution
from pathlib import Path


def verify_native_executable(executable: Path):
    """Verify that the frozen executable matches the current operating system."""
    if not executable.is_file():
        print(f"❌ 可执行文件不存在: {executable}")
        return False

    magic = executable.read_bytes()[:4]
    system = platform.system()
    if system == 'Windows':
        valid = magic[:2] == b'MZ'
    elif system == 'Darwin':
        valid = magic in {
            b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe',
            b'\xfe\xed\xfa\xcf', b'\xcf\xfa\xed\xfe',
            b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
        }
    elif system == 'Linux':
        valid = magic == b'\x7fELF'
    else:
        print(f"❌ 不支持的宿主平台: {system}")
        return False

    if not valid:
        print(f"❌ 可执行文件格式与宿主平台 {system} 不匹配: {executable}")
        return False
    if system != 'Windows' and not os.access(executable, os.X_OK):
        print(f"❌ 可执行文件缺少执行权限: {executable}")
        return False
    return True

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
        ('av', 'PyAV'),
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


def check_native_build_host():
    """Only release hosts supported by package.json may pass this gate."""
    system = platform.system()
    machine = platform.machine().lower()
    expected = {
        'Darwin': {'arm64', 'aarch64'},
        'Windows': {'amd64', 'x86_64'},
    }
    if system not in expected:
        print(f"❌ 不支持在 {system}/{machine} 构建发布包")
        return False
    if machine not in expected[system]:
        print(f"❌ 构建架构不受支持: {system}/{machine}")
        return False
    print(f"✅ 原生发布宿主: {system}/{machine}")
    return True


def check_pyav_runtime():
    """Verify PyAV can load its wheel-bundled FFmpeg runtime without a CLI."""
    print("\n" + "=" * 60)
    print("检查 PyAV / FFmpeg 运行时...")
    print("=" * 60)
    try:
        import av

        if av.__version__ != '18.0.0':
            print(f"❌ PyAV 版本漂移: 期望 18.0.0，实际 {av.__version__}")
            return False
        expected_versions = {
            'libavcodec': (62, 28, 102),
            'libavdevice': (62, 3, 102),
            'libavfilter': (11, 14, 102),
            'libavformat': (62, 12, 102),
            'libavutil': (60, 26, 102),
            'libswresample': (6, 3, 102),
            'libswscale': (9, 5, 102),
        }
        required = set(expected_versions)
        versions = set(av.library_versions)
        missing = sorted(required - versions)
        if missing:
            print(f"❌ PyAV 缺少 FFmpeg 组件: {', '.join(missing)}")
            return False
        mismatched = {
            name: av.library_versions[name]
            for name, expected in expected_versions.items()
            if tuple(av.library_versions[name]) != expected
        }
        if mismatched:
            print(f"❌ PyAV wheel 的 FFmpeg 版本漂移: {mismatched}")
            return False
        dist = distribution('av')
        license_files = [
            item for item in (dist.files or [])
            if any(token in Path(str(item)).name.lower() for token in ('license', 'copying', 'notice'))
        ]
        if not license_files:
            print("❌ PyAV wheel 元数据中没有许可证文件")
            return False
        print(f"✅ PyAV {av.__version__}; FFmpeg: {', '.join(sorted(required))}")
        print(f"✅ PyAV 许可证: {', '.join(str(item) for item in license_files)}")
        return True
    except Exception as exc:
        print(f"❌ PyAV / FFmpeg 检查失败: {exc}")
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
        'core/audio_service.py',
        '../tests/build/licenses/THIRD_PARTY_AUDIO_NOTICES.txt',
        '../tests/build/fixtures/manifest.json',
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
                
                executable_ok = verify_native_executable(exe_path)
                if executable_ok:
                    size_mb = exe_path.stat().st_size / 1024 / 1024
                    print(f"✅ 可执行文件创建成功: {exe_path} ({size_mb:.1f} MB)")
                else:
                    print(f"❌ 可执行文件路径或格式异常: {exe_path}")
                    # 列出目录内容
                    print("   目录内容:")
                    for item in output_dir.iterdir():
                        print(f"     - {item.name}")
                
                runtime_ok = (output_dir / '_internal').is_dir() or (output_dir / 'lib').is_dir()
                if not runtime_ok:
                    print("❌ PyInstaller 运行时目录缺失（需要 _internal 或 lib）")

                frozen_check = subprocess.run([
                    sys.executable,
                    str(backend_dir.parent / 'tests' / 'build' / 'verify_frozen_bundle.py'),
                    '--bundle', str(output_dir),
                    '--platform', 'windows' if sys.platform == 'win32' else 'macos',
                    '--arch', 'x64' if sys.platform == 'win32' else 'arm64',
                ], capture_output=True, text=True)
                print(frozen_check.stdout)
                if frozen_check.returncode:
                    print(frozen_check.stderr)

                # 清理测试构建
                shutil.rmtree(backend_dir / 'dist' / 'test', ignore_errors=True)
                shutil.rmtree(backend_dir / 'dist' / 'build', ignore_errors=True)
                
                return executable_ok and runtime_ok and frozen_check.returncode == 0
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
    parser = argparse.ArgumentParser(description='检查 SoundBot PyInstaller 打包环境')
    parser.add_argument(
        '--build',
        action='store_true',
        help='实际执行一次当前宿主平台的 PyInstaller 测试构建（默认只做静态检查）'
    )
    args = parser.parse_args()

    print("=" * 60)
    print("SoundBot PyInstaller 打包环境检查")
    print("=" * 60)
    
    results = []
    
    # 运行所有检查
    results.append(("原生构建宿主", check_native_build_host()))
    results.append(("依赖导入", check_imports()))
    results.append(("PyAV / FFmpeg", check_pyav_runtime()))
    results.append(("文件结构", check_file_structure()))
    results.append(("multiprocessing 修复", check_multiprocessing_fix()))
    
    if args.build:
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
        print("  README.md / README.en.md")
    print("=" * 60)
    
    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
