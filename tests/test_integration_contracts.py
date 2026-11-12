from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Importing the FastAPI module initializes its logger/config paths. Keep those
# writes inside a disposable test root rather than the developer's real app data.
_MODULE_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["SOUNDBOT_USER_DATA_DIR"] = _MODULE_DATA_DIR.name

import config
import main
from core.audio_service import FileFingerprint, WaveformResult
from core.database import AudioFileRecord, DatabaseManager
from models import schemas


class DesktopAccessTests(unittest.TestCase):
    def test_http_and_websocket_require_the_desktop_token(self) -> None:
        with mock.patch.dict(os.environ, {
            "SOUNDBOT_REQUIRE_DESKTOP_TOKEN": "1", "SOUNDBOT_IPC_TOKEN": "test-local-token",
        }), TestClient(main.app) as client:
            self.assertEqual(client.get("/api/v1/health").status_code, 200)
            self.assertEqual(client.get("/api/v1/runtime/capabilities").status_code, 403)
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/ws/scan/fixture"):
                    self.fail("the WebSocket accepted an unauthenticated client")
            with client.websocket_connect("/ws/scan/fixture", headers={
                "X-SoundBot-IPC": "test-local-token",
            }) as websocket:
                websocket.send_json({"type": "ping"})
                self.assertEqual(websocket.receive_json(), {"type": "pong"})


def _artifact_state(manager: DatabaseManager, project_id: str, kind: str) -> str:
    with manager.get_cursor() as cursor:
        row = cursor.execute(
            "SELECT state FROM file_artifacts WHERE project_id=? AND kind=?",
            (project_id, kind),
        ).fetchone()
    if row is None:
        raise AssertionError(f"missing artifact state for {project_id}/{kind}")
    return str(row["state"])


class DatabaseWaveformContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.manager = DatabaseManager(str(self.root / "contracts.db"))
        self.audio = self.root / "中文 % # + tone.wav"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    async def test_stale_peaks_are_not_returned_after_source_change(self) -> None:
        self.audio.write_bytes(b"old")
        old_fingerprint = FileFingerprint(
            self.audio.stat().st_size, self.audio.stat().st_mtime_ns
        )
        old_peaks = [0.2] * config.WAVEFORM_PEAK_COUNT
        self.manager.upsert_file(
            AudioFileRecord(
                path=str(self.audio),
                filename=self.audio.name,
                duration=1.0,
                sample_rate=48_000,
                channels=1,
                file_size=self.audio.stat().st_size,
                peaks_json=json.dumps(old_peaks),
                source_fingerprint=old_fingerprint.source_key,
                waveform_fingerprint=old_fingerprint.key,
                waveform_version=int(old_fingerprint.waveform_version),
            )
        )

        # This is the import failure shape: the file changed, but decoding did
        # not provide replacement peaks. The old array may remain stored for
        # recovery, but the waveform API must never report it as current.
        self.audio.write_bytes(b"new source bytes")
        current = FileFingerprint(
            self.audio.stat().st_size, self.audio.stat().st_mtime_ns
        )
        self.manager.upsert_file(
            AudioFileRecord(
                path=str(self.audio),
                filename=self.audio.name,
                duration=2.0,
                sample_rate=48_000,
                channels=1,
                file_size=self.audio.stat().st_size,
                source_fingerprint=current.source_key,
            )
        )
        self.assertEqual(
            _artifact_state(self.manager, "default", "waveform"), "stale"
        )

        new_peaks = [0.8] * config.WAVEFORM_PEAK_COUNT

        class FakeAudioService:
            waveform_calls = 0

            def fingerprint(self, _path: str) -> FileFingerprint:
                return current

            def waveform(self, _path: str) -> WaveformResult:
                self.waveform_calls += 1
                return WaveformResult(new_peaks, 2.0, 48_000, current)

        service = FakeAudioService()
        with (
            mock.patch.object(main, "get_db_manager", return_value=self.manager),
            mock.patch("core.audio_service.get_audio_service", return_value=service),
        ):
            payload = await main._waveform_payload(str(self.audio), "default")

        self.assertEqual(service.waveform_calls, 1)
        self.assertFalse(payload["cached"])
        self.assertEqual(payload["peaks"], new_peaks)

    async def test_fresh_replacement_peaks_finish_in_ready_state(self) -> None:
        self.audio.write_bytes(b"first")
        first = FileFingerprint(self.audio.stat().st_size, self.audio.stat().st_mtime_ns)
        self.manager.upsert_file(
            AudioFileRecord(
                path=str(self.audio),
                filename=self.audio.name,
                peaks_json=json.dumps([0.1] * config.WAVEFORM_PEAK_COUNT),
                source_fingerprint=first.source_key,
                waveform_fingerprint=first.key,
                waveform_version=int(first.waveform_version),
            )
        )

        self.audio.write_bytes(b"second and different")
        second = FileFingerprint(self.audio.stat().st_size, self.audio.stat().st_mtime_ns)
        self.manager.upsert_file(
            AudioFileRecord(
                path=str(self.audio),
                filename=self.audio.name,
                peaks_json=json.dumps([0.9] * config.WAVEFORM_PEAK_COUNT),
                source_fingerprint=second.source_key,
                waveform_fingerprint=second.key,
                waveform_version=int(second.waveform_version),
            )
        )

        self.assertEqual(
            _artifact_state(self.manager, "default", "waveform"), "ready"
        )





