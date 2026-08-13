from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_release_metadata.py")
SPEC = importlib.util.spec_from_file_location("verify_release_metadata", MODULE_PATH)
release_metadata = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(release_metadata)


class ReleaseMetadataTests(unittest.TestCase):
    def make_tree(self, root: Path, *, backend_version: str = "0.2.0", bullet: str | None = None) -> None:
        package = {"name": "soundbot", "version": "0.2.0"}
        lock = {"version": "0.2.0", "packages": {"": {"version": "0.2.0"}}}
        (root / "backend").mkdir()
        (root / ".github").mkdir()
        (root / "package.json").write_text(json.dumps(package), encoding="utf-8")
        (root / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
        (root / "backend" / "config.py").write_text(
            f'APP_VERSION = "{backend_version}"\n', encoding="utf-8"
        )
        entry = bullet or "- Added a gate. / 新增发布门禁。"
        (root / "CHANGELOG.md").write_text(
            "# Changelog / 更新日志\n\n"
            "## [Unreleased] / 未发布\n\n"
            "## [0.2.0] - 2026-08-11\n\n"
            "### Added / 新增\n\n"
            f"{entry}\n\n"
            "## [0.1.4] - 2026-01-01\n",
            encoding="utf-8",
        )
        for name in ("README.md", "README.en.md"):
            declaration = (
                "当前源码版本为 **v0.2.0（预发布）**。\n"
                if name == "README.md"
                else "The current source version is **v0.2.0 (prerelease)**.\n"
            )
            (root / name).write_text(declaration, encoding="utf-8")
        (root / ".github" / "RELEASE_TEMPLATE.md").write_text(
            "**Status / 状态:** Prerelease / 预发布<br>\n"
            "**Version / 版本:** 0.2.0<br>\n",
            encoding="utf-8",
        )

    def test_accepts_synchronized_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            version, section = release_metadata.validate_release_metadata(root, tag="v0.2.0")
            self.assertEqual(version, "0.2.0")
            self.assertIn("Added a gate. / 新增发布门禁。", section)

    def test_rejects_version_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root, backend_version="0.1.4")
            with self.assertRaises(release_metadata.ReleaseMetadataError):
                release_metadata.validate_release_metadata(root)

    def test_rejects_monolingual_changelog_bullet(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root, bullet="- Added a release gate.")
            with self.assertRaises(release_metadata.ReleaseMetadataError):
                release_metadata.validate_release_metadata(root)

    def test_rejects_bilingual_placeholder_release_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(
                root,
                bullet="- TODO: describe the release changes. / TODO：发布前请补充。",
            )
            with self.assertRaisesRegex(
                release_metadata.ReleaseMetadataError, "placeholders"
            ):
                release_metadata.validate_release_metadata(root)

    def test_rejects_release_tag_without_v_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            with self.assertRaises(release_metadata.ReleaseMetadataError):
                release_metadata.validate_release_metadata(root, tag="0.2.0")

    def test_rejects_release_tag_for_another_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            with self.assertRaises(release_metadata.ReleaseMetadataError):
                release_metadata.validate_release_metadata(root, tag="v0.2.1")

    def test_rejects_wrong_readme_release_channel(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            english = root / "README.en.md"
            english.write_text(
                english.read_text(encoding="utf-8").replace("prerelease", "stable"),
                encoding="utf-8",
            )
            with self.assertRaises(release_metadata.ReleaseMetadataError):
                release_metadata.validate_release_metadata(root)

    def test_rejects_release_template_version_or_channel_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_tree(root)
            template = root / ".github" / "RELEASE_TEMPLATE.md"
            template.write_text(
                template.read_text(encoding="utf-8").replace("0.2.0", "0.2.1"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                release_metadata.ReleaseMetadataError, "RELEASE_TEMPLATE drift"
            ):
                release_metadata.validate_release_metadata(root)


if __name__ == "__main__":
    unittest.main()
