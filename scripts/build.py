#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SoundBot 统一构建脚本
====================
构建 PyInstaller 后端 + Electron 前端一体化应用

使用方法:
    python scripts/build.py                    # 构建当前平台
    python scripts/build.py --platform macos   # 构建 macOS
    python scripts/build.py --platform windows # 构建 Windows
    python scripts/build.py --platform all     # 构建所有平台

输出:
    dist-electron/SoundBot-*.dmg  (macOS)
    dist-electron/SoundBot-*.exe  (Windows)
"""

import os
import sys
import subprocess
import shutil
import argparse
import platform
from pathlib import Path

# Windows 编码修复
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 项目路径配置
PROJECT_ROOT = Path(__file__).parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DIST_DIR = PROJECT_ROOT / "dist"
ELECTRON_DIST_DIR = PROJECT_ROOT / "dist-electron"


def log(message: str, level: str = "INFO"):
    """打印带颜色的日志"""
    # Windows 控制台不支持 ANSI 颜色，禁用颜色
    if sys.platform == 'win32':
        print(f"[{level}] {message}")
        return
    
    colors = {
        "INFO": "\033[94m",      # 蓝色
        "SUCCESS": "\033[92m",   # 绿色
        "WARNING": "\033[93m",   # 黄色
        "ERROR": "\033[91m",     # 红色
        "RESET": "\033[0m"
    }
    color = colors.get(level, colors["INFO"])
    reset = colors["RESET"]
    print(f"{color}[{level}]{reset} {message}")


def run_command(cmd: list, cwd: Path = None, env: dict = None, shell: bool = False, capture: bool = True) -> subprocess.CompletedProcess:
    """执行命令并检查返回值"""
    log(f"执行: {' '.join(str(c) for c in cmd)}")
    
    # Windows 上使用 shell=True 来正确找到 npm
    if sys.platform == 'win32' and not shell:
        # 检查是否是 npm 命令
        if len(cmd) > 0 and cmd[0] in ['npm', 'npx']:
            shell = True
            cmd = ' '.join(str(c) for c in cmd)
    
    # 对于长时间运行的命令（如 PyInstaller），实时输出避免卡住
    if not capture:
        result = subprocess.run(cmd, cwd=cwd, env=env, shell=shell)
        if result.returncode != 0:
            raise RuntimeError(f"命令失败: {cmd if isinstance(cmd, str) else ' '.join(str(c) for c in cmd)}")
        return result
    
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, shell=shell)
    if result.returncode != 0:
        log("=" * 60, "ERROR")
        log("命令 stdout:", "ERROR")
        log(result.stdout, "ERROR")
        log("命令 stderr:", "ERROR")
        log(result.stderr, "ERROR")
        log("=" * 60, "ERROR")
        raise RuntimeError(f"命令失败: {cmd if isinstance(cmd, str) else ' '.join(str(c) for c in cmd)}")
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result


def clean_build_dirs():
    """清理构建目录"""
    log("清理构建目录...")
    
    dirs_to_clean = [
        DIST_DIR,
        BACKEND_DIR / "dist",
        BACKEND_DIR / "build",
        PROJECT_ROOT / "dist-electron",
    ]
    
    for dir_path in dirs_to_clean:
        if dir_path.exists():
            log(f"删除: {dir_path}")
            shutil.rmtree(dir_path)


def install_python_deps():
    """安装 Python 依赖"""
    log("安装 Python 依赖...")
    
    # 安装 PyInstaller
    run_command([sys.executable, "-m", "pip", "install", "pyinstaller", "-q"])
    
    # 安装后端依赖
    requirements_file = BACKEND_DIR / "requirements.txt"
    if requirements_file.exists():
        run_command([sys.executable, "-m", "pip", "install", "-r", str(requirements_file), "-q"])


def build_backend() -> Path:
    """
    使用 PyInstaller 构建后端
    
    Returns:
        后端可执行文件路径
    """
    log("=" * 60)
    log("步骤 1: 构建 PyInstaller 后端")
    log("=" * 60)
    
    # 确保依赖已安装
    install_python_deps()
    
    # PyInstaller 构建参数
    spec_file = BACKEND_DIR / "main.spec"
    backend_dist = DIST_DIR / "backend"
    backend_build = DIST_DIR / "build"
    
    # 执行 PyInstaller
    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(spec_file),
        "--distpath", str(backend_dist),
        "--workpath", str(backend_build),
        "--noconfirm",
        "--clean"
    ]
    
    # PyInstaller 使用实时输出避免缓冲区卡住
    run_command(cmd, capture=False)

    # onedir 模式：输出是目录，不是单文件
    system = platform.system().lower()
    backend_dir_name = "soundbot-backend"
    if system == "windows":
        backend_dir_name += ".exe"
    backend_dir_path = backend_dist / "soundbot-backend"

    if not backend_dir_path.exists():
        raise RuntimeError(f"后端目录未生成: {backend_dir_path}")

    # 检查目录大小
    total_size = 0
    for root, dirs, files in os.walk(backend_dir_path):
        for f in files:
            fp = os.path.join(root, f)
            total_size += os.path.getsize(fp)

    size_mb = total_size / 1024 / 1024
    log(f"后端目录: {backend_dir_path}", "SUCCESS")
    log(f"目录大小: {size_mb:.1f} MB")

    if size_mb > 800:
        log(f"警告: 后端体积较大 ({size_mb:.1f} MB)，建议优化", "WARNING")

    return backend_dir_path


def install_npm_deps():
    """安装 npm 依赖"""
    log("安装 npm 依赖...")
    # npm 使用实时输出避免卡住
    run_command(["npm", "install"], cwd=PROJECT_ROOT, capture=False)


def build_electron(target_platform: str = None):
    """
    构建 Electron 应用
    
    Args:
        target_platform: 目标平台 (macos, windows, linux)
    """
    log("=" * 60)
    log("步骤 2: 构建 Electron 应用")
    log("=" * 60)
    
    # 确保 npm 依赖已安装
    install_npm_deps()
    
    # 根据平台选择构建命令（直接调用 electron-builder，避免 npm 脚本循环）
    if target_platform == "macos" or (target_platform is None and platform.system() == "Darwin"):
        log("构建 macOS 应用...")
        run_command(["npx", "electron-builder", "--mac"], cwd=PROJECT_ROOT, capture=False)
    elif target_platform == "windows" or (target_platform is None and platform.system() == "Windows"):
        log("构建 Windows 应用...")
        run_command(["npx", "electron-builder", "--win"], cwd=PROJECT_ROOT, capture=False)
    elif target_platform == "linux":
        log("构建 Linux 应用...")
        run_command(["npx", "electron-builder", "--linux"], cwd=PROJECT_ROOT, capture=False)
    else:
        # 自动检测平台
        log("自动检测平台并构建...")
        run_command(["npx", "electron-builder"], cwd=PROJECT_ROOT, capture=False)
    
    log("Electron 构建完成", "SUCCESS")


def verify_build(target_platform: str = None):
    """验证构建结果"""
    log("=" * 60)
    log("步骤 3: 验证构建结果")
    log("=" * 60)
    
    electron_dist = ELECTRON_DIST_DIR
    
    if not electron_dist.exists():
        raise RuntimeError(f"构建输出目录不存在: {electron_dist}")
    
    # 查找构建产物
    if target_platform == "macos" or platform.system() == "Darwin":
        artifacts = list(electron_dist.glob("*.dmg"))
    elif target_platform == "windows" or platform.system() == "Windows":
        artifacts = list(electron_dist.glob("*.exe"))
    else:
        artifacts = list(electron_dist.glob("*.AppImage")) + list(electron_dist.glob("*.dmg")) + list(electron_dist.glob("*.exe"))
    
    if not artifacts:
        raise RuntimeError("未找到构建产物")
    
    log("构建产物:", "SUCCESS")
    total_size = 0
    for artifact in artifacts:
        size_mb = artifact.stat().st_size / 1024 / 1024
        total_size += size_mb
        log(f"  - {artifact.name} ({size_mb:.1f} MB)")
    
    log(f"总大小: {total_size:.1f} MB")
    
    if total_size > 2000:
        log("警告: 总大小超过 2GB，可能无法上传到 GitHub Releases", "WARNING")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="SoundBot 统一构建脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                    # 构建当前平台
  %(prog)s --platform macos   # 构建 macOS 版本
  %(prog)s --platform windows # 构建 Windows 版本
  %(prog)s --clean            # 清理构建目录
        """
    )
    parser.add_argument(
        "--platform",
        choices=["macos", "windows", "linux", "all"],
        default=None,
        help="目标平台 (默认: 当前平台)"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="仅清理构建目录，不执行构建"
    )
    parser.add_argument(
        "--skip-backend",
        action="store_true",
        help="跳过后端构建 (用于调试)"
    )
    parser.add_argument(
        "--skip-electron",
        action="store_true",
        help="跳过 Electron 构建 (用于调试)"
    )
    
    args = parser.parse_args()
    
    try:
        # 仅清理
        if args.clean:
            clean_build_dirs()
            log("清理完成", "SUCCESS")
            return
        
        # 清理旧构建
        clean_build_dirs()
        
        # 构建后端
        if not args.skip_backend:
            build_backend()
        else:
            log("跳过后端构建", "WARNING")
        
        # 构建 Electron
        if not args.skip_electron:
            if args.platform == "all":
                # 构建所有平台
                for plat in ["macos", "windows"]:
                    try:
                        build_electron(plat)
                    except Exception as e:
                        log(f"构建 {plat} 失败: {e}", "ERROR")
            else:
                build_electron(args.platform)
        else:
            log("跳过 Electron 构建", "WARNING")
        
        # 验证构建结果
        if not args.skip_electron:
            verify_build(args.platform)
        
        log("=" * 60)
        log("🎉 构建成功！", "SUCCESS")
        log("=" * 60)
        log(f"输出目录: {ELECTRON_DIST_DIR}")
        
    except Exception as e:
        log("=" * 60)
        log(f"❌ 构建失败: {e}", "ERROR")
        log("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
