import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import av
import numpy as np

from ..build import build_info
from ..capture.tools import utc_now
from ..detection.detector import Detector, DetectorConfig
from ..evidence.service import EvidenceService
from ..storage.ledger import Ledger
from ..tracking.tracker import Tracker, TrackState


class OfflineProcessor:
    """Versioned, exhaustive replay of indexed source frames. No live queue shortcut."""

    version = "offline-pipeline-1"

    def __init__(
        self,
        ledger: Ledger,
        evidence: EvidenceService,
        config: DetectorConfig | None = None,
        exclusion: Any = None,
    ) -> None:
        self.exclusion = exclusion
        self.ledger, self.evidence, self.config = ledger, evidence, config or DetectorConfig()

    def process(
        self,
        session_id: str,
        pre: float = 30,
        post: float = 45,
        until: threading.Event | None = None,
    ) -> dict[str, Any]:
        session = self.ledger.query("SELECT * FROM sessions WHERE id=?", (session_id,))[0]
        run_id = uuid4().hex
        detector = Detector(self.config, self.exclusion)
        tracker = Tracker()
        self.ledger.execute(
            "INSERT INTO processing_runs(id,session_id,created_utc,version,parameters_json) VALUES (?,?,?,?,?)",
            (run_id, session_id, utc_now(), self.version, json.dumps(detector.parameters())),
        )
        track_events: dict[str, str] = {}
        frames_processed = 0
        candidates_count = 0
        fragment_count = 0

        def persist(track: TrackState, final: bool = False) -> None:
            nonlocal fragment_count
            if not track.observations:
                return
            self.ledger.execute(
                "INSERT INTO tracks VALUES (?,?,?,?) ON CONFLICT(id,run_id) DO UPDATE SET state=excluded.state,data_json=excluded.data_json",
                (track.id, run_id, track.state, json.dumps(track.snapshot())),
            )
            start, end = track.first_observed, track.last_observed
            if track.observation_count >= tracker.confirm_hits and end - start >= 0.15:
                if track.logical_id not in track_events:
                    track_events[track.logical_id] = self.evidence.trigger(
                        session_id, start, end, pre, post
                    )
                event_id = track_events[track.logical_id]
                with self.ledger.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute(
                        "INSERT OR IGNORE INTO event_tracks VALUES (?,?,?)",
                        (event_id, track.id, run_id),
                    )
                    db.execute(
                        "UPDATE events SET end_seconds=max(end_seconds,?),requested_end=max(requested_end,?) WHERE id=? AND state='capturing_postroll'",
                        (end, end + post, event_id),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO event_segments SELECT ?,id FROM segments WHERE session_id=? AND state='sealed' AND start_seconds<=? AND end_seconds>=?",
                        (event_id, session_id, end + post, start - pre),
                    )
                if final:
                    rationale = {
                        "class": "unresolved_insufficient_data",
                        "reason_codes": ["uncalibrated", "synthetic_or_uncommissioned_source"],
                        "detector_version": detector.version,
                        "parameter_hash": detector.parameter_hash,
                        "parameters": detector.parameters(),
                        "application_build": build_info(),
                        "tracker_version": tracker.version,
                        "tracker_parameters": tracker.parameters(),
                        "event_parameters": {
                            "preserve_pre_seconds": pre,
                            "preserve_post_seconds": post,
                            "minimum_track_span_seconds": 0.15,
                        },
                        "track_quality": {
                            "fragment_observed_frames": len(track.observations),
                            "cumulative_observed_frames": track.observation_count,
                            "duration_seconds": end - start,
                            "ambiguous_associations": sum(
                                a["ambiguous"] for a in track.associations
                            ),
                        },
                        "track": track.snapshot(),
                        "run_id": run_id,
                    }
                    self.ledger.execute(
                        "INSERT INTO analyses VALUES (?,?,?,?)",
                        (uuid4().hex, event_id, utc_now(), json.dumps(rationale)),
                    )
                    if track.end_reason != "history_chunk_limit":
                        self.evidence.seal(event_id)
                        track_events.pop(track.logical_id)
            if final:
                fragment_count += 1

        def segments() -> Iterator[dict[str, Any]]:
            seen: set[str] = set()
            while True:
                pending = self.ledger.query(
                    "SELECT * FROM segments WHERE session_id=? AND state='sealed' AND indexed=1 ORDER BY start_seconds",
                    (session_id,),
                )
                for item in pending:
                    if item["id"] not in seen:
                        seen.add(item["id"])
                        yield item
                waiting = self.ledger.query(
                    "SELECT id FROM segments WHERE session_id=? AND indexed=0 AND state='sealed'",
                    (session_id,),
                )
                if until is None:
                    return
                if until.is_set() and not waiting:
                    ready = self.ledger.query(
                        "SELECT id FROM segments WHERE session_id=? AND state='sealed' AND indexed=1",
                        (session_id,),
                    )
                    if not any(item["id"] not in seen for item in ready):
                        return
                threading.Event().wait(0.05)

        for segment in segments():
            rows = self.ledger.query(
                "SELECT * FROM frames WHERE segment_id=? ORDER BY segment_frame_index",
                (segment["id"],),
            )
            with av.open(str(Path(segment["path"]))) as source:
                for index, frame in enumerate(source.decode(video=0)):
                    if index >= len(rows):
                        raise ValueError("source frame index unavailable")
                    row = rows[index]
                    if row["mapping_state"] != "exact" or row["media_seconds"] is None:
                        self.ledger.execute(
                            "INSERT INTO frame_quality VALUES (?,?,?,?)",
                            (
                                run_id,
                                row["id"],
                                '["source_mapping_unavailable"]',
                                "source_mapping_unavailable",
                            ),
                        )
                        continue
                    pixels = np.asarray(frame.to_ndarray(format="bgr24"), dtype=np.uint8)
                    result = detector.process(pixels, row["id"], session["camera_id"], session_id)
                    self.ledger.execute(
                        "INSERT INTO frame_quality VALUES (?,?,?,?)",
                        (run_id, row["id"], json.dumps(result.flags), result.suppressed),
                    )
                    for candidate in result.candidates:
                        self.ledger.execute(
                            "INSERT INTO detections VALUES (?,?,?,?)",
                            (
                                candidate.candidate_id,
                                run_id,
                                row["id"],
                                candidate.model_dump_json(),
                            ),
                        )
                    frames_processed += 1
                    candidates_count += len(result.candidates)
                    for track in tracker.update(row["media_seconds"], result.candidates):
                        if track.state == "confirmed":
                            persist(track)
                    for track in tracker.drain_ended():
                        persist(track, final=True)

        for track in tracker.tracks:
            track.state, track.end_reason = "ended", "source_epoch_ended"
        for track in tracker.drain_ended():
            persist(track, final=True)
        event_ids = [
            row["event_id"]
            for row in self.ledger.query(
                "SELECT DISTINCT event_id FROM event_tracks WHERE run_id=?", (run_id,)
            )
        ]
        for event_id in event_ids:
            self.evidence.seal(event_id)
        metrics = {
            "run_id": run_id,
            "session_id": session_id,
            "frames_processed": frames_processed,
            "candidates": candidates_count,
            "track_fragments": fragment_count,
            "tracks": fragment_count,
            "events": event_ids,
        }
        self.ledger.execute(
            "UPDATE processing_runs SET metrics_json=? WHERE id=?", (json.dumps(metrics), run_id)
        )
        return metrics


