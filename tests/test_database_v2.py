from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core import database as database_module
from core.database import (
    AudioFileRecord,
    DatabaseManager,
    canonical_path_is_within,
    canonicalize_path,
    is_safe_project_id,
)


database_module.logger = logging.getLogger("soundbot.database.tests")


class DatabaseV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_fresh_database_creates_artifact_state_and_metadata_page(self) -> None:
        manager = DatabaseManager(str(self.root / "fresh.db"))
        audio = self.root / "中文 %_ tone.wav"
        audio.write_bytes(b"RIFF-test")
        record = AudioFileRecord(path=str(audio), filename=audio.name)

        file_uuid = manager.upsert_file(record, "default")

        self.assertIsNotNone(file_uuid)
        counts = manager.get_artifact_counts("default")
        self.assertEqual(counts["waveform"]["pending"], 1)
        self.assertEqual(counts["audio_vector"]["pending"], 1)
        self.assertEqual(counts["text_vector"]["pending"], 1)
        files, cursor = manager.get_files_cursor_page("default")
        self.assertEqual(len(files), 1)
        self.assertNotIn("peaks", files[0])
        self.assertIsNone(cursor)

    def test_legacy_database_migrates_once_and_keeps_backup(self) -> None:
        database_path = self.root / "legacy.db"
        audio = self.root / "legacy.wav"
        audio.write_bytes(b"RIFF")
        connection = sqlite3.connect(database_path)
        connection.executescript("""
            CREATE TABLE files (
                path TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                duration REAL DEFAULT 0,
                sample_rate INTEGER DEFAULT 0,
                channels INTEGER DEFAULT 0,
                file_size INTEGER DEFAULT 0,
                peaks_json TEXT,
                tags TEXT DEFAULT '[]',
                created_at TEXT,
                updated_at TEXT
            );
        """)
        connection.execute(
            "INSERT INTO files(path, filename, peaks_json, tags) VALUES (?, ?, ?, ?)",
            (str(audio), audio.name, "[]", json.dumps(["legacy-tag"])),
        )
        connection.commit()
        connection.close()

        manager = DatabaseManager(str(database_path))
        migrated = manager.get_file(str(audio), "default")
        backup = Path(f"{database_path}.pre-v0-to-v3.bak")

        self.assertIsNotNone(migrated)
        self.assertEqual(migrated.get_tags(), ["legacy-tag"])
        self.assertTrue(backup.exists())
        self.assertEqual(
            manager.get_artifact_counts("default")["waveform"]["pending"], 1
        )
        backup_mtime = backup.stat().st_mtime_ns
        DatabaseManager(str(database_path))
        self.assertEqual(backup.stat().st_mtime_ns, backup_mtime)

    def test_failed_migration_restores_original_and_keeps_backup(self) -> None:
        database_path = self.root / "failed-migration.db"
        connection = sqlite3.connect(database_path)
        connection.execute(
            "CREATE TABLE files(path TEXT PRIMARY KEY, filename TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO files(path, filename) VALUES (?, ?)",
            ("original.wav", "original.wav"),
        )
        connection.commit()
        connection.close()

        def fail_after_mutation(manager, cursor, projects_exist, current_version):
            cursor.execute("DROP TABLE files")
            raise sqlite3.OperationalError("simulated migration failure")

        with (
            mock.patch.object(
                DatabaseManager,
                "_migrate_db_v2",
                autospec=True,
                side_effect=fail_after_mutation,
            ),
            self.assertRaises(RuntimeError),
        ):
            DatabaseManager(str(database_path))

        restored = sqlite3.connect(database_path)
        try:
            row = restored.execute(
                "SELECT path, filename FROM files"
            ).fetchone()
        finally:
            restored.close()
        self.assertEqual(row, ("original.wav", "original.wav"))
        self.assertTrue(Path(f"{database_path}.pre-v0-to-v3.bak").is_file())

    def test_migration_schema_and_backfill_roll_back_as_one_transaction(self) -> None:
        database_path = self.root / "atomic-migration.db"
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE files(path TEXT PRIMARY KEY, filename TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO files(path, filename) VALUES (?, ?)",
            ("original.wav", "original.wav"),
        )
        connection.execute("""
            CREATE TABLE file_artifacts (
                project_id TEXT NOT NULL,
                file_uuid TEXT NOT NULL,
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                source_fingerprint TEXT,
                engine_fingerprint TEXT,
                error_code TEXT,
                error_message TEXT,
                updated_at TEXT,
                PRIMARY KEY (project_id, file_uuid, kind)
            )
        """)
        connection.execute("""
            CREATE TRIGGER reject_migration_artifact
            BEFORE INSERT ON file_artifacts
            BEGIN
                SELECT RAISE(ABORT, 'simulated artifact backfill failure');
            END
        """)
        connection.commit()

        manager = object.__new__(DatabaseManager)
        cursor = connection.cursor()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                manager._migrate_db_v2(cursor, False, 0)
            self.assertTrue(connection.in_transaction)
            connection.rollback()

            columns = [
                row[1] for row in connection.execute("PRAGMA table_info(files)")
            ]
            row = connection.execute(
                "SELECT path, filename FROM files"
            ).fetchone()
            schema_meta_exists = connection.execute("""
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='schema_meta'
            """).fetchone()
            projects_exists = connection.execute("""
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='projects'
            """).fetchone()
        finally:
            cursor.close()
            connection.close()

        self.assertEqual(columns, ["path", "filename"])
        self.assertEqual(tuple(row), ("original.wav", "original.wav"))
        self.assertIsNone(schema_meta_exists)
        self.assertIsNone(projects_exists)

    def test_windows_path_key_is_case_and_separator_stable(self) -> None:
        first = canonicalize_path(r"C:\Audio\中文\Hit.WAV")
        second = canonicalize_path(r"c:/audio/中文/hit.wav")
        self.assertEqual(first, second)
        self.assertEqual(first, "c:/audio/中文/hit.wav")
        self.assertEqual(
            canonicalize_path(r"\\Server\Share\FX\Hit.WAV"),
            "//server/share/fx/hit.wav",
        )
        self.assertEqual(
            canonicalize_path(r"\\?\C:\Long\Hit.WAV"),
            "//?/c:/long/hit.wav",
        )
        self.assertEqual(
            canonicalize_path(r"\\?\UNC\Server\Share\Hit.WAV"),
            "//?/unc/server/share/hit.wav",
        )

    @unittest.skipIf(os.name == "nt", "POSIX symlink identity test")
    def test_posix_symlink_alias_upserts_one_physical_record(self) -> None:
        real_folder = self.root / "real"
        alias_folder = self.root / "alias"
        real_folder.mkdir()
        alias_folder.symlink_to(real_folder, target_is_directory=True)
        real_audio = real_folder / "tone.wav"
        alias_audio = alias_folder / "tone.wav"
        real_audio.write_bytes(b"RIFF-alias")
        manager = DatabaseManager(str(self.root / "alias-upsert.db"))

        first_uuid = manager.upsert_file(
            AudioFileRecord(path=str(alias_audio), filename=alias_audio.name), "default"
        )
        second_uuid = manager.upsert_file(
            AudioFileRecord(
                path=str(real_audio), filename=real_audio.name, duration=0.35
            ),
            "default",
        )

        self.assertEqual(canonicalize_path(str(alias_audio)), str(real_audio.resolve()))
        self.assertEqual(first_uuid, second_uuid)
        self.assertEqual(manager.get_file_count("default"), 1)
        self.assertEqual(manager.get_file(str(alias_audio), "default").duration, 0.35)
        self.assertTrue(
            manager.add_imported_folder_mapping(
                "default", str(alias_folder), "alias", file_count=1
            )
        )
        self.assertTrue(
            manager.add_imported_folder_mapping(
                "default", str(real_folder), "real", file_count=1
            )
        )
        mappings = manager.get_imported_folder_mappings("default")
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["folder_name"], "real")

    @unittest.skipIf(os.name == "nt", "POSIX symlink migration test")
    def test_v2_to_v3_merges_alias_rows_without_resetting_ready_artifacts(self) -> None:
        real_folder = self.root / "migration-real"
        alias_folder = self.root / "migration-alias"
        real_folder.mkdir()
        alias_folder.symlink_to(real_folder, target_is_directory=True)
        real_audio = real_folder / "tone.wav"
        alias_audio = alias_folder / "tone.wav"
        real_audio.write_bytes(b"RIFF-alias")
        database_path = self.root / "alias-v2.db"
        connection = sqlite3.connect(database_path)
        connection.executescript(database_module.CREATE_TABLE_SQL)
        connection.execute(
            "INSERT INTO schema_meta(key, value) VALUES ('schema_version', '2')"
        )
        legacy_real_key = os.path.normpath(os.path.abspath(real_audio))
        legacy_alias_key = os.path.normpath(os.path.abspath(alias_audio))
        connection.execute("""
            INSERT INTO files
            (file_uuid, path, canonical_path, project_id, filename, file_size,
             source_fingerprint, tags)
            VALUES ('stub-uuid', ?, ?, 'default', 'tone.wav', ?, 'same-source', ?)
        """, (
            str(real_audio), legacy_real_key, real_audio.stat().st_size,
            json.dumps(["stub-tag"]),
        ))
        peaks = json.dumps([0.5] * 2000)
        connection.execute("""
            INSERT INTO files
            (file_uuid, path, canonical_path, project_id, filename, duration,
             sample_rate, channels, file_size, source_fingerprint, peaks_json,
             waveform_fingerprint, waveform_version, tags)
            VALUES ('rich-uuid', ?, ?, 'default', 'tone.wav', 0.35,
                    48000, 1, ?, 'same-source', ?, 'waveform-v2', 2, ?)
        """, (
            str(alias_audio), legacy_alias_key, real_audio.stat().st_size,
            peaks, json.dumps(["rich-tag"]),
        ))
        for file_uuid in ("stub-uuid", "rich-uuid"):
            for kind in database_module.ARTIFACT_KINDS:
                state = (
                    "ready"
                    if file_uuid == "rich-uuid" or kind == "audio_vector"
                    else "pending"
                )
                connection.execute("""
                    INSERT INTO file_artifacts
                    (project_id, file_uuid, kind, state, source_fingerprint)
                    VALUES ('default', ?, ?, ?, 'same-source')
                """, (file_uuid, kind, state))
        for folder_path in (str(real_folder), str(alias_folder)):
            connection.execute("""
                INSERT INTO imported_folder_mappings
                (project_id, folder_path, folder_name)
                VALUES ('default', ?, 'library')
            """, (folder_path,))
        connection.execute("""
            INSERT INTO index_manifests
            (project_id, kind, collection_name, revision, state)
            VALUES ('default', 'audio_vector', 'audio_embeddings', 4, 'ready')
        """)
        connection.commit()
        connection.close()

        manager = DatabaseManager(str(database_path))
        records = manager.get_files_by_project("default")

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].file_uuid, "rich-uuid")
        self.assertEqual(records[0].path, str(real_audio.resolve()))
        self.assertEqual(records[0].duration, 0.35)
        self.assertEqual(records[0].get_peaks(), [0.5] * 2000)
        self.assertEqual(records[0].get_tags(), ["rich-tag", "stub-tag"])
        for kind in database_module.ARTIFACT_KINDS:
            self.assertEqual(
                manager.get_artifact("default", "rich-uuid", kind)["state"],
                "ready",
            )
        self.assertEqual(len(manager.get_imported_folder_mappings("default")), 1)
        audio_manifest = manager.get_index_manifest("default", "audio_vector")
        self.assertEqual(audio_manifest["state"], "stale")
        self.assertEqual(audio_manifest["revision"], 5)
        self.assertTrue(Path(f"{database_path}.pre-v2-to-v3.bak").is_file())
        with manager.get_cursor() as cursor:
            version = cursor.execute(
                "SELECT value FROM schema_meta WHERE key='schema_version'"
            ).fetchone()[0]
        self.assertEqual(version, "3")

    def test_windows_aware_ancestor_check_uses_separator_boundary(self) -> None:
        self.assertTrue(
            canonical_path_is_within(
                r"c:/AUDIO/中文/Child/hit.wav", r"C:\audio\中文"
            )
        )
        self.assertFalse(
            canonical_path_is_within(
                r"C:\audio\中文-other\hit.wav", r"c:/audio/中文"
            )
        )

    def test_project_ids_keep_safe_legacy_values_and_reject_windows_devices(self) -> None:
        self.assertTrue(is_safe_project_id("legacy.project-01"))
        for value in ("CON", "con.txt", "LPT1", "project.", "../escape"):
            self.assertFalse(is_safe_project_id(value), value)

    def test_source_change_marks_waveform_and_audio_stale(self) -> None:
        manager = DatabaseManager(str(self.root / "stale.db"))
        audio = self.root / "tone.wav"
        audio.write_bytes(b"first")
        file_uuid = manager.upsert_file(
            AudioFileRecord(path=str(audio), filename=audio.name), "default"
        )
        manager.set_artifact_state("default", file_uuid, "waveform", "ready")
        manager.set_artifact_state("default", file_uuid, "audio_vector", "ready")
        audio.write_bytes(b"second-content")

        manager.upsert_file(
            AudioFileRecord(path=str(audio), filename=audio.name), "default"
        )

        counts = manager.get_artifact_counts("default")
        self.assertEqual(counts["waveform"]["stale"], 1)
        self.assertEqual(counts["audio_vector"]["stale"], 1)

    def test_source_change_without_new_peaks_never_reuses_old_waveform(self) -> None:
        manager = DatabaseManager(str(self.root / "waveform-invalidation.db"))
        audio = self.root / "tone.wav"
        audio.write_bytes(b"first")
        old_peaks = json.dumps([0.25] * 2000)
        file_uuid = manager.upsert_file(
            AudioFileRecord(
                path=str(audio),
                filename=audio.name,
                peaks_json=old_peaks,
                waveform_fingerprint="old-waveform",
                waveform_version=2,
            ),
            "default",
        )
        self.assertEqual(manager.get_file(str(audio), "default").get_peaks(), [0.25] * 2000)

        audio.write_bytes(b"second-content")
        manager.upsert_file(
            AudioFileRecord(path=str(audio), filename=audio.name), "default"
        )

        updated = manager.get_file(str(audio), "default")
        artifact = manager.get_artifact("default", file_uuid, "waveform")
        self.assertIsNone(updated.get_peaks())
        self.assertIsNone(updated.waveform_fingerprint)
        self.assertIsNone(updated.waveform_version)
        self.assertEqual(artifact["state"], "stale")

    def test_source_change_with_new_peaks_finishes_waveform_artifact(self) -> None:
        manager = DatabaseManager(str(self.root / "waveform-refresh.db"))
        audio = self.root / "tone.wav"
        audio.write_bytes(b"first")
        file_uuid = manager.upsert_file(
            AudioFileRecord(path=str(audio), filename=audio.name), "default"
        )

        audio.write_bytes(b"second-content")
        new_peaks = json.dumps([0.75] * 2000)
        manager.upsert_file(
            AudioFileRecord(
                path=str(audio),
                filename=audio.name,
                peaks_json=new_peaks,
                waveform_fingerprint="new-waveform",
                waveform_version=2,
            ),
            "default",
        )

        artifact = manager.get_artifact("default", file_uuid, "waveform")
        self.assertEqual(artifact["state"], "ready")
        self.assertEqual(artifact["engine_fingerprint"], "new-waveform")

    def test_lazy_waveform_refresh_invalidates_both_vector_artifacts(self) -> None:
        manager = DatabaseManager(str(self.root / "lazy-waveform-refresh.db"))
        audio = self.root / "tone.wav"
        audio.write_bytes(b"first")
        file_uuid = manager.upsert_file(
            AudioFileRecord(path=str(audio), filename=audio.name), "default"
        )
        for kind in ("audio_vector", "text_vector"):
            manager.set_artifact_state(
                "default", file_uuid, kind, "ready", source_fingerprint="old"
            )

        audio.write_bytes(b"second-content")
        new_source = database_module.make_source_fingerprint(str(audio))
        self.assertTrue(
            manager.update_peaks(
                str(audio),
                [0.5] * 2000,
                "default",
                source_fingerprint=new_source,
                waveform_fingerprint="waveform:new",
                waveform_version=2,
            )
        )

        self.assertEqual(
            manager.get_artifact("default", file_uuid, "waveform")["state"],
            "ready",
        )
        for kind in ("audio_vector", "text_vector"):
            artifact = manager.get_artifact("default", file_uuid, kind)
            self.assertEqual(artifact["state"], "stale")
            self.assertEqual(artifact["source_fingerprint"], new_source)

    def test_folder_query_escapes_sql_wildcards(self) -> None:
        manager = DatabaseManager(str(self.root / "folder.db"))
        target = self.root / "100%_fx" / "hit.wav"
        sibling = self.root / "100XXfx" / "other.wav"
        target.parent.mkdir()
        sibling.parent.mkdir()
        target.write_bytes(b"target")
        sibling.write_bytes(b"sibling")
        manager.upsert_file(AudioFileRecord(path=str(target), filename=target.name))
        manager.upsert_file(AudioFileRecord(path=str(sibling), filename=sibling.name))

        files = manager.get_files_by_folder(str(target.parent), "default")

        self.assertEqual([record.filename for record in files], ["hit.wav"])

    def test_folder_metadata_invalidation_matches_literal_paths_only(self) -> None:
        manager = DatabaseManager(str(self.root / "folder-artifacts.db"))
        target = self.root / "100%_fx" / "nested" / "hit.wav"
        sibling = self.root / "100XXfx" / "other.wav"
        prefix_sibling = self.root / "100%_fx-extra" / "third.wav"
        for path in (target, sibling, prefix_sibling):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"audio")
            file_uuid = manager.upsert_file(
                AudioFileRecord(path=str(path), filename=path.name)
            )
            manager.set_artifact_state(
                "default", file_uuid, "text_vector", "ready"
            )

        affected = manager.mark_text_artifacts_for_folders(
            "default", [str(self.root / "100%_fx")]
        )

        self.assertEqual(affected, 1)
        self.assertEqual(
            manager.get_artifact(
                "default",
                manager.get_file(str(target), "default").file_uuid,
                "text_vector",
            )["state"],
            "stale",
        )
        for path in (sibling, prefix_sibling):
            self.assertEqual(
                manager.get_artifact(
                    "default",
                    manager.get_file(str(path), "default").file_uuid,
                    "text_vector",
                )["state"],
                "ready",
            )

    def test_startup_recovers_interrupted_artifacts_and_jobs(self) -> None:
        database_path = self.root / "recovery.db"
        manager = DatabaseManager(str(database_path))
        audio = self.root / "interrupted.wav"
        audio.write_bytes(b"RIFF")
        file_uuid = manager.upsert_file(
            AudioFileRecord(path=str(audio), filename=audio.name), "default"
        )
        manager.set_artifact_state(
            "default", file_uuid, "audio_vector", "processing"
        )
        job_id = manager.create_job("default", "index_reconcile", 1)
        manager.update_job(job_id, state="running", stage="audio_vector")

        recovered = DatabaseManager(str(database_path))

        artifact = recovered.get_artifact("default", file_uuid, "audio_vector")
        job = recovered.get_job(job_id)
        self.assertEqual(artifact["state"], "pending")
        self.assertEqual(artifact["error_code"], "interrupted")
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error_code"], "job_interrupted")


if __name__ == "__main__":
    unittest.main()
