"""Read installed plug-in metadata without executing plug-in code."""

from __future__ import annotations

import json
import plistlib
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import json5


_MAX_METADATA = 256 * 1024
_FREQ_ECHO = "com.valhalladsp.valhallafreqecho"
_TYPES = {
    "analyzer": "analyzer", "delay": "delay", "distortion": "distortion", "dynamics": "dynamics",
    "eq": "eq", "filter": "filter", "modulation": "modulation", "pitch shift": "pitch",
    "restoration": "restoration", "reverb": "reverb", "spatial": "spatial", "surround": "spatial",
    "up-downmix": "utility", "tools": "utility", "mastering": "mastering",
}


@dataclass(frozen=True)
class Plugin:
    path: Path
    name: str
    vendor: str = ""
    kind: str = "unknown"
    types: tuple[str, ...] = ()
    class_name: str | None = None


def _text(value):
    if not isinstance(value, str):
        return ""
    return value.encode("utf-8", errors="replace").decode().strip()[:200]


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _entries(value):
    return (row for row in value if isinstance(row, dict)) if isinstance(value, list) else ()


def _read(path):
    try:
        with path.open("rb") as stream:
            data = stream.read(_MAX_METADATA + 1)
    except OSError:
        return b""
    return data if len(data) <= _MAX_METADATA else b""


def _plist(path):
    try:
        return _mapping(plistlib.loads(_read(path / "Contents/Info.plist")))
    except (ValueError, TypeError, OverflowError, RecursionError, plistlib.InvalidFileException):
        return {}


def _bundle_id(info):
    identifier = _text(info.get("CFBundleIdentifier")).casefold()
    return re.sub(r"\.(?:vst3|audiounit|au)$", "", identifier)


def _manifest(path):
    for filename in ("Contents/Resources/moduleinfo.json", "Contents/moduleinfo.json"):
        try:
            data = _read(path / filename).decode("utf-8-sig")
            if not data:
                continue
            try:
                return _mapping(json.loads(data))
            except ValueError:
                return _mapping(json5.loads(data))
        except (ValueError, RecursionError):
            continue
    return {}


def categories(value):
    parts = value if isinstance(value, list) else [value]
    tags = {_text(tag).casefold() for part in parts if isinstance(part, str) for tag in part.split("|")}
    types = tuple(dict.fromkeys(label for tag, label in _TYPES.items() if tag in tags))
    if "synth" in tags:
        return "synth", ()
    if "sampler" in tags:
        return "sampler", ()
    if "instrument" in tags:
        return "instrument", ()
    if "generator" in tags and "fx" not in tags:
        return "generator", ()
    if "fx" in tags or types:
        return "effect", types
    return "unknown", ()


def _windows_vendor(binary):
    import ctypes as ct
    from ctypes import wintypes as wt

    version = ct.WinDLL("version.dll", use_last_error=True, winmode=0x800)
    pointer = ct.POINTER(wt.UINT)
    version.GetFileVersionInfoSizeW.argtypes = [wt.LPCWSTR, ct.POINTER(wt.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wt.DWORD
    version.GetFileVersionInfoW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID]
    version.VerQueryValueW.argtypes = [wt.LPCVOID, wt.LPCWSTR, ct.POINTER(ct.c_void_p), pointer]

    size = version.GetFileVersionInfoSizeW(str(binary), None)
    if not 0 < size <= _MAX_METADATA:
        return ""
    block = ct.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(binary), 0, size, block):
        return ""
    data = ct.c_void_p()
    count = wt.UINT()
    translations = []
    if version.VerQueryValueW(block, r"\VarFileInfo\Translation", ct.byref(data), ct.byref(count)):
        tags = ct.cast(data, ct.POINTER(ct.c_ushort))
        translations.extend(f"{tags[i]:04x}{tags[i + 1]:04x}" for i in range(0, min(count.value // 4, 16) * 2, 2))
    for language in dict.fromkeys((*translations, "040904b0", "040904e4")):
        key = f"\\StringFileInfo\\{language}\\CompanyName"
        if version.VerQueryValueW(block, key, ct.byref(data), ct.byref(count)) and count.value:
            return _text(ct.wstring_at(data, min(count.value, 200)).rstrip("\0"))
    return ""


def _fallback(path, binary, units):
    info = _plist(path) if sys.platform == "darwin" else {}
    bundle = _bundle_id(info)
    component = units.get(bundle)
    vendor = _windows_vendor(binary) if sys.platform == "win32" else ""
    name = path.stem
    if bundle == _FREQ_ECHO:
        return Plugin(path, name, component.vendor if component else "Valhalla DSP, LLC", "effect", ("delay",))
    if component:
        kind = {"aufx": "effect", "aumf": "effect", "aumu": "instrument", "augn": "generator"}
        return Plugin(path, name, component.vendor, kind.get(component.kind, "unknown"), component.types)
    # Older releases of this sampler don't always ship a factory manifest.
    if re.fullmatch(r"Kontakt(?:[ _-]+\d+)?(?:[ _-]+Player)?", name, re.I) and (
        not vendor or vendor.casefold().startswith("native instruments")
    ):
        return Plugin(path, name, vendor or "Native Instruments", "sampler")
    return Plugin(path, name, vendor)


def vst3_entries(path, binary, units):
    manifest = _manifest(path)
    default = _fallback(path, binary, units)
    vendor = _text(_mapping(manifest.get("Factory Info")).get("Vendor")) or default.vendor
    entries = {}
    classes = [row for row in _entries(manifest.get("Classes")) if row.get("Category") == "Audio Module Class"]
    for row in classes:
        class_name = _text(row.get("Name"))
        if not class_name:
            continue
        kind, types = categories(row.get("Sub Categories"))
        if kind == "unknown" and len(classes) == 1:
            kind, types = default.kind, default.types
        entries[class_name] = Plugin(path, class_name, _text(row.get("Vendor")) or vendor,
                                     kind, types, class_name)
    return tuple(entries.values()) or (replace(default, vendor=vendor),)


def _audio_units():
    if sys.platform != "darwin":
        return
    for root in (Path.home() / "Library/Audio/Plug-Ins/Components", Path("/Library/Audio/Plug-Ins/Components")):
        for path in sorted(root.glob("*.component")):
            try:
                yield path.resolve(strict=True)
            except (OSError, RuntimeError):
                continue


def audio_unit_metadata():
    """Classify older VST3 bundles using declarations from matching Audio Units."""
    units = {}
    for path in _audio_units():
        info = _plist(path)
        bundle = _bundle_id(info)
        rows = tuple(_entries(info.get("AudioComponents")))
        if bundle and len(rows) == 1:
            row = rows[0]
            label = _text(row.get("name"))
            vendor, separator, display = label.partition(":")
            units.setdefault(bundle, Plugin(path, display.strip() or path.stem, vendor.strip() if separator else "",
                                           _text(row.get("type")), categories(row.get("tags"))[1]))
    return units
