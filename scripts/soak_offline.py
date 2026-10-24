"""Actual wall-clock + media-duration offline reliability workload; no accelerated pass."""

import argparse
import json
import os
import platform
import resource
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import av
import cv2
import numpy as np
from fastapi.testclient import TestClient
from uap_observer.capture.analysis import ArchiveAnalysis
from uap_observer.capture.recorder import Recorder
from uap_observer.capture.tools import utc_now
from uap_observer.events.processor import OfflineProcessor
from uap_observer.evidence.files import atomic_write, canonical, digest
from uap_observer.evidence.verify import verify_package
from uap_observer.main import create_app


def generate(path: Path, duration: int) -> None:
    encoder = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            "320x180",
            "-r",
            "10",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-g",
            "10",
            "-bf",
            "0",
            str(path),
        ],
        stdin=subprocess.PIPE,
    )
    assert encoder.stdin
    for index in range(duration * 10):
        t = index / 10
        image = np.full((180, 320, 3), 12, dtype=np.uint8)
        for x, y in ((20, 20), (70, 30), (120, 15), (170, 30), (220, 20), (270, 30)):
            cv2.circle(image, (x, y), 1, (100, 100, 100), -1)
        if duration * 0.3 <= t < duration * 0.55:
            cv2.circle(
                image, (40 + int(5 * (t - duration * 0.3)), 70), 2, (200, 200, 200), -1
            )
        if duration * 0.36 <= t < duration * 0.46 and (index // 3) % 2 == 0:
            cv2.circle(
                image,
                (270 - int(6 * (t - duration * 0.36)), 120),
                3,
                (160, 160, 160),
                -1,
            )
        encoder.stdin.write(image.tobytes())
    encoder.stdin.close()
    if encoder.wait(timeout=30):
        raise RuntimeError("soak fixture generation failed")


def run(root: Path, seconds: int) -> dict[str, Any]:
    if seconds < 2:
        raise ValueError("duration must be at least two seconds")
    root.mkdir(parents=True, exist_ok=False)
    source_root = Path(__file__).resolve().parents[1]
    source_files = (
        list((source_root / "backend/src").rglob("*.py"))
        + list((source_root / "frontend/src").rglob("*"))
        + list((source_root / "config").rglob("*.yaml"))
        + list((source_root / "scripts").glob("*.py"))
    )
    source_files += [
        source_root / name
        for name in (
            "Makefile",
            "backend/uv.lock",
            "backend/pyproject.toml",
            "frontend/package.json",
            "frontend/package-lock.json",
            ".python-version",
        )
    ]
    code_manifest = {
        str(path.relative_to(source_root)): digest(path)
        for path in source_files
        if path.is_file()
    }
    atomic_write(root / "code_manifest.json", canonical(code_manifest))
    fixture = root / "source.mkv"
    fixture_seconds = min(120, seconds)
    generate(fixture, fixture_seconds)
    app = create_app(data_directory=root)
    client = TestClient(app, base_url="http://127.0.0.1:8000")
    client.__enter__()
    runtime = app.state.runtime
    ledger = runtime.ledger
    evidence = runtime.evidence
    started = time.monotonic()
    cpu_started = time.process_time()
    started_utc = utc_now()
    report: dict[str, Any] = {
        "runtime": {
            "python": platform.python_version(),
            "pyav": av.__version__,
            "ffmpeg_libraries": av.library_versions,
        },
        "code_manifest_sha256": digest(root / "code_manifest.json"),
        "status": "running",
        "requested_seconds": seconds,
        "started_utc": started_utc,
        "source_sha256": digest(fixture),
        "source_width": 320,
        "source_height": 180,
        "source_native_fps": 10,
        "wall_seconds": 0,
        "media_seconds": 0,
        "sessions": 0,
        "frames": 0,
        "events": 0,
        "errors": [],
        "description": "Each finite file loop is a separate capture epoch. Inter-epoch processing time is not camera coverage.",
    }
    report["pid"] = os.getpid()
    atomic_write(root / "status.json", canonical(report))
    monitor_stop = threading.Event()
    recorder: Recorder | None = None

    def heartbeat() -> None:
        while not monitor_stop.wait(30):
            snapshot = dict(report)
            snapshot["wall_seconds"] = time.monotonic() - started
            snapshot["heartbeat_utc"] = utc_now()
            snapshot["active_source_packets"] = recorder.packet_count if recorder else 0
            atomic_write(root / "heartbeat.json", canonical(snapshot))
            print(
                json.dumps(
                    {
                        "kind": "heartbeat",
                        "wall_seconds": snapshot["wall_seconds"],
                        "completed_sessions": snapshot["sessions"],
                        "active_source_packets": snapshot["active_source_packets"],
                    }
                ),
                flush=True,
            )

    monitor = threading.Thread(target=heartbeat, daemon=True)
    monitor.start()
    try:
        while report["media_seconds"] < seconds or time.monotonic() - started < seconds:
            if shutil.disk_usage(root).free < 128 * 1024 * 1024:
                raise RuntimeError(
                    "storage reserve low; soak stopped before next capture"
                )
            recorder = Recorder(ledger, root / "archive", str(fixture), realtime=True)
            recorder.start()
            deadline = time.monotonic() + 5
            while not ledger.query(
                "SELECT id FROM sessions WHERE id=?", (recorder.session_id,)
            ):
                if time.monotonic() > deadline:
                    raise RuntimeError("capture did not initialize")
                time.sleep(0.01)
            analysis = ArchiveAnalysis(ledger, capacity=2)
            analysis.start()
            try:
                result = OfflineProcessor(ledger, evidence).process(
                    recorder.session_id, until=recorder.done
                )
                recorder.wait()
                if analysis.errors:
                    raise RuntimeError("archive analysis failure")
            finally:
                recorder.stop()
                analysis.stop()
            expected = fixture_seconds * 10
            if result["frames_processed"] != expected:
                raise RuntimeError("replay frame loss")
            rows = ledger.query(
                "SELECT min(start_seconds) AS first,max(end_seconds) AS last FROM segments WHERE session_id=?",
                (recorder.session_id,),
            )
            media = float(rows[0]["last"] - rows[0]["first"])
            report["media_seconds"] += media
            report["sessions"] += 1
            report["frames"] += expected
            report["events"] += len(result["events"])
            if not result["events"]:
                raise RuntimeError("controlled source produced no event")
            # Exercise frozen export and independent verification once per epoch.
            package = evidence.export(result["events"][0])
            if not verify_package(package)["verified"]:
                raise RuntimeError("evidence verification failed")
            before = evidence.event(result["events"][0])["manifest_sha256"]
            evidence.review(
                result["events"][0],
                "likely_conventional",
                "Known generated soak target",
            )
            if evidence.event(result["events"][0])["manifest_sha256"] != before:
                raise RuntimeError("capture mutated during review")
            response = client.get("/api/v1/events?limit=1")
            if response.status_code != 200 or not response.json():
                raise RuntimeError("searchable event API failed")
            report["api_event_list_checks"] = report.get("api_event_list_checks", 0) + 1
            cleanup = evidence.cleanup(
                max_bytes=8 * 1024 * 1024, reserve_bytes=128 * 1024 * 1024
            )
            if cleanup["reserve_low"]:
                raise RuntimeError("storage reserve could not be recovered safely")
            for event_id in result["events"]:
                if not all(
                    Path(s["path"]).is_file() for s in evidence.segments(event_id)
                ):
                    raise RuntimeError("protected source deleted")
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            report.setdefault("baseline_peak_rss_kib", peak)
            report["peak_growth_kib"] = peak - report["baseline_peak_rss_kib"]
            if report["peak_growth_kib"] > 128 * 1024:
                raise RuntimeError("memory growth exceeded declared 128MiB allowance")
            report["processing_mode"] = (
                "exhaustive native-cadence archived frames; bounded delivery queue is independently dropped"
            )
            report.update(
                wall_seconds=time.monotonic() - started,
                cpu_seconds=time.process_time() - cpu_started,
                peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                latest_retention=cleanup,
                last_heartbeat_utc=utc_now(),
                analysis_delivery_drops=analysis.dropped,
            )
            atomic_write(root / "status.json", canonical(report))
            with (root / "history.jsonl").open("ab") as handle:
                handle.write(canonical(report))
            print(
                json.dumps(
                    {
                        k: report[k]
                        for k in (
                            "sessions",
                            "wall_seconds",
                            "media_seconds",
                            "frames",
                            "events",
                            "peak_rss_kib",
                        )
                    }
                ),
                flush=True,
            )
        if any(
            digest(source_root / name) != expected
            for name, expected in code_manifest.items()
        ):
            raise RuntimeError("source code changed during soak")
        report["code_unchanged"] = True
        report["status"] = "passed"
    except BaseException as exc:
        report["status"] = (
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        )
        report["errors"].append(type(exc).__name__)
        raise
    finally:
        monitor_stop.set()
        monitor.join(2)
        client.__exit__(None, None, None)
        report["wall_seconds"] = time.monotonic() - started
        report["finished_utc"] = utc_now()
        atomic_write(root / "status.json", canonical(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=28800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.seconds)
