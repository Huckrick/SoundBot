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

PyInstaller 产物包含宿主平台的原生二进制，因此不支持跨操作系统构建。
请使用 CI 的各平台 runner 分别构建。

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
import struct
from pathlib import Path
from typing import Optional

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
PYINSTALLER_VERSION = "6.16.0"

NPM_MIRROR_ENV_VARS = (
    "ELECTRON_MIRROR",
    "electron_mirror",
    "npm_config_electron_mirror",
    "NPM_CONFIG_ELECTRON_MIRROR",
    "ELECTRON_BUILDER_BINARIES_MIRROR",
    "npm_config_electron_builder_binaries_mirror",
    "NPM_CONFIG_ELECTRON_BUILDER_BINARIES_MIRROR",
)

HOST_TARGETS = {
    "Darwin": "macos",
    "Windows": "windows",
}


def resolve_native_target(requested: Optional[str]) -> str:
    """Resolve the build target and reject cross-platform native bundles."""
    host_system = platform.system()
    host_target = HOST_TARGETS.get(host_system)
    if not host_target:
        raise RuntimeError(f"不支持的构建宿主平台: {host_system}")
    if requested == "all":
        raise RuntimeError("不支持 --platform all：PyInstaller 后端必须在各目标系统的原生宿主上分别构建")

    target = requested or host_target
    if target != host_target:
        raise RuntimeError(
            f"拒绝跨平台构建：当前宿主为 {host_target}，请求目标为 {target}。"
            "请在目标系统或对应 CI runner 上构建。"
        )

    machine = platform.machine().lower()
    if target == "macos" and machine not in {"arm64", "aarch64"}:
        raise RuntimeError(
            f"当前 macOS 架构为 {machine or 'unknown'}，但 package.json 仅打包 arm64；"
            "请使用 Apple Silicon 宿主。"
        )
    if target == "windows" and machine not in {"amd64", "x86_64"}:
        raise RuntimeError(
            f"当前 Windows 架构为 {machine or 'unknown'}，但 package.json 仅打包 x64。"
        )
    return target


def backend_executable_path(bundle_dir: Path) -> Path:
    exe_name = "soundbot-backend.exe" if platform.system() == "Windows" else "soundbot-backend"
    return bundle_dir / exe_name


def native_executable_architecture(executable: Path) -> tuple[str, str]:
    """Read PE/Mach-O headers so an x86 artifact cannot pass an arm64 gate."""
    data = executable.read_bytes()
    if data.startswith(b"MZ"):
        if len(data) < 0x40:
            raise RuntimeError(f"Windows PE 文件已截断: {executable}")
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_offset:pe_offset + 4] != b"PE\0\0":
            raise RuntimeError(f"Windows PE 签名无效: {executable}")
        machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
        return "windows", "x64" if machine == 0x8664 else f"pe-0x{machine:04x}"
    if data[:4] == b"\xcf\xfa\xed\xfe":
        cpu = struct.unpack_from("<I", data, 4)[0]
        return "macos", "arm64" if cpu == 0x0100000C else f"macho-0x{cpu:08x}"
    if data[:4] == b"\xfe\xed\xfa\xcf":
        cpu = struct.unpack_from(">I", data, 4)[0]
        return "macos", "arm64" if cpu == 0x0100000C else f"macho-0x{cpu:08x}"
    raise RuntimeError(f"不支持的原生可执行文件格式: {executable}")


def verify_native_backend_bundle(bundle_dir: Path) -> Path:
    """Fail fast when a missing or foreign-platform backend would be packaged."""
    executable = backend_executable_path(bundle_dir)
    if not bundle_dir.is_dir():
        raise RuntimeError(f"后端目录不存在: {bundle_dir}")
    if not executable.is_file():
        raise RuntimeError(f"后端可执行文件不存在: {executable}")
    if not ((bundle_dir / "_internal").is_dir() or (bundle_dir / "lib").is_dir()):
        raise RuntimeError(f"后端运行时目录缺失（需要 _internal 或 lib）: {bundle_dir}")

    host_system = platform.system()
    expected = ("windows", "x64") if host_system == "Windows" else ("macos", "arm64")
    detected = native_executable_architecture(executable)
    if detected != expected:
        raise RuntimeError(f"后端架构不匹配，期望 {expected}，实际 {detected}: {executable}")
    if host_system != "Windows" and not os.access(executable, os.X_OK):
        raise RuntimeError(f"后端可执行文件没有执行权限: {executable}")
    return executable


def verify_frozen_runtime_assets(bundle_dir: Path, target_platform: str) -> None:
    """Run the release-level PyAV/FFmpeg/license gate."""
    arch = "x64" if target_platform == "windows" else "arm64"
    run_command([
        sys.executable,
        str(PROJECT_ROOT / "tests" / "build" / "verify_frozen_bundle.py"),
        "--bundle", str(bundle_dir),
        "--platform", target_platform,
        "--arch", arch,
    ])


