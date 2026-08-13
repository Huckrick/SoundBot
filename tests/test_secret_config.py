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
from core.llm_config_manager import LLMConfigManager


class SecretConfigTests(unittest.TestCase):
    def tearDown(self) -> None:
        LLMConfigManager._instance = None
        manager_module._config_manager = None

    def _manager(self, root: Path) -> LLMConfigManager:
        LLMConfigManager._instance = None
        manager_module._config_manager = None
        with mock.patch.object(
            manager_module.config, "get_user_data_dir", return_value=root
        ):
            return LLMConfigManager()

    def test_legacy_plaintext_is_runtime_only_and_disk_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = {
                "llm": {
                    "provider": "openai",
                    "openai": {
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4o-mini",
                        "api_key": "legacy-llm-secret",
                    },
                },
                "embedding": {
                    "provider": "external",
                    "external": {
                        "base_url": "https://api.openai.com/v1",
                        "api_key": "legacy-embedding-secret",
                    },
                },
            }
            (root / "ai_config.json").write_text(
                json.dumps(legacy), encoding="utf-8"
            )

            manager = self._manager(root)

            # Backward compatibility for this process: Electron migrates the
            # same values to safeStorage before starting the backend.
            self.assertEqual(
                manager.get_current_llm_config().api_key, "legacy-llm-secret"
            )
            self.assertEqual(
                manager.get_current_embedding_config().api_key,
                "legacy-embedding-secret",
            )
            persisted = (root / "ai_config.json").read_text(encoding="utf-8")
            self.assertNotIn("legacy-llm-secret", persisted)
            self.assertNotIn("legacy-embedding-secret", persisted)
            self.assertNotIn('"api_key"', persisted)

    def test_omitted_secret_preserves_memory_and_empty_string_clears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = self._manager(root)
            manager.save_full_config(
                "openai",
                {
                    "base_url": "https://api.openai.com/v1",
                    "model": "gpt-4o-mini",
                    "api_key": "runtime-only",
                },
                "external",
                {
                    "base_url": "https://api.openai.com/v1",
                    "model": "text-embedding-3-small",
                    "api_key": "embedding-runtime-only",
                },
            )

            manager.save_full_config(
                "openai",
                {"base_url": "https://example.com/v1", "model": "new-model"},
                "external",
                {"base_url": "https://example.com/v1", "model": "new-embed"},
            )
            self.assertEqual(manager.get_current_llm_config().api_key, "runtime-only")
            self.assertEqual(
                manager.get_current_embedding_config().api_key,
                "embedding-runtime-only",
            )

            manager.save_full_config(
                "openai",
                {"api_key": ""},
                "external",
                {"api_key": ""},
            )
            self.assertEqual(manager.get_current_llm_config().api_key, "")
            self.assertEqual(manager.get_current_embedding_config().api_key, "")
            persisted = json.loads(
                (root / "ai_config.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("api_key", persisted["llm"]["openai"])
            self.assertNotIn("api_key", persisted["embedding"]["external"])

    def test_public_and_exported_config_never_return_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = self._manager(Path(directory))
            manager.save_full_config(
                "custom",
                {
                    "base_url": "https://example.com/v1",
                    "api_key": "do-not-return",
                    "headers": {
                        "Authorization": "Bearer another-secret",
                        "X-Trace-ID": "safe-metadata",
                    },
                },
                "default",
                {},
            )

            public = manager.get_public_llm_config()
            exported = manager.export_config()
            encoded = json.dumps({"public": public, "exported": exported})
            self.assertNotIn("do-not-return", encoded)
            self.assertNotIn("another-secret", encoded)
            self.assertNotIn('"api_key"', encoded)
            self.assertTrue(public["custom"]["has_api_key"])


if __name__ == "__main__":
    unittest.main()
