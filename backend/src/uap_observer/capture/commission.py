"""Finite LAN commissioning using the existing source-copy and evidence pipeline.

URLs exist only in process memory. Nothing invokes FFmpeg with a secret argv.
The independent watchdog stops ingest even when indexing/detection is behind.
"""

import json
import resource
import shutil
import sys
import threading
import time
from fractions import Fraction
from pathlib import Path
from queue import Empty
from typing import Any

import cv2
import numpy as np

from ..detection.detector import DetectorConfig
from ..events.processor import OfflineProcessor
from ..evidence.files import atomic_write, canonical
from ..evidence.service import EvidenceService
from ..evidence.verify import verify_package
from ..storage.ledger import Ledger
from .analysis import ArchiveAnalysis
from .supervisor import CaptureSupervisor
from .tools import utc_now, validate_input


class CommissioningCapture:
    def __init__(
        self,
        ledger: Ledger,
        evidence: EvidenceService,
        data: Path,
        reserve_bytes: int,
        quota_bytes: int,
    ) -> None:
        self.ledger, self.evidence, self.data = ledger, evidence, data
        self.reserve_bytes, self.quota_bytes = reserve_bytes, quota_bytes
        self.stop_requested = threading.Event()
        self.done = threading.Event()
        self.processing_cancel = threading.Event()
        self.state = "ready"
        self.reason: str | None = None
        self.started: float | None = None
        self.seconds = 600.0
        self.supervisor: CaptureSupervisor | None = None
        self.preview: bytes | None = None
        self.preview_utc: str | None = None
        self.preview_lock = threading.Lock()
        self.result: dict[str, Any] | None = None

    def stop(self) -> None:
        self.reason = self.reason or "user_stop"
        self.stop_requested.set()
        if self.supervisor:
            self.supervisor.stop_requested.set()
            if self.supervisor.current:
                self.supervisor.current.stop_requested.set()

    def status(self) -> dict[str, Any]:
        supervisor = self.supervisor
        current = supervisor.current if supervisor else None
        connection = supervisor.state if supervisor else "stopped"
        if current and connection == "running":
            rows = self.ledger.query("SELECT state FROM sessions WHERE id=?", (current.session_id,))
            connection = rows[0]["state"] if rows else "connecting"
        return {
            "state": self.state,
            "connection": connection,
            "elapsed_seconds": round(time.monotonic() - self.started, 1)
            if self.started and not self.done.is_set()
            else None,
            "capture_limit_seconds": self.seconds,
            "session_count": len(supervisor.sessions) if supervisor else 0,
            "packets": sum(r.packet_count for r in supervisor.recorders) if supervisor else 0,
            "last_packet_age_seconds": round(time.monotonic() - current.last_packet_monotonic, 2)
            if current and current.last_packet_monotonic
            else None,
            "preview_receipt_utc": self.preview_utc,
            "reason": self.reason,
            "result": self.result,
        }

    def run(self, source: str, job_id: str, seconds: float = 600) -> None:
        source = validate_input(source, allow_remote=True)
        if not 0 < seconds <= 600:
            raise ValueError("commissioning must last between 0 and 600 seconds")
        self.seconds, self.started, self.state = seconds, time.monotonic(), "capturing"
        started_utc = utc_now()
        usage_before = resource.getrusage(resource.RUSAGE_SELF)
        analysis = ArchiveAnalysis(self.ledger)
        supervisor = CaptureSupervisor(
            self.ledger,
            self.data / "archive",
            source,
            retries=3,
            backoff_seconds=1,
            camera_id="g100-01",
            segment_seconds=5,
            read_timeout=3,
            allow_lan=True,
        )
        self.supervisor = supervisor
        source = ""
        capture_thread = threading.Thread(target=supervisor.run, name="lan-capture", daemon=True)
        processing_error: list[str] = []
        metrics: list[dict[str, Any]] = []

        def process_sessions() -> None:
            ordinal = 0
            try:
                while capture_thread.is_alive() or ordinal < len(supervisor.recorders):
                    if ordinal >= len(supervisor.recorders):
                        self.done.wait(0.05)
                        continue
                    recorder = supervisor.recorders[ordinal]
                    rows = self.ledger.query(
                        "SELECT id FROM sessions WHERE id=?", (recorder.session_id,)
                    )
                    if not rows:
                        if recorder.done.is_set():
                            ordinal += 1
                        else:
                            self.done.wait(0.05)
                        continue
                    self.ledger.execute(
                        "UPDATE jobs SET session_id=? WHERE id=?", (recorder.session_id, job_id)
                    )
                    # Native resolution/cadence; fixture masks are deliberately not reused.
                    processor = OfflineProcessor(self.ledger, self.evidence, DetectorConfig())
                    metrics.append(
                        processor.process(
                            recorder.session_id, until=recorder.done, cancel=self.processing_cancel
                        )
                    )
                    ordinal += 1
            except Exception as exc:
                processing_error.append(type(exc).__name__)
                self.stop_requested.set()

        processing_thread = threading.Thread(
            target=process_sessions, name="lan-detection", daemon=True
        )
        analysis.start()
        capture_thread.start()
        processing_thread.start()
        watchdog = threading.Timer(seconds, self._deadline)
        watchdog.daemon = True
        watchdog.start()
        try:
            while capture_thread.is_alive():
                elapsed = time.monotonic() - self.started
                try:
                    self.evidence.budget.check(16 * 1024 * 1024, fresh=False)
                except OSError:
                    self.reason = "total_evidence_storage_budget"
                    self.stop()
                if analysis.errors or (analysis.thread and not analysis.thread.is_alive()):
                    self.processing_cancel.set()
                    self.reason = "source_indexing_failed"
                    self.stop()
                if elapsed >= seconds:
                    self.reason = "commissioning_time_limit"
                    self.stop_requested.set()
                if self.stop_requested.is_set():
                    self.reason = self.reason or "user_stop"
                    supervisor.stop()
                    break
                if shutil.disk_usage(self.data).free < self.reserve_bytes:
                    self.reason = "storage_reserve_low"
                    self.stop_requested.set()
                    continue
                archive_bytes = self.ledger.query(
                    "SELECT coalesce(sum(bytes),0) AS n FROM segments WHERE state='sealed'"
                )[0]["n"]
                if archive_bytes >= self.quota_bytes:
                    self.reason = "commissioning_archive_quota"
                    self.stop_requested.set()
                    continue
                try:
                    mapped = analysis.queue.get(timeout=0.1)
                    # Bound browser preview size only. Source and analysis retain native pixels.
                    pixels = mapped.pixels
                    if pixels.shape[1] > 960:
                        pixels = np.asarray(
                            cv2.resize(
                                pixels, (960, round(pixels.shape[0] * 960 / pixels.shape[1]))
                            ),
                            dtype=np.uint8,
                        )
                    ok, encoded = cv2.imencode(".jpg", pixels)
                    if ok:
                        with self.preview_lock:
                            self.preview = encoded.tobytes()
                            self.preview_utc = mapped.metadata["receipt_utc"]
                except Empty:
                    pass
            capture_thread.join(10)
            if capture_thread.is_alive():
                raise TimeoutError("capture shutdown timeout")
            capture_elapsed = time.monotonic() - self.started
            self.state = "processing"
            # Indexer remains active until exhaustive processing has drained sealed segments.
            while processing_thread.is_alive():
                if analysis.errors or (analysis.thread and not analysis.thread.is_alive()):
                    self.processing_cancel.set()
                processing_thread.join(0.2)
            if processing_error or analysis.errors:
                raise RuntimeError("source indexing or detection failed")
            report = self._report(supervisor.sessions)
            report.update(
                {
                    "started_utc": started_utc,
                    "finished_utc": utc_now(),
                    "requested_seconds": seconds,
                    "capture_wall_seconds": round(capture_elapsed, 3),
                    "stop_reason": self.reason or "retries_exhausted",
                    "processing": metrics,
                    "analysis_delivery_queue_drops": analysis.dropped,
                    "queue_drop_meaning": "Preview delivery only; detector exhaustively reads indexed source",
                    "apple_home_verified": False,
                    "ordinary_target_verified": False,
                    "field_accuracy_validated": False,
                }
            )
            events = [event for item in metrics for event in item["events"]]
            events = [event for event in events if self.evidence.segments(event)]
            report["independent_export"] = None
            if events:
                archive = self.evidence.export(events[0])
                verification = verify_package(archive)
                report["independent_export"] = {
                    "event_id": events[0],
                    "file": str(archive.relative_to(self.data)),
                    "verification": verification,
                }
            usage_after = resource.getrusage(resource.RUSAGE_SELF)
            report["host_process"] = {
                "cpu_seconds": round(
                    usage_after.ru_utime
                    + usage_after.ru_stime
                    - usage_before.ru_utime
                    - usage_before.ru_stime,
                    3,
                ),
                "peak_rss_bytes": usage_after.ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                "free_disk_bytes": shutil.disk_usage(self.data).free,
            }
            self.result = report
            self.state = "complete" if report["frames"] and not report["errors"] else "failed"
            if self.state == "failed":
                self.reason = "no_frames_or_capture_errors"
            folder = self.data / "commissioning" / job_id
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write(folder / "report.json", canonical(report))
            self.ledger.execute(
                "UPDATE jobs SET state=?,result_json=?,error=? WHERE id=?",
                (
                    self.state,
                    json.dumps(report),
                    self.reason if self.state == "failed" else None,
                    job_id,
                ),
            )
        except Exception as exc:
            stop_reason = self.reason
            self.state, self.reason = "failed", f"commissioning_failed:{type(exc).__name__}"
            try:
                report = self._report(supervisor.sessions)
                report.update(
                    {
                        "started_utc": started_utc,
                        "finished_utc": utc_now(),
                        "requested_seconds": seconds,
                        "stop_reason": stop_reason,
                        "failure": self.reason,
                        "analysis_errors": analysis.errors,
                        "processing_errors": processing_error,
                        "independent_export": None,
                        "apple_home_verified": False,
                        "ordinary_target_verified": False,
                    }
                )
                self.result = report
                folder = self.data / "commissioning" / job_id
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                atomic_write(folder / "report.json", canonical(report))
                self.ledger.execute(
                    "UPDATE jobs SET state='failed',error=?,result_json=? WHERE id=?",
                    (self.reason, json.dumps(report), job_id),
                )
            except Exception:
                # Storage itself may be unavailable. In-memory status still reports failure.
                pass
        finally:
            watchdog.cancel()
            self.stop_requested.set()
            try:
                supervisor.stop()
                capture_thread.join(10)
                self.processing_cancel.set()
                processing_thread.join(30)
                analysis.stop()
            except Exception as exc:
                self.state, self.reason = "failed", f"shutdown_failed:{type(exc).__name__}"
                try:
                    self.ledger.execute(
                        "UPDATE jobs SET state='failed',error=? WHERE id=?", (self.reason, job_id)
                    )
                except Exception:
                    pass
            finally:
                supervisor.source = ""
                for recorder in supervisor.recorders:
                    recorder.source = ""
                self.done.set()

    def _deadline(self) -> None:
        self.reason = "commissioning_time_limit"
        self.stop()

    def _report(self, session_ids: list[str]) -> dict[str, Any]:
        streams, errors, gaps = [], [], []
        frame_count = 0
        exact_count = 0
        durations = 0.0
        media_steps: list[float] = []
        arrival_steps: list[float] = []
        keyframe_steps: list[float] = []
        for session_id in session_ids:
            rows = self.ledger.query("SELECT * FROM sessions WHERE id=?", (session_id,))
            if not rows:
                continue
            session = rows[0]
            info = json.loads(session["stream_json"])
            streams.append(
                {
                    "session_id": session_id,
                    **{
                        key: info.get(key)
                        for key in ("codec", "width", "height", "fps", "time_base")
                    },
                }
            )
            if session["reason"]:
                errors.append(session["reason"])
            gaps.extend(
                self.ledger.query(
                    "SELECT observed_utc,reason FROM gaps WHERE session_id=?", (session_id,)
                )
            )
            frames = self.ledger.query(
                "SELECT media_seconds,mapping_state FROM frames WHERE session_id=? ORDER BY media_seconds",
                (session_id,),
            )
            frame_count += len(frames)
            exact_count += sum(f["mapping_state"] == "exact" for f in frames)
            times = [f["media_seconds"] for f in frames if f["media_seconds"] is not None]
            if len(times) > 1:
                durations += times[-1] - times[0]
                media_steps.extend(b - a for a, b in zip(times, times[1:]))
            packets = self.ledger.query(
                "SELECT receipt_monotonic,keyframe,source_pts,source_time_base FROM packets WHERE session_id=? ORDER BY sequence",
                (session_id,),
            )
            arrivals = [p["receipt_monotonic"] for p in packets]
            arrival_steps.extend(b - a for a, b in zip(arrivals, arrivals[1:]))
            keys = [
                float(p["source_pts"] * Fraction(p["source_time_base"]))
                for p in packets
                if p["keyframe"] and p["source_pts"] is not None
            ]
            keyframe_steps.extend(b - a for a, b in zip(keys, keys[1:]))
        steps = sorted(media_steps)
        return {
            "streams": streams,
            "frames": frame_count,
            "exact_mapped_frames": exact_count,
            "source_span_seconds": round(durations, 3),
            "observed_fps": round(len(media_steps) / durations, 3) if durations else None,
            "frame_step_p50_seconds": steps[len(steps) // 2] if steps else None,
            "max_frame_step_seconds": max(steps, default=None),
            "max_packet_arrival_gap_seconds": max(arrival_steps, default=None),
            "max_keyframe_interval_seconds": max(keyframe_steps, default=None),
            "errors": errors,
            "gaps": gaps,
            "limitations": [
                "exposure_utc_unknown",
                "uncalibrated",
                "ten_minute_smoke_only",
                "native_default_detector_not_field_profiled",
                "preview_is_delayed_derivative",
            ],
        }