def verify_release_metadata(release_tag: Optional[str] = None) -> None:
    """Validate synchronized release metadata, optionally against an exact tag."""
    command = [
        sys.executable,
        str(PROJECT_ROOT / "tests" / "build" / "verify_release_metadata.py"),
        "--root", str(PROJECT_ROOT),
    ]
    if release_tag:
        command.extend(["--tag", release_tag])
    run_command(command)


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
    
    # Windows 上使用 shell=True 来正确找到 npm.cmd/npx.cmd。
    # list2cmdline 会按 Windows CRT 规则引用含空格或特殊字符的参数，
    # 避免手工 join 改变参数边界。
    if sys.platform == 'win32' and not shell:
        if len(cmd) > 0 and cmd[0] in ['npm', 'npx']:
            shell = True
            cmd = subprocess.list2cmdline([str(c) for c in cmd])
    
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


def clean_build_dirs(preserve_backend: bool = False):
    """清理构建目录"""
    log("清理构建目录...")

    # 只有调用者显式跳过后端构建时才能复用 dist/backend。
    # 不依赖 GITHUB_ACTIONS，避免 CI 误打包上一次的陈旧后端。
    if preserve_backend:
        log("保留 dist/backend 原生后端目录")
        dirs_to_clean = [
            BACKEND_DIR / "dist",
            BACKEND_DIR / "build",
            ELECTRON_DIST_DIR,
        ]
    else:
        dirs_to_clean = [
            DIST_DIR,
            BACKEND_DIR / "dist",
            BACKEND_DIR / "build",
            ELECTRON_DIST_DIR,
        ]
    
    for dir_path in dirs_to_clean:
        if dir_path.exists():
            log(f"删除: {dir_path}")
            shutil.rmtree(dir_path)


def install_python_deps():
    """安装 Python 依赖"""
    log("安装 Python 依赖...")
    
    # 固定 PyInstaller 与 hooks，避免构建图随环境漂移。
    build_requirements = BACKEND_DIR / "requirements-build.txt"
    run_command([
        sys.executable, "-m", "pip", "install", "-r", str(build_requirements), "-q"
    ])
    
    # 安装后端依赖
    requirements_file = BACKEND_DIR / "requirements.txt"
    if requirements_file.exists():
        run_command([
            sys.executable, "-m", "pip", "install", "--only-binary=av",
            "-r", str(requirements_file), "-q"
        ])


def build_backend(install_dependencies: bool = True) -> Path:
    """
    使用 PyInstaller 构建后端
    
    Returns:
        后端可执行文件路径
    """
    log("=" * 60)
    log("步骤 1: 构建 PyInstaller 后端")
    log("=" * 60)
    
    # onedir 模式：输出是目录，不是单文件
    backend_dir_path = DIST_DIR / "backend" / "soundbot-backend"
    
    # 调试日志
    log(f"检查后端路径: {backend_dir_path}")
    log(f"路径是否存在: {backend_dir_path.exists()}")
    if backend_dir_path.exists():
        log(f"路径是绝对路径: {backend_dir_path.is_absolute()}")
        log(f"路径内容: {list(backend_dir_path.iterdir())[:5]}...")  # 只显示前5个
    
    if install_dependencies:
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

    if not backend_dir_path.exists():
        raise RuntimeError(f"后端目录未生成: {backend_dir_path}")

    verify_native_backend_bundle(backend_dir_path)
    target = "windows" if platform.system() == "Windows" else "macos"
    verify_frozen_runtime_assets(backend_dir_path, target)

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
    install_env = official_npm_environment()
    # npm 使用实时输出避免卡住
    run_command(
        ["npm", "ci", "--progress=false"],
        cwd=PROJECT_ROOT,
        env=install_env,
        capture=False,
    )


def official_npm_environment() -> dict[str, str]:
    """Return a deterministic npm/Electron environment for release builds."""
    environment = os.environ.copy()
    for name in NPM_MIRROR_ENV_VARS:
        environment.pop(name, None)
    # Assignment is intentional: inherited npm_config values and a developer
    # user-level .npmrc must not redirect a release build to another registry.
    environment["npm_config_registry"] = "https://registry.npmjs.org/"
    environment["npm_config_userconfig"] = os.devnull
    return environment


def build_electron(target_platform: str = None, install_dependencies: bool = True):
    """
    构建 Electron 应用
    
    Args:
        target_platform: 目标平台 (macos, windows)
    """
    log("=" * 60)
    log("步骤 2: 构建 Electron 应用")
    log("=" * 60)
    
    if install_dependencies:
        install_npm_deps()

    verify_native_backend_bundle(DIST_DIR / "backend" / "soundbot-backend")

    # 根据平台选择构建命令（直接调用 electron-builder，避免 npm 脚本循环）
    # 使用 capture=False 实时输出，避免 Windows 编码问题和大缓冲区超时
    # Local npm configuration must not redirect official release builds to a
    # third-party mirror. GitHub Actions already has a clean config, while this
    # explicit environment makes developer builds deterministic as well.
    build_env = official_npm_environment()

    if target_platform == "macos":
        log("构建 macOS 应用...")
        run_command(
            ["npx", "--no-install", "electron-builder", "--mac", "--arm64"],
            cwd=PROJECT_ROOT,
            env=build_env,
            capture=False,
        )
    elif target_platform == "windows":
        log("构建 Windows 应用...")
        run_command(
            ["npx", "--no-install", "electron-builder", "--win", "--x64"],
            cwd=PROJECT_ROOT,
            env=build_env,
            capture=False,
        )
    else:
        raise RuntimeError(f"未知构建平台: {target_platform}")
    
    log("Electron 构建完成", "SUCCESS")


