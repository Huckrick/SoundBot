from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core import llm_config_manager as manager_module
from core.llm_config_manager import LLMConfigManager, LLMProvider


class LLMConfigTests(unittest.TestCase):
    def tearDown(self):
        LLMConfigManager._instance = None
        manager_module._config_manager = None

    def test_legacy_selection_falls_back_but_config_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ai_config.json").write_text(
                json.dumps(
                    {
                        "llm": {
                            "provider": "anthropic",
                            "anthropic": {
                                "base_url": "https://api.anthropic.com/v1",
                                "api_key": "legacy-secret",
                                "model": "legacy-model",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            LLMConfigManager._instance = None
            with mock.patch.object(
                manager_module.config, "get_user_data_dir", return_value=root
            ):
                manager = LLMConfigManager()

            configured = manager.get_llm_config()
            self.assertEqual(configured["provider"], LLMProvider.LM_STUDIO)
            self.assertEqual(configured["legacy_provider"], "anthropic")
            self.assertEqual(configured["anthropic"]["api_key"], "legacy-secret")

    def test_public_config_returns_presence_not_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            LLMConfigManager._instance = None
            with mock.patch.object(
                manager_module.config, "get_user_data_dir", return_value=root
            ):
                manager = LLMConfigManager()
            manager._config["llm"]["openai"]["api_key"] = "private"
            manager._config["embedding"]["external"]["api_key"] = "embed-private"

            llm = manager.get_public_llm_config()
            embedding = manager.get_public_embedding_config()
            self.assertNotIn("api_key", llm["openai"])
            self.assertTrue(llm["openai"]["has_api_key"])
            self.assertNotIn("api_key", embedding["external"])
            self.assertTrue(embedding["external"]["has_api_key"])
            # Public export must not mutate the in-memory secret config.
            self.assertEqual(manager.get_llm_config()["openai"]["api_key"], "private")

    def test_exposed_provider_list_excludes_unverified_adapters(self):
        self.assertNotIn("anthropic", LLMProvider.ALL)
        self.assertNotIn("gemini", LLMProvider.ALL)
        self.assertIn("openai", LLMProvider.ALL)
        self.assertIn("ollama", LLMProvider.ALL)


if __name__ == "__main__":
    unittest.main()
