"""PyAV/FFmpeg packet-copy recorder; never waits for the analysis consumer."""

import hashlib
import json
import os
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

import av

from ..build import build_info
from ..evidence.files import atomic_write, canonical, sync_directory
from ..storage.ledger import Ledger
from .tools import utc_now, validate_input


class Recorder:
    def __init__(
        self,
        ledger: Ledger,
        root: Path,
        source: str,
        camera_id: str = "fixture-sky",
        segment_seconds: float = 1.0,
        realtime: bool = False,
        transport: str = "tcp",
        read_timeout: float = 3.0,
        reset_segment_timestamps: bool = False,
        max_segment_packets: int = 10000,
    ) -> None:
        if segment_seconds <= 0 or transport not in ("tcp", "udp") or max_segment_packets < 1:
            raise ValueError("invalid capture settings")
        self.ledger, self.root = ledger, root.resolve()
        self.source = validate_input(source)
        self.camera_id = camera_id
        self.segment_seconds, self.realtime = segment_seconds, realtime
        self.transport, self.read_timeout = transport, read_timeout
        self.reset_segment_timestamps = reset_segment_timestamps
        self.max_segment_packets = max_segment_packets
        self.session_id = uuid4().hex
        self.stop_requested = threading.Event()
        self.done = threading.Event()
        self.thread: threading.Thread | None = None
        self.error: str | None = None
        self.packet_count = 0
        self.last_packet_monotonic: float | None = None
        self.output: Any = None
        self.out_stream: Any = None
        self.segment_id = ""
        self.partial: Path | None = None
        self.rows: list[tuple[Any, ...]] = []
        self.segment_start: float | None = None
        self.segment_end: float | None = None
        self.offset = 0
        self.started = time.monotonic()

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(
            target=self._run, name=f"capture-{self.session_id}", daemon=True
        )
        self.thread.start()

    def stop(self, timeout: float = 8) -> None:
        self.stop_requested.set()
        if self.thread:
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise TimeoutError("capture did not stop within its configured read timeout")

    def wait(self, timeout: float = 30) -> None:
        if not self.done.wait(timeout):
            raise TimeoutError("capture did not finish")
        if self.error:
            raise RuntimeError(self.error)

    def _open_segment(self, stream: Any, seconds: float | None, dts: int | None) -> None:
        self.segment_id = uuid4().hex
        folder = self.root / self.session_id
        folder.mkdir(parents=True, exist_ok=True)
        self.partial = folder / f"{self.segment_id}.partial"
        self.output = av.open(
            str(self.partial), "w", format="matroska", options={"avoid_negative_ts": "disabled"}
        )
        self.out_stream = self.output.add_stream_from_template(stream)
        self.segment_start, self.segment_end = seconds, seconds
        self.offset = (dts or 0) if self.reset_segment_timestamps else 0
        self.rows = []

    def _seal(self) -> None:
        if self.output is None or self.partial is None:
            return
        self.output.close()
        self.output = None
        final = self.partial.with_suffix(".mkv")
        with self.partial.open("rb") as handle:
            os.fsync(handle.fileno())
            sealed_hash = hashlib.file_digest(handle, "sha256").hexdigest()
        metadata = {
            "sha256": sealed_hash,
            "bytes": self.partial.stat().st_size,
            "id": self.segment_id,
            "session_id": self.session_id,
            "path": str(final),
            "start_seconds": self.segment_start,
            "end_seconds": self.segment_end,
            "packets": self.rows,
        }
        atomic_write(final.with_suffix(".ledger.json"), canonical(metadata))
        self.partial.replace(final)
        sync_directory(final.parent)
        digest = sealed_hash
        with self.ledger.connect() as db:
            db.execute(
                "INSERT INTO segments VALUES (?,?,?,?,?,?,?,?,0)",
                (
                    self.segment_id,
                    self.session_id,
                    str(final),
                    digest,
                    final.stat().st_size,
                    self.segment_start,
                    self.segment_end,
                    "sealed",
                ),
            )
            db.executemany("INSERT INTO packets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", self.rows)
            db.execute(
                """INSERT OR IGNORE INTO event_segments SELECT id,? FROM events
                       WHERE session_id=? AND state='capturing_postroll'
                       AND requested_start<=? AND requested_end>=?""",
                (self.segment_id, self.session_id, self.segment_end, self.segment_start),
            )
        self.rows = []

    def _run(self) -> None:
        try:
            self._capture()
        except Exception as exc:
            self.error = f"capture_failed:{type(exc).__name__}"
        finally:
            self.done.set()

    def _capture(self) -> None:
        live = self.source.startswith(("rtsp://", "rtsps://"))
        self.ledger.execute(
            "INSERT OR IGNORE INTO cameras VALUES (?,?)",
            (self.camera_id, "rtsp" if live else "video_file"),
        )
        source_id = f"{self.camera_id}:{'rtsp' if live else 'video_file'}"
        public_config = {
            "camera_id": self.camera_id,
            "kind": "rtsp" if live else "video_file",
            "transport": self.transport if live else "file",
            "audio_recorded": False,
            "nominal_segment_seconds": self.segment_seconds,
            "read_timeout_seconds": self.read_timeout,
            "reset_segment_timestamps": self.reset_segment_timestamps,
            "max_segment_packets": self.max_segment_packets,
        }
        provenance = {
            "source_id": source_id,
            "source_kind": "rtsp" if live else "video_file",
            "source_config_sha256": hashlib.sha256(canonical(public_config)).hexdigest(),
            "application_build": build_info(),
            "source_configuration": public_config.copy(),
        }
        self.ledger.execute(
            "INSERT INTO sessions(id,camera_id,started_utc,state,stream_json) VALUES (?,?,?,?,?)",
            (self.session_id, self.camera_id, utc_now(), "connecting", json.dumps(provenance)),
        )
        try:
            if not live:
                with Path(self.source).open("rb") as handle:
                    source_file_hash = hashlib.file_digest(handle, "sha256").hexdigest()
                provenance["source_file_sha256"] = source_file_hash
                public_config["source_file_sha256"] = source_file_hash
                provenance["source_configuration"] = public_config.copy()
                provenance["source_config_sha256"] = hashlib.sha256(
                    canonical(public_config)
                ).hexdigest()
            options = (
                {"rtsp_transport": self.transport} if live else {"protocol_whitelist": "file,pipe"}
            )
            with av.open(
                self.source, options=options, timeout=(self.read_timeout, self.read_timeout)
            ) as src:
                stream = src.streams.video[0]
                info = {
                    **provenance,
                    "codec": stream.codec_context.name,
                    "width": stream.width,
                    "height": stream.height,
                    "fps": str(stream.average_rate),
                    "time_base": str(stream.time_base),
                    "audio_recorded": False,
                    "pyav_version": av.__version__,
                    "libraries": av.library_versions,
                }
                self.ledger.execute(
                    "UPDATE sessions SET state='running',stream_json=? WHERE id=?",
                    (json.dumps(info), self.session_id),
                )
                first: float | None = None
                last_dts_time: Fraction | None = None
                previous_duration = 0.0
                initial_wait_packets = 0
                for packet in src.demux(stream):
                    if self.stop_requested.is_set():
                        break
                    if packet.size == 0:
                        continue
                    receipt, arrival = utc_now(), time.monotonic()
                    self.last_packet_monotonic = arrival
                    pts, dts, tb = packet.pts, packet.dts, packet.time_base
                    if tb is None:
                        raise ValueError("source packet has no time base")
                    if dts is not None:
                        current_dts = Fraction(dts) * tb
                        if last_dts_time is not None:
                            if current_dts < last_dts_time:
                                raise ValueError("source timestamp reset; new session required")
                            if float(current_dts - last_dts_time) > max(
                                2.0, previous_duration * 1.5 + 0.01
                            ):
                                self.ledger.execute(
                                    "INSERT INTO gaps(session_id,observed_utc,reason) VALUES (?,?,?)",
                                    (
                                        self.session_id,
                                        receipt,
                                        f"source_timeline_gap:{float(last_dts_time):.6f}:{float(current_dts):.6f}",
                                    ),
                                )
                        last_dts_time = current_dts
                        previous_duration = float(packet.duration * tb) if packet.duration else 0.0
                    seconds = float(pts * tb) if pts is not None else None
                    if first is None and seconds is not None:
                        first = seconds
                    if self.realtime and not live and seconds is not None and first is not None:
                        if self.stop_requested.wait(
                            max(0, self.started + seconds - first - time.monotonic())
                        ):
                            break
                    if (
                        self.output is not None
                        and packet.is_keyframe
                        and seconds is not None
                        and self.segment_start is not None
                        and seconds - self.segment_start >= self.segment_seconds
                    ):
                        self._seal()
                    if self.output is None:
                        if not packet.is_keyframe:
                            initial_wait_packets += 1
                            if initial_wait_packets > self.max_segment_packets:
                                raise ValueError("initial keyframe safety limit")
                            self.ledger.execute(
                                "INSERT INTO gaps(session_id,observed_utc,reason) VALUES (?,?,?)",
                                (self.session_id, receipt, "waiting_for_initial_keyframe"),
                            )
                            continue
                        self._open_segment(stream, seconds, dts)
                    if len(self.rows) >= self.max_segment_packets:
                        self.ledger.execute(
                            "INSERT INTO gaps(session_id,observed_utc,reason) VALUES (?,?,?)",
                            (self.session_id, receipt, "keyframe_segment_packet_limit"),
                        )
                        raise ValueError("segment keyframe safety limit")
                    payload_hash = hashlib.sha256(bytes(packet)).hexdigest()
                    keyframe = packet.is_keyframe
                    if pts is not None:
                        packet.pts = pts - self.offset
                    if dts is not None:
                        packet.dts = dts - self.offset
                    packet.stream = self.out_stream
                    self.output.mux(packet)
                    self.rows.append(
                        (
                            f"{self.session_id}:{self.packet_count}",
                            self.session_id,
                            self.segment_id,
                            self.packet_count,
                            pts,
                            dts,
                            str(tb),
                            packet.pts,
                            packet.dts,
                            str(packet.time_base),
                            receipt,
                            arrival,
                            int(keyframe),
                            payload_hash,
                        )
                    )
                    self.packet_count += 1
                    if seconds is not None:
                        duration = (
                            float(packet.duration * packet.time_base) if packet.duration else 0.0
                        )
                        self.segment_end = max(self.segment_end or seconds, seconds + duration)
                self._seal()
                state = "stopped" if self.stop_requested.is_set() else "ended"
                if live and state == "ended":
                    self.ledger.execute(
                        "INSERT INTO gaps(session_id,observed_utc,reason) VALUES (?,?,?)",
                        (self.session_id, utc_now(), "live_source_ended"),
                    )
                self.ledger.execute(
                    "UPDATE sessions SET state=?,ended_utc=? WHERE id=?",
                    (state, utc_now(), self.session_id),
                )
        except Exception as exc:
            # Library exception messages may contain credential-bearing URLs: store only class.
            self.error = f"capture_failed:{type(exc).__name__}"
            try:
                self._seal()
            except Exception:
                pass  # An unfinished artifact remains .partial for explicit recovery.
            self.ledger.execute(
                "UPDATE sessions SET state='failed',ended_utc=?,reason=? WHERE id=?",
                (utc_now(), self.error, self.session_id),
            )
            self.ledger.execute(
                "INSERT INTO gaps(session_id,observed_utc,reason) VALUES (?,?,?)",
                (self.session_id, utc_now(), self.error),
            )
        finally:
            self.done.set()
