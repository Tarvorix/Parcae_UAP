import json
import subprocess
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

from ..models import FramePacket, SourceHealth, SourceKind, StreamInfo
from .base import CameraSource, DecodedFrame


class VideoFileSource(CameraSource):
    """Finite local files only. No live capture or archived-segment mapping yet."""

    def __init__(
        self, path: Path, source_id: str = "video-file", camera_id: str = "fixture-sky"
    ) -> None:
        self.path = path.resolve(strict=True)
        if not self.path.is_file():
            raise ValueError("source must be a local regular file")
        self.source_id, self.camera_id = source_id, camera_id
        self.session_id = ""
        self.health = SourceHealth(source_id=source_id, state="stopped")
        self.capture: cv2.VideoCapture | None = None

    def _inspect(self) -> dict:  # type: ignore[type-arg]
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-show_streams",
                "-show_frames",
                "-of",
                "json",
                str(self.path),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        return json.loads(result.stdout)  # type: ignore[no-any-return]

    def probe(self) -> StreamInfo:
        data = self._inspect()
        stream = next(s for s in data["streams"] if s["codec_type"] == "video")
        rate = Fraction(stream.get("avg_frame_rate", "0/1"))
        return StreamInfo(
            source_id=self.source_id,
            camera_id=self.camera_id,
            kind=SourceKind.video_file,
            codec=stream["codec_name"],
            width=stream["width"],
            height=stream["height"],
            fps=float(rate) if rate else None,
            frame_count=sum(f["media_type"] == "video" for f in data["frames"]),
            has_audio=any(s["codec_type"] == "audio" for s in data["streams"]),
            time_base=stream.get("time_base"),
        )

    def start(self) -> None:
        if self.health.state == "running":
            return
        self.stop()
        self.session_id = uuid4().hex
        self.capture = cv2.VideoCapture(str(self.path))
        if not self.capture.isOpened():
            self.health = SourceHealth(source_id=self.source_id, state="failed", decode_errors=1)
            self.stop_capture()
            raise ValueError("local video cannot be decoded")
        self.health = SourceHealth(source_id=self.source_id, state="running")

    def stop_capture(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def stop(self) -> None:
        self.stop_capture()
        self.health = self.health.model_copy(update={"state": "stopped"})

    def get_health(self) -> SourceHealth:
        return self.health

    def frames(self) -> Iterator[DecodedFrame]:
        if self.capture is None or self.health.state != "running":
            raise RuntimeError("start source before reading")
        try:
            data = self._inspect()
            stream = next(s for s in data["streams"] if s["codec_type"] == "video")
            metadata = [
                f
                for f in data["frames"]
                if f["media_type"] == "video" and f["stream_index"] == stream["index"]
            ]
            rate = Fraction(stream.get("avg_frame_rate", "0/1"))
            origin: float | None = None
            for index, meta in enumerate(metadata):
                if self.health.state != "running" or self.capture is None:
                    return
                ok, pixels = self.capture.read()
                if not ok:
                    raise ValueError("decode ended before probed frame count")
                pts = meta.get("best_effort_timestamp")
                seconds = meta.get("best_effort_timestamp_time")
                if seconds is None and not rate:
                    raise ValueError("file has no usable timestamps or frame rate")
                timestamp = float(seconds) if seconds is not None else index / float(rate)
                if origin is None:
                    origin = timestamp
                packet = FramePacket(
                    source_id=self.source_id,
                    camera_id=self.camera_id,
                    session_id=self.session_id,
                    frame_id=f"{self.session_id}:{index}",
                    source_frame_index=index,
                    source_pts=pts,
                    time_base=stream.get("time_base"),
                    relative_seconds=max(0, timestamp - origin),
                    timestamp_basis="file_best_effort_pts"
                    if seconds is not None
                    else "frame_rate_estimate",
                    width=pixels.shape[1],
                    height=pixels.shape[0],
                )
                self.health = self.health.model_copy(
                    update={
                        "decoded_frames": index + 1,
                        "last_relative_seconds": packet.relative_seconds,
                    }
                )
                yield DecodedFrame(packet, np.asarray(pixels, dtype=np.uint8))
            self.health = self.health.model_copy(update={"state": "ended"})
        except Exception:
            self.health = self.health.model_copy(update={"state": "failed", "decode_errors": 1})
            raise
        finally:
            self.stop_capture()
