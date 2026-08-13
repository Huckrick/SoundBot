#!/usr/bin/env python3
"""Fail closed when private runtime data or likely credentials enter Git.

The release repository intentionally audits the current source tree, not old
commits. Rotated historical credentials are handled through revocation; making
the release gate inspect history would permanently block every descendant tag.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable
from xml.etree import ElementTree


FORBIDDEN_NAMES = {
    ".DS_Store",
    "indexed_files_meta.json",
}
FORBIDDEN_PATHS = {
    "config/user_config.json",
}
FORBIDDEN_PREFIXES = (
    "backend/db/",
    "chroma/",
    "chroma_projects/",
    "downloads/",
    "models/",
    "temp_clips/",
    "test_audio/",
    "waveform_cache/",
)
FORBIDDEN_SUFFIXES = (
    ".db-wal",
    ".db-shm",
    ".sqlite-wal",
    ".sqlite-shm",
    ".reapeaks",
)
SENSITIVE_JSON_KEYS = {
    "api_key",
    "access_token",
    "client_secret",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}

TEXT_PATTERNS = {
    "private-key material": re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    ),
    "credential-bearing URL": re.compile(
        rb"https?://[^/\s:@]+:[^/\s@]+@[^\s]+", re.IGNORECASE
    ),
    "GitHub access token": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "GitHub fine-grained access token": re.compile(
        rb"\bgithub_pat_[A-Za-z0-9_]{20,}\b"
    ),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "Google API key": re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Slack access token": re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "npm access token": re.compile(rb"\bnpm_[A-Za-z0-9]{30,}\b"),
    "OpenAI-style secret": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "Stripe live secret": re.compile(rb"\bsk_live_[A-Za-z0-9]{20,}\b"),
    "Hugging Face token": re.compile(rb"\bhf_[A-Za-z0-9]{20,}\b"),
    "credential-bearing query URL": re.compile(
        rb"https?://[^\s\"'<>?]+\?[^\s\"'<>]*"
        rb"(?:access_token|api_key|key|password|secret|token)="
        rb"[^&\s\"'<>]+",
        re.IGNORECASE,
    ),
    "personal macOS path": re.compile(rb"/(?:Users|Volumes)/[^\s\"']+"),
    "personal Windows path": re.compile(
        rb"\b[A-Za-z]:\\Users\\[^\s\"']+", re.IGNORECASE
    ),
}

OFFICE_DOCUMENT_SUFFIXES = {".docx", ".pptx", ".xlsx"}
PRIVATE_OFFICE_PROPERTIES = {"creator", "lastModifiedBy", "lastPrinted"}


def git_lines(root: Path, *args: str) -> list[str]:
    output = subprocess.check_output(
        ["git", "-c", "core.quotepath=false", *args, "-z"], cwd=root
    )
    return [
        path.decode("utf-8", errors="surrogateescape")
        for path in output.split(b"\0")
        if path
    ]


def audit_tracked_paths(paths: Iterable[str]) -> list[str]:
    findings: list[str] = []
    for raw_path in paths:
        path = PurePosixPath(raw_path).as_posix()
        if (
            PurePosixPath(path).name in FORBIDDEN_NAMES
            or path in FORBIDDEN_PATHS
            or path.startswith(FORBIDDEN_PREFIXES)
            or path.lower().endswith(FORBIDDEN_SUFFIXES)
            or PurePosixPath(path).name == ".env"
            or (
                PurePosixPath(path).name.startswith(".env.")
                and PurePosixPath(path).name != ".env.example"
            )
        ):
            findings.append(path)
    return findings


def find_nonempty_secret_fields(value: object, prefix: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in SENSITIVE_JSON_KEYS and child not in (None, "", [], {}):
                findings.append(child_prefix)
            findings.extend(find_nonempty_secret_fields(child, child_prefix))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(find_nonempty_secret_fields(child, f"{prefix}[{index}]"))
    return findings


def scan_payload(payload: bytes) -> list[str]:
    return [label for label, pattern in TEXT_PATTERNS.items() if pattern.search(payload)]


def audit_office_metadata(path: Path) -> list[str]:
    """Return private OOXML property names without exposing their values."""

    if path.suffix.lower() not in OFFICE_DOCUMENT_SUFFIXES:
        return []
    try:
        with zipfile.ZipFile(path) as archive:
            payload = archive.read("docProps/core.xml")
    except (KeyError, OSError, zipfile.BadZipFile):
        return []
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError:
        return ["invalid core metadata"]

    findings: list[str] = []
    for element in root.iter():
        property_name = element.tag.rsplit("}", 1)[-1]
        if property_name in PRIVATE_OFFICE_PROPERTIES and (element.text or "").strip():
            findings.append(property_name)
    return sorted(set(findings))


def audit_repository(root: Path) -> list[str]:
    findings: list[str] = []
    tracked = git_lines(root, "ls-files")
    tracked_ignored = git_lines(root, "ls-files", "-ci", "--exclude-standard")
    if tracked_ignored:
        findings.extend(f"tracked but ignored: {path}" for path in tracked_ignored)

    findings.extend(f"private runtime path: {path}" for path in audit_tracked_paths(tracked))

    for relative in tracked:
        path = root / relative
        if path.is_symlink():
            findings.append(f"tracked symlink: {relative}")
            continue
        if not path.is_file():
            continue
        payload = path.read_bytes()
        for label in scan_payload(payload):
            findings.append(f"{label}: {relative}")
        findings.extend(
            f"private Office metadata: {relative}:{property_name}"
            for property_name in audit_office_metadata(path)
        )
        if path.suffix.lower() == ".json" and b"\0" not in payload:
            try:
                value = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            findings.extend(
                f"non-empty sensitive JSON field: {relative}:{field}"
                for field in find_nonempty_secret_fields(value)
            )
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Git repository root (default: project root)",
    )
    args = parser.parse_args()
    findings = audit_repository(args.root.resolve())
    if findings:
        print("Repository hygiene verification failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("Repository hygiene verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
