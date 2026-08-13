from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.model_preloader import ModelPreloader


class ModelPreloaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_load_retries_only_after_local_model_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir) / "clap"
            model_dir.mkdir()
            preloader = ModelPreloader()
            try:
                with (
                    mock.patch("config.get_clap_model_name", return_value=str(model_dir)),
                    mock.patch.object(
                        preloader,
                        "_load_models_sync",
                        side_effect=RuntimeError("model missing"),
                    ),
                ):
                    await preloader.preload_models()
                    self.assertIsNotNone(preloader.get_error())
                    self.assertIsNone(preloader.retry_if_source_changed())

                (model_dir / "config.json").write_text("{}", encoding="utf-8")
                with (
                    mock.patch("config.get_clap_model_name", return_value=str(model_dir)),
                    mock.patch.object(preloader, "_load_models_sync", return_value=None),
                ):
                    task = preloader.retry_if_source_changed()
                    self.assertIsInstance(task, asyncio.Task)
                    await task
                self.assertTrue(preloader.is_loaded())
                self.assertIsNone(preloader.get_error())
            finally:
                await preloader.close()

    async def test_progress_callback_can_be_removed(self) -> None:
        preloader = ModelPreloader()
        stages = []
        callback = lambda stage, _progress: stages.append(stage)
        preloader.add_progress_callback(callback)
        preloader.remove_progress_callback(callback)
        try:
            with mock.patch.object(preloader, "_load_models_sync", return_value=None):
                await preloader.preload_models()
            self.assertEqual(stages, [])
        finally:
            await preloader.close()


if __name__ == "__main__":
    unittest.main()
