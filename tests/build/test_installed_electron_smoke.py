from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock
import wave


MODULE_PATH = Path(__file__).with_name("check_installed_electron.py")
SPEC = importlib.util.spec_from_file_location("check_installed_electron", MODULE_PATH)
installed_smoke = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(installed_smoke)


class InstalledElectronSmokeTests(unittest.TestCase):
    def _make_pinned_model(self, root: Path) -> tuple[Path, Path]:
        models = root / "models"
        model_file = models / "clap" / "config.json"
        model_file.parent.mkdir(parents=True)
        model_file.write_text('{"model":"test"}\n', encoding="utf-8")
        revision = "a" * 40
        (models / "model-manifest.json").write_text(
            json.dumps({
                "model_id": "test/clap",
                "revision": revision,
                "files": {
                    "clap/config.json": hashlib.sha256(model_file.read_bytes()).hexdigest()
                },
            }),
            encoding="utf-8",
        )
        source_notice = root / "licenses" / "CLAP_MODEL_NOTICE.txt"
        source_notice.parent.mkdir(parents=True)
        source_notice.write_text("pinned notice\n", encoding="utf-8")
        (models / "CLAP_MODEL_NOTICE.txt").write_bytes(source_notice.read_bytes())
        bundle_config = root / "model_bundle.json"
        bundle_config.write_text(
            json.dumps({
                "model_id": "test/clap",
                "revision": revision,
                "notice_file": "licenses/CLAP_MODEL_NOTICE.txt",
            }),
            encoding="utf-8",
        )
        return models, bundle_config

    def test_installed_models_match_hashes_revision_and_controlled_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            models, bundle_config = self._make_pinned_model(root)
            manifest = installed_smoke._verify_installed_models(
                models,
                bundle_config_path=bundle_config,
                source_root=root,
            )
            self.assertEqual(manifest["revision"], "a" * 40)

            (models / "CLAP_MODEL_NOTICE.txt").write_text(
                "tampered notice\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "notice"):
                installed_smoke._verify_installed_models(
                    models,
                    bundle_config_path=bundle_config,
                    source_root=root,
                )

    def test_generated_fixture_is_a_real_one_second_pcm_wave(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "中文 % # + (fixture).wav"
            installed_smoke._write_fixture(fixture, 440.0)
            with wave.open(str(fixture), "rb") as source:
                self.assertEqual(source.getnchannels(), 1)
                self.assertEqual(source.getsampwidth(), 2)
                self.assertEqual(source.getframerate(), 44_100)
                self.assertEqual(source.getnframes(), 44_100)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for JS syntax validation")
    def test_renderer_smoke_expression_is_valid_javascript(self) -> None:
        expression = installed_smoke._renderer_smoke_expression(
            Path(r"C:\临时 路径\tone % # + (A).wav"),
            Path(r"C:\临时 路径\文件夹\tone % # + (B).wav"),
            Path(r"C:\临时 路径\压缩\tone % # + (WMA).wma"),
            timeout_seconds=60,
        )
        checked = subprocess.run(
            ["node", "--check", "-"],
            input=expression,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertIn("audio_decoder_available", expression)
        self.assertIn("model_status?.loaded", expression)
        self.assertIn("importFolderAsync", expression)
        self.assertIn("getWaveformById", expression)
        self.assertIn("audio_index_state", expression)
        self.assertIn("text_index_state", expression)
        self.assertIn("searchAudio", expression)
        self.assertIn("transcoded_wav", expression)

    def test_native_file_and_folder_choosers_are_real_dialog_exercises(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.expressions = []

            def evaluate(self, expression: str) -> dict:
                self.expressions.append(expression)
                if "triggered: true" in expression:
                    return {"triggered": True}
                return {"settled": True}

        for method in ("selectAudioFiles", "selectFolder"):
            client = FakeClient()
            dialog = {
                "handle": 100,
                "pid": 200,
                "class": "#32770",
                "title": "Open",
            }
            with (
                mock.patch.object(installed_smoke, "_enumerate_native_dialogs", return_value=[]),
                mock.patch.object(installed_smoke, "_wait_for_new_native_dialog", return_value=dialog),
                mock.patch.object(installed_smoke, "_close_native_dialog") as close,
            ):
                result = installed_smoke._exercise_native_chooser(
                    client, 123, method, timeout=1.0
                )

            self.assertTrue(result["appeared"])
            self.assertTrue(result["closed"])
            self.assertEqual(result["class"], "#32770")
            close.assert_called_once_with(100)
            self.assertIn(method, client.expressions[0])

    def test_installed_runtime_cannot_inherit_the_build_workspace_model(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn('env.pop("SOUNDBOT_MODELS_PATH", None)', source)
        self.assertIn('"ENABLE_MODEL_PRELOAD": "true" if args.require_models', source)
        self.assertIn('"HF_HUB_OFFLINE": "1"', source)
        self.assertIn('_windows_process_tree(root_pid)', source)
        self.assertNotIn("SOUNDBOT_TEST_DIALOG", source)


if __name__ == "__main__":
    unittest.main()
