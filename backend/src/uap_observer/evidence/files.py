import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def canonical(data: Any) -> bytes:
    return (
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_json_records(path: Path, records: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(b"[")
        first = True
        for record in records:
            if not first:
                handle.write(b",")
            handle.write(canonical(record).rstrip(b"\n"))
            first = False
        handle.write(b"]\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def safe_member(root: Path, name: str) -> Path:
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(p in ("", ".", "..") for p in name.split("/"))
    ):
        raise ValueError("unsafe package path")
    path = root / name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("unsafe package member")
    return path
