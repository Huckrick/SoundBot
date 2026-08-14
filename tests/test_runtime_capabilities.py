from __future__ import annotations

import asyncio
import json
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

_MODULE_DATA_DIR = tempfile.TemporaryDirectory()
os.environ.setdefault("SOUNDBOT_USER_DATA_DIR", _MODULE_DATA_DIR.name)

import main
from core import audio_service as audio_service_module


class RuntimeCapabilitiesTests(unittest.TestCase):
    def test_health_is_degraded_when_bundled_decoder_cannot_load(self) -> None:
        private_error = OSError(
            r"DLL load failed at C:\\Users\\private-user\\SoundBot\\avcodec.dll"
        )
        with (
            mock.patch.object(audio_service_module, "_av", None),
            mock.patch.object(audio_service_module, "_AV_IMPORT_ERROR", private_error),
            mock.patch.object(main, "is_embedder_loaded", return_value=False),
        ):
            health = asyncio.run(main.health_check())
            capabilities = asyncio.run(main.get_runtime_capabilities())

        self.assertEqual(health.status, "degraded")
        self.assertFalse(health.audio_decoder_available)
        self.assertEqual(capabilities.status, "degraded")
        self.assertFalse(capabilities.audio_decoder.available)
        self.assertEqual(
            capabilities.audio_decoder.error_code, "audio_decoder_unavailable"
        )
        self.assertFalse(capabilities.semantic_search.required)
        self.assertNotIn("private-user", json.dumps(capabilities.model_dump()))

    def test_health_is_healthy_when_bundled_decoder_is_loaded(self) -> None:
        class FakeAV:
            __version__ = "18.0.0"
            library_versions = {"libavcodec": (62, 11, 100)}

        with (
            mock.patch.object(audio_service_module, "_av", FakeAV()),
            mock.patch.object(main, "is_embedder_loaded", return_value=False),
        ):
            health = asyncio.run(main.health_check())
            capabilities = asyncio.run(main.get_runtime_capabilities())

        self.assertEqual(health.status, "healthy")
        self.assertTrue(health.audio_decoder_available)
        self.assertEqual(capabilities.status, "ready")
        self.assertTrue(capabilities.audio_decoder.available)
        self.assertIn(".wav", capabilities.supported_audio_extensions)


if __name__ == "__main__":
    unittest.main()
