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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from download_manager import verify_model_manifest


ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def create_model_archive(source: Path, output: Path) -> None:
    source = source.resolve()
    output = output.resolve()
    verify_model_manifest(source)

    files: list[Path] = []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"model package cannot contain symlinks: {path}")
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            if relative != "model-manifest.json" and not relative.startswith("clap/"):
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    create_model_archive(args.source, args.output)
    print(f"[OK] deterministic model archive: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
