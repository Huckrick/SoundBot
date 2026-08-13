from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "bump_version.py"
SPEC = importlib.util.spec_from_file_location("soundbot_bump_version", MODULE_PATH)
bump_version = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bump_version)


class VersionBumpTests(unittest.TestCase):
    def make_tree(self, root: Path) -> None:
        (root / "backend").mkdir()
        (root / ".github").mkdir()
        (root / "package.json").write_text(
            json.dumps({"name": "soundbot", "version": "0.2.0"}, indent=2) + "\n",
            encoding="utf-8",
        )
        (root / "package-lock.json").write_text(
            json.dumps(
                {
                    "name": "soundbot",
                    "version": "0.2.0",
                    "lockfileVersion": 3,
                    "packages": {"": {"name": "soundbot", "version": "0.2.0"}},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "backend" / "config.py").write_text(
            'APP_NAME = "SoundBot"\nAPP_VERSION = "0.2.0"\n', encoding="utf-8"
        )
        (root / "README.md").write_text(
            "# SoundBot\n\n"
            "当前源码版本为 **v0.2.0（预发布）**。这里是版本说明。\n",
            encoding="utf-8",
        )
        (root / "README.en.md").write_text(
            "# SoundBot\n\n"
            "The current source version is **v0.2.0 (prerelease)**. Version notes.\n",
            encoding="utf-8",
        )
        (root / ".github" / "RELEASE_TEMPLATE.md").write_text(
            "# SoundBot v0.2.0 — Release notes / 发布说明\n\n"
            "**Status / 状态:** Prerelease / 预发布<br>\n"
            "**Version / 版本:** 0.2.0<br>\n\n"
            "See https://example.test/blob/v0.2.0/README.md.\n",
            encoding="utf-8",
        )
        (root / "CHANGELOG.md").write_text(
            "# Changelog / 更新日志\n\n"
            "## [Unreleased] / 未发布\n\n"
            "### Notes / 说明\n\n"
            "- Nothing yet. / 暂无变更。\n\n"
            "## [0.2.0] - 2026-08-12 (Prerelease / 预发布)\n\n"
            "### Added / 新增\n\n"
            "- Added a release. / 新增一个版本。\n\n"
            "[Unreleased]: https://github.com/Huckrick/SoundBot/compare/v0.2.0...HEAD\n"
            "[0.2.0]: https://github.com/Huckrick/SoundBot/compare/v0.1.4...v0.2.0\n",
            encoding="utf-8",
        )

    @staticmethod
    def snapshot(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(path for path in root.rglob("*") if path.is_file())
        }

    def test_default_is_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            before = self.snapshot(root)
            output = io.StringIO()

            with redirect_stdout(output):
                result = bump_version.main(["--root", str(root), "--version", "0.3.0-rc.1"])

            self.assertEqual(result, 0)
            self.assertEqual(self.snapshot(root), before)
            self.assertIn("[DRY RUN]", output.getvalue())

    def test_write_synchronizes_all_version_sources_and_changelog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            plan = bump_version.build_version_bump_plan(
                root, "0.3.0-rc.1", release_date=date(2026, 8, 13)
            )
            bump_version.apply_version_bump(plan)

            package = json.loads((root / "package.json").read_text(encoding="utf-8"))
            lock = json.loads((root / "package-lock.json").read_text(encoding="utf-8"))
            self.assertEqual(package["version"], "0.3.0-rc.1")
            self.assertEqual(lock["version"], "0.3.0-rc.1")
            self.assertEqual(lock["packages"][""]["version"], "0.3.0-rc.1")
            self.assertIn('APP_VERSION = "0.3.0-rc.1"', (root / "backend" / "config.py").read_text())
            self.assertIn("**v0.3.0-rc.1（预发布）**", (root / "README.md").read_text())
            self.assertIn(
                "**v0.3.0-rc.1 (prerelease)**", (root / "README.en.md").read_text()
            )
            release_template = (root / ".github" / "RELEASE_TEMPLATE.md").read_text()
            self.assertIn("**Version / 版本:** 0.3.0-rc.1<br>", release_template)
            self.assertIn("**Status / 状态:** Prerelease / 预发布<br>", release_template)
            self.assertIn("blob/v0.3.0-rc.1/README.md", release_template)

            changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertIn(
                "## [0.3.0-rc.1] - 2026-08-13 (Prerelease / 预发布)", changelog
            )
            self.assertIn("### Added / 新增", changelog)
            self.assertIn("TODO：发布前请补充本版本变更。", changelog)
            self.assertIn("compare/v0.3.0-rc.1...HEAD", changelog)
            self.assertIn("compare/v0.2.0...v0.3.0-rc.1", changelog)

    def test_stable_version_updates_readme_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            plan = bump_version.build_version_bump_plan(
                root, "1.0.0", release_date=date(2026, 8, 13)
            )
            bump_version.apply_version_bump(plan)

            self.assertIn("**v1.0.0（稳定版）**", (root / "README.md").read_text())
            self.assertIn("**v1.0.0 (stable)**", (root / "README.en.md").read_text())
            self.assertIn("## [1.0.0] - 2026-08-13\n", (root / "CHANGELOG.md").read_text())
            self.assertIn(
                "**Status / 状态:** Stable / 稳定版<br>",
                (root / ".github" / "RELEASE_TEMPLATE.md").read_text(),
            )

    def test_bump_from_dotted_prerelease_updates_comparison_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            for path in (
                root / "package.json",
                root / "package-lock.json",
                root / "backend" / "config.py",
                root / "README.md",
                root / "README.en.md",
                root / ".github" / "RELEASE_TEMPLATE.md",
                root / "CHANGELOG.md",
            ):
                text = path.read_text(encoding="utf-8").replace("0.2.0", "0.3.0-rc.1")
                path.write_text(text, encoding="utf-8")

            plan = bump_version.build_version_bump_plan(
                root, "0.3.0-rc.2", release_date=date(2026, 8, 13)
            )
            bump_version.apply_version_bump(plan)

            changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertIn("compare/v0.3.0-rc.2...HEAD", changelog)
            self.assertIn("compare/v0.3.0-rc.1...v0.3.0-rc.2", changelog)

    def test_rejects_invalid_or_prefixed_versions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            for invalid in ("v0.3.0", "0.3", "01.2.3", "1.2.3+build", "1.2.3-"):
                with self.subTest(version=invalid):
                    with self.assertRaises(bump_version.VersionBumpError):
                        bump_version.build_version_bump_plan(root, invalid)

    def test_existing_release_section_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            changelog = root / "CHANGELOG.md"
            original = changelog.read_text(encoding="utf-8")
            changelog.write_text(
                original.replace(
                    "## [0.2.0]",
                    "## [0.3.0] - 2026-08-13\n\n"
                    "### Added / 新增\n\n"
                    "- Existing notes. / 已有说明。\n\n"
                    "## [0.2.0]",
                    1,
                ),
                encoding="utf-8",
            )
            before = self.snapshot(root)

            with self.assertRaisesRegex(bump_version.VersionBumpError, "refusing to overwrite"):
                bump_version.build_version_bump_plan(root, "0.3.0")

            self.assertEqual(self.snapshot(root), before)

    def test_rejects_downgrade_and_accepts_semver_prerelease_progression(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            with self.assertRaisesRegex(bump_version.VersionBumpError, "greater"):
                bump_version.build_version_bump_plan(root, "0.1.9")

        self.assertLess(bump_version.compare_versions("1.0.0-rc.1", "1.0.0"), 0)
        self.assertLess(bump_version.compare_versions("1.0.0-rc.1", "1.0.0-rc.2"), 0)
        self.assertGreater(bump_version.compare_versions("1.0.0", "0.99.99"), 0)

    def test_validation_failure_cannot_partially_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            readme = root / "README.en.md"
            readme.write_text(
                readme.read_text(encoding="utf-8").replace("v0.2.0", "v9.9.9"),
                encoding="utf-8",
            )
            before = self.snapshot(root)
            errors = io.StringIO()

            with redirect_stderr(errors):
                result = bump_version.main(
                    ["--root", str(root), "--version", "0.3.0", "--write"]
                )

            self.assertEqual(result, 2)
            self.assertIn("drift", errors.getvalue())
            self.assertEqual(self.snapshot(root), before)


if __name__ == "__main__":
    unittest.main()
