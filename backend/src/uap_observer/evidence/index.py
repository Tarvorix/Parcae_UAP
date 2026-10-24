"""Disk-backed metadata inspection; video and metadata size do not grow verifier RAM."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import IO, Any

import ijson  # type: ignore[import-untyped]


def items(handle: IO[bytes]) -> Iterator[dict[str, Any]]:
    try:
        for row in ijson.items(handle, "item", use_float=True):
            if not isinstance(row, dict):
                raise ValueError("metadata item must be an object")
            yield row
    except ijson.JSONError as exc:
        raise ValueError("invalid metadata JSON") from exc


@contextmanager
def metadata_index(
    frames: IO[bytes], packets: IO[bytes] | None = None
) -> Iterator[sqlite3.Connection]:
    with TemporaryDirectory(prefix="uap-verify-") as directory:
        db = sqlite3.connect(Path(directory) / "index.sqlite")
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA cache_size=-2048")
            db.execute("PRAGMA journal_mode=OFF")
            db.execute(
                "CREATE TABLE frames(id TEXT PRIMARY KEY,segment_id TEXT NOT NULL,ordinal INTEGER NOT NULL,packet_id TEXT,session_id TEXT NOT NULL,data_json TEXT NOT NULL,UNIQUE(segment_id,ordinal))"
            )
            db.execute(
                "CREATE TABLE packets(id TEXT PRIMARY KEY,segment_id TEXT NOT NULL,session_id TEXT NOT NULL,data_json TEXT NOT NULL)"
            )

            def frame_rows() -> Iterator[tuple[Any, ...]]:
                for row in items(frames):
                    ordinal = row["segment_frame_index"]
                    if not isinstance(ordinal, int) or ordinal < 0:
                        raise ValueError("invalid source frame ordinal")
                    yield (
                        row["id"],
                        row["segment_id"],
                        ordinal,
                        row["packet_id"],
                        row["session_id"],
                        json.dumps(row),
                    )

            db.executemany("INSERT INTO frames VALUES (?,?,?,?,?,?)", frame_rows())
            if packets is not None:
                db.executemany(
                    "INSERT INTO packets VALUES (?,?,?,?)",
                    (
                        (row["id"], row["segment_id"], row["session_id"], json.dumps(row))
                        for row in items(packets)
                    ),
                )
            db.commit()
            yield db
        finally:
            db.close()
