import json
import shutil
import threading
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..build import build_info
from ..capture.tools import utc_now
from ..models import CaptureManifest, ExportManifest
from ..storage.ledger import Ledger
from .files import atomic_write, canonical, digest, safe_member, sync_directory, write_json_records


class EvidenceService:
    def __init__(self, ledger: Ledger, root: Path) -> None:
        self.ledger, self.root = ledger, root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def trigger(
        self, session_id: str, start: float, end: float, pre: float = 30, post: float = 45
    ) -> str:
        if end < start or pre < 0 or post < 0:
            raise ValueError("invalid event time window")
        event_id = uuid4().hex
        with self.ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO events(id,session_id,start_seconds,end_seconds,requested_start,requested_end,state,created_utc) VALUES (?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    session_id,
                    start,
                    end,
                    start - pre,
                    end + post,
                    "capturing_postroll",
                    utc_now(),
                ),
            )
            db.execute(
                "INSERT INTO event_segments SELECT ?,id FROM segments WHERE session_id=? AND state='sealed' AND start_seconds<=? AND end_seconds>=?",
                (event_id, session_id, end + post, start - pre),
            )
            db.execute(
                "INSERT INTO audit(observed_utc,action,subject_id,detail) VALUES (?,?,?,?)",
                (utc_now(), "event_trigger", event_id, "segment references pinned transactionally"),
            )
        return event_id

    def event(self, event_id: str) -> dict[str, Any]:
        rows = self.ledger.query("SELECT * FROM events WHERE id=?", (event_id,))
        if not rows:
            raise KeyError("event unavailable")
        return rows[0]

    def segments(self, event_id: str) -> list[dict[str, Any]]:
        return self.ledger.query(
            "SELECT segments.* FROM segments JOIN event_segments ON segments.id=event_segments.segment_id WHERE event_id=? ORDER BY start_seconds",
            (event_id,),
        )

    def seal(self, event_id: str) -> bool:
        with self.lock:
            return self._seal(event_id)

    def _seal(self, event_id: str) -> bool:
        event = self.event(event_id)
        if event["manifest_sha256"]:
            manifest = Path(event["manifest_path"])
            if digest(manifest) != event["manifest_sha256"]:
                raise ValueError("capture manifest integrity failure")
            return True
        existing_manifest = self.root / "events" / event_id / "capture_manifest.json"
        if existing_manifest.is_file():
            sealed = CaptureManifest.model_validate_json(existing_manifest.read_bytes())
            if sealed.capture_id != event_id or sealed.session_id != event["session_id"]:
                raise ValueError("orphan capture identity mismatch")
            for sealed_artifact in sealed.artifacts:
                path = safe_member(existing_manifest.parent, sealed_artifact.relative_path)
                if not path.is_file() or digest(path) != sealed_artifact.sha256:
                    raise ValueError("orphan sealed capture artifact integrity failure")
            self.ledger.execute(
                "UPDATE events SET state=?,manifest_path=?,manifest_sha256=?,limitations_json=? WHERE id=?",
                (
                    "sealed" if sealed.complete else "incomplete_source_gap",
                    str(existing_manifest),
                    digest(existing_manifest),
                    json.dumps(sealed.limitations),
                    event_id,
                ),
            )
            self.ledger.execute(
                "INSERT INTO audit(observed_utc,action,subject_id,detail) VALUES (?,?,?,?)",
                (utc_now(), "adopt_sealed_capture", event_id, digest(existing_manifest)),
            )
            return True
        session = self.ledger.query("SELECT * FROM sessions WHERE id=?", (event["session_id"],))[0]
        with self.ledger.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO event_segments SELECT ?,id FROM segments WHERE session_id=? AND state='sealed' AND start_seconds<=? AND end_seconds>=?",
                (event_id, event["session_id"], event["requested_end"], event["requested_start"]),
            )
        segments = self.segments(event_id)
        if any(segment["indexed"] == 0 for segment in segments):
            return False
        latest = max((s["end_seconds"] or 0 for s in segments), default=0)
        if session["state"] in ("running", "connecting") and latest < event["requested_end"]:
            return False
        limitations = ["frame_index_unavailable"] if any(s["indexed"] < 0 for s in segments) else []
        cursor = event["requested_start"]
        for segment in segments:
            if segment["start_seconds"] is None or segment["end_seconds"] is None:
                limitations.append("unknown_segment_timestamps")
                continue
            if segment["start_seconds"] > cursor + 0.001:
                limitations.append(f"source_gap:{cursor:.6f}:{segment['start_seconds']:.6f}")
            cursor = max(cursor, segment["end_seconds"])
        if cursor < event["requested_end"] - 0.001:
            limitations.append("postroll_unavailable")
        gaps = self.ledger.query(
            "SELECT observed_utc,reason FROM gaps WHERE session_id=?", (event["session_id"],)
        )
        if gaps:
            limitations.append("session_contains_reported_source_gap")
        if not segments:
            limitations.append("no_source_segments")
        folder = self.root / "events" / event_id
        stage = folder.with_name(event_id + ".staging")
        stage.mkdir(parents=True, exist_ok=True)
        artifacts = []
        coverage = []
        for segment in segments:
            source = Path(segment["path"])
            if not source.is_file() or digest(source) != segment["sha256"]:
                raise ValueError("source segment integrity failure")
            name = f"source_segments/{segment['id']}.mkv"
            target = safe_member(stage, name)
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(source, target)
            artifacts.append(
                {"relative_path": name, "sha256": segment["sha256"], "size_bytes": segment["bytes"]}
            )
            coverage.append(
                {k: segment[k] for k in ("id", "session_id", "start_seconds", "end_seconds")}
            )
        with self.ledger.connect() as db:
            write_json_records(
                stage / "frames.json",
                (
                    dict(row)
                    for row in db.execute(
                        "SELECT frames.* FROM frames JOIN event_segments ON frames.segment_id=event_segments.segment_id WHERE event_id=? ORDER BY media_seconds",
                        (event_id,),
                    )
                ),
            )
        artifacts.append(
            {
                "relative_path": "frames.json",
                "sha256": digest(stage / "frames.json"),
                "size_bytes": (stage / "frames.json").stat().st_size,
            }
        )
        with self.ledger.connect() as db:
            write_json_records(
                stage / "packets.json",
                (
                    dict(row)
                    for row in db.execute(
                        "SELECT packets.* FROM packets JOIN event_segments ON packets.segment_id=event_segments.segment_id WHERE event_id=? ORDER BY sequence",
                        (event_id,),
                    )
                ),
            )
        artifacts.append(
            {
                "relative_path": "packets.json",
                "sha256": digest(stage / "packets.json"),
                "size_bytes": (stage / "packets.json").stat().st_size,
            }
        )
        recorded_stream = json.loads(session["stream_json"])
        data = {
            "schema_version": "1.1",
            "source_id": recorded_stream.get("source_id"),
            "capture_id": event_id,
            "camera_id": session["camera_id"],
            "session_id": event["session_id"],
            "requested_interval": [event["requested_start"], event["requested_end"]],
            "trigger_interval": [event["start_seconds"], event["end_seconds"]],
            "stream_info": json.loads(session["stream_json"]),
            "complete": not limitations,
            "limitations": limitations,
            "gaps": gaps,
            "coverage": coverage,
            "artifacts": artifacts,
            "timestamp_basis": "original_source_pts",
            "exposure_utc_quality": "unknown",
            "audio_recorded": False,
            "source_preservation": "encoded packet copy/remux; not raw transport bytes",
        }
        data = CaptureManifest.model_validate(data).model_dump(mode="json", exclude_none=True)
        atomic_write(stage / "capture_manifest.json", canonical(data))
        if folder.exists():
            # Recovery after directory seal but before database commit: never overwrite.
            existing = json.loads((folder / "capture_manifest.json").read_text())
            if existing != data:
                raise ValueError("sealed capture conflicts with recovery snapshot")
            shutil.rmtree(stage)
        else:
            for artifact in artifacts:
                with safe_member(stage, artifact["relative_path"]).open("rb") as handle:
                    import os

                    os.fsync(handle.fileno())
            sync_directory(stage / "source_segments") if (
                stage / "source_segments"
            ).exists() else None
            stage.replace(folder)
            sync_directory(folder.parent)
        manifest = folder / "capture_manifest.json"
        with self.ledger.connect() as db:
            db.execute(
                "UPDATE events SET state=?,manifest_path=?,manifest_sha256=?,limitations_json=? WHERE id=? AND manifest_sha256 IS NULL",
                (
                    "sealed" if not limitations else "incomplete_source_gap",
                    str(manifest),
                    digest(manifest),
                    json.dumps(limitations),
                    event_id,
                ),
            )
            db.execute(
                "INSERT INTO audit(observed_utc,action,subject_id,detail) VALUES (?,?,?,?)",
                (utc_now(), "capture_sealed", event_id, digest(manifest)),
            )
        return True

    def review(self, event_id: str, assessment: str, note: str) -> int:
        self.event(event_id)
        allowed = {
            "not_evaluable",
            "likely_artifact",
            "likely_conventional",
            "unresolved_insufficient_data",
            "unresolved_review_candidate",
            "reviewed_identified",
        }
        if assessment not in allowed or len(note) > 4000:
            raise ValueError("invalid review")
        with self.ledger.connect() as db:
            cursor = db.execute(
                "INSERT INTO reviews(event_id,created_utc,assessment,note) VALUES (?,?,?,?)",
                (event_id, utc_now(), assessment, note),
            )
            return int(cursor.lastrowid or 0)

    def export(self, event_id: str) -> Path:
        with self.lock:
            return self._export(event_id)

    def _export(self, event_id: str) -> Path:
        if not self.seal(event_id):
            raise ValueError("event still capturing postroll")
        event = self.event(event_id)
        source = Path(event["manifest_path"]).parent
        capture = CaptureManifest.model_validate_json(
            (source / "capture_manifest.json").read_bytes()
        ).model_dump(mode="json", exclude_none=True)
        if digest(source / "capture_manifest.json") != event["manifest_sha256"]:
            raise ValueError("sealed capture integrity failure")
        export_id = uuid4().hex
        folder = self.root / "exports"
        folder.mkdir(exist_ok=True)
        target = folder / f"{export_id}.zip"
        file_artifacts: dict[str, Path] = {}
        artifact_manifest = []
        for artifact in capture["artifacts"]:
            path = safe_member(source, artifact["relative_path"])
            if not path.is_file() or digest(path) != artifact["sha256"]:
                raise ValueError("capture artifact integrity failure")
            file_artifacts[artifact["relative_path"]] = path
            artifact_manifest.append(artifact)
        file_artifacts["capture_manifest.json"] = source / "capture_manifest.json"
        artifact_manifest.append(
            {
                "relative_path": "capture_manifest.json",
                "size_bytes": file_artifacts["capture_manifest.json"].stat().st_size,
                "sha256": event["manifest_sha256"],
            }
        )
        from tempfile import TemporaryDirectory

        from .index import items

        # One read transaction freezes selected reviews/analyses while streaming to disk.
        with TemporaryDirectory(prefix="snapshot-", dir=folder) as temporary:
            snapshot = Path(temporary)
            with self.ledger.connect() as db:
                db.execute("BEGIN")
                write_json_records(
                    snapshot / "review.json",
                    (
                        dict(row)
                        for row in db.execute(
                            "SELECT id,created_utc,assessment,note FROM reviews WHERE event_id=? ORDER BY id",
                            (event_id,),
                        )
                    ),
                )
                write_json_records(
                    snapshot / "analyses.json",
                    (
                        dict(row)
                        for row in db.execute(
                            "SELECT id,created_utc,data_json FROM analyses WHERE event_id=? ORDER BY created_utc,id",
                            (event_id,),
                        )
                    ),
                )
                last_review = db.execute(
                    "SELECT id FROM reviews WHERE event_id=? ORDER BY id DESC LIMIT 1", (event_id,)
                ).fetchone()
                last_analysis = db.execute(
                    "SELECT id FROM analyses WHERE event_id=? ORDER BY created_utc DESC,id DESC LIMIT 1",
                    (event_id,),
                ).fetchone()
            with (snapshot / "analyses.json").open("rb") as handle:
                legacy_parameters = any(
                    "parameters" not in json.loads(a["data_json"]) for a in items(handle)
                )
            for name in ("review.json", "analyses.json"):
                file_artifacts[name] = snapshot / name
                artifact_manifest.append(
                    {
                        "relative_path": name,
                        "size_bytes": (snapshot / name).stat().st_size,
                        "sha256": digest(snapshot / name),
                    }
                )
            manifest = ExportManifest.model_validate(
                {
                    "schema_version": "1.1",
                    "export_build": build_info(),
                    "processing_metadata": "legacy_partial"
                    if legacy_parameters
                    else "complete"
                    if last_analysis
                    else "not_analyzed",
                    "limitations": ["legacy_detector_parameters_unavailable"]
                    if legacy_parameters
                    else [],
                    "export_id": export_id,
                    "capture_id": event_id,
                    "capture_manifest_sha256": event["manifest_sha256"],
                    "review_revision": last_review["id"] if last_review else None,
                    "analysis_revision": last_analysis["id"] if last_analysis else None,
                    "artifacts": artifact_manifest,
                }
            ).model_dump(mode="json", exclude_none=True)
            with zipfile.ZipFile(
                target.with_suffix(".partial"), "x", zipfile.ZIP_STORED
            ) as archive:
                for name, path in file_artifacts.items():
                    archive.write(path, name)
                archive.writestr("manifest.json", canonical(manifest))
        with target.with_suffix(".partial").open("rb") as handle:
            import os

            os.fsync(handle.fileno())
        target.with_suffix(".partial").replace(target)
        sync_directory(target.parent)
        self.ledger.execute(
            "INSERT INTO exports VALUES (?,?,?,?,?)",
            (export_id, event_id, str(target), digest(target), utc_now()),
        )
        return target

    def cleanup(self, max_bytes: int, reserve_bytes: int = 0) -> dict[str, Any]:
        if max_bytes < 0 or reserve_bytes < 0:
            raise ValueError("invalid quota")
        removed = []
        while True:
            free = shutil.disk_usage(self.root).free
            with self.ledger.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                total = int(
                    db.execute(
                        "SELECT coalesce(sum(bytes),0) FROM segments WHERE state='sealed'"
                    ).fetchone()[0]
                )
                if total <= max_bytes and free >= reserve_bytes:
                    break
                candidates = db.execute("""SELECT * FROM segments s WHERE state='sealed'
                    AND NOT EXISTS(SELECT 1 FROM event_segments es WHERE es.segment_id=s.id)
                    AND NOT EXISTS(SELECT 1 FROM events e WHERE e.session_id=s.session_id
                      AND e.state='capturing_postroll' AND e.requested_start<=s.end_seconds AND e.requested_end>=s.start_seconds)
                    ORDER BY s.rowid LIMIT 64""").fetchall()
                if not candidates:
                    break
                selected = []
                for segment in candidates:
                    selected.append(dict(segment))
                    db.execute("UPDATE segments SET state='deleting' WHERE id=?", (segment["id"],))
                    total -= segment["bytes"]
                    free += segment["bytes"]
                    if total <= max_bytes and free >= reserve_bytes:
                        break
            # No filesystem operation occurs while the recorder's SQLite writer lock is held.
            for segment in selected:
                try:
                    Path(segment["path"]).unlink(missing_ok=True)
                    Path(segment["path"]).with_suffix(".ledger.json").unlink(missing_ok=True)
                except OSError:
                    if Path(segment["path"]).exists():
                        self.ledger.execute(
                            "UPDATE segments SET state='sealed' WHERE id=?", (segment["id"],)
                        )
                    raise
                self.ledger.execute(
                    "UPDATE segments SET state='deleted' WHERE id=?", (segment["id"],)
                )
                removed.append(segment["id"])
        self.ledger.execute(
            "INSERT INTO audit(observed_utc,action,subject_id,detail) VALUES (?,?,?,?)",
            (utc_now(), "retention_cleanup", "archive", json.dumps(removed)),
        )
        total = int(
            self.ledger.query(
                "SELECT coalesce(sum(bytes),0) AS n FROM segments WHERE state='sealed'"
            )[0]["n"]
        )
        return {
            "removed": removed,
            "retained_bytes": total,
            "quota_exceeded": total > max_bytes,
            "reserve_low": shutil.disk_usage(self.root).free < reserve_bytes,
        }

    def recover(self, archive: Path) -> dict[str, int]:
        recovered = 0
        for metadata_path in archive.glob("*/*.ledger.json"):
            meta = json.loads(metadata_path.read_text())
            path = metadata_path.with_suffix("").with_suffix(".mkv")
            if not path.exists() or self.ledger.query(
                "SELECT id FROM segments WHERE id=?", (meta["id"],)
            ):
                continue
            # A checksum recorded before source rename is required for unambiguous recovery.
            if (
                "sha256" not in meta
                or digest(path) != meta["sha256"]
                or path.stat().st_size != meta["bytes"]
            ):
                self.ledger.execute(
                    "INSERT INTO gaps(session_id,observed_utc,reason) VALUES (?,?,?)",
                    (meta["session_id"], utc_now(), "orphan_segment_integrity_unavailable"),
                )
                self.ledger.execute(
                    "INSERT INTO health(session_id,observed_utc,kind,detail) VALUES (?,?,?,?)",
                    (meta["session_id"], utc_now(), "orphan_quarantined", meta["id"]),
                )
                continue
            with self.ledger.connect() as db:
                db.execute(
                    "INSERT INTO segments VALUES (?,?,?,?,?,?,?,?,0)",
                    (
                        meta["id"],
                        meta["session_id"],
                        str(path),
                        digest(path),
                        path.stat().st_size,
                        meta["start_seconds"],
                        meta["end_seconds"],
                        "sealed",
                    ),
                )
                db.executemany(
                    "INSERT INTO packets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", meta["packets"]
                )
                db.execute(
                    "INSERT OR IGNORE INTO event_segments SELECT id,? FROM events WHERE session_id=? AND state='capturing_postroll' AND requested_start<=? AND requested_end>=?",
                    (meta["id"], meta["session_id"], meta["end_seconds"], meta["start_seconds"]),
                )
            recovered += 1
        for row in self.ledger.query(
            "SELECT id FROM sessions WHERE state IN ('running','connecting')"
        ):
            self.ledger.execute(
                "UPDATE sessions SET state='interrupted',ended_utc=? WHERE id=?",
                (utc_now(), row["id"]),
            )
            self.ledger.execute(
                "INSERT INTO gaps(session_id,observed_utc,reason) VALUES (?,?,?)",
                (row["id"], utc_now(), "restart_interruption"),
            )
        for row in self.ledger.query("SELECT * FROM segments WHERE state='deleting'"):
            Path(row["path"]).unlink(missing_ok=True)
            Path(row["path"]).with_suffix(".ledger.json").unlink(missing_ok=True)
            self.ledger.execute("UPDATE segments SET state='deleted' WHERE id=?", (row["id"],))
        sealed = 0
        for event in self.ledger.query("SELECT id FROM events WHERE state='capturing_postroll'"):
            sealed += int(self.seal(event["id"]))
        return {
            "recovered_segments": recovered,
            "recovered_events": sealed,
            "unfinished_source_files": len(list(archive.glob("*/*.partial"))),
        }
