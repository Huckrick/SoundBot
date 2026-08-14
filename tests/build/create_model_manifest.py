#!/usr/bin/env python3
"""Create SoundBot's pinned, licensed CLAP model bundle manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLE_CONFIG = PROJECT_ROOT / "config" / "model_bundle.json"
NOTICE_BUNDLE_NAME = "CLAP_MODEL_NOTICE.txt"
IMMUTABLE_REVISION = re.compile(r"^[0-9a-fA-F]{40,64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_SCHEMA_VERSION = 1
REQUIRED_CONFIG_FIELDS = {
    "schema_version",
    "model_id",
    "revision",
    "license",
    "source_url",
    "notice_file",
}


@dataclass(frozen=True)
class ModelBundleConfig:
    schema_version: int
    model_id: str
    revision: str
    license: str
    source_url: str
    notice_path: Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_bundle_config(path: Path = DEFAULT_BUNDLE_CONFIG) -> ModelBundleConfig:
    """Load and strictly validate the repository's single model identity source."""
    config_path = Path(path).expanduser().resolve(strict=True)
    loaded: Any = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        raise ValueError("model bundle config must be a JSON object")

    missing = sorted(REQUIRED_CONFIG_FIELDS - set(loaded))
    if missing:
        raise ValueError(f"model bundle config is missing fields: {missing}")
    unknown = sorted(set(loaded) - REQUIRED_CONFIG_FIELDS)
    if unknown:
        raise ValueError(f"model bundle config has unknown fields: {unknown}")
    if (
        type(loaded["schema_version"]) is not int
        or loaded["schema_version"] != SUPPORTED_SCHEMA_VERSION
    ):
        raise ValueError(
            f"unsupported model bundle schema_version: {loaded['schema_version']!r}"
        )

    model_id = str(loaded["model_id"]).strip()
    revision = str(loaded["revision"]).strip().lower()
    license_id = str(loaded["license"]).strip()
    source_url = str(loaded["source_url"]).strip()
    notice_value = str(loaded["notice_file"]).strip()
    if not model_id or "/" not in model_id:
        raise ValueError("model_id must be a non-empty Hugging Face repository id")
    if not IMMUTABLE_REVISION.fullmatch(revision):
        raise ValueError(
            "model revision must be an immutable 40-64 character hexadecimal commit"
        )
    if not license_id:
        raise ValueError("model license identifier must not be empty")
    if not source_url.startswith("https://"):
        raise ValueError("model source_url must use HTTPS")
    if not notice_value:
        raise ValueError("model notice_file must not be empty")

    configured_notice = Path(notice_value).expanduser()
    notice_candidate = (
        configured_notice
        if configured_notice.is_absolute()
        else PROJECT_ROOT / configured_notice
    )
    if notice_candidate.is_symlink():
        raise ValueError("model notice_file must be a regular, non-symlink file")
    notice_path = notice_candidate.resolve(strict=True)
    if not notice_path.is_file():
        raise ValueError("model notice_file must be a regular, non-symlink file")

    return ModelBundleConfig(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        model_id=model_id,
        revision=revision,
        license=license_id,
        source_url=source_url,
        notice_path=notice_path,
    )


def collect_hashes(models_dir: Path) -> Dict[str, str]:
    clap_dir = models_dir / "clap"
    if not clap_dir.is_dir() or clap_dir.is_symlink():
        raise FileNotFoundError(f"missing regular CLAP model directory: {clap_dir}")

    hashes: Dict[str, str] = {}
    for path in sorted(clap_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"model bundle may not contain symlinks: {path}")
        if path.is_file():
            relative = path.relative_to(models_dir).as_posix()
            hashes[relative] = sha256_file(path)

    if not hashes:
        raise ValueError("CLAP model directory contains no files")
    return hashes


def download_pinned_model(
    models_dir: Path,
    bundle: ModelBundleConfig,
) -> None:
    """Download model and processor from the configured immutable Hub commit."""
    try:
        from transformers import ClapModel, ClapProcessor
    except ImportError as exc:
        raise RuntimeError(
            "transformers is required; install the repository's pinned build dependencies first"
        ) from exc

    destination = models_dir / "clap"
    destination.mkdir(parents=True, exist_ok=True)
    model = ClapModel.from_pretrained(bundle.model_id, revision=bundle.revision)
    processor = ClapProcessor.from_pretrained(bundle.model_id, revision=bundle.revision)
    model.save_pretrained(destination)
    processor.save_pretrained(destination)


def copy_notice(models_dir: Path, bundle: ModelBundleConfig) -> Path:
    destination = models_dir / NOTICE_BUNDLE_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundle.notice_path, destination)
    if destination.read_bytes() != bundle.notice_path.read_bytes():
        raise RuntimeError("model notice verification failed after copying")
    return destination


