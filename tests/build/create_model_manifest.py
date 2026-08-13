#!/usr/bin/env python3
"""Create and validate the release manifest for SoundBot's CLAP model bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, Optional


IMMUTABLE_REVISION = re.compile(r"^[0-9a-fA-F]{40,64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_revision(model_id: str, supplied_revision: Optional[str]) -> str:
    """Resolve an immutable Hugging Face commit; mutable labels are rejected."""
    revision = supplied_revision
    if not revision:
        try:
            from huggingface_hub import model_info
        except ImportError as exc:
            raise RuntimeError(
                "huggingface_hub is required when --revision is not provided"
            ) from exc
        revision = model_info(model_id).sha

    revision = str(revision or "").strip()
    if not IMMUTABLE_REVISION.fullmatch(revision):
        raise ValueError(
            "model revision must be an immutable 40-64 character hexadecimal commit"
        )
    return revision.lower()


def collect_hashes(models_dir: Path) -> Dict[str, str]:
    clap_dir = models_dir / "clap"
    if not clap_dir.is_dir():
        raise FileNotFoundError(f"missing CLAP model directory: {clap_dir}")

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


def download_pinned_model(models_dir: Path, model_id: str, revision: str) -> None:
    """Download model and processor from the exact immutable Hub commit."""
    from transformers import ClapModel, ClapProcessor

    destination = models_dir / "clap"
    destination.mkdir(parents=True, exist_ok=True)
    model = ClapModel.from_pretrained(model_id, revision=revision)
    processor = ClapProcessor.from_pretrained(model_id, revision=revision)
    model.save_pretrained(destination)
    processor.save_pretrained(destination)


def write_manifest(models_dir: Path, model_id: str, revision: str) -> Path:
    if models_dir.is_symlink():
        raise ValueError("models directory may not be a symlink")
    models_dir = models_dir.resolve(strict=True)
    manifest_path = models_dir / "model-manifest.json"
    manifest = {
        "model_id": model_id,
        "revision": resolve_revision(model_id, revision),
        "files": collect_hashes(models_dir),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Read the finished file back so malformed/truncated output fails the CI job.
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    if loaded != manifest:
        raise RuntimeError("model manifest verification failed after writing")
    return manifest_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--model-id", default="laion/larger_clap_general")
    parser.add_argument(
        "--revision",
        help="Immutable Hugging Face commit; resolved from the Hub when omitted",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download CLAP from the resolved immutable revision before hashing it",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    revision = resolve_revision(args.model_id, args.revision)
    if args.download:
        download_pinned_model(args.models_dir, args.model_id, revision)
    manifest_path = write_manifest(args.models_dir, args.model_id, revision)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    print(
        f"[OK] {manifest_path}: {manifest['model_id']} @ {manifest['revision']} "
        f"({len(manifest['files'])} files)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
