from __future__ import annotations

import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_frozen_bundle.py")
SPEC = importlib.util.spec_from_file_location("verify_frozen_bundle", MODULE_PATH)
frozen_bundle = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(frozen_bundle)


class FrozenBundleTests(unittest.TestCase):
    def test_detects_x64_pe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.exe"
            payload = bytearray(256)
            payload[:2] = b"MZ"
            struct.pack_into("<I", payload, 0x3C, 128)
            payload[128:132] = b"PE\0\0"
            struct.pack_into("<H", payload, 132, frozen_bundle.PE_AMD64)
            path.write_bytes(payload)
            self.assertEqual(frozen_bundle.executable_architecture(path), ("windows", "x64"))

    def test_detects_arm64_macho(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app"
            payload = bytearray(32)
            payload[:4] = frozen_bundle.MACHO_64_LE
            struct.pack_into("<I", payload, 4, frozen_bundle.MACHO_ARM64)
            path.write_bytes(payload)
            self.assertEqual(frozen_bundle.executable_architecture(path), ("macos", "arm64"))

    def test_rejects_foreign_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            executable = bundle / "soundbot-backend.exe"
            payload = bytearray(256)
            payload[:2] = b"MZ"
            struct.pack_into("<I", payload, 0x3C, 128)
            payload[128:132] = b"PE\0\0"
            struct.pack_into("<H", payload, 132, 0x014C)
            executable.write_bytes(payload)
            (bundle / "_internal").mkdir()
            with self.assertRaises(RuntimeError):
                frozen_bundle.verify_bundle(bundle, "windows", "x64")


if __name__ == "__main__":
    unittest.main()