def _manifest_payload(
    models_dir: Path, bundle: ModelBundleConfig, notice_path: Path
) -> dict[str, Any]:
    return {
        "schema_version": bundle.schema_version,
        "model_id": bundle.model_id,
        "revision": bundle.revision,
        "license": bundle.license,
        "source_url": bundle.source_url,
        "notice": {
            "path": NOTICE_BUNDLE_NAME,
            "sha256": sha256_file(notice_path),
        },
        "files": collect_hashes(models_dir),
    }


def verify_manifest_against_config(
    models_dir: Path, bundle: ModelBundleConfig
) -> dict[str, Any]:
    """Verify bundle identity, notice bytes, and every declared model hash."""
    candidate = Path(models_dir).expanduser()
    if candidate.is_symlink():
        raise ValueError("models directory may not be a symlink")
    root = candidate.resolve(strict=True)
    manifest_path = root / "model-manifest.json"
    notice_path = root / NOTICE_BUNDLE_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise FileNotFoundError(f"missing regular model manifest: {manifest_path}")
    if not notice_path.is_file() or notice_path.is_symlink():
        raise FileNotFoundError(f"missing regular model notice: {notice_path}")
    if notice_path.read_bytes() != bundle.notice_path.read_bytes():
        raise ValueError("bundled model notice does not match the controlled notice")

    loaded: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_metadata = {
        "schema_version": bundle.schema_version,
        "model_id": bundle.model_id,
        "revision": bundle.revision,
        "license": bundle.license,
        "source_url": bundle.source_url,
        "notice": {
            "path": NOTICE_BUNDLE_NAME,
            "sha256": sha256_file(notice_path),
        },
    }
    if not isinstance(loaded, dict):
        raise ValueError("model manifest must be a JSON object")
    expected_keys = {*expected_metadata, "files"}
    if set(loaded) != expected_keys:
        raise ValueError(
            "model manifest fields do not match the strict schema: "
            f"expected={sorted(expected_keys)}, actual={sorted(loaded)}"
        )
    for key, expected in expected_metadata.items():
        if loaded.get(key) != expected:
            raise ValueError(f"model manifest {key} does not match bundle config")

    declared = loaded.get("files")
    actual = collect_hashes(root)
    if not isinstance(declared, dict) or declared != actual:
        raise ValueError("model manifest file hashes do not match the model directory")
    if any(
        not isinstance(value, str) or not SHA256.fullmatch(value)
        for value in declared.values()
    ):
        raise ValueError("model manifest contains an invalid SHA-256 value")
    return loaded


def write_manifest(models_dir: Path, bundle: ModelBundleConfig) -> Path:
    if models_dir.is_symlink():
        raise ValueError("models directory may not be a symlink")
    root = models_dir.expanduser().resolve(strict=True)
    notice_path = copy_notice(root, bundle)
    manifest_path = root / "model-manifest.json"
    manifest = _manifest_payload(root, bundle, notice_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    verify_manifest_against_config(root, bundle)
    return manifest_path


def create_bundle(
    models_dir: Path,
    bundle_config: Path = DEFAULT_BUNDLE_CONFIG,
    *,
    download: bool = False,
) -> Path:
    bundle = load_bundle_config(bundle_config)
    models_dir = Path(models_dir).expanduser()
    if not download:
        raise ValueError(
            "refusing to bless an existing model tree with the pinned revision; "
            "bundle creation requires an exact --download"
        )

    if models_dir.is_symlink():
        raise ValueError("models directory may not be a symlink")
    if models_dir.exists() and not models_dir.is_dir():
        raise ValueError("models destination must be a directory")
    models_dir = models_dir.resolve(strict=False)
    parent = models_dir.parent
    parent.mkdir(parents=True, exist_ok=True)

    staging = Path(
        tempfile.mkdtemp(prefix=f".{models_dir.name}.download-", dir=parent)
    )
    backup = parent / f".{models_dir.name}.backup-{uuid.uuid4().hex}"
    moved_existing = False
    try:
        download_pinned_model(staging, bundle)
        write_manifest(staging, bundle)
        verify_manifest_against_config(staging, bundle)

        if models_dir.exists():
            os.replace(models_dir, backup)
            moved_existing = True
        try:
            os.replace(staging, models_dir)
        except Exception:
            if moved_existing:
                os.replace(backup, models_dir)
                moved_existing = False
            raise
        if moved_existing:
            shutil.rmtree(backup)
            moved_existing = False
        return models_dir / "model-manifest.json"
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if moved_existing and backup.exists() and not models_dir.exists():
            os.replace(backup, models_dir)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument(
        "--bundle-config",
        type=Path,
        default=DEFAULT_BUNDLE_CONFIG,
        help="Single pinned model identity/notice config",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        required=True,
        help="Download CLAP from the configured immutable revision before hashing it",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    manifest_path = create_bundle(
        args.models_dir, args.bundle_config, download=args.download
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(
        f"[OK] {manifest_path}: {manifest['model_id']} @ {manifest['revision']} "
        f"({len(manifest['files'])} files; notice verified)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
