#!/usr/bin/env python3
"""Verify SoundBot's local model path, pinned identity, hashes, and notice."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = PROJECT_ROOT / "backend"
BUILD_HELPER = PROJECT_ROOT / "tests" / "build" / "create_model_manifest.py"
DEFAULT_BUNDLE_CONFIG = PROJECT_ROOT / "config" / "model_bundle.json"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _load_build_helper() -> Any:
    spec = importlib.util.spec_from_file_location(
        "soundbot_create_model_manifest", BUILD_HELPER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load model verifier: {BUILD_HELPER}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules while executing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def resolve_models_dir(explicit: Path | None = None) -> Path:
    """Resolve one local path; an explicit environment override is authoritative."""
    if explicit is not None:
        return explicit.expanduser().resolve(strict=False)
    configured = os.environ.get("SOUNDBOT_MODELS_PATH")
    if configured:
        return Path(configured).expanduser().resolve(strict=False)

    import config

    return config.find_models_dir_runtime().expanduser().resolve(strict=False)


def verify_local_bundle(
    models_dir: Path,
    bundle_config: Path = DEFAULT_BUNDLE_CONFIG,
) -> dict[str, Any]:
    helper = _load_build_helper()
    bundle = helper.load_bundle_config(bundle_config)
    return helper.verify_manifest_against_config(models_dir, bundle)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models-dir",
        type=Path,
        help="Local models root; defaults to SOUNDBOT_MODELS_PATH/app resolution",
    )
    parser.add_argument(
        "--bundle-config",
        type=Path,
        default=DEFAULT_BUNDLE_CONFIG,
        help="Single pinned model identity/revision/notice config",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    models_dir = resolve_models_dir(args.models_dir)
    print("SoundBot local model bundle verification")
    print(f"Path: {models_dir}")
    print("Mode: local-only (this command never downloads or uses a remote fallback)")
    try:
        manifest = verify_local_bundle(models_dir, args.bundle_config)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] Local model bundle is unavailable or invalid: {exc}", file=sys.stderr)
        return 1

    print(
        f"[OK] {manifest['model_id']} @ {manifest['revision']} "
        f"({len(manifest['files'])} files; notice verified)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
