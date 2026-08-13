#!/usr/bin/env python3
"""Verify a frozen backend's native architecture, PyAV libraries, and notices."""

from __future__ import annotations

import argparse
import os
import struct
from importlib.metadata import distribution
from pathlib import Path


PE_AMD64 = 0x8664
MACHO_ARM64 = 0x0100000C
MACHO_64_LE = b"\xcf\xfa\xed\xfe"
MACHO_64_BE = b"\xfe\xed\xfa\xcf"


def executable_architecture(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    if data.startswith(b"MZ"):
        if len(data) < 0x40:
            raise ValueError(f"truncated PE executable: {path}")
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_offset : pe_offset + 4] != b"PE\0\0":
            raise ValueError(f"invalid PE signature: {path}")
        machine = struct.unpack_from("<H", data, pe_offset + 4)[0]
        return "windows", "x64" if machine == PE_AMD64 else f"pe-machine-0x{machine:04x}"

    magic = data[:4]
    if magic == MACHO_64_LE:
        cpu_type = struct.unpack_from("<I", data, 4)[0]
        return "macos", "arm64" if cpu_type == MACHO_ARM64 else f"macho-cpu-0x{cpu_type:08x}"
    if magic == MACHO_64_BE:
        cpu_type = struct.unpack_from(">I", data, 4)[0]
        return "macos", "arm64" if cpu_type == MACHO_ARM64 else f"macho-cpu-0x{cpu_type:08x}"
    raise ValueError(f"unsupported executable format: {path}")


def _all_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


def _expected_pyav_native_paths() -> set[str]:
    """Return every native path shipped by the pinned PyAV wheel."""
    expected: set[str] = set()
    for item in distribution("av").files or []:
        relative = Path(str(item)).as_posix().casefold()
        suffix = Path(relative).suffix.casefold()
        if suffix not in {".dll", ".dylib", ".so", ".pyd"}:
            continue
        if relative.startswith("av/") or relative.startswith("av.libs/"):
            expected.add(relative)
    if not expected:
        raise RuntimeError("installed PyAV distribution exposes no native wheel files")
    return expected


def verify_bundle(bundle: Path, platform_name: str, architecture: str) -> dict:
    if not bundle.is_dir():
        raise FileNotFoundError(f"frozen backend directory does not exist: {bundle}")
    executable = bundle / ("soundbot-backend.exe" if platform_name == "windows" else "soundbot-backend")
    if not executable.is_file():
        raise FileNotFoundError(f"frozen backend executable does not exist: {executable}")
    detected_platform, detected_arch = executable_architecture(executable)
    if (detected_platform, detected_arch) != (platform_name, architecture):
        raise RuntimeError(
            f"native backend mismatch: expected {platform_name}/{architecture}, "
            f"found {detected_platform}/{detected_arch}"
        )
    if platform_name != "windows" and not os.access(executable, os.X_OK):
        raise PermissionError(f"frozen backend is not executable: {executable}")

    runtime_dirs = [path for path in (bundle / "_internal", bundle / "lib") if path.is_dir()]
    if not runtime_dirs:
        raise FileNotFoundError("PyInstaller runtime directory (_internal or lib) is missing")

    files = _all_files(bundle)
    lower_names = [path.name.casefold() for path in files]
    path_strings = [path.relative_to(bundle).as_posix().casefold() for path in files]
    pyav_extensions = [
        path for path in files
        if "/av/" in f"/{path.relative_to(bundle).as_posix().casefold()}"
        and path.suffix.casefold() in {".pyd", ".so", ".dylib"}
    ]
    if not pyav_extensions:
        raise FileNotFoundError("PyAV native extension modules are missing from frozen backend")

    expected_native = _expected_pyav_native_paths()
    missing_native = sorted(
        relative
        for relative in expected_native
        if not any(path.endswith(relative) for path in path_strings)
    )
    if missing_native:
        raise FileNotFoundError(
            "frozen backend is missing files from the pinned PyAV wheel: "
            + ", ".join(missing_native[:12])
        )

    ffmpeg_components = (
        "avcodec", "avdevice", "avfilter", "avformat", "avutil", "swresample", "swscale"
    )
    missing_ffmpeg = [
        component for component in ffmpeg_components
        if not any(component in name for name in lower_names)
    ]
    if missing_ffmpeg:
        raise FileNotFoundError(
            "PyAV wheel-bundled FFmpeg libraries are incomplete: " + ", ".join(missing_ffmpeg)
        )

    if platform_name == "windows":
        if not any(path.endswith("/python3.dll") or path == "python3.dll" for path in path_strings):
            raise FileNotFoundError("PyAV abi3 runtime requires python3.dll in the Windows bundle")
        if not any("/av.libs/" in f"/{path}" for path in path_strings):
            raise FileNotFoundError("the Windows PyAV av.libs sibling directory was not preserved")
    elif not any("/av/.dylibs/" in f"/{path}" for path in path_strings):
        raise FileNotFoundError("the macOS PyAV av/.dylibs directory was not preserved")

    notice_found = any(path.endswith("licenses/third_party_audio_notices.txt") for path in path_strings)
    pyav_license_found = any(
        (
            ("av-" in path and ".dist-info/" in path)
            or path.startswith("licenses/pyav/")
            or "/licenses/pyav/" in path
        )
        and any(token in path for token in ("license", "copying", "notice"))
        for path in path_strings
    )
    if not notice_found:
        raise FileNotFoundError("licenses/THIRD_PARTY_AUDIO_NOTICES.txt is missing")
    if not pyav_license_found:
        raise FileNotFoundError("the PyAV wheel license was not collected from distribution metadata")

    return {
        "executable": str(executable),
        "platform": detected_platform,
        "architecture": detected_arch,
        "pyav_extensions": len(pyav_extensions),
        "pyav_native_files": len(expected_native),
        "ffmpeg_components": list(ffmpeg_components),
        "files": len(files),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--platform", required=True, choices=("windows", "macos"))
    parser.add_argument("--arch", required=True, choices=("x64", "arm64"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = verify_bundle(args.bundle.resolve(), args.platform, args.arch)
    print(
        f"[OK] {result['platform']}/{result['architecture']} frozen backend; "
        f"{result['pyav_extensions']} PyAV extensions; "
        f"FFmpeg components: {', '.join(result['ffmpeg_components'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
