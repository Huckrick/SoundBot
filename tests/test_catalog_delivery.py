"""Exercise the core library and host delivery without vector dependencies."""

from __future__ import annotations

import io
import asyncio
import json
import math
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import main
from core import catalog, delivery, editing, vst, wwise
from core.database import DatabaseManager, get_db_manager, reset_db_manager
from core.media_api import ClipRequest


def audio_file(path: Path, frequency: int = 220) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        handle.writeframes(b"".join(
            struct.pack("<h", round(8000 * math.sin(2 * math.pi * frequency * frame / 48000)))
            for frame in range(48000)
        ))
    return path


def tagged_wave(path):
    from mutagen.id3 import APIC, TIT2, TPE1, TALB, TXXX
    from mutagen.wave import WAVE
    from PIL import Image

    audio_file(path, 440)
    bext = bytearray(602)
    bext[:11] = b"Room tone 1"
    bext[256:266] = b"Stage Crew"
    struct.pack_into("<QH", bext, 338, 96000, 2)
    struct.pack_into("<5h", bext, 412, -2300, 500, -100, 0x7fff, 0x7fff)
    ixml = b"<BWFXML><PROJECT>Studio</PROJECT><SCENE>14</SCENE><TAKE>2</TAKE></BWFXML>"
    cue = struct.pack("<III4sIII", 1, 7, 0, b"data", 0, 0, 24000)
    original = path.read_bytes()
    payload = original[8:]
    for name, data in [(b"bext", bext), (b"iXML", ixml), (b"cue ", cue)]:
        payload += name + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")
    path.write_bytes(b"RIFF" + struct.pack("<I", len(payload)) + payload)
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text=["Room tone"]))
    audio.tags.add(TPE1(encoding=3, text=["Field Unit"]))
    audio.tags.add(TALB(encoding=3, text=["Studio Sessions"]))
    audio.tags.add(TXXX(encoding=3, desc="MICROPHONE", text=["Stereo pair"]))
    for role, name, size, color in [
        (4, "Back artwork", (80, 80), "#9e483a"), (3, "Front artwork", (1250, 900), "#336a58")
    ]:
        cover = io.BytesIO()
        Image.new("RGB", size, color).save(cover, "WEBP")
        audio.tags.add(APIC(encoding=3, mime="image/webp", type=role, desc=name, data=cover.getvalue()))
    audio.save()
    return path


class CoreDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.environment = patch.dict(os.environ, {
            "SOUNDBOT_USER_DATA_DIR": str(self.root / "data"),
            "SOUNDBOT_IPC_TOKEN": "test-desktop",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        reset_db_manager()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"X-SoundBot-IPC": "test-desktop"}

    def import_folder(self, directory):
        response = self.client.post("/api/v1/projects/default/imports", json={"folder_path": str(directory)})
        self.assertEqual(response.status_code, 200, response.text)
        return self.client.get(f"/api/v1/jobs/{response.json()['job_id']}").json()

    def items(self, **params):
        response = self.client.get("/api/v1/projects/default/catalog", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_remove_last_library_keeps_sources_and_stays_empty_after_reopen(self):
        source = audio_file(self.root / "source.wav")
        self.client.post("/api/v1/projects/default/imports", json={"file_paths": [str(source)]}).raise_for_status()
        file_id = self.items()["files"][0]["id"]
        self.client.delete("/api/v1/projects/default").raise_for_status()
        self.assertEqual(self.client.get("/api/v1/projects").json()["projects"], [])
        self.assertEqual(self.client.get(f"/api/v1/files/{file_id}/source?project_id=default").status_code, 404)
        self.assertTrue(source.is_file())
        reset_db_manager()
        self.assertEqual(get_db_manager().get_all_projects(), [])
        created = self.client.post("/api/v1/projects", json={"name": "New library"}).json()["project_id"]
        self.assertEqual(self.client.get(f"/api/v1/projects/{created}").json()["file_count"], 0)

    def test_active_jobs_and_ranged_native_source(self):
        source = audio_file(self.root / "source.wav")
        job_id = catalog.create_import(get_db_manager(), "default", paths=[str(source)])
        (job,) = self.client.get("/api/v1/jobs/active").json()
        self.assertEqual((job["id"], job["state"], job["expected"], job["processed"]), (job_id, "pending", 1, 0))
        catalog.run_import(get_db_manager(), job_id)
        self.assertEqual(self.client.get("/api/v1/jobs/active").json(), [])
        file_id = self.items()["files"][0]["id"]
        with patch.dict(os.environ, {"SOUNDBOT_REQUIRE_DESKTOP_TOKEN": "1"}):
            route = f"/api/v1/files/{file_id}/source?project_id=default"
            self.assertEqual(self.client.get(route).status_code, 403)
            response = self.client.get(route, headers={**self.headers, "Origin": "null", "Range": "bytes=0-11"})
        self.assertEqual(response.status_code, 206, response.text)
        self.assertEqual(response.headers["access-control-allow-origin"], "null")
        self.assertEqual(response.content, source.read_bytes()[:12])

    def test_overlap_crossfade_matches_full_mix_when_seeking(self):
        import numpy as np

        def read_region(path, offset, frames, rate, channels):
            yield np.full((frames, channels), 0.2 if path.name == "a.wav" else 0.6, dtype=np.float32)

        rate = 8000
        clips = (editing.Clip(Path("a.wav"), 0, 1), editing.Clip(Path("b.wav"), 0.5, 1))
        with patch.object(editing, "read_region", read_region):
            track = editing.Track(clips, auto_crossfade=True)
            whole = np.concatenate(list(editing.mix([track], rate, 1, 1.5)))[:, 0]
            sought = np.concatenate(list(editing.mix([track], rate, 1, 1.5, start=0.75)))[:, 0]
            unblended = np.concatenate(list(editing.mix([editing.Track(clips)], rate, 1, 1.5)))[:, 0]
        self.assertAlmostEqual(float(whole[4000]), 0.2, places=5)
        self.assertAlmostEqual(float(whole[7999]), 0.6, places=5)
        self.assertAlmostEqual(float(whole[6000]), 0.4, places=3)
        self.assertAlmostEqual(float(unblended[6000]), 0.8, places=5)
        np.testing.assert_allclose(sought, whole[6000:])

    def test_timeline_curves_and_pan_match_when_seeking(self):
        import numpy as np

        def read_region(path, offset, frames, rate, channels):
            yield np.full((frames, channels), 0.2 if path.name == "a.wav" else 0.4, dtype=np.float32)

        rate = 8000
        fade = editing.Clip(Path("a.wav"), 0, 2, fade_in=1, fade_out=1,
                            fade_in_curve="ease_in", fade_out_curve="ease_out",
                            pan=((0, -1), (0.5, 0), (1, 1)))
        with patch.object(editing, "read_region", read_region):
            track = editing.Track((fade,), channels=1)
            whole = np.concatenate(list(editing.mix([track], rate, 2, 2)))
            sought = np.concatenate(list(editing.mix([track], rate, 2, 2, start=1.05)))
            mono = np.concatenate(list(editing.mix([track], rate, 1, 2)))
            clips = (editing.Clip(Path("a.wav"), 0, 1), editing.Clip(Path("b.wav"), 0.5, 1))
            overlap = {}
            for shape in ("linear", "equal_power", "ease_in", "ease_out", "smooth"):
                pair = editing.Track(clips, auto_crossfade=True, crossfade_curve=shape)
                overlap[shape] = np.concatenate(list(editing.mix([pair], rate, 1, 1.5)))[:, 0]
        np.testing.assert_allclose(sought, whole[8400:])
        self.assertAlmostEqual(float(mono[4000, 0]), 0.05, places=5)
        self.assertAlmostEqual(float(mono[12000, 0]), 0.15, places=4)
        self.assertGreater(whole[4000, 0], whole[4000, 1])
        self.assertGreater(whole[12000, 1], whole[12000, 0])
        self.assertGreater(overlap["equal_power"][6000], overlap["linear"][6000])
        self.assertLess(overlap["ease_in"][6000], overlap["ease_out"][6000])
        self.assertLess(overlap["smooth"][5000], overlap["linear"][5000])
        reversed_clips = tuple(reversed(clips))
        track = editing.Track(reversed_clips, auto_crossfade=True,
                              crossfade_overrides=((1, 0, "off"),))
        with patch.object(editing, "read_region", read_region):
            off = np.concatenate(list(editing.mix([track], rate, 1, 1.5)))[:, 0]
        self.assertAlmostEqual(float(off[6000]), 0.6, places=5)
        with self.assertRaises(ValueError):
            ClipRequest(duration=1, pan=[(0.5, 0), (0.5, 1)])

    def test_installed_vst_discovery_never_loads_code(self):
        base = self.root / "standard"
        bundle = base / "Vendor" / "Room.vst3"
        binary = bundle / "Contents" / "x86_64-win" / "Room.vst3"
        binary.parent.mkdir(parents=True)
        binary.touch()
        (bundle / "Contents" / "MacOS").mkdir()
        candidate = "Not a plug-in.txt"
        (base / candidate).touch()
        with patch.object(vst, "_plugin_roots", return_value=(base, base)), \
                patch.object(vst, "available", return_value=True), patch.object(vst, "register") as register:
            catalog = self.client.get("/api/v1/effects/plugins").raise_for_status().json()
            (plugin,) = catalog["plugins"]
            self.assertEqual(plugin["name"], "Room")
            url = f"/api/v1/effects/plugins/{plugin['id']}/load"
            self.assertEqual(self.client.post(url).status_code, 403)
            register.assert_not_called()
            register.return_value = {"id": "registered", "name": "Room", "parameters": []}
            self.assertEqual(self.client.post(url, headers=self.headers).raise_for_status().json()["id"], "registered")
            register.assert_called_once_with(bundle.resolve())
            self.assertEqual(self.client.post("/api/v1/effects/plugins/invalid/load", headers=self.headers).status_code, 422)

    def test_native_editor_state_is_bound_to_registered_plugin(self):
        from contextlib import contextmanager
        from unittest.mock import Mock

        plugin_id = "a" * 32
        state = vst._encode_state(plugin_id, b"old state")
        with self.assertRaises(ValueError):
            vst._decode_state("b" * 32, state)
        with self.assertRaises(ValueError):
            vst._decode_state(plugin_id, state[:-1] + "!")

        connection = Mock()
        connection.poll.return_value = True
        connection.recv_bytes.return_value = b"Snew state"

        @contextmanager
        def host(path, *, values, state, editor):
            self.assertEqual(path, "fixture.vst3")
            self.assertIsNone(values)
            self.assertEqual(state, b"old state")
            self.assertTrue(editor)
            yield connection, {}

        route = f"/api/v1/effects/plugins/{plugin_id}/edit"
        self.assertEqual(self.client.post(route, json={"state": state}).status_code, 403)
        with patch.object(vst, "_registered_path", return_value="fixture.vst3"), patch.object(vst, "_host", host):
            result = self.client.post(route, headers=self.headers, json={"state": state}).raise_for_status().json()
        self.assertTrue(result["changed"])
        self.assertEqual(vst._decode_state(plugin_id, result["state"]), b"new state")

    def test_import_metadata_fts_rescan_and_exclusion(self):
        folder = self.root / "input"
        folder.mkdir()
        first = audio_file(folder / "DOORWood_木门关闭.wav")
        audio_file(folder / "DOORWood_DoorSlam.wav")
        job = self.import_folder(folder)
        self.assertEqual((job["state"], job["imported"]), ("completed", 2))
        page = self.items(q="DOORWood", limit=1)
        self.assertEqual(page["total"], 2)
        following = self.items(q="DOORWood", limit=1, cursor=json.dumps(page["next_cursor"]))
        self.assertNotEqual(page["files"][0]["id"], following["files"][0]["id"])
        selected = self.items(q="木门")["files"][0]
        self.assertEqual(selected["path"], str(first.resolve()))
        self.assertIsNone(selected["waveform_fingerprint"])
        update = self.client.post("/api/v1/projects/default/catalog/update", json={
            "ids": [selected["id"]], "action": "tags", "value": ["走廊"]
        })
        self.assertEqual(update.status_code, 200, update.text)
        self.assertEqual(self.items(q="走廊")["files"][0]["id"], selected["id"])
        job = self.import_folder(folder)
        self.assertEqual((job["imported"], job["unchanged"]), (0, 2))
        self.client.post("/api/v1/projects/default/catalog/update", json={
            "ids": [selected["id"]], "action": "remove"
        }).raise_for_status()
        self.import_folder(folder)
        self.assertEqual(self.items()["total"], 1)
        self.assertTrue(first.is_file())
        self.assertFalse({"torch", "chromadb", "transformers"} & sys.modules.keys())

    def test_loose_assets_share_one_directory_and_can_join_and_leave_groups(self):
        assets = [str(audio_file(self.root / name)) for name in ("one.wav", "two.wav")]
        self.client.post("/api/v1/projects/default/imports", json={"file_paths": assets}).raise_for_status()
        directories = self.client.get("/api/v1/projects/default/catalog/folders").json()
        self.assertEqual([(entry["path"], entry["count"]) for entry in directories], [(str(self.root.resolve()), 2)])

        group = self.client.post("/api/v1/projects/default/folders", json={"name": "Foley"}).json()["folder_id"]
        file_id = self.items()["files"][0]["id"]
        memberships = f"/api/v1/projects/default/catalog/items/{file_id}/groups"
        for action, expected in (("group", 1), ("ungroup", 0)):
            self.client.post("/api/v1/projects/default/catalog/update", json={
                "ids": [file_id], "action": action, "value": group
            }).raise_for_status()
            self.assertEqual(self.client.get(memberships).json(), [{"id": group, "name": "Foley", "member": expected}])
            self.assertEqual(self.items(group=group)["total"], expected)

    def test_missing_file_and_relocation_preserve_identity(self):
        folder = self.root / "input"
        folder.mkdir()
        audio = audio_file(folder / "source.wav")
        self.import_folder(folder)
        asset = self.items()["files"][0]
        moved = self.root / "moved.wav"
        audio.rename(moved)
        self.import_folder(folder)
        self.assertTrue(self.items()["files"][0]["missing"])
        result = self.client.post("/api/v1/projects/default/catalog/relocate", json={
            "file_id": asset["id"], "destination": str(moved)
        })
        self.assertEqual(result.status_code, 200, result.text)
        result = self.items(q="moved")["files"][0]
        self.assertEqual(result["id"], asset["id"])
        self.assertFalse(result["missing"])

    def test_root_relocation_preserves_edits_and_detects_changed_audio(self):
        folder = self.root / "input"
        folder.mkdir()
        path = audio_file(folder / "source.wav")
        source_id = self.import_folder(folder)["source_id"]
        item = self.items()["files"][0]
        self.client.post("/api/v1/projects/default/catalog/update", json={
            "ids": [item["id"]], "action": "metadata",
            "value": {"title": "木质脚步", "description": "潮湿走廊"}
        }).raise_for_status()
        moved = self.root / "moved"
        folder.rename(moved)
        offline = self.client.post(f"/api/v1/projects/default/sources/{source_id}/scan")
        job = self.client.get(f"/api/v1/jobs/{offline.json()['job_id']}").json()
        self.assertEqual(job["stage"], "partial")
        self.assertFalse(self.items()["files"][0]["missing"])
        audio_file(moved / path.name, frequency=330)
        result = self.client.post("/api/v1/projects/default/catalog/relocate", json={
            "source_id": source_id, "destination": str(moved)
        })
        result.raise_for_status()
        job = self.client.get(f"/api/v1/jobs/{result.json()['job_id']}").json()
        self.assertEqual(job["imported"], 1)
        self.assertEqual(self.items(q="走廊")["files"][0]["id"], item["id"])
        self.assertEqual(self.items(q="脚步")["files"][0]["title"], "木质脚步")

    def test_probe_failure_is_searchable_and_retryable(self):
        file = self.root / "broken.wav"
        file.write_bytes(b"incomplete")
        job = self.import_folder(self.root)
        self.assertEqual((job["stage"], job["failures"]), ("partial", 1))
        first = self.items(q="broken")["files"][0]
        self.assertTrue(first["probe_error"])
        audio_file(file)
        retry = self.client.post(f"/api/v1/jobs/{job['id']}/retry")
        retry.raise_for_status()
        result = self.client.get(f"/api/v1/jobs/{retry.json()['job_id']}").json()
        self.assertEqual((result["stage"], result["failures"]), ("done", 0))
        repaired = self.items(q="broken")["files"][0]
        self.assertEqual(repaired["id"], first["id"])
        self.assertEqual(repaired["sample_rate"], 48000)

    def test_parametric_eq_and_reverb_process_the_preview(self):
        import numpy as np

        audio_file(self.root / "eq-tone.wav", frequency=1000)
        with wave.open(str(self.root / "reverb-impulse.wav"), "wb") as handle:
            handle.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            handle.writeframes(struct.pack("<h", 12000) + bytes((48000 - 1) * 2))
        self.import_folder(self.root)

        def preview(query, effects):
            item = self.items(q=query)["files"][0]
            response = self.client.post("/api/v1/preview", json={
                "project_id": "default", "rate": 48000, "channels": 1,
                "tracks": [{"clips": [{"file_id": item["id"], "duration": 1, "effects": effects}]}],
            })
            response.raise_for_status()
            with wave.open(io.BytesIO(response.content)) as streamed:
                self.assertEqual(streamed.getnframes(), 48000)
                return np.frombuffer(streamed.readframes(48000), dtype="<i2").astype(float)

        dry = preview("eq-tone", [])
        cut = preview("eq-tone", [{"kind": "eq", "bands": [
            {"shape": "bell", "frequency": 1000, "gain": -12, "q": 2.5}
        ]}])
        self.assertAlmostEqual(20 * math.log10(cut[16000:].std() / dry[16000:].std()), -12, places=1)
        reverb = {"kind": "reverb", "values": {"mix": 100, "size": 60, "damping": 45, "width": 100}}
        room = preview("reverb-impulse", [reverb])
        self.assertEqual(room[0], 0)
        self.assertGreater(np.max(np.abs(room[12000:18000])), 20)
        reverb["values"]["mix"] = 25
        blended = preview("reverb-impulse", [reverb])
        self.assertAlmostEqual(blended[0], 9000, delta=2)

    def test_source_metadata_and_embedded_artwork_for_production_wave(self):
        from PIL import Image

        source = tagged_wave(self.root / "recorded-room.wav")
        audio_file(self.root / "unmarked.wav")
        original = delivery.sha256(source)
        self.import_folder(self.root)
        items = {item["filename"]: item["id"] for item in self.items()["files"]}
        base = f"/api/v1/files/{items[source.name]}"
        params = {"project_id": "default"}
        details = self.client.get(f"{base}/details", params=params).raise_for_status().json()
        self.assertEqual(details["audio"]["bit_depth"], 16)
        self.assertEqual(details["audio"]["codec"], "pcm_s16le")
        self.assertEqual(details["tags"]["embedded"]["artist"], "Field Unit")
        self.assertEqual(details["tags"]["embedded"]["MICROPHONE"], "Stereo pair")
        self.assertEqual(details["ixml"]["TAKE"], "2")
        self.assertEqual(details["bwf"]["originator"], "Stage Crew")
        self.assertEqual(details["bwf"]["loudness_value"], "-23.00")
        self.assertEqual(details["markers"], [{"id": 7, "sample": 24000}])
        cover = self.client.get(f"{base}/artwork", params=params).raise_for_status()
        info = json.loads(cover.headers["X-SoundBot-Artwork"])
        self.assertEqual((info["role"], info["description"], info["format"]), (3, "Front artwork", "WEBP"))
        self.assertEqual((info["width"], info["height"]), (1250, 900))
        with Image.open(io.BytesIO(cover.content)) as preview:
            self.assertLessEqual(max(preview.size), 480)
        self.assertEqual(self.client.get(f"/api/v1/files/{items['unmarked.wav']}/artwork",
                                         params=params).status_code, 204)
        self.assertEqual(delivery.sha256(source), original)

    def test_lazy_waveform_preview_and_atomic_daw_delivery(self):
        media = self.root / "daw"
        media.mkdir()
        source = audio_file(self.root / "tone.wav")
        self.import_folder(self.root)
        asset = self.items(q="tone")["files"][0]
        identifier = asset["id"]
        batch = {"project_id": "default", "file_ids": [identifier], "cached_only": True}
        self.assertFalse(self.client.post("/api/v1/waveforms/batch", json=batch).json()["items"][0]["ok"])
        peaks = self.client.get(f"/api/v1/files/{identifier}/waveform", params={"project_id": "default"}).json()
        self.assertEqual(len(peaks["peaks"]), 2000)
        compact = self.client.post("/api/v1/waveforms/batch", json={**batch, "points": 256}).json()["items"][0]
        self.assertTrue(compact["cached"])
        self.assertEqual(len(compact["peaks"]), 256)
        self.assertEqual(compact["source_fingerprint"], peaks["source_fingerprint"])
        self.assertAlmostEqual(max(compact["peaks"]), max(peaks["peaks"]), places=4)
        self.assertTrue(self.client.post("/api/v1/waveforms/batch", json=batch).json()["items"][0]["cached"])
        clip = {"file_id": identifier, "offset": 0.25, "duration": 0.5, "fade_in": 0.025}
        preview = self.client.post("/api/v1/preview", json={
            "project_id": "default", "rate": 48000, "channels": 1, "tracks": [{"clips": [clip]}]
        })
        self.assertEqual(preview.status_code, 200, preview.text if preview.status_code != 200 else "")
        with wave.open(io.BytesIO(preview.content)) as streamed:
            self.assertEqual(streamed.getnframes(), 24000)
            frames = struct.unpack("<24000h", streamed.readframes(24000))
        self.assertLess(max(map(abs, frames[:150])), 1500)
        self.assertGreater(max(map(abs, frames[2000:2500])), 7000)
        request = {"project_id": "default", "clip": clip, "directory": str(media)}
        self.assertEqual(self.client.post("/api/v1/delivery", json=request).status_code, 403)
        original = delivery.sha256(source)
        first = self.client.post("/api/v1/delivery", headers=self.headers, json=request)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post("/api/v1/delivery", headers=self.headers, json=request)
        self.assertEqual(first.json(), second.json())
        path = Path(first.json()["path"])
        with wave.open(str(path)) as output:
            self.assertEqual((output.getsampwidth(), output.getnframes()), (3, 24000))
        self.assertEqual(list(media.resolve().iterdir()), [path])
        self.assertEqual(delivery.sha256(source), original)

    def test_middleware_folder_workflow_renders_without_mutating_source_or_project(self):
        clip = audio_file(self.root / "Library effect.wav", 540)
        self.import_folder(self.root)
        asset = self.items(q="Library effect")["files"][0]
        for host, suffix, directory in [("fmod", ".fspro", "Assets"), ("cri", ".atmcproject", "Materials")]:
            with self.subTest(host=host):
                project = self.root / f"Demo {host}{suffix}"
                project.write_bytes(b"authoring project data")
                folder = self.root / directory
                nested = folder / "Room"
                nested.mkdir(parents=True)
                source = audio_file(nested / "Impact # 中文.wav")
                original = delivery.sha256(source)
                (folder / "output.hca").write_bytes(b"not a source")
                (folder / "output.bank").write_bytes(b"not a source")
                if os.name != "nt":
                    (nested / "alias.wav").symlink_to(clip)
                settings = {"host": host, "project_path": str(project), "source_directory": str(folder)}
                url = "/api/v1/middleware/connect"
                self.assertEqual(self.client.post(url, json=settings).status_code, 403)
                wrong_host = {**settings, "host": "cri" if host == "fmod" else "fmod"}
                self.assertEqual(self.client.post(url, headers=self.headers, json=wrong_host).status_code, 422)
                connected = self.client.post(url, headers=self.headers, json=settings).raise_for_status().json()
                self.assertEqual((connected["host"], connected["delivery"]), (host, "new_wave"))
                self.assertEqual([item["path"] for item in connected["sources"]], [f"Room/{source.name}"])
                identifier = connected["sources"][0]["id"]
                select = f"/api/v1/middleware/sources/{identifier}/select"
                selected = self.client.post(select, headers=self.headers).raise_for_status().json()
                token = selected["token"]
                peaks = self.client.get(f"/api/v1/middleware/selection/{token}/waveform").raise_for_status().json()
                self.assertEqual(len(peaks["peaks"]), 2000)
                session = {"token": token, "project_id": "default", "rate": 48000, "channels": 1,
                           "tracks": [{"clips": [{"middleware_token": token, "duration": 0.25, "gain": -3}]},
                                      {"clips": [{"file_id": asset["id"], "start": 0.1, "duration": 0.25}]}]}
                with wave.open(io.BytesIO(self.client.post("/api/v1/preview", json=session).raise_for_status().content)) as preview:
                    self.assertEqual((preview.getframerate(), preview.getnframes()), (48000, 16800))
                url = "/api/v1/middleware/deliver"
                self.assertEqual(self.client.post(url, json=session).status_code, 403)
                first = self.client.post(url, headers=self.headers, json=session).raise_for_status().json()
                self.assertEqual(first["status"], "file_delivered")
                self.assertEqual(first["registration"], "pending")
                second = self.client.post(url, headers=self.headers, json=session).raise_for_status().json()
                self.assertEqual(first, second)
                target = Path(first["path"])
                self.assertEqual(target.parent, folder.resolve())
                self.assertNotEqual(target, source)
                with wave.open(str(target)) as rendered:
                    self.assertEqual((rendered.getsampwidth(), rendered.getnframes()), (3, 16800))
                    self.assertTrue(any(rendered.readframes(16800)))
                self.assertEqual(project.read_bytes(), b"authoring project data")
                self.assertEqual(delivery.sha256(source), original)
                self.assertFalse(list(folder.glob("*.part")))
                audio_file(source, 350)
                self.assertEqual(self.client.post(url, headers=self.headers, json=session).status_code, 422)
                self.client.post("/api/v1/middleware/disconnect", headers=self.headers).raise_for_status()
                self.assertEqual(self.client.get(f"/api/v1/middleware/selection/{token}/waveform").status_code, 422)

    def test_wwise_revalidates_binding_and_can_restore(self):
        wproj = self.root / "Game.wproj"
        wproj.touch()
        originals = self.root / "Originals"
        originals.mkdir()
        original = audio_file(originals / "impact.wav")
        original_digest = delivery.sha256(original)
        source = {"id": "source", "name": "impact", "path": "\\Actor-Mixer\\Unit\\Folder\\Sound\\impact",
                  "parent": {"id": "sound"}, "originalWavFilePath": str(original)}

        class Authoring:
            async def project(self):
                return {"id": "project", "name": "Game", "path": str(wproj)}

            async def sources(self):
                return [source]

            async def sounds(self, *, colors=False):
                row = {"id": "sound", "path": "\\Actor-Mixer\\Unit\\Folder\\Sound", "@IsVoice": False}
                return [{**row, "@@Color": 3} if colors else row]

            async def explorer_objects(self, sources):
                return [
                    {"id": "unit", "path": "\\Actor-Mixer\\Unit", "type": "WorkUnit", "@Color": 2},
                    {"id": "folder", "path": "\\Actor-Mixer\\Unit\\Folder", "type": "Folder", "@@Color": 4},
                ]

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def local_authoring(_url):
            yield Authoring()

        with patch.object(wwise, "_connection", local_authoring):
            connected = self.client.post("/api/v1/middleware/connect", headers=self.headers, json={"host": "wwise"}).raise_for_status().json()
            self.assertEqual(connected["sources"][0]["color"], "#086868")
            self.assertEqual(connected["objects"]["\\Actor-Mixer\\Unit"],
                             {"id": "unit", "type": "WorkUnit", "color": "#1a55cb"})
            self.assertEqual(connected["objects"]["\\Actor-Mixer\\Unit\\Folder"]["type"], "Folder")
            selected = self.client.post("/api/v1/middleware/sources/source/select", headers=self.headers, json={})
            selected.raise_for_status()
            token = selected.json()["token"]
            result = self.client.post("/api/v1/middleware/deliver", headers=self.headers, json={
                "token": token, "project_id": "default", "tracks": [{
                    "clips": [{"middleware_token": token, "duration": 0.5, "gain": -3}]
                }]
            })
            self.assertEqual(result.status_code, 200, result.text)
            self.assertNotEqual(delivery.sha256(original), original_digest)
            self.assertEqual(result.json()["conversion"], "unverified")
            backup = result.json()["backup_id"]
            self.client.post(f"/api/v1/wwise/backups/{backup}/restore", headers=self.headers).raise_for_status()
            self.assertEqual(delivery.sha256(original), original_digest)
            self.assertFalse(list(originals.glob("*.part")))

    def test_interrupted_replacement_recovers_registered_original(self):
        original = audio_file(self.root / "source.wav")
        digest = delivery.sha256(original)
        with delivery.staging(self.root) as stage:
            audio_file(stage, frequency=330)
            delivery.backup_and_replace(stage, original, digest)
        self.assertNotEqual(delivery.sha256(original), digest)
        delivery.recover_managed_files()
        self.assertEqual(delivery.sha256(original), digest)
        self.assertFalse(list(self.root.glob("*.part")))

    def test_waapi_uses_wamp_call_options(self):
        class Socket:
            async def send(inner, payload):
                self.assertEqual(json.loads(payload), [
                    48, 1, {"return": ["id", "path"]}, "ak.wwise.core.object.get",
                    [], {"waql": "$ from type AudioFileSource"},
                ])

            async def recv(inner):
                return json.dumps([50, 1, {}, [], {"return": [{"id": "source", "path": "path"}]}])

        client = wwise._WAAPI(Socket())
        rows = asyncio.run(client.objects("$ from type AudioFileSource", ["id", "path"]))
        self.assertEqual(rows, [{"id": "source", "path": "path"}])


if __name__ == "__main__":
    unittest.main()
