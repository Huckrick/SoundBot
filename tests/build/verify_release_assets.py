#!/usr/bin/env python3
"""Verify local release assets and the draft GitHub Release asset inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED_EXACT = {"models.zip", "models.zip.sha256", "SHA256SUMS.txt"}
REQUIRED_SUFFIX_COUNTS = {".dmg": 1, ".exe": 1}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def local_inventory(directory: Path) -> dict[str, dict[str, object]]:
    files = [path for path in directory.iterdir() if path.is_file()]
    names = {path.name for path in files}
    missing = REQUIRED_EXACT - names
    if missing:
        raise ValueError(f"missing required release assets: {sorted(missing)}")
    for suffix, expected in REQUIRED_SUFFIX_COUNTS.items():
        actual = sum(path.suffix.lower() == suffix for path in files)
        if actual != expected:
            raise ValueError(f"expected {expected} {suffix} asset, found {actual}")
    if len(files) != 5:
        raise ValueError(f"expected exactly 5 release assets, found {len(files)}: {sorted(names)}")
    empty = [path.name for path in files if path.stat().st_size <= 0]
    if empty:
        raise ValueError(f"empty release assets: {empty}")
    return {
        path.name: {
            "size": path.stat().st_size,
            "digest": f"sha256:{_sha256(path)}",
            "state": "uploaded",
        }
        for path in files
    }


def remote_inventory(path: Path) -> dict[str, dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError("remote JSON has no assets array")
    result: dict[str, dict[str, object]] = {}
    for item in assets:
        name = item.get("name")
        size = item.get("size")
        digest = item.get("digest")
        state = item.get("state")
        if (
            not isinstance(name, str)
            or not isinstance(size, int)
            or not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or state != "uploaded"
        ):
            raise ValueError(f"malformed remote asset: {item}")
        if name in result:
            raise ValueError(f"duplicate remote asset name: {name}")
        result[name] = {"size": size, "digest": digest.lower(), "state": state}
    return result


def verify_release_assets(directory: Path, remote_json: Path) -> None:
    local = local_inventory(directory)
    remote = remote_inventory(remote_json)
    if remote != local:
        missing = sorted(set(local) - set(remote))
        extra = sorted(set(remote) - set(local))
        metadata_mismatch = {
            name: {"local": local[name], "remote": remote[name]}
            for name in local.keys() & remote.keys()
            if local[name] != remote[name]
        }
        raise ValueError(
            f"release asset inventory mismatch: missing={missing}, extra={extra}, "
            f"metadata_mismatch={metadata_mismatch}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--remote-json", required=True, type=Path)
    args = parser.parse_args()
    verify_release_assets(args.directory.resolve(), args.remote_json.resolve())
    print("[OK] draft GitHub Release names, sizes, SHA-256 digests, and states match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
