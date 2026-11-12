#!/usr/bin/env python3
"""Check audio decoding and local search on a frozen backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_FIXTURES = SCRIPT_DIR / "fixtures"
TERMINAL_JOB_STATES = {"completed", "failed", "cancelled"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_fixtures(destination_root: Path) -> list[Path]:
    manifest = json.loads((SOURCE_FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    target = destination_root / "空 格_%_#+()" / "深层目录"
    target.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []
    for filename, expected_hash in sorted(manifest["files"].items()):
        source = SOURCE_FIXTURES / filename
        if sha256_file(source) != expected_hash:
            raise RuntimeError(f"committed fixture checksum mismatch: {source}")
        suffix = source.suffix.lower()
        copied = target / f"tone 声 音_%_#+(){suffix}"
        shutil.copyfile(source, copied)
        result.append(copied.resolve())
    return result


def request_json(
    base_url: str,
    method: str,
    route: str,
    *,
    query: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
    timeout: float = 90,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{route}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"{method} {route} returned HTTP {response.status}: {payload}")
            return json.loads(payload)
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {route} returned HTTP {exc.code}: {payload}") from exc


def assert_waveform(path: Path, payload: dict[str, Any]) -> None:
    peaks = payload.get("peaks")
    if not isinstance(peaks, list) or len(peaks) != 2000:
        raise AssertionError(f"{path.suffix}: expected exactly 2000 peaks, got {len(peaks or [])}")
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0.0 <= value <= 1.0
        for value in peaks
    ):
        raise AssertionError(f"{path.suffix}: waveform contains a non-finite or out-of-range peak")
    if not any(value > 0.01 for value in peaks):
        raise AssertionError(f"{path.suffix}: decoded waveform is unexpectedly silent")
    duration = payload.get("duration")
    if not isinstance(duration, (int, float)) or not 0.25 <= duration <= 0.50:
        raise AssertionError(f"{path.suffix}: unexpected duration {duration!r}")


def wait_for_job(base_url: str, job_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = request_json(base_url, "GET", f"/api/v1/jobs/{urllib.parse.quote(job_id)}")
        if last.get("state") in TERMINAL_JOB_STATES:
            break
        time.sleep(1)
    else:
        raise TimeoutError(f"job {job_id} did not finish within {timeout:.0f}s; last={last}")
    if last.get("state") != "completed":
        raise RuntimeError(f"job {job_id} ended in {last.get('state')}: {last}")
    return last


def run_smoke(base_url: str, fixture_root: Path, job_timeout: float) -> dict[str, Any]:
    fixtures = prepare_fixtures(fixture_root)
    expected_suffixes = {".wav", ".mp3", ".flac", ".aiff", ".aif", ".ogg", ".m4a", ".aac", ".wma"}
    actual_suffixes = {path.suffix.lower() for path in fixtures}
    if actual_suffixes != expected_suffixes:
        raise AssertionError(f"fixture matrix drift: {sorted(actual_suffixes)}")

    waveform_summary = {}
    for path in fixtures:
        payload = request_json(base_url, "GET", "/api/waveform", query={"path": str(path)})
        assert_waveform(path, payload)
        waveform_summary[path.suffix.lower()] = {
            "peaks": len(payload["peaks"]),
            "sample_rate": payload.get("sample_rate"),
            "channels": payload.get("channels"),
        }

    imported = request_json(
        base_url,
        "POST",
        "/api/v1/import/files",
        body={
            "file_paths": [str(path) for path in fixtures],
            "client_id": "release-smoke",
            "project_id": "default",
        },
        timeout=120,
    )
    job = wait_for_job(base_url, imported["job_id"], job_timeout)

    status = request_json(base_url, "GET", "/api/v1/projects/default/index/status")
    if status.get("keyword", {}).get("indexed") != len(fixtures):
        raise AssertionError(f"keyword index does not cover the imported fixtures: {status}")
    health = request_json(base_url, "GET", "/api/v1/health")
    if health.get("model_loaded") is not False:
        raise AssertionError("core backend must start without a model")
    artifacts = status.get("artifacts", {})

    files_payload = request_json(
        base_url, "GET", "/api/v1/files", query={"project_id": "default", "limit": 100}
    )
    files = files_payload.get("files", [])
    if len(files) != len(fixtures):
        raise AssertionError(f"metadata API returned {len(files)} files, expected {len(fixtures)}")
    if any(item.get("peaks") is not None for item in files):
        raise AssertionError("metadata-only files endpoint unexpectedly returned waveform arrays")

    for item in files:
        cached = request_json(
            base_url,
            "GET",
            f"/api/v1/files/{urllib.parse.quote(str(item['id']))}/waveform",
            query={"project_id": "default"},
        )
        assert_waveform(Path(str(item["path"])), cached)
        if cached.get("cached") is not False:
            raise AssertionError(f"import should leave waveform generation on demand: {item['path']}")

    batch = request_json(
        base_url,
        "POST",
        "/api/v1/waveforms/batch",
        body={"project_id": "default", "file_ids": [item["id"] for item in files]},
    )
    batch_items = batch.get("items") or []
    if len(batch_items) != len(files) or not all(
        item.get("ok") and item.get("cached") and len(item.get("peaks") or []) == 2000 for item in batch_items
    ):
        raise AssertionError(f"batch waveform contract failed: {batch}")

    wma = next((item for item in files if str(item.get("path", "")).lower().endswith(".wma")), None)
    if not wma:
        raise AssertionError("imported WMA metadata is missing")
    playback = request_json(
        base_url,
        "GET",
        f"/api/v1/files/{urllib.parse.quote(str(wma['id']))}/playback-source",
        query={"project_id": "default"},
        timeout=120,
    )
    playback_path = Path(playback.get("path", ""))
    if playback.get("mode") != "transcoded_wav" or playback_path.suffix.lower() != ".wav":
        raise AssertionError(f"WMA playback fallback was not a transcoded WAV: {playback}")
    if not playback_path.is_file() or playback_path.read_bytes()[:4] != b"RIFF":
        raise AssertionError(f"WMA playback cache is not a valid local WAV: {playback_path}")

    search = request_json(
        base_url,
        "POST",
        "/api/v1/search",
        body={
            "query": "tone",
            "project_id": "default",
            "top_k": 20,
            "threshold": 0.0,
            "page": 1,
            "page_size": 20,
        },
        timeout=180,
    )
    if search.get("total") != len(fixtures):
        raise AssertionError(f"keyword search missed imported fixtures: {search}")

    return {
        "fixtures": len(fixtures),
        "waveforms": waveform_summary,
        "job": {"state": job.get("state"), "processed": job.get("processed")},
        "artifacts": artifacts,
        "search_results": search.get("total"),
        "wma_playback": str(playback_path),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18765")
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--job-timeout", type=float, default=600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_smoke(args.base_url, args.fixture_root.resolve(), args.job_timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    print("[OK] frozen PyAV formats, lazy waveforms, keyword search, and WMA playback")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
