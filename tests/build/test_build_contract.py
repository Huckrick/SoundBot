from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build.py"
SPEC = importlib.util.spec_from_file_location("soundbot_build", MODULE_PATH)
soundbot_build = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(soundbot_build)


class NativeBuildContractTests(unittest.TestCase):
    def test_rejects_cross_os_target_before_build(self) -> None:
        with mock.patch.object(soundbot_build.platform, "system", return_value="Darwin"), mock.patch.object(
            soundbot_build.platform, "machine", return_value="arm64"
        ):
            with self.assertRaises(RuntimeError):
                soundbot_build.resolve_native_target("windows")

    def test_rejects_intel_macos_host(self) -> None:
        with mock.patch.object(soundbot_build.platform, "system", return_value="Darwin"), mock.patch.object(
            soundbot_build.platform, "machine", return_value="x86_64"
        ):
            with self.assertRaises(RuntimeError):
                soundbot_build.resolve_native_target("macos")

    def test_accepts_windows_x64_host(self) -> None:
        with mock.patch.object(soundbot_build.platform, "system", return_value="Windows"), mock.patch.object(
            soundbot_build.platform, "machine", return_value="AMD64"
        ):
            self.assertEqual(soundbot_build.resolve_native_target("windows"), "windows")

    def test_reads_pe_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "backend.exe"
            data = bytearray(256)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 0x3C, 128)
            data[128:132] = b"PE\0\0"
            struct.pack_into("<H", data, 132, 0x8664)
            executable.write_bytes(data)
            self.assertEqual(
                soundbot_build.native_executable_architecture(executable), ("windows", "x64")
            )

    def test_release_targets_and_audio_runtime_are_pinned(self) -> None:
        root = MODULE_PATH.parents[1]
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
        self.assertNotIn("linux", package["build"])
        self.assertNotIn("build:linux", package["scripts"])
        self.assertEqual(package["build"]["mac"]["target"][0]["arch"], ["arm64"])
        self.assertEqual(package["build"]["mac"]["minimumSystemVersion"], "14.0")
        self.assertEqual(package["build"]["win"]["target"][0]["arch"], ["x64"])

        requirements = (root / "backend" / "requirements.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^av==18\.0\.0$")
        self.assertNotRegex(requirements, r"(?m)^sounddevice(?:==|$)")

        build_requirements = (root / "backend" / "requirements-build.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("pyinstaller==6.16.0", build_requirements)
        self.assertIn("pyinstaller-hooks-contrib==2026.6", build_requirements)

        workflow = (root / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        revision = re.search(r"CLAP_MODEL_REVISION:\s*'([0-9a-f]+)'", workflow)
        self.assertIsNotNone(revision)
        self.assertEqual(len(revision.group(1)), 40)
        workflow_pyinstaller = re.search(r"PYINSTALLER_VERSION:\s*'([^']+)'", workflow)
        self.assertIsNotNone(workflow_pyinstaller)
        self.assertEqual(workflow_pyinstaller.group(1), soundbot_build.PYINSTALLER_VERSION)
        self.assertIn("runs-on: macos-15", workflow)
        self.assertIn("python scripts/build.py", workflow)
        self.assertIn("--platform macos", workflow)
        self.assertIn("--platform windows", workflow)
        self.assertNotIn("softprops/action-gh-release", workflow)
        self.assertRegex(workflow, r"uses: actions/checkout@[0-9a-f]{40}")
        self.assertIn("SHA256SUMS.txt", workflow)
        self.assertIn("gh release create", workflow)

        lock_text = (root / "package-lock.json").read_text(encoding="utf-8")
        self.assertNotIn("registry.npmmirror.com", lock_text)
        self.assertIn("registry.npmjs.org", lock_text)


class BuildScriptReliabilityTests(unittest.TestCase):
    def test_ci_environment_does_not_implicitly_preserve_dist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            backend = root / "backend"
            electron_dist = root / "dist-electron"
            for path in (dist, backend / "dist", backend / "build", electron_dist):
                path.mkdir(parents=True)

            with mock.patch.object(soundbot_build, "DIST_DIR", dist), mock.patch.object(
                soundbot_build, "BACKEND_DIR", backend
            ), mock.patch.object(
                soundbot_build, "ELECTRON_DIST_DIR", electron_dist
            ), mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
                soundbot_build.clean_build_dirs()

            self.assertFalse(dist.exists())
            self.assertFalse(electron_dist.exists())

    def test_explicit_preserve_backend_keeps_dist_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / "dist"
            backend = root / "backend"
            electron_dist = root / "dist-electron"
            for path in (dist, backend / "dist", backend / "build", electron_dist):
                path.mkdir(parents=True)

            with mock.patch.object(soundbot_build, "DIST_DIR", dist), mock.patch.object(
                soundbot_build, "BACKEND_DIR", backend
            ), mock.patch.object(
                soundbot_build, "ELECTRON_DIST_DIR", electron_dist
            ):
                soundbot_build.clean_build_dirs(preserve_backend=True)

            self.assertTrue(dist.exists())
            self.assertFalse((backend / "dist").exists())
            self.assertFalse((backend / "build").exists())
            self.assertFalse(electron_dist.exists())

    def test_release_tag_is_forwarded_to_metadata_gate(self) -> None:
        with mock.patch.object(soundbot_build, "run_command") as run:
            soundbot_build.verify_release_metadata("v0.2.0")

        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--tag", "v0.2.0"])

    def test_backend_dependency_install_can_be_explicitly_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dist = Path(directory) / "dist"
            backend_bundle = dist / "backend" / "soundbot-backend"
            backend_bundle.mkdir(parents=True)
            (backend_bundle / "placeholder").write_bytes(b"x")

            with mock.patch.object(soundbot_build, "DIST_DIR", dist), mock.patch.object(
                soundbot_build, "install_python_deps"
            ) as install, mock.patch.object(
                soundbot_build, "run_command"
            ), mock.patch.object(
                soundbot_build, "verify_native_backend_bundle"
            ), mock.patch.object(
                soundbot_build, "verify_frozen_runtime_assets"
            ), mock.patch.object(
                soundbot_build.platform, "system", return_value="Darwin"
            ):
                result = soundbot_build.build_backend(install_dependencies=False)

            install.assert_not_called()
            self.assertEqual(result, backend_bundle)

    def test_electron_uses_installed_builder_without_network_fallback(self) -> None:
        inherited = {
            "npm_config_registry": "https://registry.example.invalid/",
            "npm_config_userconfig": "/tmp/untrusted-npmrc",
            "ELECTRON_MIRROR": "https://mirror.example.invalid/",
        }
        with mock.patch.dict(os.environ, inherited), mock.patch.object(
            soundbot_build, "install_npm_deps"
        ) as install, mock.patch.object(
            soundbot_build, "verify_native_backend_bundle"
        ), mock.patch.object(soundbot_build, "run_command") as run:
            soundbot_build.build_electron("windows", install_dependencies=False)

        install.assert_not_called()
        self.assertEqual(
            run.call_args.args[0],
            ["npx", "--no-install", "electron-builder", "--win", "--x64"],
        )
        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["npm_config_registry"], "https://registry.npmjs.org/")
        self.assertEqual(environment["npm_config_userconfig"], os.devnull)
        self.assertNotIn("ELECTRON_MIRROR", environment)

    def test_dependency_install_also_ignores_local_mirrors(self) -> None:
        inherited = {
            "npm_config_registry": "https://registry.example.invalid/",
            "npm_config_userconfig": "/tmp/untrusted-npmrc",
            "ELECTRON_BUILDER_BINARIES_MIRROR": "https://mirror.example.invalid/",
        }
        with mock.patch.dict(os.environ, inherited), mock.patch.object(
            soundbot_build, "run_command"
        ) as run:
            soundbot_build.install_npm_deps()

        environment = run.call_args.kwargs["env"]
        self.assertEqual(environment["npm_config_registry"], "https://registry.npmjs.org/")
        self.assertEqual(environment["npm_config_userconfig"], os.devnull)
        self.assertNotIn("ELECTRON_BUILDER_BINARIES_MIRROR", environment)

    def test_electron_keeps_dependency_install_default_for_compatibility(self) -> None:
        with mock.patch.object(soundbot_build, "install_npm_deps") as install, mock.patch.object(
            soundbot_build, "verify_native_backend_bundle"
        ), mock.patch.object(soundbot_build, "run_command"):
            soundbot_build.build_electron("macos")

        install.assert_called_once_with()

    def test_windows_npm_command_preserves_argument_boundaries(self) -> None:
        command = [
            "npx",
            "--no-install",
            "electron-builder",
            "--config",
            r"C:\build files\release config.json",
        ]
        completed = subprocess_result = soundbot_build.subprocess.CompletedProcess(
            args=command, returncode=0, stdout="", stderr=""
        )
        with mock.patch.object(soundbot_build.sys, "platform", "win32"), mock.patch.object(
            soundbot_build.subprocess, "run", return_value=completed
        ) as run:
            soundbot_build.run_command(command)

        self.assertIs(completed, subprocess_result)
        self.assertEqual(run.call_args.args[0], soundbot_build.subprocess.list2cmdline(command))
        self.assertTrue(run.call_args.kwargs["shell"])

    def test_verify_build_rejects_multiple_stale_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            electron_dist = Path(directory)
            (electron_dist / "SoundBot-0.2.0-arm64.dmg").touch()
            (electron_dist / "SoundBot-0.1.0-arm64.dmg").touch()
            with mock.patch.object(soundbot_build, "ELECTRON_DIST_DIR", electron_dist):
                with self.assertRaisesRegex(RuntimeError, "恰好有一个"):
                    soundbot_build.verify_build("macos")

    def test_verify_build_rejects_artifact_without_current_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            electron_dist = Path(directory)
            (electron_dist / "SoundBot-0.1.0-arm64.dmg").touch()
            with mock.patch.object(
                soundbot_build, "ELECTRON_DIST_DIR", electron_dist
            ), mock.patch.object(soundbot_build, "package_version", return_value="0.2.0"):
                with self.assertRaisesRegex(RuntimeError, "不包含当前版本"):
                    soundbot_build.verify_build("macos")

    def test_verify_build_checks_packaged_backend_after_unique_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            electron_dist = Path(directory)
            artifact = electron_dist / "SoundBot-0.2.0-arm64.dmg"
            artifact.write_bytes(b"\0" * (1024 * 1024 + 1))
            packaged_backend = (
                electron_dist
                / "mac-arm64"
                / "SoundBot.app"
                / "Contents"
                / "Resources"
                / "backend"
                / "soundbot-backend"
            )
            packaged_backend.mkdir(parents=True)

            with mock.patch.object(
                soundbot_build, "ELECTRON_DIST_DIR", electron_dist
            ), mock.patch.object(
                soundbot_build, "package_version", return_value="0.2.0"
            ), mock.patch.object(
                soundbot_build, "verify_native_backend_bundle"
            ) as verify_backend, mock.patch.object(
                soundbot_build, "verify_frozen_runtime_assets"
            ) as verify_runtime:
                soundbot_build.verify_build("macos")

            verify_backend.assert_called_once_with(packaged_backend)
            verify_runtime.assert_called_once_with(packaged_backend, "macos")

    def test_cli_rejects_skipping_both_build_stages(self) -> None:
        with mock.patch.object(
            soundbot_build.sys,
            "argv",
            ["build.py", "--skip-backend", "--skip-electron"],
        ), mock.patch("sys.stderr", new_callable=io.StringIO):
            with self.assertRaisesRegex(SystemExit, "2"):
                soundbot_build.main()

    def test_cli_forwards_release_tag_and_dependency_policy(self) -> None:
        with mock.patch.object(
            soundbot_build.sys,
            "argv",
            [
                "build.py",
                "--platform",
                "macos",
                "--release-tag",
                "v0.2.0",
                "--skip-dependency-install",
                "--skip-electron",
            ],
        ), mock.patch.object(
            soundbot_build, "resolve_native_target", return_value="macos"
        ), mock.patch.object(
            soundbot_build, "verify_release_metadata"
        ) as verify_metadata, mock.patch.object(
            soundbot_build, "clean_build_dirs"
        ), mock.patch.object(
            soundbot_build, "build_backend"
        ) as build_backend:
            soundbot_build.main()

        verify_metadata.assert_called_once_with("v0.2.0")
        build_backend.assert_called_once_with(install_dependencies=False)


if __name__ == "__main__":
    unittest.main()
