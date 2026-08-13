from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.database import DatabaseManager
from core.index_lifecycle import (
    activate_verified_shadows,
    resolve_active_collections,
    shadow_collection_name,
)


class FakeShadow:
    def __init__(self, kind: str, count: int, metric: str = "cosine"):
        self.kind = kind
        self.count = count
        self.metric = metric

    def get_manifest(self):
        prefix = "audio" if self.kind == "audio_vector" else "text"
        return {
            "collection": f"{prefix}_shadow_job123",
            "count": self.count,
            "metric": self.metric,
            "needs_rebuild": self.metric != "cosine",
            "engine_fingerprint": f"{prefix}:fingerprint",
            "model_id": "test/model" if prefix == "audio" else None,
            "model_revision": "abc123" if prefix == "audio" else None,
            "dimensions": 4,
            "preprocessing_version": "test-v1",
        }


class IndexLifecycleTests(unittest.TestCase):
    def test_shadow_names_are_unique_kind_scoped_collections(self) -> None:
        self.assertEqual(
            shadow_collection_name("audio_vector", "job/with spaces"),
            "audio_shadow_job_with_spaces",
        )
        self.assertEqual(
            shadow_collection_name("text_vector", "job-1"),
            "text_shadow_job-1",
        )

    def test_verified_shadow_switch_is_atomic_and_preserves_old_pointer_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = DatabaseManager(str(Path(directory) / "db.sqlite"))
            manager.upsert_index_manifest(
                "default",
                "audio_vector",
                collection_name="audio_embeddings",
                metric="cosine",
                state="ready",
            )

            with self.assertRaises(RuntimeError):
                activate_verified_shadows(
                    manager,
                    "default",
                    {"audio_vector": FakeShadow("audio_vector", 1)},
                    {"audio_vector": 2},
                )
            self.assertEqual(
                resolve_active_collections(manager, "default").audio,
                "audio_embeddings",
            )

            activated = activate_verified_shadows(
                manager,
                "default",
                {
                    "audio_vector": FakeShadow("audio_vector", 2),
                    "text_vector": FakeShadow("text_vector", 2),
                },
                {"audio_vector": 2, "text_vector": 2},
            )
            self.assertEqual(activated["audio_vector"]["state"], "ready")
            active = resolve_active_collections(manager, "default")
            self.assertEqual(active.audio, "audio_shadow_job123")
            self.assertEqual(active.text, "text_shadow_job123")


if __name__ == "__main__":
    unittest.main()
