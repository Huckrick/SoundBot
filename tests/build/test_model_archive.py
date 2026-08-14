from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.build.create_model_archive import ZIP_EPOCH, create_model_archive
from tests.build.create_model_manifest import load_bundle_config, write_manifest


class ModelArchiveTests(unittest.TestCase):
    def make_model(self, root: Path) -> Path:
        model = root / "clap" / "config.json"
        model.parent.mkdir(parents=True)
        model.write_text('{"model":"test"}', encoding="utf-8")
        notice = root.parent / "controlled-notice.txt"
        notice.write_text("Test model license notice\n", encoding="utf-8")
        config = root.parent / "model_bundle.json"
        config.write_text(
            json.dumps({
                "schema_version": 1,
                "model_id": "test/clap",
                "revision": "a" * 40,
                "license": "Apache-2.0",
                "source_url": "https://example.invalid/test/clap",
                "notice_file": str(notice),
            }),
            encoding="utf-8",
        )
        write_manifest(root, load_bundle_config(config))
        return config

    def test_archive_is_deterministic_and_has_fixed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "models"
            config = self.make_model(source)
            first = root / "first.zip"
            second = root / "second.zip"
            create_model_archive(source, first, config)
            for path in source.rglob("*"):
                if path.is_file():
                    os.utime(path, (1_700_000_000, 1_700_000_000))
            create_model_archive(source, second, config)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(
                    archive.namelist(),
                    [
                        "CLAP_MODEL_NOTICE.txt",
                        "clap/config.json",
                        "model-manifest.json",
                    ],
                )
                self.assertTrue(all(info.date_time == ZIP_EPOCH for info in archive.infolist()))
                self.assertIsNone(archive.testzip())

    def test_archive_rejects_unexpected_root_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "models"
            config = self.make_model(source)
            (source / "unexpected.txt").write_text("no", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid model archive root"):
                create_model_archive(source, root / "models.zip", config)

    def test_archive_rejects_missing_or_changed_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "models"
            config = self.make_model(source)
            notice = source / "CLAP_MODEL_NOTICE.txt"
            notice.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "controlled notice"):
                create_model_archive(source, root / "models.zip", config)

    def test_archive_rejects_output_inside_source_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "models"
            config = self.make_model(source)
            with self.assertRaisesRegex(ValueError, "outside the source"):
                create_model_archive(source, source / "models.zip", config)


if __name__ == "__main__":
    unittest.main()
