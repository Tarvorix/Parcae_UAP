import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import cv2
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ..capture.analysis import ArchiveAnalysis
from ..detection.detector import DetectorConfig
from ..evidence.files import digest
from ..evidence.verify import verify_package
from .runtime import Runtime


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment: str
    note: str = Field(default="", max_length=4000)


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detector: DetectorConfig
    rectangles: list[list[int]] = Field(default_factory=list)


def review_router(runtime: Runtime) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def known_event(event_id: str) -> dict[str, Any]:
        try:
            return runtime.evidence.event(event_id)
        except KeyError as exc:
            raise HTTPException(404, "Event unavailable") from exc

    @router.get("/stats")
    def stats() -> dict[str, Any]:
        import shutil

        usage = shutil.disk_usage(runtime.data)
        stored = 0
        for path in runtime.data.rglob("*"):
            try:
                if path.is_file():
                    stored += path.stat().st_size
            except FileNotFoundError:
                # Retention may remove an unprotected file during this read-only scan.
                continue
        return {
            "disk_free_bytes": usage.free,
            "application_bytes": stored,
            "events": runtime.ledger.query("SELECT count(*) AS n FROM events")[0]["n"],
            "sessions": runtime.ledger.query("SELECT count(*) AS n FROM sessions")[0]["n"],
            "active_job": bool(runtime.future and not runtime.future.done()),
            "camera_connected": False,
            "rolling_quota_bytes": runtime.station.rolling_quota_bytes,
            "safety_reserve_bytes": runtime.station.safety_reserve_bytes,
            "reserve_low": usage.free < runtime.station.safety_reserve_bytes,
            "active_capture_sessions": runtime.ledger.query(
                "SELECT count(*) AS n FROM sessions WHERE state='running'"
            )[0]["n"],
            "archived_media_seconds": runtime.ledger.query(
                "SELECT coalesce(sum(end_seconds-start_seconds),0) AS seconds FROM segments WHERE state='sealed'"
            )[0]["seconds"],
        }

    @router.post("/demo/start")
    def start() -> dict[str, str]:
        try:
            return {"job_id": runtime.start_fixture()}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        rows = runtime.ledger.query("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not rows:
            raise HTTPException(404, "Job unavailable")
        row = rows[0]
        return {
            "job_id": row["id"],
            "state": row["state"],
            "error": row["error"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    @router.get("/settings")
    def settings() -> dict[str, Any]:
        return runtime.settings()

    @router.put("/settings")
    def save_settings(data: SettingsRequest) -> dict[str, Any]:
        try:
            return runtime.save_settings(data.detector, data.rectangles)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/events")
    def events(
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=20, ge=1, le=100),
        q: str = Query(default="", max_length=120),
    ) -> list[dict[str, Any]]:
        return runtime.ledger.query(
            "SELECT id,session_id,start_seconds,end_seconds,state,created_utc,limitations_json FROM events WHERE id LIKE ? OR created_utc LIKE ? ORDER BY created_utc DESC,id LIMIT ? OFFSET ?",
            ("%" + q + "%", "%" + q + "%", limit, (page - 1) * limit),
        )

    @router.get("/events/{event_id}")
    def detail(event_id: str) -> dict[str, Any]:
        event = known_event(event_id)
        segments = runtime.evidence.segments(event_id)
        analyses = runtime.ledger.query(
            "SELECT id,created_utc,data_json FROM analyses WHERE event_id=? ORDER BY created_utc",
            (event_id,),
        )
        reviews = runtime.ledger.query(
            "SELECT id,created_utc,assessment,note FROM reviews WHERE event_id=? ORDER BY id",
            (event_id,),
        )
        manifest = Path(event["manifest_path"]) if event["manifest_path"] else None
        capture = json.loads(manifest.read_text()) if manifest and manifest.is_file() else None
        return {
            "event_id": event_id,
            "state": event["state"],
            "start_seconds": event["start_seconds"],
            "end_seconds": event["end_seconds"],
            "capture_manifest_sha256": event["manifest_sha256"],
            "capture": capture,
            "segments": [
                {
                    k: s[k]
                    for k in ("id", "sha256", "bytes", "start_seconds", "end_seconds", "state")
                }
                for s in segments
            ],
            "analyses": [
                {"id": a["id"], "created_utc": a["created_utc"], "data": json.loads(a["data_json"])}
                for a in analyses
            ],
            "reviews": reviews,
            "calibration": {"state": "unavailable", "reason": "not_calibrated"},
        }

    @router.post("/events/{event_id}/reviews")
    def review(event_id: str, data: ReviewRequest) -> dict[str, int]:
        known_event(event_id)
        try:
            return {
                "review_revision": runtime.evidence.review(event_id, data.assessment, data.note)
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/events/{event_id}/export")
    def export(event_id: str) -> dict[str, Any]:
        known_event(event_id)
        try:
            path = runtime.evidence.export(event_id)
            verification = verify_package(path)
            if not verification["verified"]:
                raise ValueError("export verification failed")
            return {
                "export_id": path.stem,
                "verification": verification,
                "download_url": f"/api/v1/exports/{path.stem}",
            }
        except (ValueError, OSError) as exc:
            raise HTTPException(
                409, "Evidence unavailable or failed integrity verification"
            ) from exc

    @router.get("/exports/{export_id}")
    def download(export_id: str) -> FileResponse:
        rows = runtime.ledger.query("SELECT path,sha256 FROM exports WHERE id=?", (export_id,))
        if not rows:
            raise HTTPException(404, "Export unavailable")
        path = Path(rows[0]["path"])
        if not path.is_file() or digest(path) != rows[0]["sha256"]:
            raise HTTPException(409, "Export integrity failure")
        return FileResponse(path, media_type="application/zip", filename=f"event-{export_id}.zip")

    def source_segment(event_id: str, segment_id: str) -> Path:
        event = known_event(event_id)
        if not any(s["id"] == segment_id for s in runtime.evidence.segments(event_id)):
            raise HTTPException(404, "Segment unavailable")
        if not event["manifest_path"]:
            raise HTTPException(409, "Capture is not sealed")
        path = Path(event["manifest_path"]).parent / "source_segments" / f"{segment_id}.mkv"
        expected = next(
            s["sha256"] for s in runtime.evidence.segments(event_id) if s["id"] == segment_id
        )
        if not path.is_file() or digest(path) != expected:
            raise HTTPException(409, "Source integrity failure")
        return path

    @router.get("/events/{event_id}/segments/{segment_id}/source")
    def original(event_id: str, segment_id: str) -> FileResponse:
        path = source_segment(event_id, segment_id)
        return FileResponse(
            path, media_type="video/x-matroska", filename=f"source-{segment_id}.mkv"
        )

    @router.get("/events/{event_id}/segments/{segment_id}/preview.mp4")
    def playback(event_id: str, segment_id: str) -> FileResponse:
        source = source_segment(event_id, segment_id)
        derived = runtime.data / "derivatives"
        derived.mkdir(exist_ok=True)
        target = derived / f"{segment_id}.mp4"
        # Unique temporary paths allow concurrent independent requests; no shell parsing.
        if not target.exists():
            from uuid import uuid4

            temporary = derived / f"{uuid4().hex}.mp4"
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-an",
                        "-vf",
                        "setpts=PTS-STARTPTS",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-crf",
                        "18",
                        "-pix_fmt",
                        "yuv420p",
                        "-movflags",
                        "+faststart",
                        str(temporary),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
                temporary.replace(target)
            except (subprocess.SubprocessError, OSError) as exc:
                temporary.unlink(missing_ok=True)
                raise HTTPException(
                    409, "Derivative playback unavailable; source remains available"
                ) from exc
        return FileResponse(target, media_type="video/mp4")

    @router.get("/events/{event_id}/frames/{frame_id}")
    def frame_image(event_id: str, frame_id: str) -> Response:
        known_event(event_id)
        rows = runtime.ledger.query(
            "SELECT frames.id FROM frames JOIN event_segments ON frames.segment_id=event_segments.segment_id WHERE event_id=? AND frames.id=?",
            (event_id, frame_id),
        )
        if not rows:
            raise HTTPException(404, "Frame unavailable")
        try:
            frame = ArchiveAnalysis(runtime.ledger).lookup(frame_id)
            ok, data = cv2.imencode(".jpg", frame.pixels)
            if not ok:
                raise ValueError("encoding failed")
            return Response(
                data.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"}
            )
        except (ValueError, OSError) as exc:
            raise HTTPException(409, "Source integrity failure") from exc

    @router.websocket("/ws/updates")
    async def updates(socket: WebSocket) -> None:
        origin = socket.headers.get("origin")
        if origin and origin not in (
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ):
            await socket.close(code=1008)
            return
        await socket.accept()
        try:
            while True:
                await socket.send_json(
                    {
                        "type": "health",
                        "active_job": bool(runtime.future and not runtime.future.done()),
                        "event_count": runtime.ledger.query("SELECT count(*) AS n FROM events")[0][
                            "n"
                        ],
                    }
                )
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            return

    return router
