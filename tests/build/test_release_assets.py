from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.build.verify_release_assets import verify_release_assets


class ReleaseAssetTests(unittest.TestCase):
    def make_assets(self, root: Path) -> dict[str, dict[str, object]]:
        payloads = {
            "models.zip": b"models",
            "models.zip.sha256": b"hash  models.zip\n",
            "SoundBot-0.2.0-arm64.dmg": b"dmg",
            "SoundBot.Setup.0.2.0.exe": b"exe",
            "SHA256SUMS.txt": b"checksums",
        }
        for name, payload in payloads.items():
            (root / name).write_bytes(payload)
        return {
            name: {
                "size": len(payload),
                "digest": f"sha256:{hashlib.sha256(payload).hexdigest()}",
                "state": "uploaded",
            }
            for name, payload in payloads.items()
        }

    def test_accepts_exact_remote_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = self.make_assets(root)
            remote = root.parent / f"{root.name}-remote.json"
            remote.write_text(json.dumps({"assets": [
                {"name": name, **metadata} for name, metadata in inventory.items()
            ]}), encoding="utf-8")
            self.addCleanup(remote.unlink, missing_ok=True)
            verify_release_assets(root, remote)

    def test_rejects_size_mismatch_or_extra_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = self.make_assets(root)
            assets = [{"name": name, **metadata} for name, metadata in inventory.items()]
            assets[0]["size"] += 1
            assets.append({
                "name": "stale.dmg",
                "size": 99,
                "digest": f"sha256:{'0' * 64}",
                "state": "uploaded",
            })
            remote = root.parent / f"{root.name}-remote.json"
            remote.write_text(json.dumps({"assets": assets}), encoding="utf-8")
            self.addCleanup(remote.unlink, missing_ok=True)
            with self.assertRaisesRegex(ValueError, "inventory mismatch"):
                verify_release_assets(root, remote)

    def test_rejects_same_size_wrong_digest_or_incomplete_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = self.make_assets(root)
            assets = [{"name": name, **metadata} for name, metadata in inventory.items()]
            assets[0]["digest"] = f"sha256:{'f' * 64}"
            assets[1]["state"] = "new"
            remote = root.parent / f"{root.name}-remote.json"
            remote.write_text(json.dumps({"assets": assets}), encoding="utf-8")
            self.addCleanup(remote.unlink, missing_ok=True)
            with self.assertRaises(ValueError):
                verify_release_assets(root, remote)


if __name__ == "__main__":
    unittest.main()
