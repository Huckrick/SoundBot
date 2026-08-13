#!/usr/bin/env python3
"""
SoundBot 资源下载管理器
用于从 GitHub Releases 下载模型和 Python 环境
"""

import os
import sys
import json
import hashlib
import zipfile
import shutil
import subprocess
import fnmatch
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
import ssl
import uuid
import re

# 设置 UTF-8 编码（Windows 兼容）
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    os.environ['PYTHONIOENCODING'] = 'utf-8'

# 默认配置
DEFAULT_CONFIG = {
    "github_repo": "Huckrick/SoundBot",
    "resources": {
        "models": {
            "filename": "models.zip",
            "filename_patterns": ["models.zip", "models-*.zip"],
            "extract_to": "models",
            "required": False,
            "require_checksum": True,
            "description": "AI 模型文件 (CLAP等)"
        }
    }
}

CONFIG_FILE = "download_config.json"
INSTALL_RECEIPT = ".soundbot-install.json"


def get_config():
    """获取下载配置"""
    config_path = Path(__file__).parent.parent / CONFIG_FILE
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return DEFAULT_CONFIG


def get_project_root():
    """获取项目根目录"""
    return Path(__file__).parent.parent


def get_resource_root() -> Path:
    """Return the writable resource root used for model installation.

    Desktop callers pass Electron's userData directory through
    ``SOUNDBOT_USER_DATA_DIR``. Source checkouts default to the repository so
    existing developer commands remain compatible.
    """
    configured = os.environ.get("SOUNDBOT_RESOURCE_ROOT") or os.environ.get(
        "SOUNDBOT_USER_DATA_DIR"
    )
    root = Path(configured).expanduser() if configured else get_project_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_platform():
    """获取当前平台"""
    if sys.platform == 'darwin':
        return 'darwin'
    elif sys.platform == 'win32':
        return 'win32'
    else:
        return 'linux'


def get_download_dir():
    """获取下载目录"""
    download_dir = get_resource_root() / "downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    return download_dir


def get_github_releases(repo):
    """获取 GitHub Releases 列表"""
    url = f"https://api.github.com/repos/{repo}/releases"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "SoundBot-DownloadManager/1.0"
    }
    
    try:
        ctx = ssl.create_default_context()
        req = Request(url, headers=headers)
        with urlopen(req, context=ctx, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"[ERROR] 获取 releases 失败: {e}")
        return None


def get_release_by_tag(repo, tag):
    """Fetch one exact release so prerelease model assets remain version-safe."""
    from urllib.parse import quote

    url = f"https://api.github.com/repos/{repo}/releases/tags/{quote(tag, safe='')}"
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "SoundBot-DownloadManager/1.0"
    }
    
    try:
        ctx = ssl.create_default_context()
        req = Request(url, headers=headers)
        with urlopen(req, context=ctx, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"[ERROR] 获取 release {tag} 失败: {e}")
        return None


def get_application_release_tag() -> str:
    """Return the exact release tag matching this source/application version."""
    config_text = (get_project_root() / "backend" / "config.py").read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', config_text, re.MULTILINE)
    if match:
        version = match.group(1)
    else:
        package = json.loads((get_project_root() / "package.json").read_text(encoding="utf-8"))
        version = str(package["version"])
    return f"v{version}"


