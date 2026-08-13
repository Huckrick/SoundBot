#!/usr/bin/env python3
"""Validate SoundBot's single release version and bilingual changelog entry."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)
CJK = re.compile(r"[\u3400-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")


class ReleaseMetadataError(RuntimeError):
    """Raised when independently stored release metadata has drifted."""


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseMetadataError(f"cannot read valid JSON from {path}: {exc}") from exc


def _backend_version(root: Path) -> str:
    config_text = (root / "backend" / "config.py").read_text(encoding="utf-8")
    matches = re.findall(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', config_text, re.MULTILINE)
    if len(matches) != 1:
        raise ReleaseMetadataError("backend/config.py must define APP_VERSION exactly once")
    return matches[0]


def extract_changelog_section(changelog: str, version: str) -> str:
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(?P<body>.*?)(?=^## \[|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(changelog)
    if not match:
        raise ReleaseMetadataError(f"CHANGELOG.md has no [{version}] release section")
    heading = match.group(0).splitlines()[0]
    body = re.split(r"^\[[^\]]+\]:\s+", match.group("body"), maxsplit=1, flags=re.MULTILINE)[0]
    return f"{heading}\n{body.rstrip()}\n"


def _validate_bilingual_section(section: str, version: str) -> None:
    headings = [line for line in section.splitlines()[1:] if line.startswith("### ")]
    bullets = [line[2:].strip() for line in section.splitlines() if line.startswith("- ")]
    if not headings:
        raise ReleaseMetadataError(f"CHANGELOG [{version}] must contain category headings")
    if not bullets:
        raise ReleaseMetadataError(f"CHANGELOG [{version}] must contain release entries")

    invalid_headings = [line for line in headings if " / " not in line]
    if invalid_headings:
        raise ReleaseMetadataError(
            "changelog category headings must be bilingual: " + ", ".join(invalid_headings)
        )

    invalid_bullets = [
        bullet
        for bullet in bullets
        if " / " not in bullet or not CJK.search(bullet) or not LATIN.search(bullet)
    ]
    if invalid_bullets:
        raise ReleaseMetadataError(
            "every changelog bullet must contain synchronized English and Chinese text: "
            + " | ".join(invalid_bullets[:3])
        )

    placeholders = [
        bullet
        for bullet in bullets
        if re.search(r"\b(?:TODO|TBD|PLACEHOLDER)\b|待补充|发布前请补充", bullet, re.IGNORECASE)
    ]
    if placeholders:
        raise ReleaseMetadataError(
            "changelog release entries still contain placeholders: "
            + " | ".join(placeholders[:3])
        )




def _readme_declared_version(readme: str, *, english: bool) -> tuple[str, bool]:
    if english:
        pattern = re.compile(
            r"^The current source version is \*\*v(?P<version>[^\s*]+) "
            r"\((?P<channel>stable|prerelease)\)\*\*\.",
            re.MULTILINE,
        )
    else:
        pattern = re.compile(
            r"^当前源码版本为 \*\*v(?P<version>[^*（]+)"
            r"（(?P<channel>稳定版|预发布)）\*\*。",
            re.MULTILINE,
        )
    matches = list(pattern.finditer(readme))
    if len(matches) != 1:
        raise ReleaseMetadataError(
            "README current-version declaration must occur exactly once"
        )
    match = matches[0]
    prerelease = match.group("channel") in {"prerelease", "预发布"}
    return match.group("version"), prerelease


def _release_template_declared_version(template: str) -> tuple[str, bool]:
    version_matches = re.findall(
        r"^\*\*Version / 版本:\*\* ([^<\s]+)<br>$", template, re.MULTILINE
    )
    status_matches = re.findall(
        r"^\*\*Status / 状态:\*\* (Stable / 稳定版|Prerelease / 预发布)<br>$",
        template,
        re.MULTILINE,
    )
    if len(version_matches) != 1 or len(status_matches) != 1:
        raise ReleaseMetadataError(
            "RELEASE_TEMPLATE version and release channel must occur exactly once"
        )
    return version_matches[0], status_matches[0].startswith("Prerelease")

def validate_release_metadata(
    root: Path = PROJECT_ROOT,
    *,
    expected_version: Optional[str] = None,
    tag: Optional[str] = None,
) -> tuple[str, str]:
    package = _read_json(root / "package.json")
    lock = _read_json(root / "package-lock.json")
    version = str(package.get("version", ""))
    if not SEMVER.fullmatch(version):
        raise ReleaseMetadataError(f"package.json has invalid semantic version: {version!r}")

    versions = {
        "package.json": version,
        "package-lock.json": str(lock.get("version", "")),
        "package-lock root package": str(lock.get("packages", {}).get("", {}).get("version", "")),
        "backend/config.py": _backend_version(root),
    }
    drift = {name: value for name, value in versions.items() if value != version}
    if drift:
        raise ReleaseMetadataError(f"release version drift: expected {version}, found {drift}")

    if expected_version and expected_version.removeprefix("v") != version:
        raise ReleaseMetadataError(
            f"expected version {expected_version.removeprefix('v')}, package version is {version}"
        )
    if tag and tag != f"v{version}":
        raise ReleaseMetadataError(
            f"release tag must be exactly v{version}; received {tag!r}"
        )

    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    section = extract_changelog_section(changelog, version)
    _validate_bilingual_section(section, version)

    # v0.2.0 is intentionally the stabilization prerelease even though the
    # historical version number has no SemVer suffix. Future channels follow
    # SemVer suffixes and the version-bump tool writes the matching declaration.
    expected_prerelease = "-" in version or version == "0.2.0"
    for readme_name in ("README.md", "README.en.md"):
        readme = (root / readme_name).read_text(encoding="utf-8")
        declared, prerelease = _readme_declared_version(
            readme, english=readme_name == "README.en.md"
        )
        if declared != version:
            raise ReleaseMetadataError(
                f"{readme_name} current version is {declared}, expected {version}"
            )
        if prerelease != expected_prerelease:
            expected = "prerelease" if expected_prerelease else "stable"
            raise ReleaseMetadataError(
                f"{readme_name} release channel must be {expected} for {version}"
            )

    release_template = (root / ".github" / "RELEASE_TEMPLATE.md").read_text(
        encoding="utf-8"
    )
    template_version, template_prerelease = _release_template_declared_version(
        release_template
    )
    if template_version != version or template_prerelease != expected_prerelease:
        actual_channel = "prerelease" if template_prerelease else "stable"
        expected_channel = "prerelease" if expected_prerelease else "stable"
        raise ReleaseMetadataError(
            "RELEASE_TEMPLATE drift: "
            f"version={template_version}, channel={actual_channel}; "
            f"expected {version} {expected_channel}"
        )
    return version, section


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--expected-version")
    parser.add_argument("--tag", help="Release tag; must be exactly v<package.version>")
    parser.add_argument("--write-release-notes", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    version, section = validate_release_metadata(
        args.root.resolve(), expected_version=args.expected_version, tag=args.tag
    )
    if args.write_release_notes:
        args.write_release_notes.write_text(section, encoding="utf-8")
        print(f"[OK] wrote {args.write_release_notes}")
    print(f"[OK] release metadata is synchronized for v{version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
