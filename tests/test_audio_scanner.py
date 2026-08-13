from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / 'backend'
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config import SUPPORTED_FORMATS
from core.audio_service import AudioMetadata
from core.scanner import AudioScanner, SUPPORTED_AUDIO_FORMATS


class FakeAudioService:
    def __init__(self) -> None:
        self.probed: list[Path] = []

    def probe(self, path: Path) -> AudioMetadata:
        self.probed.append(path)
        return AudioMetadata(
            duration=1.25,
            sample_rate=48000,
            channels=2,
            format=path.suffix.lstrip('.'),
            codec='mock',
        )


class AudioScannerTests(unittest.TestCase):
    def test_scanner_derives_extensions_from_canonical_config(self) -> None:
        self.assertEqual(SUPPORTED_AUDIO_FORMATS, frozenset(SUPPORTED_FORMATS))
        scanner = AudioScanner(audio_service=FakeAudioService())
        self.assertTrue(scanner.is_audio_file('C:/声音/impact.WMA'))
        self.assertTrue(scanner.is_audio_file('/tmp/impact.aif'))

    def test_scan_does_not_require_soundfile_info_before_service_probe(self) -> None:
        fake = FakeAudioService()
        scanner = AudioScanner(audio_service=fake)
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / 'whoosh.WMA'
            source.write_bytes(b'container bytes are handled by the service')
            resolved_source = source.resolve()
            result = scanner._process_file(source)

        self.assertIsNotNone(result)
        self.assertEqual(result.format, 'WMA')
        self.assertEqual(result.sample_rate, 48000)
        self.assertEqual(fake.probed, [resolved_source])
        self.assertEqual(result.path, str(resolved_source))


if __name__ == '__main__':
    unittest.main()