def download_file(url, dest_path, progress_callback=None):
    """下载文件并显示进度"""
    try:
        ctx = ssl.create_default_context()
        req = Request(url, headers={"User-Agent": "SoundBot-DownloadManager/1.0"})
        
        with urlopen(req, context=ctx, timeout=60) as response:
            total_size = int(response.headers.get('Content-Length', 0))
            downloaded = 0
            block_size = 8192
            
            with open(dest_path, 'wb') as f:
                while True:
                    chunk = response.read(block_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if progress_callback and total_size > 0:
                        progress = (downloaded / total_size) * 100
                        progress_callback(downloaded, total_size, progress)
        
        return True
    except Exception as e:
        print(f"[ERROR] 下载失败: {e}")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        return False


def extract_zip(zip_path, extract_to, progress_callback=None):
    """安全解压 zip，拒绝路径逃逸和符号链接。"""
    try:
        root = Path(extract_to).resolve(strict=False)
        root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            members = zip_ref.infolist()
            total_files = len(members)

            for i, member in enumerate(members):
                # Unix symlink bit in the upper mode word.
                mode = member.external_attr >> 16
                if (mode & 0o170000) == 0o120000:
                    raise ValueError(f"压缩包包含符号链接: {member.filename}")
                member_name = member.filename.replace('\\', '/')
                if member_name.startswith('/') or '..' in Path(member_name).parts:
                    raise ValueError(f"压缩包路径越界: {member.filename}")
                destination = (root / member_name).resolve(strict=False)
                destination.relative_to(root)
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with zip_ref.open(member, 'r') as source, open(destination, 'wb') as output:
                        shutil.copyfileobj(source, output)
                if progress_callback:
                    progress = ((i + 1) / total_files) * 100
                    progress_callback(i + 1, total_files, progress)
        
        return True
    except Exception as e:
        print(f"[ERROR] 解压失败: {e}")
        return False


def verify_download(file_path, expected_hash=None):
    """验证下载的文件"""
    if not os.path.exists(file_path):
        return False
    
    if expected_hash:
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        
        actual_hash = sha256_hash.hexdigest()
        if actual_hash.lower() != str(expected_hash).strip().lower():
            print(f"[ERROR] 文件校验失败: {actual_hash} != {expected_hash}")
            return False
    
    return True


def read_release_checksum(resource, release, asset_name, download_dir):
    """Resolve a pinned checksum from config or a sibling release asset."""
    configured = resource.get("expected_sha256")
    if configured:
        return configured
    checksum_names = {
        f"{asset_name}.sha256",
        f"{asset_name}.sha256.txt",
        "SHA256SUMS",
        "SHA256SUMS.txt",
    }
    checksum_asset = next(
        (item for item in release.get("assets", []) if item.get("name") in checksum_names),
        None,
    )
    if not checksum_asset:
        return None
    checksum_path = download_dir / checksum_asset["name"]
    if not download_file(checksum_asset["browser_download_url"], checksum_path):
        return None
    try:
        for line in checksum_path.read_text(encoding="utf-8").splitlines():
            parts = line.strip().split()
            if not parts:
                continue
            if len(parts) == 1 or parts[-1].lstrip('*') == asset_name:
                candidate = parts[0]
                if len(candidate) == 64 and all(ch in '0123456789abcdefABCDEF' for ch in candidate):
                    return candidate.lower()
    finally:
        checksum_path.unlink(missing_ok=True)
    return None


def verify_model_manifest(staging_dir):
    """Verify immutable identity and a complete per-file model hash manifest."""
    staging = Path(staging_dir)
    clap_dir = staging / "clap"
    if not clap_dir.is_dir():
        raise ValueError("模型包必须包含 clap/ 目录")
    manifest_path = staging / "model-manifest.json"
    if not manifest_path.is_file():
        raise ValueError("模型包缺少 model-manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not manifest.get("model_id") or not manifest.get("revision"):
        raise ValueError("模型 manifest 缺少 model_id/revision")
    revision = str(manifest["revision"])
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", revision):
        raise ValueError("模型 manifest revision 必须是 40-64 位不可变十六进制 commit")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("模型 manifest 必须包含非空逐文件 SHA-256 清单")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not relative.startswith("clap/"):
            raise ValueError(f"模型 manifest 路径无效: {relative!r}")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError(f"模型 manifest SHA-256 无效: {relative}")
        candidate = (staging / relative).resolve(strict=False)
        candidate.relative_to(staging.resolve(strict=False))
        if not candidate.is_file() or not verify_download(candidate, expected):
            raise ValueError(f"模型文件校验失败: {relative}")
    actual_files = {
        path.relative_to(staging).as_posix()
        for path in clap_dir.rglob("*")
        if path.is_file()
    }
    declared_files = set(files)
    if actual_files != declared_files:
        missing = sorted(declared_files - actual_files)
        extra = sorted(actual_files - declared_files)
        raise ValueError(f"模型 manifest 文件集合不完整: missing={missing}, extra={extra}")
    return manifest


def write_install_receipt(
    staging_dir: Path,
    *,
    release_tag: str,
    asset_name: str,
    archive_sha256: str,
    manifest: dict,
) -> None:
    """Bind an installed model directory to one immutable release asset."""
    receipt = {
        "schema_version": 1,
        "release_tag": release_tag,
        "asset_name": asset_name,
        "archive_sha256": archive_sha256.lower(),
        "model_id": manifest["model_id"],
        "revision": manifest["revision"],
    }
    (Path(staging_dir) / INSTALL_RECEIPT).write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def verify_install_receipt(
    installed_dir: Path,
    *,
    release_tag: str,
    asset_name: str,
    archive_sha256: str,
) -> dict:
    """Reject an internally valid model installed for another release."""
    receipt_path = Path(installed_dir) / INSTALL_RECEIPT
    if not receipt_path.is_file():
        raise ValueError("已安装模型缺少 release 来源凭据")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"已安装模型来源凭据无效: {exc}") from exc
    expected = {
        "schema_version": 1,
        "release_tag": release_tag,
        "asset_name": asset_name,
        "archive_sha256": archive_sha256.lower(),
    }
    mismatch = {
        key: {"installed": receipt.get(key), "expected": value}
        for key, value in expected.items()
        if receipt.get(key) != value
    }
    if mismatch:
        raise ValueError(f"已安装模型与当前 release 不匹配: {mismatch}")
    return receipt


def atomic_replace_directory(staging_dir, target_dir):
    """Replace a directory only after staging has been fully validated."""
    staging = Path(staging_dir)
    target = Path(target_dir)
    backup = target.with_name(f"{target.name}.previous-{uuid.uuid4().hex[:8]}")
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(staging, target)
    except Exception:
        if backup.exists() and not target.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        if backup.is_dir():
            shutil.rmtree(backup)
        else:
            backup.unlink()


def find_release_asset(resource, release):
    """按固定名称或模式查找 release 资源文件"""
    filename = resource["filename"]
    filename_patterns = resource.get("filename_patterns", [filename])

    for asset in release.get("assets", []):
        if asset["name"] == filename:
            return asset, asset["name"]

    for pattern in filename_patterns:
        for asset in release.get("assets", []):
            if fnmatch.fnmatch(asset["name"], pattern):
                return asset, asset["name"]

    return None, filename


def download_resource(resource_type, release_tag=None, force=False):
    """
    下载指定资源
    
    Args:
        resource_type: 'models'
        release_tag: 指定 release 标签；None 表示当前应用版本的精确标签
        force: 是否强制重新下载
    """
    config = get_config()
    repo = config.get("github_repo", DEFAULT_CONFIG["github_repo"])
    
    if resource_type not in config.get("resources", {}):
        print(f"[ERROR] 未知资源类型: {resource_type}")
        return False
    
    resource = config["resources"][resource_type]
    
    # 检查平台要求
    if "platform" in resource:
        if resource["platform"] != get_platform():
            print(f"[INFO] 跳过 {resource_type}: 不适用于当前平台")
            return True
    
    # 获取 release 信息
    release_tag = release_tag or get_application_release_tag()
    release = get_release_by_tag(repo, release_tag)
    if not release:
        print(f"[ERROR] 未找到与当前应用兼容的 release: {release_tag}")
        return False
    if release.get("tag_name") != release_tag:
        print(f"[ERROR] Release tag 响应不匹配: {release.get('tag_name')} != {release_tag}")
        return False
    
    # 查找资源文件
    filename = resource["filename"]
    asset, asset_name = find_release_asset(resource, release)
    
    if not asset:
        print(f"[ERROR] 在 release {release['tag_name']} 中未找到 {filename}")
        print(f"[INFO] 可用资源: {[a['name'] for a in release.get('assets', [])]}")
        return False

    download_dir = get_download_dir()
    expected_hash = read_release_checksum(resource, release, asset_name, download_dir)
    if resource.get("require_checksum", False) and not expected_hash:
        print("[ERROR] Release 未提供必需的 SHA-256 校验值")
        return False
    
    # 检查是否已存在
    resource_root = get_resource_root()
    extract_to = resource_root / resource["extract_to"]
    
    if extract_to.exists() and not force:
        try:
            if resource_type == "models":
                verify_model_manifest(extract_to)
                verify_install_receipt(
                    extract_to,
                    release_tag=release_tag,
                    asset_name=asset_name,
                    archive_sha256=expected_hash or "",
                )
            print(f"[INFO] {resource_type} 已存在且校验通过: {extract_to}")
            print(f"[INFO] 使用 --force 重新下载")
            return True
        except Exception as exc:
            print(f"[WARN] 已安装 {resource_type} 校验失败，将重新下载: {exc}")
    
    # 下载文件
    download_path = download_dir / asset_name
    
    print(f"[INFO] 下载 {resource_type}...")
    print(f"[INFO] 版本: {release['tag_name']}")
    print(f"[INFO] 大小: {asset['size'] / 1024 / 1024:.1f} MB")
    print(f"[INFO] 保存到: {download_path}")
    
    def progress_callback(downloaded, total, percent):
        mb = downloaded / 1024 / 1024
        total_mb = total / 1024 / 1024
        print(f"\r[PROGRESS] {mb:.1f}/{total_mb:.1f} MB ({percent:.1f}%)", end='', flush=True)
    
    if not download_file(asset["browser_download_url"], download_path, progress_callback):
        print()
        return False
    
    print()
    print(f"[OK] 下载完成: {download_path}")

    if not verify_download(download_path, expected_hash):
        download_path.unlink(missing_ok=True)
        return False
    
    # 解压文件
    print(f"[INFO] 解压到: {extract_to}")
    
    extract_to.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = extract_to.parent / f".{extract_to.name}.staging-{uuid.uuid4().hex}"
    
    def extract_progress(current, total, percent):
        print(f"\r[PROGRESS] 解压中... {current}/{total} ({percent:.1f}%)", end='', flush=True)
    
    if not extract_zip(download_path, staging_dir, extract_progress):
        print()
        shutil.rmtree(staging_dir, ignore_errors=True)
        return False

    try:
        manifest = verify_model_manifest(staging_dir)
        write_install_receipt(
            staging_dir,
            release_tag=release_tag,
            asset_name=asset_name,
            archive_sha256=expected_hash or "",
            manifest=manifest,
        )
        atomic_replace_directory(staging_dir, extract_to)
    except Exception as exc:
        print(f"\n[ERROR] 模型包校验/替换失败: {exc}")
        shutil.rmtree(staging_dir, ignore_errors=True)
        download_path.unlink(missing_ok=True)
        return False
    
    print()
    print(f"[OK] 模型已原子安装: {extract_to}")
    print(f"[INFO] 模型: {manifest['model_id']} @ {manifest['revision']}")
    
    # 清理下载文件
    if download_path.exists():
        download_path.unlink()
        print(f"[INFO] 清理临时文件")
    
    return True


def check_resources():
    """检查所需资源是否已下载"""
    config = get_config()
    project_root = get_resource_root()
    
    results = {}
    all_ready = True
    
    print("=" * 60)
    print("资源检查")
    print("=" * 60)
    
    for resource_type, resource in config.get("resources", {}).items():
        # 跳过不适用当前平台的资源
        if "platform" in resource:
            if resource["platform"] != get_platform():
                continue
        
        extract_to = project_root / resource["extract_to"]
        exists = extract_to.exists()
        error = None
        if resource_type == "models" and exists:
            try:
                verify_model_manifest(extract_to)
            except Exception as exc:
                exists = False
                error = str(exc)
        required = resource.get("required", False)
        
        status = "✓" if exists else "✗"
        req_mark = "[必需]" if required else "[可选]"
        
        print(f"{status} {resource_type} {req_mark}: {extract_to}")
        
        results[resource_type] = {
            "exists": exists,
            "required": required,
            "path": str(extract_to),
            "error": error,
        }
        
        if required and not exists:
            all_ready = False
    
    print("=" * 60)
    if all_ready:
        print("[OK] 所有必需资源已准备就绪")
    else:
        print("[WARN] 部分必需资源缺失，请运行下载命令")
    
    return results, all_ready


def setup_python_env():
    """设置 Python 环境（如果没有 venv）"""
    project_root = get_project_root()
    venv_path = project_root / "backend" / "venv"
    
    if venv_path.exists():
        print(f"[INFO] Python 环境已存在: {venv_path}")
        return True
    
    print("[INFO] 创建 Python 虚拟环境...")
    
    try:
        backend_path = project_root / "backend"
        backend_path.mkdir(exist_ok=True)
        
        # 创建 venv
        subprocess.check_call([sys.executable, "-m", "venv", str(venv_path)])
        print(f"[OK] 虚拟环境创建成功: {venv_path}")
        
        # 安装依赖
        print("[INFO] 安装依赖...")
        requirements = backend_path / "requirements.txt"
        
        if requirements.exists():
            if sys.platform == 'win32':
                pip_cmd = venv_path / "Scripts" / "pip.exe"
            else:
                pip_cmd = venv_path / "bin" / "pip"
            
            subprocess.check_call([str(pip_cmd), "install", "-r", str(requirements)])
            print("[OK] 依赖安装完成")
        
        return True
    except Exception as e:
        print(f"[ERROR] 创建 Python 环境失败: {e}")
        return False


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="SoundBot 资源下载管理器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s check              检查资源状态
  %(prog)s download models    下载 AI 模型
  %(prog)s download all       下载所有必需资源
  %(prog)s setup              自动设置环境（下载 AI 模型）
        """
    )
    
    parser.add_argument(
        "command",
        choices=["check", "download", "setup"],
        help="要执行的命令"
    )
    
    parser.add_argument(
        "resource",
        nargs="?",
        choices=["models", "all"],
        help="要下载的资源类型"
    )
    
    parser.add_argument(
        "--tag", "-t",
        help="指定 release 标签（默认使用当前应用版本的精确标签）"
    )
    
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="强制重新下载"
    )
    
    parser.add_argument(
        "--repo", "-r",
        help="GitHub 仓库地址（格式: owner/repo）"
    )
    
    args = parser.parse_args()
    
    # 更新配置
    if args.repo:
        config = get_config()
        config["github_repo"] = args.repo
        config_path = get_project_root() / CONFIG_FILE
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"[INFO] 已更新仓库配置: {args.repo}")
    
    if args.command == "check":
        check_resources()
    
    elif args.command == "download":
        if not args.resource:
            print("[ERROR] 请指定要下载的资源类型")
            parser.print_help()
            sys.exit(1)
        
        if args.resource == "all":
            resources = ["models"]
            
            success = True
            for res in resources:
                if not download_resource(res, args.tag, args.force):
                    success = False
            
            sys.exit(0 if success else 1)

        else:
            success = download_resource(args.resource, args.tag, args.force)
            sys.exit(0 if success else 1)
    
    elif args.command == "setup":
        print("=" * 60)
        print("SoundBot 自动设置")
        print("=" * 60)
        
        # 检查资源
        results, all_ready = check_resources()
        
        # Always resolve the exact application release. A model can be
        # internally valid while still belonging to an older app version.
        print("\n[INFO] 检查并安装当前版本的精确模型资源...")
        compatible_release = download_resource("models", args.tag, args.force)
        if not compatible_release:
            print("[WARN] 模型下载失败，应用仍可以使用基础功能")
        
        # 最终检查
        print("\n" + "=" * 60)
        results, all_ready = check_resources()
        models_ready = results.get("models", {}).get("exists", False)
        sys.exit(0 if compatible_release and all_ready and models_ready else 1)


if __name__ == "__main__":
    main()
