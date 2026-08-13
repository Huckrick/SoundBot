#!/usr/bin/env python3
"""Smoke-test the waveform endpoint of a running frozen SoundBot backend."""

import argparse
import json
import math
import struct
import tempfile
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from check_frozen_audio_matrix import assert_waveform, prepare_fixtures, request_json


def create_fixture(path: Path) -> None:
    sample_rate = 44_100
    frame_count = sample_rate
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            envelope = 0.2 + 0.8 * (index / max(1, frame_count - 1))
            value = int(20_000 * envelope * math.sin(2 * math.pi * 440 * index / sample_rate))
            frames.extend(struct.pack("<h", value))
        output.writeframes(frames)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18765")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--matrix", action="store_true")
    parser.add_argument("--fixture-root", type=Path)
    args = parser.parse_args()

    if args.matrix:
        fixture_root = args.fixture_root or Path(tempfile.gettempdir()) / "soundbot-audio-matrix"
        fixtures = prepare_fixtures(fixture_root.resolve())
        summary = {}
        for source in fixtures:
            payload = request_json(
                args.base_url, "GET", "/api/waveform", query={"path": str(source)}
            )
            assert_waveform(source, payload)
            summary[source.suffix.lower()] = len(payload["peaks"])
        print(json.dumps({"fixtures": len(fixtures), "peaks": summary}, sort_keys=True))
        return 0

    fixture = args.fixture or Path(tempfile.gettempdir()) / "soundbot-waveform-smoke.wav"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    create_fixture(fixture)

    query = urllib.parse.urlencode({"path": str(fixture.resolve())})
    url = f"{args.base_url.rstrip('/')}/api/waveform?{query}"
    with urllib.request.urlopen(url, timeout=45) as response:
        if response.status != 200:
            raise RuntimeError(f"waveform endpoint returned HTTP {response.status}")
        payload = json.loads(response.read().decode("utf-8"))

    peaks = payload.get("peaks")
    if not isinstance(peaks, list) or not peaks:
        raise AssertionError("waveform response has no peaks")
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in peaks):
        raise AssertionError("waveform response contains non-finite peaks")
    if not any(abs(value) > 0.01 for value in peaks):
        raise AssertionError("waveform response is unexpectedly silent")
    if payload.get("sample_rate") != 44_100:
        raise AssertionError(f"unexpected sample rate: {payload.get('sample_rate')}")
    if payload.get("channels") != 1:
        raise AssertionError(f"unexpected channel count: {payload.get('channels')}")
    duration = payload.get("duration")
    if not isinstance(duration, (int, float)) or not 0.9 <= duration <= 1.1:
        raise AssertionError(f"unexpected duration: {duration}")

    print(json.dumps({
        "fixture": str(fixture),
        "peak_count": len(peaks),
        "duration": duration,
        "sample_rate": payload.get("sample_rate"),
        "channels": payload.get("channels"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
