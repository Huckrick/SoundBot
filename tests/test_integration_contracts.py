from __future__ import annotations

import json
import os
import asyncio
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from pydantic import ValidationError


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
from core.search_engine import OptimizedAudioSearcher
from models import schemas


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

    async def test_lazy_source_change_schedules_vector_reconcile(self) -> None:
        self.audio.write_bytes(b"old")
        old = FileFingerprint(self.audio.stat().st_size, self.audio.stat().st_mtime_ns)
        file_uuid = self.manager.upsert_file(
            AudioFileRecord(
                path=str(self.audio),
                filename=self.audio.name,
                channels=1,
                source_fingerprint=old.source_key,
            ),
            "default",
        )
        for kind in ("audio_vector", "text_vector"):
            self.manager.set_artifact_state("default", file_uuid, kind, "ready")

        self.audio.write_bytes(b"new source")
        current = FileFingerprint(
            self.audio.stat().st_size, self.audio.stat().st_mtime_ns
        )

        class FakeAudioService:
            def fingerprint(self, _path: str) -> FileFingerprint:
                return current

            def waveform(self, _path: str) -> WaveformResult:
                return WaveformResult(
                    [0.7] * config.WAVEFORM_PEAK_COUNT, 1.0, 48_000, current
                )

        with (
            mock.patch.object(main, "get_db_manager", return_value=self.manager),
            mock.patch(
                "core.audio_service.get_audio_service",
                return_value=FakeAudioService(),
            ),
            mock.patch.object(main, "_schedule_project_reconcile") as schedule,
            mock.patch("core.search_engine.reset_optimized_searcher"),
            mock.patch("core.ai_chat_service.reset_ai_chat_service"),
        ):
            await main._waveform_payload(str(self.audio), "default")

        for kind in ("audio_vector", "text_vector"):
            self.assertEqual(
                self.manager.get_artifact("default", file_uuid, kind)["state"],
                "stale",
            )
        schedule.assert_called_once_with(
            "default",
            ("audio_vector", "text_vector"),
            reason="source_changed",
        )


