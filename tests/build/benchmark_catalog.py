#!/usr/bin/env python3
"""Measure catalog ingest and search in an isolated database."""

from __future__ import annotations

import argparse
from collections import defaultdict
import concurrent.futures
import json
import os
try:
    import resource
except ImportError:
    resource = None
import sys
import tempfile
import time
from pathlib import Path
from statistics import median
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core import catalog
from core.database import DatabaseManager


def fixture_probe(candidate):
    return candidate, (1.25, 48000, 2, "wav", "", "door slam", "impact", "fixture", "DOORWood", None)


def ingest_fixture(db, count, timings):
    job = catalog.create_import(db, "default")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool, patch.object(catalog, "_probe", fixture_probe):
        for first in range(0, count, catalog.BATCH_SIZE):
            items = []
            for index in range(first, min(count, first + catalog.BATCH_SIZE)):
                path = f"/benchmark/volumes/{index // 1000}/tone_{index:07d}.wav"
                items.append(catalog.Candidate(path, path, 96044, index + 1))
            catalog._commit_batch(db, job, "default", None, items, pool, False, timings=timings)
            if first == 0:
                first_visible = time.monotonic()
    return job, first_visible


def ingest_root(db, root, timings):
    job = catalog.create_import(db, "default", root=str(root))
    first_visible = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(catalog.run_import, db, job, timings=timings)
        while not future.done():
            if first_visible is None and catalog.list_items(db, "default", limit=1)["files"]:
                first_visible = time.monotonic()
            time.sleep(0.05)
        future.result()
    return job, first_visible


def latency(db, queries):
    times = []
    counts = []
    for query in queries:
        start = time.perf_counter()
        page = catalog.list_items(db, "default", query=query, limit=50, count=True)
        times.append((time.perf_counter() - start) * 1000)
        counts.append(page["total"])
    ordered = sorted(times)
    return {"first_ms": round(times[0], 2), "p50_ms": round(median(times), 2),
            "p95_ms": round(ordered[int(0.95 * (len(ordered) - 1))], 2),
            "min_hits": min(counts), "max_hits": max(counts)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rows", type=int)
    mode.add_argument("--source", type=Path)
    parser.add_argument("--query", action="append")
    args = parser.parse_args()
    if args.rows is not None and args.rows < 1:
        parser.error("--rows must be positive")
    if args.source is not None and not args.source.is_dir():
        parser.error("--source must be an existing directory")
    with tempfile.TemporaryDirectory(prefix="soundbot-benchmark-") as tmp:
        path = Path(tmp) / "library.db"
        os.environ["SOUNDBOT_USER_DATA_DIR"] = tmp
        db = DatabaseManager(str(path))
        timings = defaultdict(float)
        start = time.monotonic()
        job_id, first_visible = (
            ingest_fixture(db, args.rows, timings) if args.rows else ingest_root(db, args.source.resolve(), timings)
        )
        elapsed = time.monotonic() - start
        job = catalog.import_status(db, job_id)
        if args.source and job["state"] != "completed":
            raise RuntimeError(job["error_message"] or "Import benchmark failed")
        queries = args.query or (["tone_000", "door", "DOORWood"] if args.rows else ["door", "impact", "ambience"])
        results = latency(db, queries * 8)
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss if resource else None
        peak_bytes = peak_rss if sys.platform == "darwin" or peak_rss is None else peak_rss * 1024
        print(json.dumps({
            "mode": "metadata_fixture" if args.rows else "read_only_audio_source",
            "records": job["imported"], "probe_failures": job["failures"],
            "seconds": round(elapsed, 3),
            "first_records_seconds": round(first_visible - start, 3) if first_visible else None,
            "records_per_second": round(job["imported"] / elapsed, 1),
            "ingest_stage_seconds": {
                "traversal_and_job_overhead": round(elapsed - sum(timings.values()), 3),
                **{stage: round(seconds, 3) for stage, seconds in timings.items()},
            },
            "database_bytes": path.stat().st_size,
            "process_peak_bytes": peak_bytes,
            "query": results,
        }, indent=2))


if __name__ == "__main__":
    main()
