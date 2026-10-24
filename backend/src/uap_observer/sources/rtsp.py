"""Generic local RTSP source backed by the same recorder/ledger as file capture."""

import json
from collections.abc import Iterator
from pathlib import Path
from queue import Empty
from typing import Literal

from ..capture.analysis import ArchiveAnalysis
from ..capture.recorder import Recorder
from ..capture.tools import validate_input
from ..models import FramePacket, SourceHealth, SourceKind, StreamInfo
from ..storage.ledger import Ledger
from .base import CameraSource, DecodedFrame


class RTSPSource(CameraSource):
    def __init__(
        self, source_id: str, camera_id: str, url: str | None = None, workspace: Path | None = None
    ) -> None:
        self.source_id, self.camera_id = source_id, camera_id
        self.url = validate_input(url) if url else None
        self.workspace = workspace
        self.recorder: Recorder | None = None
        self.analysis: ArchiveAnalysis | None = None
        self.ledger: Ledger | None = None

    def start(self) -> None:
        if self.recorder:
            return
        if not self.url or self.workspace is None:
            raise NotImplementedError("Supply loopback RTSP URL and a private capture workspace")
        self.ledger = Ledger(self.workspace / "ledger.sqlite")
        self.recorder = Recorder(self.ledger, self.workspace / "archive", self.url, self.camera_id)
        self.analysis = ArchiveAnalysis(self.ledger)
        self.analysis.start()
        self.recorder.start()

    def probe(self) -> StreamInfo:
        if not self.ledger or not self.recorder:
            raise NotImplementedError("Start configured source before inspecting measured stream")
        rows = self.ledger.query(
            "SELECT stream_json FROM sessions WHERE id=?", (self.recorder.session_id,)
        )
        data = json.loads(rows[0]["stream_json"]) if rows else {}
        if not data:
            raise RuntimeError("stream probe not yet available")
        from fractions import Fraction

        return StreamInfo(
            source_id=self.source_id,
            camera_id=self.camera_id,
            kind=SourceKind.rtsp,
            codec=data["codec"],
            width=data["width"],
            height=data["height"],
            fps=float(Fraction(data["fps"])) if data["fps"] != "None" else None,
            time_base=data["time_base"],
            has_audio=False,
        )

    def stop(self) -> None:
        if self.recorder:
            self.recorder.stop()
        if self.analysis:
            self.analysis.stop()
        self.recorder = None

    def get_health(self) -> SourceHealth:
        if not self.recorder:
            return SourceHealth(
                source_id=self.source_id, state="unsupported" if not self.url else "stopped"
            )
        state: Literal["failed", "ended", "running"] = (
            "failed"
            if self.recorder.error
            else "ended"
            if self.recorder.done.is_set()
            else "running"
        )
        return SourceHealth(
            source_id=self.source_id,
            state=state,
            decoded_frames=int(
                self.ledger.query(
                    "SELECT count(*) AS n FROM frames WHERE session_id=?",
                    (self.recorder.session_id,),
                )[0]["n"]
            )
            if self.ledger
            else 0,
            detail=self.recorder.error,
        )

    def frames(self) -> Iterator[DecodedFrame]:
        if not self.recorder or not self.analysis:
            raise RuntimeError("start source before reading")
        sequence = 0
        while (
            not self.recorder.done.is_set()
            or not self.analysis.queue.empty()
            or (
                self.ledger
                and self.ledger.query(
                    "SELECT id FROM segments WHERE session_id=? AND indexed=0",
                    (self.recorder.session_id,),
                )
            )
        ):
            try:
                frame = self.analysis.queue.get(timeout=0.1)
            except Empty:
                continue
            row = frame.metadata
            yield DecodedFrame(
                FramePacket(
                    source_id=self.source_id,
                    camera_id=self.camera_id,
                    session_id=row["session_id"],
                    frame_id=row["id"],
                    source_frame_index=None,
                    analysis_frame_index=sequence,
                    source_packet_sequence=row["source_sequence"],
                    source_pts=row["source_pts"],
                    time_base=row["source_time_base"],
                    relative_seconds=max(0, row["media_seconds"] or 0),
                    timestamp_basis="archived_source_pts",
                    segment_id=row["segment_id"],
                    source_packet_id=row["packet_id"],
                    mapping_state=row["mapping_state"],
                    receipt_utc=row["receipt_utc"],
                    width=frame.pixels.shape[1],
                    height=frame.pixels.shape[0],
                ),
                frame.pixels,
            )
            sequence += 1
