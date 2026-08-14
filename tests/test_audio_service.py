from __future__ import annotations

import math
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock
import wave

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / 'backend'
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import AUDIO_FORMAT_CAPABILITIES, WAVEFORM_PEAK_COUNT, WAVEFORM_VERSION
from core import audio_service as audio_service_module
from core.audio_service import AudioService, AudioServiceError, DecodedAudio


def write_test_wav(path: Path, *, frequency: float = 440.0, frames: int = 800) -> None:
    sample_rate = 8000
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        payload = bytearray()
        for index in range(frames):
            value = int(22000 * math.sin(2 * math.pi * frequency * index / sample_rate))
            payload.extend(struct.pack('<h', value))
        output.writeframes(payload)


class AudioServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.cache = self.root / 'cache'
        self.source = self.root / '测试 tone.wav'
        write_test_wav(self.source)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_canonical_capability_table_contains_all_supported_formats(self) -> None:
        manifest = json.loads(
            (REPO_ROOT / "config" / "audio_capabilities.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            set(AUDIO_FORMAT_CAPABILITIES),
            {'.wav', '.mp3', '.flac', '.aiff', '.aif', '.ogg', '.m4a', '.aac', '.wma'},
        )
        self.assertEqual(AUDIO_FORMAT_CAPABILITIES, manifest["formats"])

    @unittest.skipIf(audio_service_module._av is None, "PyAV is not installed")
    def test_pyav_probes_decodes_and_produces_exact_peaks(self) -> None:
        service = AudioService(self.cache)
        metadata = service.probe(self.source)
        decoded = service.decode(self.source, mono=True)
        first = service.waveform(self.source)
        second = service.waveform(self.source)

        self.assertEqual(metadata.sample_rate, 8000)
        self.assertEqual(metadata.channels, 1)
        self.assertEqual(decoded.samples.ndim, 2)
        self.assertEqual(decoded.samples.shape[1], 1)
        self.assertEqual(len(first.peaks), WAVEFORM_PEAK_COUNT)
        self.assertEqual(first.peaks, second.peaks)
        self.assertTrue(all(math.isfinite(value) for value in first.peaks))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in first.peaks))
        self.assertEqual(first.fingerprint.waveform_version, WAVEFORM_VERSION)

    def test_pyav_decoder_is_preferred_and_fallback_is_not_called(self) -> None:
        service = AudioService(self.cache)
        expected = DecodedAudio(
            samples=np.ones((4, 1), dtype=np.float32),
            sample_rate=48000,
            channels=1,
            duration=4 / 48000,
        )
        with (
            mock.patch.object(audio_service_module, '_av', object()),
            mock.patch.object(service, '_decode_with_pyav', return_value=expected) as pyav_decode,
        ):
            result = service.decode(self.source, mono=True)

        self.assertIs(result, expected)
        pyav_decode.assert_called_once()

    def test_decoder_failure_never_falls_back_to_system_codecs(self) -> None:
        service = AudioService(self.cache)
        with (
            mock.patch.object(audio_service_module, '_av', object()),
            mock.patch.object(service, '_decode_with_pyav', side_effect=ValueError('bad container')),
            self.assertRaises(AudioServiceError) as caught,
        ):
            service.decode(self.source, mono=True)

        self.assertEqual(caught.exception.code, 'audio_decode_failed')
        self.assertEqual(caught.exception.details['decoder'], 'pyav')

    def test_missing_pyav_is_a_structured_runtime_error(self) -> None:
        service = AudioService(self.cache)
        with (
            mock.patch.object(audio_service_module, '_av', None),
            self.assertRaises(AudioServiceError) as caught,
        ):
            service.decode(self.source)

        self.assertEqual(caught.exception.code, 'audio_decoder_unavailable')
        self.assertEqual(caught.exception.details['decoder'], 'pyav')

    def test_runtime_status_rejects_missing_pyav_without_leaking_paths(self) -> None:
        service = AudioService(self.cache)
        private_error = OSError(
            r"DLL load failed at C:\\Users\\private-user\\SoundBot\\avcodec.dll"
        )
        with (
            mock.patch.object(audio_service_module, '_av', None),
            mock.patch.object(audio_service_module, '_AV_IMPORT_ERROR', private_error),
        ):
            status = service.runtime_status()

        self.assertFalse(status['available'])
        self.assertTrue(status['required'])
        self.assertEqual(status['engine'], 'pyav')
        self.assertEqual(status['error_code'], 'audio_decoder_unavailable')
        self.assertEqual(status['error_type'], 'OSError')
        self.assertNotIn('private-user', json.dumps(status))

    def test_runtime_status_serialises_pyav_and_ffmpeg_versions(self) -> None:
        class FakeAV:
            __version__ = '18.0.0'
            library_versions = {
                'libavcodec': (62, 11, 100),
                'libavformat': (62, 3, 100),
            }

        with mock.patch.object(audio_service_module, '_av', FakeAV()):
            status = AudioService(self.cache).runtime_status()

        self.assertTrue(status['available'])
        self.assertEqual(status['version'], '18.0.0')
        self.assertEqual(status['ffmpeg_libraries']['libavcodec'], '62.11.100')
        self.assertIsNone(status['error_code'])

    def test_fingerprint_uses_size_mtime_ns_and_waveform_version(self) -> None:
        service = AudioService(self.cache)
        before = service.fingerprint(self.source)
        new_mtime = before.mtime_ns + 1_000_000
        os.utime(self.source, ns=(new_mtime, new_mtime))
        after = service.fingerprint(self.source)

        self.assertEqual(before.size, after.size)
        self.assertNotEqual(before.mtime_ns, after.mtime_ns)
        self.assertNotEqual(before.key, after.key)
        self.assertEqual(after.waveform_version, WAVEFORM_VERSION)

    def test_playback_wav_cache_is_atomic_and_lru_bounded(self) -> None:
        second_source = self.root / 'second.wav'
        write_test_wav(second_source, frequency=880.0)
        service = AudioService(
            self.cache,
            playback_cache_max_bytes=10 * 1024 * 1024,
            playback_cache_max_files=1,
        )
        if audio_service_module._av is None:
            self.skipTest("PyAV is not installed")
        first_cached = service.prepare_playback_wav(self.source)
        second_cached = service.prepare_playback_wav(second_source)

        self.assertFalse(first_cached.exists())
        self.assertTrue(second_cached.exists())
        self.assertEqual(len(list(self.cache.glob('*.wav'))), 1)
        self.assertEqual(list(self.cache.glob('*.tmp.wav')), [])
        with wave.open(str(second_cached), 'rb') as cached_wave:
            self.assertGreater(cached_wave.getnframes(), 0)

    def test_playback_cache_rejects_an_item_larger_than_its_byte_bound(self) -> None:
        service = AudioService(
            self.cache,
            playback_cache_max_bytes=128,
            playback_cache_max_files=2,
        )
        if audio_service_module._av is None:
            self.skipTest("PyAV is not installed")
        with self.assertRaises(AudioServiceError) as caught:
            service.prepare_playback_wav(self.source)

        self.assertEqual(caught.exception.code, 'playback_cache_limit_exceeded')
        self.assertEqual(list(self.cache.glob('*.wav')), [])
        self.assertEqual(list(self.cache.glob('*.tmp.wav')), [])

    def test_structured_error_does_not_leak_an_unstructured_exception(self) -> None:
        service = AudioService(self.cache)
        missing = self.root / 'missing.wav'
        with self.assertRaises(AudioServiceError) as caught:
            service.waveform(missing)

        payload = caught.exception.to_dict()
        self.assertEqual(payload['code'], 'audio_not_found')
        self.assertFalse(payload['retryable'])
        self.assertIn('details', payload)

    def test_bounded_peak_reducer_matches_fixed_contract_for_long_input(self) -> None:
        samples = np.sin(np.linspace(0.0, 400.0, 2_000_003, dtype=np.float32))
        peaks = AudioService._fixed_peaks_bounded(samples, WAVEFORM_PEAK_COUNT)

        self.assertEqual(len(peaks), WAVEFORM_PEAK_COUNT)
        self.assertTrue(all(math.isfinite(value) for value in peaks))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in peaks))
        self.assertAlmostEqual(max(peaks), 1.0, places=6)

    def test_processed_audio_export_is_atomic_pcm_wav(self) -> None:
        service = AudioService(self.cache)
        target = self.root / "processed.m4a"
        samples = np.linspace(-0.5, 0.5, 1600, dtype=np.float32)[:, None]

        exported = service.export_wav(target, samples, 8000)

        self.assertEqual(exported.suffix, ".wav")
        self.assertTrue(exported.is_file())
        self.assertEqual(list(self.root.glob("*.tmp.wav")), [])
        with wave.open(str(exported), "rb") as rendered:
            self.assertEqual(rendered.getframerate(), 8000)
            self.assertEqual(rendered.getnchannels(), 1)
            self.assertEqual(rendered.getnframes(), 1600)


if __name__ == '__main__':
    unittest.main()
