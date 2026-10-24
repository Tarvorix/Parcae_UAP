import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MIGRATIONS = [
    """
CREATE TABLE cameras(id TEXT PRIMARY KEY, source_kind TEXT NOT NULL);
CREATE TABLE sessions(id TEXT PRIMARY KEY, camera_id TEXT NOT NULL REFERENCES cameras(id),
 started_utc TEXT NOT NULL, ended_utc TEXT, state TEXT NOT NULL, stream_json TEXT NOT NULL,
 reason TEXT, dropped_analysis INTEGER NOT NULL DEFAULT 0);
CREATE TABLE segments(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 path TEXT UNIQUE NOT NULL, sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
 start_seconds REAL, end_seconds REAL, state TEXT NOT NULL, indexed INTEGER NOT NULL DEFAULT 0);
CREATE INDEX segments_time ON segments(session_id,start_seconds);
CREATE TABLE packets(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 segment_id TEXT NOT NULL REFERENCES segments(id), sequence INTEGER NOT NULL,
 source_pts INTEGER, source_dts INTEGER, source_time_base TEXT NOT NULL,
 archive_pts INTEGER, archive_dts INTEGER, archive_time_base TEXT NOT NULL,
 receipt_utc TEXT NOT NULL, receipt_monotonic REAL NOT NULL, keyframe INTEGER NOT NULL,
 payload_sha256 TEXT NOT NULL, UNIQUE(session_id,sequence));
CREATE INDEX packets_segment_pts ON packets(segment_id,archive_pts);
CREATE TABLE frames(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 segment_id TEXT NOT NULL REFERENCES segments(id), segment_frame_index INTEGER NOT NULL,
 packet_id TEXT REFERENCES packets(id), archive_pts INTEGER, archive_time_base TEXT NOT NULL,
 source_pts INTEGER, source_dts INTEGER, source_time_base TEXT,
 source_sequence INTEGER, receipt_utc TEXT, receipt_monotonic REAL,
 media_seconds REAL, mapping_state TEXT NOT NULL, pixel_sha256 TEXT NOT NULL,
 UNIQUE(segment_id,segment_frame_index));
CREATE TABLE gaps(id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 observed_utc TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE health(id INTEGER PRIMARY KEY, session_id TEXT REFERENCES sessions(id),
 observed_utc TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
""",
    """
CREATE TABLE events(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 start_seconds REAL NOT NULL, end_seconds REAL NOT NULL, requested_start REAL NOT NULL,
 requested_end REAL NOT NULL, state TEXT NOT NULL, created_utc TEXT NOT NULL,
 manifest_path TEXT, manifest_sha256 TEXT, limitations_json TEXT NOT NULL DEFAULT '[]');
CREATE TABLE event_segments(event_id TEXT NOT NULL REFERENCES events(id),
 segment_id TEXT NOT NULL REFERENCES segments(id), PRIMARY KEY(event_id,segment_id));
CREATE TABLE reviews(id INTEGER PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
 created_utc TEXT NOT NULL, assessment TEXT NOT NULL, note TEXT NOT NULL);
CREATE TABLE analyses(id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
 created_utc TEXT NOT NULL, data_json TEXT NOT NULL);
CREATE TABLE exports(id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id),
 path TEXT NOT NULL, sha256 TEXT NOT NULL, created_utc TEXT NOT NULL);
CREATE TABLE audit(id INTEGER PRIMARY KEY, observed_utc TEXT NOT NULL, action TEXT NOT NULL,
 subject_id TEXT NOT NULL, detail TEXT NOT NULL);
CREATE INDEX event_window ON events(session_id,state,requested_start,requested_end);
""",
    """
CREATE TABLE processing_runs(id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
 created_utc TEXT NOT NULL, version TEXT NOT NULL, parameters_json TEXT NOT NULL, metrics_json TEXT);
CREATE TABLE detections(id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES processing_runs(id),
 frame_id TEXT NOT NULL REFERENCES frames(id), candidate_json TEXT NOT NULL,
 PRIMARY KEY(id,run_id));
CREATE TABLE tracks(id TEXT NOT NULL,run_id TEXT NOT NULL REFERENCES processing_runs(id),
 state TEXT NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(id,run_id));
CREATE TABLE event_tracks(event_id TEXT NOT NULL REFERENCES events(id),track_id TEXT NOT NULL,
 run_id TEXT NOT NULL, PRIMARY KEY(event_id,track_id,run_id),
 FOREIGN KEY(track_id,run_id) REFERENCES tracks(id,run_id));
CREATE TABLE frame_quality(run_id TEXT NOT NULL REFERENCES processing_runs(id),
 frame_id TEXT NOT NULL REFERENCES frames(id), flags_json TEXT NOT NULL, suppressed TEXT,
 PRIMARY KEY(run_id,frame_id));
""",
    """
CREATE TABLE jobs(id TEXT PRIMARY KEY,created_utc TEXT NOT NULL,state TEXT NOT NULL,
 session_id TEXT REFERENCES sessions(id),result_json TEXT, error TEXT);
CREATE TABLE settings(revision INTEGER PRIMARY KEY,created_utc TEXT NOT NULL,data_json TEXT NOT NULL);
""",
    """
CREATE INDEX event_segments_segment ON event_segments(segment_id);
""",
]


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            current = int(db.execute("PRAGMA user_version").fetchone()[0])
            if current > len(MIGRATIONS):
                raise ValueError("database schema is newer than this application")
            for index in range(current, len(MIGRATIONS)):
                db.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + MIGRATIONS[index]
                    + f"\nPRAGMA user_version={index + 1};\nCOMMIT;"
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def query(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def execute(self, sql: str, args: tuple[Any, ...] = ()) -> None:
        with self.connect() as db:
            db.execute(sql, args)
