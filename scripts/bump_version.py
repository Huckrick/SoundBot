#!/usr/bin/env python3
"""Plan or atomically apply a synchronized SoundBot version bump."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import NamedTuple, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)
APP_VERSION = re.compile(r'^(APP_VERSION\s*=\s*["\'])([^"\']+)(["\']\s*)$', re.MULTILINE)
ZH_VERSION_LINE = re.compile(
    r"^(当前源码版本为\s+\*\*v)"
    r"(?P<version>[^\s*（）()]+)"
    r"(?:（[^）\n]+）|\s+\([^\n)]+\))"
    r"(?P<suffix>\*\*.*)$",
    re.MULTILINE,
)
EN_VERSION_LINE = re.compile(
    r"^(The current source version is\s+\*\*v)"
    r"(?P<version>[^\s*()]+)"
    r"\s+\([^\n)]+\)"
    r"(?P<suffix>\*\*.*)$",
    re.MULTILINE,
)
RELEASE_TEMPLATE_STATUS = re.compile(
    r"^\*\*Status / 状态:\*\* [^\n]+<br>$", re.MULTILINE
)
RELEASE_TEMPLATE_VERSION = re.compile(
    r"^\*\*Version / 版本:\*\* (?P<version>[^<\s]+)<br>$", re.MULTILINE
)


class VersionBumpError(RuntimeError):
    """Raised when a version bump cannot be planned safely."""


class PlannedFile(NamedTuple):
    path: Path
    original: str
    updated: str


class VersionBumpPlan(NamedTuple):
    old_version: str
    new_version: str
    files: tuple[PlannedFile, ...]


def validate_version(version: str) -> str:
    """Return a validated SemVer without accepting a release-tag ``v`` prefix."""
    if not SEMVER.fullmatch(version):
        raise VersionBumpError(
            "version must be SemVer X.Y.Z or X.Y.Z-prerelease, without a v prefix "
            "or build metadata"
        )
    return version


def compare_versions(left: str, right: str) -> int:
    """Compare two validated SemVer values, excluding build metadata."""
    left_match = SEMVER.fullmatch(validate_version(left))
    right_match = SEMVER.fullmatch(validate_version(right))
    assert left_match is not None and right_match is not None
    left_core = tuple(int(left_match.group(index)) for index in range(1, 4))
    right_core = tuple(int(right_match.group(index)) for index in range(1, 4))
    if left_core != right_core:
        return 1 if left_core > right_core else -1

    def prerelease(value: str) -> list[str] | None:
        suffix = value.split("-", 1)
        return suffix[1].split(".") if len(suffix) == 2 else None

    left_pre = prerelease(left)
    right_pre = prerelease(right)
    if left_pre is None or right_pre is None:
        if left_pre is right_pre:
            return 0
        return 1 if left_pre is None else -1
    for left_id, right_id in zip(left_pre, right_pre):
        if left_id == right_id:
            continue
        left_numeric = left_id.isdigit()
        right_numeric = right_id.isdigit()
        if left_numeric and right_numeric:
            return 1 if int(left_id) > int(right_id) else -1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return 1 if left_id > right_id else -1
    if len(left_pre) == len(right_pre):
        return 0
    return 1 if len(left_pre) > len(right_pre) else -1


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise VersionBumpError(f"required version file does not exist: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise VersionBumpError(f"cannot read UTF-8 text from {path}: {exc}") from exc


def _read_json(path: Path, text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VersionBumpError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VersionBumpError(f"expected a JSON object in {path}")
    return value


def _json_text(value: dict, *, trailing_newline: bool) -> str:
    rendered = json.dumps(value, ensure_ascii=False, indent=2)
    return rendered + ("\n" if trailing_newline else "")


def _single_match(pattern: re.Pattern[str], text: str, description: str) -> re.Match[str]:
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise VersionBumpError(f"{description} must occur exactly once (found {len(matches)})")
    return matches[0]


def _replace_backend_version(text: str, old_version: str, new_version: str) -> str:
    match = _single_match(APP_VERSION, text, "backend/config.py APP_VERSION")
    if match.group(2) != old_version:
        raise VersionBumpError(
            f"backend/config.py version drift: expected {old_version}, found {match.group(2)}"
        )
    return text[: match.start()] + f"{match.group(1)}{new_version}{match.group(3)}" + text[match.end() :]


def _replace_readme_version(
    text: str,
    *,
    old_version: str,
    new_version: str,
    english: bool,
) -> str:
    pattern = EN_VERSION_LINE if english else ZH_VERSION_LINE
    name = "README.en.md current source version line" if english else "README.md 当前源码版本行"
    match = _single_match(pattern, text, name)
    if match.group("version") != old_version:
        raise VersionBumpError(
            f"{name} drift: expected {old_version}, found {match.group('version')}"
        )
    prerelease = "-" in new_version
    status = " (prerelease)" if english and prerelease else " (stable)" if english else "（预发布）" if prerelease else "（稳定版）"
    replacement = f"{match.group(1)}{new_version}{status}{match.group('suffix')}"
    return text[: match.start()] + replacement + text[match.end() :]


def _release_skeleton(version: str, release_date: date) -> str:
    qualifier = " (Prerelease / 预发布)" if "-" in version else ""
    return (
        f"## [{version}] - {release_date.isoformat()}{qualifier}\n\n"
        "### Added / 新增\n\n"
        "- TODO: describe the release changes before publishing. / TODO：发布前请补充本版本变更。\n"
    )


def _replace_release_template_version(
    text: str, *, old_version: str, new_version: str
) -> str:
    version_match = _single_match(
        RELEASE_TEMPLATE_VERSION, text, "RELEASE_TEMPLATE version line"
    )
    if version_match.group("version") != old_version:
        raise VersionBumpError(
            "RELEASE_TEMPLATE version drift: "
            f"expected {old_version}, found {version_match.group('version')}"
        )
    _single_match(RELEASE_TEMPLATE_STATUS, text, "RELEASE_TEMPLATE status line")
    updated = text.replace(f"v{old_version}", f"v{new_version}").replace(
        old_version, new_version
    )
    channel = "Prerelease / 预发布" if "-" in new_version else "Stable / 稳定版"
    return RELEASE_TEMPLATE_STATUS.sub(
        f"**Status / 状态:** {channel}<br>", updated, count=1
    )


def _update_changelog(
    text: str,
    *,
    old_version: str,
    new_version: str,
    release_date: date,
) -> str:
    target_section = re.compile(rf"^## \[{re.escape(new_version)}\](?:\s|$)", re.MULTILINE)
    if target_section.search(text):
        raise VersionBumpError(
            f"CHANGELOG.md already contains a [{new_version}] release section; refusing to overwrite it"
        )

    target_reference = re.compile(rf"^\[{re.escape(new_version)}\]:\s+", re.MULTILINE)
    if target_reference.search(text):
        raise VersionBumpError(
            f"CHANGELOG.md already contains a [{new_version}] comparison reference"
        )

    unreleased = _single_match(
        re.compile(r"^## \[Unreleased\][^\n]*$", re.MULTILINE),
        text,
        "CHANGELOG.md [Unreleased] heading",
    )
    next_release = re.search(r"^## \[[^\]]+\][^\n]*$", text[unreleased.end() :], re.MULTILINE)
    if next_release:
        insert_at = unreleased.end() + next_release.start()
    else:
        references = re.search(r"^\[[^\]]+\]:\s+", text[unreleased.end() :], re.MULTILINE)
        insert_at = unreleased.end() + references.start() if references else len(text)

    before = text[:insert_at].rstrip()
    after = text[insert_at:].lstrip("\n")
    updated = f"{before}\n\n{_release_skeleton(new_version, release_date).rstrip()}\n\n{after}"

    comparison = re.compile(
        r"^\[Unreleased\]:\s+"
        r"(?P<base>https://github\.com/[^/\s]+/[^/\s]+)"
        r"/compare/v(?P<version>[0-9A-Za-z.-]+?)\.\.\.HEAD$",
        re.MULTILINE,
    )
    comparisons = list(comparison.finditer(updated))
    if len(comparisons) > 1:
        raise VersionBumpError("CHANGELOG.md [Unreleased] comparison reference occurs more than once")
    if comparisons:
        match = comparisons[0]
        if match.group("version") != old_version:
            raise VersionBumpError(
                "CHANGELOG.md [Unreleased] comparison reference drift: "
                f"expected v{old_version}, found v{match.group('version')}"
            )
        replacement = (
            f"[Unreleased]: {match.group('base')}/compare/v{new_version}...HEAD\n"
            f"[{new_version}]: {match.group('base')}/compare/v{old_version}...v{new_version}"
        )
        updated = updated[: match.start()] + replacement + updated[match.end() :]

    return updated


def build_version_bump_plan(
    root: Path,
    version: str,
    *,
    release_date: date | None = None,
) -> VersionBumpPlan:
    """Read and validate every versioned file before returning in-memory updates."""
    root = root.resolve()
    new_version = validate_version(version)
    release_date = release_date or date.today()
    paths = {
        "package": root / "package.json",
        "lock": root / "package-lock.json",
        "backend": root / "backend" / "config.py",
        "readme_zh": root / "README.md",
        "readme_en": root / "README.en.md",
        "release_template": root / ".github" / "RELEASE_TEMPLATE.md",
        "changelog": root / "CHANGELOG.md",
    }
    originals = {name: _read_text(path) for name, path in paths.items()}

    package = _read_json(paths["package"], originals["package"])
    lock = _read_json(paths["lock"], originals["lock"])
    old_version = str(package.get("version", ""))
    validate_version(old_version)
    if compare_versions(new_version, old_version) <= 0:
        raise VersionBumpError(
            f"new version must be greater than {old_version}; received {new_version}"
        )

    lock_packages = lock.get("packages")
    if not isinstance(lock_packages, dict) or not isinstance(lock_packages.get(""), dict):
        raise VersionBumpError("package-lock.json must contain a packages[''] root package object")
    lock_versions = (str(lock.get("version", "")), str(lock_packages[""].get("version", "")))
    if lock_versions != (old_version, old_version):
        raise VersionBumpError(
            "package-lock.json version drift: expected both root versions to be "
            f"{old_version}, found {lock_versions}"
        )

    package["version"] = new_version
    lock["version"] = new_version
    lock_packages[""]["version"] = new_version
    updates = {
        "package": _json_text(package, trailing_newline=originals["package"].endswith("\n")),
        "lock": _json_text(lock, trailing_newline=originals["lock"].endswith("\n")),
        "backend": _replace_backend_version(originals["backend"], old_version, new_version),
        "readme_zh": _replace_readme_version(
            originals["readme_zh"],
            old_version=old_version,
            new_version=new_version,
            english=False,
        ),
        "readme_en": _replace_readme_version(
            originals["readme_en"],
            old_version=old_version,
            new_version=new_version,
            english=True,
        ),
        "release_template": _replace_release_template_version(
            originals["release_template"],
            old_version=old_version,
            new_version=new_version,
        ),
        "changelog": _update_changelog(
            originals["changelog"],
            old_version=old_version,
            new_version=new_version,
            release_date=release_date,
        ),
    }
    files = tuple(
        PlannedFile(paths[name], originals[name], updates[name])
        for name in (
            "package", "lock", "backend", "readme_zh", "readme_en",
            "release_template", "changelog",
        )
    )
    return VersionBumpPlan(old_version, new_version, files)


def _stage_text(path: Path, text: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def apply_version_bump(plan: VersionBumpPlan) -> None:
    """Stage every update, then replace each destination with rollback on failure."""
    for item in plan.files:
        if _read_text(item.path) != item.original:
            raise VersionBumpError(f"version file changed after planning: {item.path}")

    staged_new: dict[Path, Path] = {}
    staged_original: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for item in plan.files:
            staged_new[item.path] = _stage_text(item.path, item.updated)
            staged_original[item.path] = _stage_text(item.path, item.original)

        for item in plan.files:
            os.replace(staged_new.pop(item.path), item.path)
            replaced.append(item.path)
    except BaseException as exc:
        rollback_errors: list[str] = []
        for path in reversed(replaced):
            try:
                os.replace(staged_original.pop(path), path)
            except OSError as rollback_exc:
                rollback_errors.append(f"{path}: {rollback_exc}")
        detail = f"; rollback errors: {' | '.join(rollback_errors)}" if rollback_errors else ""
        raise VersionBumpError(f"atomic version update failed: {exc}{detail}") from exc
    finally:
        for temporary in (*staged_new.values(), *staged_original.values()):
            temporary.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="SemVer without the release-tag v prefix")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    parser.add_argument(
        "--write",
        action="store_true",
        help="apply the update; without this flag the command is a dry run",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_version_bump_plan(args.root, args.version)
        if args.write:
            apply_version_bump(plan)
            action = "updated"
        else:
            action = "would update"
        print(f"[OK] {action} SoundBot {plan.old_version} -> {plan.new_version}")
        for item in plan.files:
            print(f"  - {item.path.relative_to(args.root.resolve())}")
        if not args.write:
            print("[DRY RUN] no files were changed; pass --write to apply")
        return 0
    except (OSError, VersionBumpError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
