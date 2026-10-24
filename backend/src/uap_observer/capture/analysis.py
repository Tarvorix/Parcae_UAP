"""Decode sealed source segments with their packet ledger; bounded consumer queue."""

import hashlib
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any

import av
import numpy as np
import numpy.typing as npt

from ..storage.ledger import Ledger
from .tools import utc_now


@dataclass(frozen=True)
class MappedFrame:
    metadata: dict[str, Any]
    pixels: npt.NDArray[np.uint8]


class ArchiveAnalysis:
    def __init__(self, ledger: Ledger, capacity: int = 8) -> None:
        if capacity < 1:
            raise ValueError("analysis queue capacity must be positive")
        self.ledger = ledger
        self.queue: Queue[MappedFrame] = Queue(capacity)
        self.dropped = 0
        self.stop_requested = threading.Event()
        self.thread: threading.Thread | None = None
        self.errors: list[str] = []

    def start(self) -> None:
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="archive-analysis", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_requested.set()
        if self.thread:
            self.thread.join(10)
            if self.thread.is_alive():
                raise TimeoutError("archive analysis did not stop")

    def _run(self) -> None:
        while not self.stop_requested.wait(0.05):
            self.process_pending()

    def process_pending(self) -> int:
        count = 0
        for segment in self.ledger.query(
            "SELECT * FROM segments WHERE state='sealed' AND indexed=0 ORDER BY rowid"
        ):
            try:
                count += self.index_segment(segment)
            except Exception as exc:
                reason = f"analysis_failed:{type(exc).__name__}"
                self.errors.append(reason)
                self.ledger.execute("UPDATE segments SET indexed=-1 WHERE id=?", (segment["id"],))
                self.ledger.execute(
                    "INSERT INTO health(session_id,observed_utc,kind,detail) VALUES (?,?,?,?)",
                    (segment["session_id"], utc_now(), "analysis_failure", reason),
                )
        return count

    def index_segment(self, segment: dict[str, Any]) -> int:
        path = Path(segment["path"])
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != segment["sha256"]:
                raise ValueError("sealed segment checksum mismatch")
        packets = self.ledger.query(
            "SELECT * FROM packets WHERE segment_id=? ORDER BY sequence", (segment["id"],)
        )
        by_pts: dict[Fraction, list[dict[str, Any]]] = {}
        for row in packets:
            if row["archive_pts"] is not None:
                key = row["archive_pts"] * Fraction(row["archive_time_base"])
                by_pts.setdefault(key, []).append(row)
        count, drops = 0, 0
        with av.open(str(path), options={"protocol_whitelist": "file,pipe"}) as container:
            for index, frame in enumerate(container.decode(video=0)):
                tb = frame.time_base
                if tb is None:
                    raise ValueError("decoded frame lacks time base")
                candidates = by_pts.get(frame.pts * tb, []) if frame.pts is not None else []
                match = candidates[0] if len(candidates) == 1 else None
                state = "exact" if match else "ambiguous" if candidates else "unavailable"
                pixels = np.asarray(frame.to_ndarray(format="bgr24"), dtype=np.uint8)
                data = {
                    "id": f"{segment['id']}:{index}",
                    "session_id": segment["session_id"],
                    "segment_id": segment["id"],
                    "segment_frame_index": index,
                    "packet_id": match["id"] if match else None,
                    "archive_pts": frame.pts,
                    "archive_time_base": str(tb),
                    "source_pts": match["source_pts"] if match else None,
                    "source_dts": match["source_dts"] if match else None,
                    "source_time_base": match["source_time_base"] if match else None,
                    "source_sequence": match["sequence"] if match else None,
                    "receipt_utc": match["receipt_utc"] if match else None,
                    "receipt_monotonic": match["receipt_monotonic"] if match else None,
                    "media_seconds": float(
                        match["source_pts"] * Fraction(match["source_time_base"])
                    )
                    if match and match["source_pts"] is not None
                    else None,
                    "mapping_state": state,
                    "pixel_sha256": hashlib.sha256(pixels.tobytes()).hexdigest(),
                }
                columns = ",".join(data)
                self.ledger.execute(
                    f"INSERT OR IGNORE INTO frames({columns}) VALUES ({','.join('?' for _ in data)})",
                    tuple(data.values()),
                )
                mapped = MappedFrame(data, pixels)
                try:
                    self.queue.put_nowait(mapped)
                except Full:
                    try:
                        self.queue.get_nowait()
                        drops += 1
                    except Empty:
                        pass
                    self.queue.put_nowait(mapped)
                count += 1
        self.dropped += drops
        with self.ledger.connect() as db:
            db.execute("UPDATE segments SET indexed=1 WHERE id=?", (segment["id"],))
            db.execute(
                "UPDATE sessions SET dropped_analysis=dropped_analysis+? WHERE id=?",
                (drops, segment["session_id"]),
            )
            if drops:
                db.execute(
                    "INSERT INTO health(session_id,observed_utc,kind,detail) VALUES (?,?,?,?)",
                    (segment["session_id"], utc_now(), "analysis_drops", str(drops)),
                )
        return count

    def lookup(self, frame_id: str) -> MappedFrame:
        rows = self.ledger.query(
            "SELECT frames.*,segments.path,segments.sha256 FROM frames JOIN segments ON segments.id=frames.segment_id WHERE frames.id=?",
            (frame_id,),
        )
        if not rows:
            raise KeyError("frame not indexed")
        row = rows[0]
        path = Path(row["path"])
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != row["sha256"]:
                raise ValueError("source evidence corrupt")
        with av.open(str(path), options={"protocol_whitelist": "file,pipe"}) as src:
            for index, frame in enumerate(src.decode(video=0)):
                if index == row["segment_frame_index"]:
                    pixels = np.asarray(frame.to_ndarray(format="bgr24"), dtype=np.uint8)
                    if hashlib.sha256(pixels.tobytes()).hexdigest() != row["pixel_sha256"]:
                        raise ValueError("source frame checksum mismatch")
                    return MappedFrame(row, pixels)
        raise ValueError("indexed source frame absent")
