#!/usr/bin/env python3
"""Download SoundBot's configured, immutable CLAP bundle for local use."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE_CONFIG = PROJECT_ROOT / "config" / "model_bundle.json"
MANIFEST_BUILDER = PROJECT_ROOT / "tests" / "build" / "create_model_manifest.py"


IMMUTABLE_REVISION = re.compile(r"^[0-9a-fA-F]{40,64}$")


def read_identity(bundle_config: Path) -> tuple[str, str, str]:
    loaded: Any = json.loads(
        Path(bundle_config).expanduser().resolve(strict=True).read_text(encoding="utf-8")
    )
    if not isinstance(loaded, dict):
        raise ValueError("model bundle config must be a JSON object")
    model_id = str(loaded.get("model_id", "")).strip()
    revision = str(loaded.get("revision", "")).strip().lower()
    notice_file = str(loaded.get("notice_file", "")).strip()
    if not model_id or not notice_file:
        raise ValueError(
            "model bundle config must define model_id, revision, and notice_file"
        )
    if not IMMUTABLE_REVISION.fullmatch(revision):
        raise ValueError("model bundle config revision must be an immutable commit")
    return model_id, revision, notice_file


def download_clap_model(
    models_dir: Path | None = None,
    bundle_config: Path = DEFAULT_BUNDLE_CONFIG,
) -> Path:
    """Run the strict release builder; never install packages or select a branch."""
    destination = Path(models_dir or PROJECT_ROOT / "models").expanduser()
    config_path = Path(bundle_config).expanduser().resolve(strict=True)
    model_id, revision, _notice_file = read_identity(config_path)
    print(f"Downloading pinned CLAP model: {model_id} @ {revision}")
    print(f"Destination: {destination.resolve(strict=False)}")

    subprocess.run(
        [
            sys.executable,
            str(MANIFEST_BUILDER),
            "--models-dir",
            str(destination),
            "--bundle-config",
            str(config_path),
            "--download",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    return destination / "clap"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=PROJECT_ROOT / "models")
    parser.add_argument(
        "--bundle-config",
        type=Path,
        default=DEFAULT_BUNDLE_CONFIG,
        help="Single pinned model identity/revision/notice config",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        clap_dir = download_clap_model(args.models_dir, args.bundle_config)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"[ERROR] Model download failed: {exc}", file=sys.stderr)
        return 1

    size_bytes = sum(
        path.stat().st_size for path in clap_dir.rglob("*") if path.is_file()
    )
    print(f"[OK] Verified local bundle ({size_bytes / 1024 / 1024:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
