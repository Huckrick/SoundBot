from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core import embedder as embedder_module
from core import model_preloader as preloader_module
from core.embedder import (
    CLIPEmbedder,
    CLAPTextEmbeddingProvider,
    get_embedder_fingerprint,
    is_embedder_available,
    is_embedder_loaded,
    normalize_embedding,
    peek_embedder,
)


class EmbedderPreprocessingTests(unittest.TestCase):
    def test_clap_text_provider_never_loads_model_on_async_construction(self):
        with (
            mock.patch.object(embedder_module, "peek_embedder", return_value=None),
            mock.patch.object(
                embedder_module,
                "get_embedder",
                side_effect=AssertionError("provider must not load CLAP"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "CLAP 模型不可用"):
                CLAPTextEmbeddingProvider()

    def test_peek_never_constructs_embedder_or_preloader(self):
        with (
            mock.patch.object(embedder_module, "_embedder", None),
            mock.patch.object(CLIPEmbedder, "_instance", None),
            mock.patch.object(preloader_module, "_preloader", None),
            mock.patch.object(
                preloader_module,
                "get_preloader",
                side_effect=AssertionError("peek must not construct preloader"),
            ),
            mock.patch.object(
                CLIPEmbedder,
                "__new__",
                side_effect=AssertionError("peek must not construct CLAP"),
            ),
        ):
            self.assertIsNone(peek_embedder())
            self.assertFalse(is_embedder_available())
            self.assertFalse(is_embedder_loaded())
            self.assertEqual(get_embedder_fingerprint(load=False), "clap:unavailable")

    def test_peek_returns_existing_preloaded_embedder(self):
        loaded = mock.Mock(fingerprint="clap:ready", _initialized=True)
        preloader = mock.Mock()
        preloader.get_embedder.return_value = loaded
        with (
            mock.patch.object(embedder_module, "_embedder", None),
            mock.patch.object(CLIPEmbedder, "_instance", None),
            mock.patch.object(preloader_module, "_preloader", preloader),
        ):
            self.assertIs(peek_embedder(), loaded)
            self.assertEqual(get_embedder_fingerprint(load=False), "clap:ready")

    def test_normalize_embedding_rejects_zero_and_nonfinite_vectors(self):
        with self.assertRaises(ValueError):
            normalize_embedding([0.0, 0.0])
        with self.assertRaises(ValueError):
            normalize_embedding([1.0, float("nan")])
        normalized = normalize_embedding([3.0, 4.0])
        self.assertAlmostEqual(float(np.linalg.norm(normalized)), 1.0)

    def test_same_size_weight_changes_local_model_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first" / "clap"
            second = root / "second" / "clap"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (first / "config.json").write_text("{}", encoding="utf-8")
            (second / "config.json").write_text("{}", encoding="utf-8")
            (first / "model.safetensors").write_bytes(b"AAAA")
            (second / "model.safetensors").write_bytes(b"BBBB")

            first_identity = CLIPEmbedder._stable_model_identity(first)
            second_identity = CLIPEmbedder._stable_model_identity(second)

        self.assertNotEqual(first_identity, second_identity)

    def test_manifest_revision_and_declared_sha_define_model_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_dir = root / "clap"
            model_dir.mkdir()
            weight = model_dir / "model.safetensors"
            weight.write_bytes(b"weights")
            checksum = hashlib.sha256(weight.read_bytes()).hexdigest()
            manifest_path = root / "model-manifest.json"

            def write_manifest(revision, declared_checksum=checksum):
                manifest_path.write_text(
                    json.dumps(
                        {
                            "model_id": "test/clap",
                            "revision": revision,
                            "files": {
                                "clap/model.safetensors": declared_checksum,
                            },
                        }
                    ),
                    encoding="utf-8",
                )

            write_manifest("revision-a")
            first_identity = CLIPEmbedder._stable_model_identity(model_dir)
            write_manifest("revision-b")
            revision_identity = CLIPEmbedder._stable_model_identity(model_dir)
            write_manifest("revision-b", "f" * 64)
            sha_identity = CLIPEmbedder._stable_model_identity(model_dir)

        self.assertNotEqual(first_identity, revision_identity)
        self.assertNotEqual(revision_identity, sha_identity)

    def test_clap_load_is_local_only(self):
        model_calls = []
        processor_calls = []

        class FakeModel:
            config = types.SimpleNamespace(projection_dim=2)

            @classmethod
            def from_pretrained(cls, path, **kwargs):
                model_calls.append((path, kwargs))
                return cls()

            def to(self, _device):
                return self

            def eval(self):
                return self

        class FakeProcessor:
            feature_extractor = types.SimpleNamespace(
                sampling_rate=48000, nb_max_samples=480000
            )

            @classmethod
            def from_pretrained(cls, path, **kwargs):
                processor_calls.append((path, kwargs))
                return cls()

        fake_transformers = types.ModuleType("transformers")
        fake_transformers.ClapModel = FakeModel
        fake_transformers.ClapProcessor = FakeProcessor

        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory) / "clap"
            model_dir.mkdir()
            (model_dir / "config.json").write_text("{}", encoding="utf-8")
            with (
                mock.patch.dict(sys.modules, {"transformers": fake_transformers}),
                mock.patch.object(
                    embedder_module.config,
                    "get_clap_model_name",
                    return_value=str(model_dir),
                ),
                mock.patch.object(CLIPEmbedder, "_instance", None),
            ):
                loaded = CLIPEmbedder()

        self.assertTrue(loaded._initialized)
        self.assertTrue(model_calls[0][1]["local_files_only"])
        self.assertTrue(processor_calls[0][1]["local_files_only"])

    def test_missing_model_configuration_does_not_return_remote_id(self):
        with tempfile.TemporaryDirectory() as directory:
            models_root = Path(directory) / "models"
            with (
                mock.patch.object(
                    embedder_module.config,
                    "find_models_dir_runtime",
                    return_value=models_root,
                ),
                mock.patch.dict("os.environ", {"CLAP_MODEL": "remote/model"}),
            ):
                configured = embedder_module.config.get_clap_model_name()
        self.assertEqual(configured, str(models_root / "clap"))

    def test_long_audio_uses_processor_sized_deterministic_windows(self):
        embedder = object.__new__(CLIPEmbedder)
        embedder.sample_rate = 10
        embedder.max_samples = 100
        observed = []

        def encode(segment):
            observed.append(segment.copy())
            return normalize_embedding([float(segment[0] + 1), 1.0])

        embedder._process_audio_segment = encode
        audio = np.arange(250, dtype=np.float32)
        first = embedder._process_long_audio(audio)
        first_windows = [window.copy() for window in observed]
        observed.clear()
        second = embedder._process_long_audio(audio)

        self.assertEqual([len(window) for window in first_windows], [100, 100, 50])
        self.assertTrue(all(np.array_equal(a, b) for a, b in zip(first_windows, observed)))
        np.testing.assert_allclose(first, second)
        self.assertAlmostEqual(float(np.linalg.norm(first)), 1.0, delta=1e-6)


if __name__ == "__main__":
    unittest.main()
