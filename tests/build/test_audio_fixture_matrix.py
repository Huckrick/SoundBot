from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("check_frozen_audio_matrix.py")
SPEC = importlib.util.spec_from_file_location("check_frozen_audio_matrix", MODULE_PATH)
audio_matrix = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audio_matrix)


class AudioFixtureMatrixTests(unittest.TestCase):
    def test_committed_matrix_is_complete_and_checksum_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            files = audio_matrix.prepare_fixtures(Path(directory))
        self.assertEqual(
            {path.suffix.lower() for path in files},
            {".wav", ".mp3", ".flac", ".aiff", ".aif", ".ogg", ".m4a", ".aac", ".wma"},
        )
        self.assertTrue(all("空 格_%_#+()" in str(path) for path in files))


if __name__ == "__main__":
    unittest.main()
