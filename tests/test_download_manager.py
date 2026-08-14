from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from download_manager import (
    atomic_replace_directory,
    extract_zip,
    get_application_release_tag,
    verify_model_manifest,
    verify_install_receipt,
    write_install_receipt,
)


class DownloadManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_extract_zip_rejects_path_escape(self) -> None:
        archive = self.root / "bad.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("../escaped.txt", "bad")

        self.assertFalse(extract_zip(archive, self.root / "staging"))
        self.assertFalse((self.root / "escaped.txt").exists())

    def test_manifest_verification_and_atomic_install(self) -> None:
        staging = self.root / "staging"
        model_file = staging / "clap" / "config.json"
        model_file.parent.mkdir(parents=True)
        model_file.write_text('{"model": "test"}', encoding="utf-8")
        digest = hashlib.sha256(model_file.read_bytes()).hexdigest()
        (staging / "model-manifest.json").write_text(
            json.dumps({
                "model_id": "test/clap",
                "revision": "a" * 40,
                "files": {"clap/config.json": digest},
            }),
            encoding="utf-8",
        )
        target = self.root / "models"
        target.mkdir()
        (target / "old.txt").write_text("old", encoding="utf-8")

        manifest = verify_model_manifest(staging)
        write_install_receipt(
            staging,
            release_tag="v0.2.0",
            asset_name="models.zip",
            archive_sha256="f" * 64,
            manifest=manifest,
        )
        atomic_replace_directory(staging, target)

        self.assertEqual(manifest["revision"], "a" * 40)
        self.assertTrue((target / "clap" / "config.json").is_file())
        self.assertFalse((target / "old.txt").exists())
        receipt = verify_install_receipt(
            target,
            release_tag="v0.2.0",
            asset_name="models.zip",
            archive_sha256="f" * 64,
        )
        self.assertEqual(receipt["revision"], "a" * 40)

    def test_install_receipt_rejects_an_old_release_with_valid_model_files(self) -> None:
        installed = self.root / "models"
        clap = installed / "clap"
        clap.mkdir(parents=True)
        model = clap / "config.json"
        model.write_text("{}", encoding="utf-8")
        digest = hashlib.sha256(model.read_bytes()).hexdigest()
        manifest = {
            "model_id": "test/clap",
            "revision": "b" * 40,
            "files": {"clap/config.json": digest},
        }
        (installed / "model-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        write_install_receipt(
            installed,
            release_tag="v0.2.0",
            asset_name="models.zip",
            archive_sha256="1" * 64,
            manifest=manifest,
        )
        verify_model_manifest(installed)
        with self.assertRaisesRegex(ValueError, "release"):
            verify_install_receipt(
                installed,
                release_tag="v0.3.0",
                asset_name="models.zip",
                archive_sha256="2" * 64,
            )

    def test_manifest_rejects_mutable_revision_and_unlisted_files(self) -> None:
        staging = self.root / "staging"
        clap = staging / "clap"
        clap.mkdir(parents=True)
        config = clap / "config.json"
        config.write_text("{}", encoding="utf-8")
        digest = hashlib.sha256(config.read_bytes()).hexdigest()
        manifest_path = staging / "model-manifest.json"
        manifest_path.write_text(json.dumps({
            "model_id": "test/clap",
            "revision": "main",
            "files": {"clap/config.json": digest},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不可变"):
            verify_model_manifest(staging)

        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["revision"] = "b" * 40
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")
        (clap / "unlisted.bin").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "文件集合"):
            verify_model_manifest(staging)

    def test_default_release_tag_matches_application_version(self) -> None:
        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(get_application_release_tag(), f"v{package['version']}")


if __name__ == "__main__":
    unittest.main()
