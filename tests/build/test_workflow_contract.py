from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
VALIDATE_WORKFLOW = ROOT / ".github" / "workflows" / "validate.yml"


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_every_action_is_pinned_to_a_full_commit(self) -> None:
        uses = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", self.text)
        self.assertTrue(uses)
        unpinned = [value for value in uses if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", value)]
        self.assertEqual(unpinned, [])

    def test_windows_python_output_is_forced_to_utf8(self) -> None:
        self.assertRegex(self.text, r"(?m)^  PYTHONUTF8: '1'$")
        self.assertRegex(self.text, r"(?m)^  PYTHONIOENCODING: 'utf-8'$")

    def test_release_workflow_is_strictly_tag_push_only(self) -> None:
        self.assertRegex(
            self.text,
            r"(?ms)^on:\n  push:\n    tags:\n      - 'v\*'\n\npermissions:",
        )
        self.assertNotIn("workflow_dispatch", self.text)
        self.assertNotIn("inputs.tag_name", self.text)
        self.assertNotIn("github.event_name", self.text)
        self.assertIn(
            "group: soundbot-release-${{ github.ref_name }}",
            self.text,
        )
        self.assertEqual(
            self.text.count("RELEASE_TAG: ${{ github.ref_name }}"),
            1,
        )

    def test_windows_asar_inventory_is_path_separator_independent(self) -> None:
        self.assertIn("$asarEntriesRaw = & npx --no-install asar list", self.text)
        self.assertIn("-replace '\\\\', '/'", self.text)
        self.assertIn(
            "@('main.js', 'preload.js', 'index.html', 'splash.html', 'assets/i18n.js', 'config/audio_capabilities.json')",
            self.text,
        )

    def test_windows_executes_real_sandboxed_preload_smoke(self) -> None:
        windows_job = self.text.split("  build-windows:", 1)[1].split(
            "  create-release:", 1
        )[0]
        self.assertIn("Test sandboxed Electron preload bridge", windows_job)
        self.assertIn(
            "npx --no-install electron tests/frontend/check_electron_preload.js",
            windows_job,
        )

    def test_every_job_checks_out_validated_release_source(self) -> None:
        self.assertIn("release_tag: ${{ steps.release-source.outputs.tag }}", self.text)
        self.assertIn("release_sha: ${{ steps.release-source.outputs.sha }}", self.text)
        self.assertIn('git show-ref --verify --quiet "refs/tags/$RELEASE_TAG"', self.text)
        self.assertIn('git cat-file -t "refs/tags/$RELEASE_TAG"', self.text)
        self.assertIn('git merge-base --is-ancestor "$TAG_SHA" "origin/$DEFAULT_BRANCH"', self.text)
        self.assertGreaterEqual(
            self.text.count("ref: ${{ needs.validate-release.outputs.release_sha }}"),
            4,
        )
        self.assertIn("--verify-tag", self.text)

    def test_builds_use_one_canonical_native_script(self) -> None:
        self.assertEqual(self.text.count("python scripts/build.py"), 2)
        self.assertNotRegex(self.text, r"(?m)^\s+pyinstaller\s+main\.spec")
        self.assertNotRegex(self.text, r"(?m)^\s+npx electron-builder")

    def test_model_and_release_gates_are_not_contradictory(self) -> None:
        self.assertIn('$env:ENABLE_MODEL_PRELOAD = "true"', self.text)
        self.assertIn("CLAP model did not become ready", self.text)
        self.assertIn("check_frozen_audio_matrix.py", self.text)
        self.assertIn("verify_packaged_models", self.text)
        self.assertIn('p["version"] == sys.argv[2].removeprefix("v")', self.text)
        self.assertIn("$resp.version -eq ($env:RELEASE_TAG).TrimStart('v')", self.text)
        self.assertIn("soundbot-models-intentionally-missing", self.text)
        self.assertIn("needs: [validate-release, build-models]", self.text)
        self.assertEqual(self.text.count("name: models-package"), 3)
        self.assertGreaterEqual(self.text.count("verify_packaged_models"), 2)

    def test_release_is_draft_verified_and_channel_aware(self) -> None:
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertIn("--draft", self.text)
        self.assertIn('"v0.2.0"', self.text)
        self.assertIn("verify_release_assets.py", self.text)
        self.assertIn(
            "python tests/build/verify_release_assets.py --directory release-assets",
            self.text,
        )
        self.assertIn("SHA256SUMS.txt", self.text)
        self.assertIn("actions/attest@", self.text)
        self.assertIn("CREATE_ARGS+=(--prerelease)", self.text)
        self.assertIn('gh release create "${CREATE_ARGS[@]}"', self.text)
        self.assertIn('--json isDraft --jq .isDraft', self.text)
        self.assertIn('gh release delete "$RELEASE_TAG" --yes', self.text)
        self.assertIn("refusing to overwrite", self.text)
        self.assertNotIn("prerelease: true", self.text)

    def test_windows_release_asset_has_a_stable_remote_name(self) -> None:
        self.assertIn("mapfile -d '' WINDOWS_INSTALLERS", self.text)
        self.assertIn('test "${#WINDOWS_INSTALLERS[@]}" = 1', self.text)
        self.assertIn(
            'WINDOWS_RELEASE_NAME="SoundBot-Setup-${RELEASE_TAG#v}.exe"',
            self.text,
        )
        self.assertIn(
            'cp "${WINDOWS_INSTALLERS[0]}" "release-assets/$WINDOWS_RELEASE_NAME"',
            self.text,
        )
        self.assertNotIn("cp artifacts/soundbot-windows/*.exe release-assets/", self.text)

    def test_windows_smokes_the_actual_nsis_installed_application(self) -> None:
        self.assertIn("Install NSIS and smoke-test installed Electron", self.text)
        self.assertIn("/S /currentuser /D=$installDir", self.text)
        self.assertIn("check_installed_electron.py", self.text)
        self.assertIn("--require-models", self.text)
        self.assertIn("soundbot-installed-electron-smoke", self.text)

    def test_packaged_macos_bundle_loads_its_embedded_model_offline(self) -> None:
        mac_job = self.text.split("  build-macos:", 1)[1].split(
            "  build-windows:", 1
        )[0]
        self.assertIn("Smoke packaged macOS bundled CLAP", mac_job)
        self.assertIn('MODELS_PATH="$APP_PATH/Contents/Resources/models"', mac_job)
        self.assertIn("verify_packaged_models(Path(sys.argv[1]))", mac_job)
        self.assertIn("ENABLE_MODEL_PRELOAD=true", mac_job)
        self.assertIn("HF_HUB_OFFLINE=1", mac_job)
        self.assertIn("TRANSFORMERS_OFFLINE=1", mac_job)
        self.assertIn("MODEL_READY=true", mac_job)


class ValidationWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = VALIDATE_WORKFLOW.read_text(encoding="utf-8")

    def test_runs_on_main_push_without_a_disabled_pr_trigger(self) -> None:
        self.assertNotRegex(self.text, r"(?m)^  pull_request:$")
        self.assertRegex(self.text, r"(?m)^  push:$")
        self.assertIn("- main", self.text)
        self.assertIn("cancel-in-progress: true", self.text)

    def test_validation_actions_are_immutable_and_permissions_are_read_only(self) -> None:
        uses = re.findall(r"(?m)^\s*uses:\s*([^\s#]+)", self.text)
        self.assertTrue(uses)
        self.assertEqual(
            [value for value in uses if not re.fullmatch(r"[^@]+@[0-9a-f]{40}", value)],
            [],
        )
        self.assertRegex(self.text, r"(?ms)^permissions:\n  contents: read$")

    def test_runs_full_source_and_renderer_contracts(self) -> None:
        self.assertIn("unittest discover -s tests", self.text)
        self.assertIn("node tests/frontend/check_renderer_contract.js", self.text)
        self.assertIn("npm ci --ignore-scripts", self.text)

    def test_rejects_private_or_ignored_repository_files(self) -> None:
        self.assertIn("Verify repository hygiene", self.text)
        self.assertIn("python tests/build/verify_repository_hygiene.py", self.text)

    def test_release_rechecks_repository_hygiene(self) -> None:
        release = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python tests/build/verify_repository_hygiene.py", release)


if __name__ == "__main__":
    unittest.main()
