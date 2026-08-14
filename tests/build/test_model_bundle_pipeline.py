from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import download_models, verify_model_path
from tests.build import create_model_manifest


class ModelBundlePipelineTests(unittest.TestCase):
    def make_config(self, root: Path, *, revision: str = "a" * 40) -> Path:
        notice = root / "controlled-notice.txt"
        notice.write_text("Pinned model notice\n", encoding="utf-8")
        config = root / "model_bundle.json"
        config.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "model_id": "test/pinned-clap",
                    "revision": revision,
                    "license": "Apache-2.0",
                    "source_url": "https://example.invalid/test/pinned-clap",
                    "notice_file": str(notice),
                }
            ),
            encoding="utf-8",
        )
        return config

    def make_bundle(self, root: Path, config: Path) -> Path:
        models = root / "models"
        model_file = models / "clap" / "config.json"
        model_file.parent.mkdir(parents=True)
        model_file.write_text('{"model":"fixture"}\n', encoding="utf-8")
        create_model_manifest.write_manifest(
            models, create_model_manifest.load_bundle_config(config)
        )
        return models

    def test_repository_config_and_notice_identify_same_pinned_model(self) -> None:
        config = create_model_manifest.load_bundle_config(
            create_model_manifest.DEFAULT_BUNDLE_CONFIG
        )
        notice = config.notice_path.read_text(encoding="utf-8")
        self.assertIn(f"Model: {config.model_id}", notice)
        self.assertIn(f"Pinned revision: {config.revision}", notice)
        self.assertIn(f"Declared model license: {config.license}", notice)

    def test_mutable_revision_and_unknown_config_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mutable = self.make_config(root, revision="main")
            with self.assertRaisesRegex(ValueError, "immutable"):
                create_model_manifest.load_bundle_config(mutable)

            loaded = json.loads(mutable.read_text(encoding="utf-8"))
            loaded["revision"] = "b" * 40
            loaded["model_revision"] = "main"
            mutable.write_text(json.dumps(loaded), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown fields"):
                create_model_manifest.load_bundle_config(mutable)

    def test_manifest_copies_and_hashes_controlled_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self.make_config(root)
            models = self.make_bundle(root, config_path)
            config = create_model_manifest.load_bundle_config(config_path)

            manifest = create_model_manifest.verify_manifest_against_config(
                models, config
            )
            self.assertEqual(manifest["model_id"], config.model_id)
            self.assertEqual(manifest["revision"], config.revision)
            self.assertEqual(manifest["notice"]["path"], "CLAP_MODEL_NOTICE.txt")
            self.assertEqual(
                manifest["notice"]["sha256"],
                create_model_manifest.sha256_file(models / "CLAP_MODEL_NOTICE.txt"),
            )

            (models / "CLAP_MODEL_NOTICE.txt").write_text(
                "changed notice\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "controlled notice"):
                create_model_manifest.verify_manifest_against_config(models, config)

    def test_download_builds_in_clean_staging_then_replaces_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.make_config(root)
            models = root / "models"
            models.mkdir()
            (models / "stale-from-old-revision.bin").write_bytes(b"stale")

            def fake_download(destination, _bundle):
                model_file = destination / "clap" / "model.safetensors"
                model_file.parent.mkdir(parents=True)
                model_file.write_bytes(b"pinned model")

            with mock.patch.object(
                create_model_manifest,
                "download_pinned_model",
                side_effect=fake_download,
            ):
                manifest_path = create_model_manifest.create_bundle(
                    models, config, download=True
                )

            self.assertEqual(
                manifest_path,
                (models / "model-manifest.json").resolve(strict=False),
            )
            self.assertFalse((models / "stale-from-old-revision.bin").exists())
            self.assertEqual(
                (models / "CLAP_MODEL_NOTICE.txt").read_text(encoding="utf-8"),
                "Pinned model notice\n",
            )
            create_model_manifest.verify_manifest_against_config(
                models, create_model_manifest.load_bundle_config(config)
            )

    def test_failed_download_preserves_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.make_config(root)
            models = root / "models"
            models.mkdir()
            original = models / "existing.bin"
            original.write_bytes(b"keep me")

            with mock.patch.object(
                create_model_manifest,
                "download_pinned_model",
                side_effect=RuntimeError("network failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "network failed"):
                    create_model_manifest.create_bundle(models, config, download=True)

            self.assertEqual(original.read_bytes(), b"keep me")

    def test_manifest_cli_has_no_model_identity_override(self) -> None:
        with mock.patch("sys.stderr", new=io.StringIO()):
            with self.assertRaises(SystemExit):
                create_model_manifest.parse_args(["--model-id", "attacker/model"])
            with self.assertRaises(SystemExit):
                create_model_manifest.parse_args(["--revision", "main"])
            with self.assertRaises(SystemExit):
                create_model_manifest.parse_args([])

    def test_existing_tree_cannot_be_blessed_without_exact_download(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.make_config(root)
            models = root / "models"
            (models / "clap").mkdir(parents=True)
            (models / "clap" / "weights.bin").write_bytes(b"unknown")
            with self.assertRaisesRegex(ValueError, "exact --download"):
                create_model_manifest.create_bundle(models, config, download=False)

    def test_legacy_downloader_delegates_without_installing_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.make_config(root)
            models = root / "models"
            with mock.patch.object(download_models.subprocess, "run") as run:
                result = download_models.download_clap_model(models, config)

            self.assertEqual(result, models / "clap")
            command = run.call_args.args[0]
            self.assertNotIn("pip", command)
            self.assertIn("--download", command)
            self.assertIn(str(config.resolve()), command)
            self.assertNotIn("main", command)
            self.assertTrue(run.call_args.kwargs["check"])

    def test_legacy_downloader_rejects_mutable_revision_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.make_config(root, revision="main")
            with mock.patch.object(download_models.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "immutable"):
                    download_models.download_clap_model(root / "models", config)
            run.assert_not_called()

    def test_local_verifier_uses_authoritative_env_path_and_checks_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.make_config(root)
            models = self.make_bundle(root, config)
            missing = root / "missing"

            with mock.patch.dict(
                os.environ, {"SOUNDBOT_MODELS_PATH": str(missing)}, clear=False
            ):
                self.assertEqual(
                    verify_model_path.resolve_models_dir(),
                    missing.resolve(strict=False),
                )

            manifest = verify_model_path.verify_local_bundle(models, config)
            self.assertEqual(manifest["revision"], "a" * 40)
            (models / "CLAP_MODEL_NOTICE.txt").unlink()
            with self.assertRaisesRegex(FileNotFoundError, "notice"):
                verify_model_path.verify_local_bundle(models, config)


if __name__ == "__main__":
    unittest.main()
