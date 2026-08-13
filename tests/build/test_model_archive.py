from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests.build.create_model_archive import ZIP_EPOCH, create_model_archive


class ModelArchiveTests(unittest.TestCase):
    def make_model(self, root: Path) -> None:
        model = root / "clap" / "config.json"
        model.parent.mkdir(parents=True)
        model.write_text('{"model":"test"}', encoding="utf-8")
        digest = hashlib.sha256(model.read_bytes()).hexdigest()
        (root / "model-manifest.json").write_text(
            json.dumps({
                "model_id": "test/clap",
                "revision": "a" * 40,
                "files": {"clap/config.json": digest},
            }),
            encoding="utf-8",
        )

    def test_archive_is_deterministic_and_has_fixed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "models"
            self.make_model(source)
            first = root / "first.zip"
            second = root / "second.zip"
            create_model_archive(source, first)
            for path in source.rglob("*"):
                if path.is_file():
                    os.utime(path, (1_700_000_000, 1_700_000_000))
            create_model_archive(source, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(
                    archive.namelist(), ["clap/config.json", "model-manifest.json"]
                )
                self.assertTrue(all(info.date_time == ZIP_EPOCH for info in archive.infolist()))
                self.assertIsNone(archive.testzip())

    def test_archive_rejects_unexpected_root_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "models"
            self.make_model(source)
            (source / "unexpected.txt").write_text("no", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid model archive root"):
                create_model_archive(source, root / "models.zip")


if __name__ == "__main__":
    unittest.main()
