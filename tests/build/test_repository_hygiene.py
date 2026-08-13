from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_repository_hygiene.py")
SPEC = importlib.util.spec_from_file_location("repository_hygiene", MODULE_PATH)
repository_hygiene = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(repository_hygiene)


class RepositoryHygieneTests(unittest.TestCase):
    def test_rejects_private_runtime_paths_without_hiding_backend_source(self) -> None:
        findings = repository_hygiene.audit_tracked_paths(
            [
                "backend/db/soundbot.db",
                "temp_clips/private.wav",
                "config/user_config.json",
                "models/clap/model.safetensors",
                "backend/models/schemas.py",
            ]
        )
        self.assertEqual(
            findings,
            [
                "backend/db/soundbot.db",
                "temp_clips/private.wav",
                "config/user_config.json",
                "models/clap/model.safetensors",
            ],
        )

    def test_detects_nonempty_sensitive_json_fields(self) -> None:
        self.assertEqual(
            repository_hygiene.find_nonempty_secret_fields(
                {
                    "llm": {"api_key": "do-not-commit"},
                    "safe": {"api_key": ""},
                    "nested": [{"client-secret": "also-private"}],
                }
            ),
            ["llm.api_key", "nested[0].client-secret"],
        )

    def test_detects_credentials_and_personal_paths_without_echoing_values(self) -> None:
        private_path = b"/" + b"Users/example/private.wav"
        credential_url = b"https://" + b"user:pass" + b"@example.invalid"
        labels = repository_hygiene.scan_payload(
            b"path=" + private_path + b"\nurl=" + credential_url + b"\n"
        )
        self.assertEqual(
            labels,
            ["credential-bearing URL", "personal macOS path"],
        )

    def test_scans_binary_payloads_and_common_provider_tokens(self) -> None:
        private_path = b"/" + b"Users/example/private.wav"
        github_token = b"github" + b"_pat_" + b"A" * 30
        google_key = b"AI" + b"za" + b"A" * 35
        slack_token = b"xox" + b"b-" + b"1" * 24
        labels = repository_hygiene.scan_payload(
            b"\0" + private_path + b"\n" + github_token + b"\n" + google_key + b"\n" + slack_token
        )
        self.assertEqual(
            labels,
            [
                "GitHub fine-grained access token",
                "Google API key",
                "Slack access token",
                "personal macOS path",
            ],
        )

    def test_reports_office_identity_property_without_its_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workbook = Path(temporary_directory) / "metadata.xlsx"
            identity = "private" + "-identity"
            core_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties">'
                f"<cp:lastModifiedBy>{identity}</cp:lastModifiedBy>"
                "</cp:coreProperties>"
            )
            with zipfile.ZipFile(workbook, "w") as archive:
                archive.writestr("docProps/core.xml", core_xml)

            self.assertEqual(
                repository_hygiene.audit_office_metadata(workbook),
                ["lastModifiedBy"],
            )

    def test_allows_env_example_but_rejects_real_environment_files(self) -> None:
        self.assertEqual(
            repository_hygiene.audit_tracked_paths(
                [".env.example", ".env", "config/.env.local"]
            ),
            [".env", "config/.env.local"],
        )


if __name__ == "__main__":
    unittest.main()
