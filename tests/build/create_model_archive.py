#!/usr/bin/env python3
"""Create a deterministic, validated SoundBot model release archive."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
BUILD_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from download_manager import verify_model_manifest

try:
    from .create_model_manifest import (
        DEFAULT_BUNDLE_CONFIG,
        NOTICE_BUNDLE_NAME,
        load_bundle_config,
        verify_manifest_against_config,
    )
except ImportError:  # Direct `python tests/build/create_model_archive.py` execution.
    if str(BUILD_SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(BUILD_SCRIPTS_DIR))
    from create_model_manifest import (
        DEFAULT_BUNDLE_CONFIG,
        NOTICE_BUNDLE_NAME,
        load_bundle_config,
        verify_manifest_against_config,
    )


ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def create_model_archive(
    source: Path,
    output: Path,
    bundle_config: Path = DEFAULT_BUNDLE_CONFIG,
) -> None:
    if source.is_symlink():
        raise ValueError("model archive source cannot be a symlink")
    if output.is_symlink():
        raise ValueError("model archive output cannot be a symlink")
    source = source.resolve(strict=True)
    output = output.resolve(strict=False)
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("model archive output must be outside the source bundle")
    bundle = load_bundle_config(bundle_config)
    legacy_manifest = verify_model_manifest(source)
    strict_manifest = verify_manifest_against_config(source, bundle)
    if legacy_manifest != strict_manifest:
        raise ValueError("model manifest verification implementations disagree")

    files: list[Path] = []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"model package cannot contain symlinks: {path}")
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            if (
                relative not in {"model-manifest.json", NOTICE_BUNDLE_NAME}
                and not relative.startswith("clap/")
            ):
                raise ValueError(f"invalid model archive root entry: {relative}")
            files.append(path)
    files.sort(key=lambda path: path.relative_to(source).as_posix())
    if not files:
        raise ValueError("model archive has no files")

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in files:
                relative = path.relative_to(source).as_posix()
                info = zipfile.ZipInfo(relative, date_time=ZIP_EPOCH)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                info._compresslevel = 9
                with path.open("rb") as source_handle, archive.open(
                    info, "w", force_zip64=True
                ) as archive_handle:
                    shutil.copyfileobj(source_handle, archive_handle, length=1024 * 1024)
        with zipfile.ZipFile(temporary) as archive:
            corrupt = archive.testzip()
            if corrupt:
                raise ValueError(f"model archive CRC failed: {corrupt}")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--bundle-config",
        type=Path,
        default=DEFAULT_BUNDLE_CONFIG,
        help="Single pinned model identity/notice config",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    create_model_archive(args.source, args.output, args.bundle_config)
    print(f"[OK] deterministic model archive: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