class APIContractTests(unittest.IsolatedAsyncioTestCase):

    async def test_ai_config_route_never_returns_api_keys(self) -> None:
        class FakeConfigManager:
            def get_llm_provider(self):
                return "openai"

            def get_embedding_provider(self):
                return "external"

            def get_llm_config(self):
                return {"provider": "openai", "openai": {"api_key": "LLM-SECRET"}}

            def get_embedding_config(self):
                return {
                    "provider": "external",
                    "external": {"api_key": "EMBED-SECRET"},
                }

            def get_public_llm_config(self):
                return {"provider": "openai", "openai": {"has_api_key": True}}

            def get_public_embedding_config(self):
                return {
                    "provider": "external",
                    "external": {"has_api_key": True},
                }

            def detect_available_local_services(self):
                return {"lm_studio": False, "ollama": False}

        with mock.patch(
            "core.llm_config_manager.get_llm_config_manager",
            return_value=FakeConfigManager(),
        ):
            response = await main.get_ai_config()

        encoded = json.dumps(response, ensure_ascii=False)
        self.assertNotIn("LLM-SECRET", encoded)
        self.assertNotIn("EMBED-SECRET", encoded)
        self.assertNotIn('"api_key"', encoded)
        self.assertTrue(response["llm"]["config"]["openai"]["has_api_key"])

    async def test_missing_project_preserves_404_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = DatabaseManager(str(Path(directory) / "project.db"))
            with mock.patch.object(main, "get_db_manager", return_value=manager):
                with self.assertRaises(Exception) as raised:
                    await main.get_project("missing-project")

        self.assertEqual(getattr(raised.exception, "status_code", None), 404)

    async def test_async_search_rejects_missing_project_before_scheduling(self) -> None:
        from fastapi import BackgroundTasks

        with tempfile.TemporaryDirectory() as directory:
            manager = DatabaseManager(str(Path(directory) / "search.db"))
            request = schemas.SearchRequest(
                query="rain", project_id="missing-project", top_k=10
            )
            tasks = BackgroundTasks()
            with mock.patch.object(main, "get_db_manager", return_value=manager):
                with self.assertRaises(main.SoundBotAPIError) as raised:
                    await main.search_audio_async(request, tasks)

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(len(tasks.tasks), 0)

    def test_project_import_route_is_declared(self) -> None:
        route_methods = {
            (route.path, method)
            for route in main.app.routes
            for method in (getattr(route, "methods", None) or set())
        }
        self.assertIn(
            ("/api/v1/projects/{project_id}/imports", "POST"), route_methods
        )

    def test_project_import_requires_exactly_one_source(self) -> None:
        self.assertEqual(
            schemas.ProjectImportRequest(folder_path="/audio").folder_path,
            "/audio",
        )
        self.assertEqual(
            schemas.ProjectImportRequest(file_paths=["/audio/hit.wav"]).file_paths,
            ["/audio/hit.wav"],
        )
        for payload in (
            {},
            {"folder_path": "/audio", "file_paths": ["/audio/hit.wav"]},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                schemas.ProjectImportRequest(**payload)

    async def test_project_folder_import_captures_path_project(self) -> None:
        from fastapi import BackgroundTasks

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "folder"
            folder.mkdir()
            manager = DatabaseManager(str(root / "imports.db"))
            tasks = BackgroundTasks()
            with mock.patch.object(main, "get_db_manager", return_value=manager):
                response = await main.import_project_files(
                    "default",
                    schemas.ProjectImportRequest(
                        folder_path=str(folder), client_id="renderer-a"
                    ),
                    tasks,
                )

        self.assertEqual(response["project_id"], "default")
        self.assertEqual(response["job_id"], response["task_id"])
        self.assertEqual(len(tasks.tasks), 1)
        self.assertEqual(tasks.tasks[0].args[1:], ("default", "renderer-a"))


    def test_project_scoped_contract_fields_are_strict(self) -> None:
        with self.assertRaises(ValidationError):
            schemas.SearchRequest(query="rain")
        self.assertEqual(
            schemas.SearchRequest(query="rain", project_id="default").project_id,
            "default",
        )
        self.assertEqual(
            schemas.WaveformBatchRequest(file_ids=["file-a"]).points,
            2000,
        )
        with self.assertRaises(ValidationError):
            schemas.WaveformBatchRequest(file_ids=["file-a"], points=512)
        self.assertNotIn("id", schemas.CreateProjectRequest.model_fields)
        with self.assertRaises(ValidationError):
            schemas.CreateProjectRequest(name="Demo", id="caller-selected")

    def test_ai_chat_request_has_explicit_project_context(self) -> None:
        self.assertIn("project_id", schemas.AIChatRequest.model_fields)






if __name__ == "__main__":
    unittest.main()