def package_version() -> str:
    """Return the package version used in electron-builder artifact names."""
    import json

    try:
        package = json.loads((PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 package.json 版本: {exc}") from exc
    version = package.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("package.json 缺少有效 version")
    return version.strip()


def verify_build(target_platform: str = None):
    """验证构建结果"""
    log("=" * 60)
    log("步骤 3: 验证构建结果")
    log("=" * 60)
    
    electron_dist = ELECTRON_DIST_DIR
    
    if not electron_dist.exists():
        raise RuntimeError(f"构建输出目录不存在: {electron_dist}")
    
    # 查找构建产物
    if target_platform == "macos":
        artifacts = sorted(electron_dist.glob("*.dmg"))
    elif target_platform == "windows":
        artifacts = sorted(electron_dist.glob("*.exe"))
    else:
        raise RuntimeError(f"未知构建平台: {target_platform}")
    
    if len(artifacts) != 1:
        names = ", ".join(artifact.name for artifact in artifacts) or "<none>"
        raise RuntimeError(f"目标平台必须恰好有一个构建产物，实际: {names}")

    version = package_version()
    if version not in artifacts[0].name:
        raise RuntimeError(
            f"构建产物文件名不包含当前版本 {version}: {artifacts[0].name}"
        )

    too_small = [artifact for artifact in artifacts if artifact.stat().st_size < 1024 * 1024]
    if too_small:
        raise RuntimeError(f"构建产物异常小: {', '.join(item.name for item in too_small)}")
    
    log("构建产物:", "SUCCESS")
    total_size = 0
    for artifact in artifacts:
        size_mb = artifact.stat().st_size / 1024 / 1024
        total_size += size_mb
        log(f"  - {artifact.name} ({size_mb:.1f} MB)")
    
    log(f"总大小: {total_size:.1f} MB")
    
    if total_size > 2000:
        log("警告: 总大小超过 2GB，可能无法上传到 GitHub Releases", "WARNING")

    if target_platform == "macos":
        app_paths = sorted(electron_dist.glob("mac*/SoundBot.app"))
        if len(app_paths) != 1:
            raise RuntimeError(
                "必须恰好找到一个解包的 SoundBot.app，"
                f"实际找到 {len(app_paths)} 个"
            )
        packaged_backend = (
            app_paths[0] / "Contents" / "Resources" / "backend" / "soundbot-backend"
        )
    else:
        unpacked_paths = sorted(path for path in electron_dist.glob("*unpacked") if path.is_dir())
        if len(unpacked_paths) != 1:
            raise RuntimeError(
                "必须恰好找到一个 win-unpacked，"
                f"实际找到 {len(unpacked_paths)} 个"
            )
        packaged_backend = unpacked_paths[0] / "resources" / "backend" / "soundbot-backend"
    verify_native_backend_bundle(packaged_backend)
    verify_frozen_runtime_assets(packaged_backend, target_platform)


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
        choices=["macos", "windows"],
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
    parser.add_argument(
        "--skip-dependency-install",
        action="store_true",
        help="跳过 Python 和 npm 依赖安装 (CI 已安装依赖时使用)"
    )
    parser.add_argument(
        "--release-tag",
        help="验证构建源与发布标签的版本一致，例如 v0.2.0"
    )
    
    args = parser.parse_args()

    if args.skip_backend and args.skip_electron:
        parser.error("--skip-backend 与 --skip-electron 不能同时使用")
    
    try:
        # 仅清理
        if args.clean:
            clean_build_dirs()
            log("清理完成", "SUCCESS")
            return

        target_platform = resolve_native_target(args.platform)
        verify_release_metadata(args.release_tag)
        
        # 清理旧构建
        clean_build_dirs(preserve_backend=args.skip_backend)
        
        # 构建后端
        if not args.skip_backend:
            build_backend(install_dependencies=not args.skip_dependency_install)
        else:
            log("跳过后端构建", "WARNING")
        
        # 构建 Electron
        if not args.skip_electron:
            build_electron(
                target_platform,
                install_dependencies=not args.skip_dependency_install,
            )
        else:
            log("跳过 Electron 构建", "WARNING")
        
        # 验证构建结果
        if not args.skip_electron:
            verify_build(target_platform)
        
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