def reanalyze_capture(
    ledger: Ledger, evidence: EvidenceService, event_id: str, config: DetectorConfig | None = None
) -> str:
    """Rerun preserved bytes into a new analysis revision; never reseal capture."""
    from ..evidence.files import digest

    event = evidence.event(event_id)
    folder = Path(event["manifest_path"]).parent
    if digest(folder / "capture_manifest.json") != event["manifest_sha256"]:
        raise ValueError("capture integrity failure")
    capture = json.loads((folder / "capture_manifest.json").read_text())
    original = ledger.query(
        "SELECT data_json FROM analyses WHERE event_id=? ORDER BY created_utc DESC LIMIT 1",
        (event_id,),
    )
    original_data = json.loads(original[0]["data_json"]) if original else {}
    if config is None and "parameters" in original_data:
        detector = Detector.from_parameters(original_data["parameters"])
    else:
        detector = Detector(config)
    tracker_parameters = original_data.get("tracker_parameters", {})
    tracker = Tracker(
        max_gap=tracker_parameters.get("max_gap_seconds", 0.75),
        gate_px=tracker_parameters.get("gate_px", 30),
        confirm_hits=tracker_parameters.get("confirm_hits", 4),
        max_history_points=tracker_parameters.get("max_history_points", 512),
    )
    from ..evidence.index import metadata_index

    camera_id = capture["camera_id"]
    replay_id = uuid4().hex
    revision = ""

    def save(fragments: list[TrackState]) -> None:
        nonlocal revision
        if not fragments:
            return
        revision = uuid4().hex
        data = {
            "class": "unresolved_insufficient_data",
            "reason_codes": ["uncalibrated", "reanalysis_of_preserved_source"],
            "capture_manifest_sha256": event["manifest_sha256"],
            "detector_version": detector.version,
            "parameter_hash": detector.parameter_hash,
            "parameters": detector.parameters(),
            "application_build": build_info(),
            "tracker_version": tracker.version,
            "tracker_parameters": tracker.parameters(),
            "tracks": [track.snapshot() for track in fragments if track.observations],
            "replay_id": replay_id,
            "revision_scope": "track_storage_fragments; all records with this replay_id form one analysis",
        }
        ledger.execute(
            "INSERT INTO analyses VALUES (?,?,?,?)",
            (revision, event_id, utc_now(), json.dumps(data)),
        )

    with (folder / "frames.json").open("rb") as metadata, metadata_index(metadata) as db:
        for segment in capture["coverage"]:
            name = f"source_segments/{segment['id']}.mkv"
            expected = next(a["sha256"] for a in capture["artifacts"] if a["relative_path"] == name)
            if digest(folder / name) != expected:
                raise ValueError("source integrity failure")
            references = db.execute(
                "SELECT data_json FROM frames WHERE segment_id=? ORDER BY ordinal", (segment["id"],)
            )
            with av.open(str(folder / name)) as source:
                for frame, stored in zip(source.decode(video=0), references, strict=True):
                    row = json.loads(stored["data_json"])
                    if row["mapping_state"] != "exact" or row["media_seconds"] is None:
                        continue
                    result = detector.process(
                        np.asarray(frame.to_ndarray(format="bgr24"), dtype=np.uint8),
                        row["id"],
                        camera_id,
                        event["session_id"],
                    )
                    tracker.update(row["media_seconds"], result.candidates)
                    save(tracker.drain_ended())
    for track in tracker.tracks:
        track.state, track.end_reason = "ended", "preserved_capture_ended"
    save(tracker.tracks)
    if not revision:
        # An explicit empty analysis is meaningful; never invent a track.
        revision = uuid4().hex
        data = {
            "class": "unresolved_insufficient_data",
            "reason_codes": ["no_candidates_in_reanalysis", "uncalibrated"],
            "detector_version": detector.version,
            "tracker_version": tracker.version,
            "parameters": detector.parameters(),
            "parameter_hash": detector.parameter_hash,
            "tracks": [],
            "replay_id": replay_id,
            "application_build": build_info(),
            "tracker_parameters": tracker.parameters(),
            "result": "no_candidates",
        }
        ledger.execute(
            "INSERT INTO analyses VALUES (?,?,?,?)",
            (revision, event_id, utc_now(), json.dumps(data)),
        )
    return revision