class ShadowRebuildContractTests(unittest.IsolatedAsyncioTestCase):
    class FakeActiveAudioIndexer:
        @staticmethod
        def get_manifest():
            return {
                "collection": "audio_embeddings",
                "metric": "cosine",
                "count": 0,
                "needs_rebuild": False,
                "engine_fingerprint": "clap:same-engine",
            }

    class FakeShadowAudioIndexer:
        def __init__(self, manager, file_ids, *, fail_path=None):
            self.manager = manager
            self.file_ids = tuple(file_ids)
            self.fail_path = str(fail_path) if fail_path else None
            self.successes = set()
            self.observed_states = []

        def add_single_audio(self, path, metadata):
            self.observed_states.append({
                file_id: self.manager.get_artifact(
                    "default", file_id, "audio_vector"
                )["state"]
                for file_id in self.file_ids
            })
            if self.fail_path and str(path) == self.fail_path:
                return False
            self.successes.add(str(metadata["file_id"]))
            return True

        def get_manifest(self):
            return {
                "collection": "audio_shadow_contract",
                "metric": "cosine",
                "count": len(self.successes),
                "needs_rebuild": False,
                "engine_fingerprint": "clap:same-engine",
                "model_id": "test/clap",
                "model_revision": "same-revision",
                "dimensions": 2,
                "preprocessing_version": "test-v1",
            }

    async def _run_audio_shadow(self, manager, shadow, *, use_start_endpoint=False):
        from fastapi import BackgroundTasks
        from core.index_lifecycle import activate_verified_shadows

        if use_start_endpoint:
            tasks = BackgroundTasks()
            with mock.patch.object(main, "get_db_manager", return_value=manager):
                response = await main._start_index_job(
                    "default", ["audio_vector"], "rebuild", tasks
                )
            job_id = response["job_id"]
        else:
            job_id = manager.create_job("default", "index_rebuild", 0)

        with (
            mock.patch.object(main, "get_db_manager", return_value=manager),
            mock.patch.object(
                main,
                "_get_active_audio_indexer",
                return_value=self.FakeActiveAudioIndexer(),
            ),
            mock.patch.object(main, "is_embedder_available", return_value=True),
            mock.patch("core.audio_service.get_audio_service", return_value=mock.Mock()),
            mock.patch(
                "core.index_lifecycle.create_shadow_indexer",
                return_value=shadow,
            ),
            mock.patch(
                "core.index_lifecycle.activate_verified_shadows",
                wraps=activate_verified_shadows,
            ) as activate,
            mock.patch("core.search_engine.reset_optimized_searcher"),
            mock.patch("core.ai_chat_service.reset_ai_chat_service"),
        ):
            await main._repair_index_task_locked(
                job_id, "default", ["audio_vector"], "rebuild"
            )
        return activate

    async def test_same_engine_shadow_rebuild_keeps_old_ready_searchable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = DatabaseManager(str(root / "same-engine-shadow.db"))
            file_ids = []
            for name in ("one.wav", "two.wav"):
                path = root / name
                path.write_bytes(name.encode("utf-8"))
                file_id = manager.upsert_file(
                    AudioFileRecord(path=str(path), filename=name), "default"
                )
                manager.set_artifact_state(
                    "default", file_id, "audio_vector", "ready",
                    engine_fingerprint="clap:same-engine",
                )
                file_ids.append(file_id)

            shadow = self.FakeShadowAudioIndexer(manager, file_ids)
            activate = await self._run_audio_shadow(
                manager, shadow, use_start_endpoint=True
            )

            self.assertTrue(shadow.observed_states)
            self.assertTrue(all(
                set(snapshot.values()) == {"ready"}
                for snapshot in shadow.observed_states
            ))
            self.assertTrue(all(
                manager.get_artifact("default", file_id, "audio_vector")["state"]
                == "ready"
                for file_id in file_ids
            ))
            expected_counts = activate.call_args.args[3]
            self.assertEqual(expected_counts, {"audio_vector": 2})
            manifest = manager.get_index_manifest("default", "audio_vector")
            self.assertEqual(manifest["collection_name"], "audio_shadow_contract")

    async def test_corrupt_audio_is_failed_but_does_not_block_shadow_activation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = DatabaseManager(str(root / "partial-shadow.db"))
            good = root / "good.wav"
            corrupt = root / "corrupt.wav"
            good.write_bytes(b"good")
            corrupt.write_bytes(b"corrupt")
            good_id = manager.upsert_file(
                AudioFileRecord(path=str(good), filename=good.name), "default"
            )
            corrupt_id = manager.upsert_file(
                AudioFileRecord(path=str(corrupt), filename=corrupt.name), "default"
            )
            manager.set_artifact_state(
                "default", good_id, "audio_vector", "ready",
                engine_fingerprint="clap:same-engine",
            )
            manager.set_artifact_state(
                "default", corrupt_id, "audio_vector", "failed",
                error_code="audio_probe_failed",
                error_message="corrupt fixture",
            )

            shadow = self.FakeShadowAudioIndexer(
                manager, [good_id, corrupt_id], fail_path=corrupt
            )
            activate = await self._run_audio_shadow(manager, shadow)

            expected_counts = activate.call_args.args[3]
            self.assertEqual(expected_counts, {"audio_vector": 1})
            self.assertEqual(shadow.successes, {good_id})
            self.assertEqual(
                manager.get_artifact("default", good_id, "audio_vector")["state"],
                "ready",
            )
            corrupt_artifact = manager.get_artifact(
                "default", corrupt_id, "audio_vector"
            )
            self.assertEqual(corrupt_artifact["state"], "failed")
            manifest = manager.get_index_manifest("default", "audio_vector")
            self.assertEqual(manifest["collection_name"], "audio_shadow_contract")


class APIContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_ready_queues_durable_vector_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = DatabaseManager(str(Path(directory) / "model-ready.db"))
            audio = Path(directory) / "pending.wav"
            audio.write_bytes(b"audio")
            manager.upsert_file(
                AudioFileRecord(path=str(audio), filename=audio.name), "default"
            )
            main._automatic_index_tasks.clear()
            main._shutting_down = False
            repair = mock.AsyncMock()
            with (
                mock.patch.object(main, "get_db_manager", return_value=manager),
                mock.patch.object(main, "_repair_index_task", repair),
            ):
                main._schedule_model_ready_reconcile()
                await asyncio.sleep(0)
                await asyncio.sleep(0)

            repair.assert_awaited_once()
            job_id, project_id, kinds, mode = repair.await_args.args
            self.assertEqual(project_id, "default")
            self.assertEqual(kinds, ["audio_vector", "text_vector"])
            self.assertEqual(mode, "reconcile")
            self.assertEqual(manager.get_job(job_id)["kind"], "index_auto_model_ready")

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

        class FakeWebSocketManager:
            def __init__(self) -> None:
                self.registrations = []

            def register_task(self, task_id: str, client_id: str) -> None:
                self.registrations.append((task_id, client_id))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "folder"
            folder.mkdir()
            manager = DatabaseManager(str(root / "imports.db"))
            tasks = BackgroundTasks()
            ws_manager = FakeWebSocketManager()
            with (
                mock.patch.object(main, "get_db_manager", return_value=manager),
                mock.patch.object(main, "get_ws_manager", return_value=ws_manager),
            ):
                response = await main.import_project_files(
                    "default",
                    schemas.ProjectImportRequest(
                        folder_path=str(folder), client_id="renderer-a"
                    ),
                    tasks,
                )

        self.assertEqual(response["project_id"], "default")
        self.assertEqual(response["job_id"], response["task_id"])
        self.assertEqual(ws_manager.registrations, [(response["job_id"], "renderer-a")])
        self.assertEqual(len(tasks.tasks), 1)
        self.assertEqual(tasks.tasks[0].kwargs["project_id"], "default")

    @unittest.skipIf(os.name == "nt", "POSIX symlink import regression")
    async def test_fresh_folder_import_merges_posix_alias_before_probe(self) -> None:
        class FakeWebSocketManager:
            active_connections = {}

            def get_connection_count(self) -> int:
                return 0

            def is_task_cancelled(self, _task_id: str) -> bool:
                return False

            def unregister_task(self, _task_id: str) -> None:
                return None

            async def send_scan_status(self, *_args, **_kwargs) -> None:
                return None

            async def send_scan_log(self, *_args, **_kwargs) -> None:
                return None

            async def send_folder_structure(self, *_args, **_kwargs) -> None:
                return None

            async def send_scan_progress(self, *_args, **_kwargs) -> None:
                return None

            async def send_scan_complete(self, *_args, **_kwargs) -> None:
                return None

            async def send_scan_error(self, *_args, **_kwargs) -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_folder = root / "real-library"
            alias_folder = root / "alias-library"
            real_folder.mkdir()
            alias_folder.symlink_to(real_folder, target_is_directory=True)
            fixture = REPO_ROOT / "tests" / "build" / "fixtures" / "tone.wav"
            audio = real_folder / "tone.wav"
            shutil.copy2(fixture, audio)
            manager = DatabaseManager(str(root / "fresh-import.db"))
            ws_manager = FakeWebSocketManager()

            async def run_import(folder: Path) -> str:
                job_id = manager.create_job("default", "folder_import")
                with (
                    mock.patch.object(main, "get_db_manager", return_value=manager),
                    mock.patch.object(main, "get_ws_manager", return_value=ws_manager),
                    mock.patch.object(main, "is_embedder_available", return_value=False),
                    mock.patch.object(
                        main,
                        "_index_text_metadata_artifact",
                        new=mock.AsyncMock(return_value=False),
                    ),
                    mock.patch.object(
                        main,
                        "_bump_project_index_revision",
                        new=mock.AsyncMock(return_value=None),
                    ),
                ):
                    await main._scan_and_import_task_locked(
                        job_id, str(folder), True, "test-client", "default"
                    )
                self.assertEqual(manager.get_job(job_id)["state"], "completed")
                return job_id

            first_job = await run_import(alias_folder)
            first_record = manager.get_files_by_project("default")[0]
            second_job = await run_import(real_folder)
            records = manager.get_files_by_project("default")

            self.assertEqual(manager.get_job(first_job)["processed"], 1)
            self.assertEqual(manager.get_job(second_job)["processed"], 1)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].file_uuid, first_record.file_uuid)
            self.assertEqual(records[0].path, str(audio.resolve()))
            self.assertGreater(records[0].duration, 0)
            self.assertEqual(len(records[0].get_peaks() or []), 2000)
            self.assertEqual(
                manager.get_artifact(
                    "default", records[0].file_uuid, "waveform"
                )["state"],
                "ready",
            )
            with manager.get_cursor() as cursor:
                artifact_count = cursor.execute(
                    "SELECT COUNT(*) FROM file_artifacts WHERE file_uuid=?",
                    (records[0].file_uuid,),
                ).fetchone()[0]
            self.assertEqual(artifact_count, 3)
            self.assertEqual(len(manager.get_imported_folder_mappings("default")), 1)

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

    async def test_logical_folder_metadata_uses_longest_mapping_and_ucs_terms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = DatabaseManager(str(root / "metadata.db"))
            broad = root / "library"
            nested = broad / "weapons"
            audio = nested / "explosion_hit.wav"
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b"audio")
            file_uuid = manager.upsert_file(
                AudioFileRecord(path=str(audio), filename=audio.name), "default"
            )
            manager.create_user_folder("broad", "default", "General")
            manager.create_user_folder(
                "nested", "default", "Weapons", "Metal impacts"
            )
            manager.add_imported_folder_mapping(
                "default", str(broad), broad.name, "broad"
            )
            manager.add_imported_folder_mapping(
                "default", str(nested), nested.name, "nested"
            )

            with (
                mock.patch.object(main, "get_db_manager", return_value=manager),
                mock.patch(
                    "core.ucs_keywords.get_ucs_keywords",
                    return_value={"爆炸": ["explosion", "blast"]},
                ),
            ):
                payload = main._build_text_metadata_payload(
                    "default",
                    file_uuid,
                    {
                        "file_path": str(audio),
                        "filename": audio.name,
                        "tags": ["metal"],
                    },
                    tags=["metal"],
                )

        self.assertEqual(payload["logical_folder"], "Weapons")
        self.assertEqual(payload["description"], "Metal impacts")
        self.assertEqual(payload["ucs_categories"], ["爆炸"])
        self.assertIn("explosion", payload["ucs_keywords"])
        self.assertIn("爆炸", payload["ucs_category"])

    async def test_folder_rename_stales_text_only_and_queues_reconcile(self) -> None:
        from fastapi import BackgroundTasks

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = DatabaseManager(str(root / "folder-mutation.db"))
            audio = root / "mapped" / "hit.wav"
            audio.parent.mkdir()
            audio.write_bytes(b"audio")
            file_uuid = manager.upsert_file(
                AudioFileRecord(path=str(audio), filename=audio.name), "default"
            )
            for kind in ("audio_vector", "text_vector"):
                manager.set_artifact_state("default", file_uuid, kind, "ready")
            manager.create_user_folder("folder_fx", "default", "FX", "Old")
            manager.add_imported_folder_mapping(
                "default", str(audio.parent), "mapped", "folder_fx"
            )
            tasks = BackgroundTasks()
            main._project_index_locks.pop("default", None)
            with (
                mock.patch.object(main, "get_db_manager", return_value=manager),
                mock.patch("core.search_engine.reset_optimized_searcher"),
                mock.patch("core.ai_chat_service.reset_ai_chat_service"),
            ):
                response = await main.update_user_folder(
                    "default",
                    "folder_fx",
                    main.UpdateFolderRequest(name="Renamed", description="New"),
                    tasks,
                )

            self.assertEqual(response["affected_files"], 1)
            self.assertIsNotNone(response["reindex_job_id"])
            self.assertEqual(len(tasks.tasks), 1)
            self.assertEqual(
                manager.get_artifact("default", file_uuid, "text_vector")["state"],
                "stale",
            )
            self.assertEqual(
                manager.get_artifact("default", file_uuid, "audio_vector")["state"],
                "ready",
            )
            manifest = manager.get_index_manifest("default", "text_vector")
            self.assertEqual(manifest["revision"], 1)
            self.assertEqual(manifest["state"], "stale")


class SearchContractTests(unittest.TestCase):
    def test_user_tag_arrays_participate_in_keyword_search(self) -> None:
        searcher = object.__new__(OptimizedAudioSearcher)
        score, level = searcher._keyword_match_score(
            "rain",
            "neutral.wav",
            {"metadata_tags": json.dumps(["rain", "weather"])},
        )
        self.assertGreater(score, 0.0)
        self.assertIn(level, {"exact", "partial", "weak"})


if __name__ == "__main__":
    unittest.main()
