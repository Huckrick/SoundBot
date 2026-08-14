from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config


class ModelPathConfigurationTests(unittest.TestCase):
    def test_explicit_missing_model_path_is_authoritative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "models-intentionally-missing"
            with mock.patch.dict(
                os.environ, {"SOUNDBOT_MODELS_PATH": str(missing)}, clear=False
            ):
                self.assertEqual(config.find_models_dir(), missing)
                self.assertEqual(config.find_models_dir_runtime(), missing)


if __name__ == "__main__":
    unittest.main()
