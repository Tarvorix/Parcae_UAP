"""Write-once segment metadata and virtual per-event payloads.

Canonical video stays at its recorded path and is protected by event references.
Standalone JSON/video bundles exist only during explicit export or reanalysis.
"""

import hashlib
import heapq
import json
import os
import shutil
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ..storage.ledger import Ledger
from .files import atomic_write, canonical, digest, safe_member, write_json_records


class StorageBudgetExceeded(OSError):
    pass


class StorageBudget:
    def __init__(self, data: Path, limit: int | None, reserve: int = 0) -> None:
        self.data, self.limit, self.reserve = data, limit, reserve
        self.lock = threading.RLock()
        self.measured = 0.0
        self.cached: dict[str, int] = {}

    def usage(self, fresh: bool = True) -> dict[str, int]:
        with self.lock:
            if fresh or time.monotonic() - self.measured > 1 or not self.cached:
                logical = allocated = 0
                seen = set()
                for base, _, files in os.walk(self.data):
                    for name in files:
                        try:
                            stat = (Path(base) / name).stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        inode = (stat.st_dev, stat.st_ino)
                        if inode in seen:
                            continue
                        seen.add(inode)
                        logical += stat.st_size
                        allocated += stat.st_blocks * 512
                self.cached = {
                    "logical_bytes": logical,
                    "allocated_bytes": allocated,
                    "free_bytes": shutil.disk_usage(self.data).free,
                }
                self.measured = time.monotonic()
            return self.cached.copy()

    def check(self, extra: int = 0, fresh: bool = True) -> None:
        if self.limit is None and self.reserve == 0:
            return
        usage = self.usage(fresh)
        if (
            self.limit is not None
            and max(usage["logical_bytes"], usage["allocated_bytes"]) + extra > self.limit
        ):
            raise StorageBudgetExceeded("total application storage budget exceeded")
        if usage["free_bytes"] - extra < self.reserve:
            raise StorageBudgetExceeded("storage safety reserve would be crossed")


class SharedStore:
    def __init__(self, ledger: Ledger, root: Path, budget: StorageBudget) -> None:
        self.ledger, self.root, self.budget = ledger, root, budget
        self.objects = root / "objects"
        self.objects.mkdir(exist_ok=True)
        self.records_cache: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self.recipe_cache: OrderedDict[tuple[str, tuple[str, ...]], dict[str, Any]] = OrderedDict()

    def object_path(self, sha: str) -> Path:
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("invalid shared object hash")
        return self.objects / sha[:2] / sha

    def put_bytes(self, data: bytes) -> dict[str, Any]:
        sha = hashlib.sha256(data).hexdigest()
        p = self.object_path(sha)
        if p.exists():
            if digest(p) != sha:
                raise ValueError("shared object integrity failure")
        else:
            self.budget.check(len(data) + 4096)
            p.parent.mkdir(exist_ok=True)
            atomic_write(p, data)
        return {"sha256": sha, "size_bytes": len(data)}

    def segment(self, segment_id: str) -> dict[str, Any]:
        rows = self.ledger.query(
            "SELECT snapshot_json FROM segment_snapshots WHERE segment_id=?", (segment_id,)
        )
        if rows:
            return dict(json.loads(rows[0]["snapshot_json"]))
        segment = self.ledger.query("SELECT * FROM segments WHERE id=?", (segment_id,))[0]
        source = Path(segment["path"])
        if segment["indexed"] != 1 or digest(source) != segment["sha256"]:
            raise ValueError("source segment integrity or index unavailable")
        snapshot = {
            "id": segment_id,
            "source_sha256": segment["sha256"],
            "source_bytes": segment["bytes"],
        }
        for kind, order in [("frames", "media_seconds"), ("packets", "sequence")]:
            records = self.ledger.query(
                f"SELECT * FROM {kind} WHERE segment_id=? ORDER BY {order}", (segment_id,)
            )
            payload = b"[" + b",".join(canonical(r).rstrip(b"\n") for r in records) + b"]\n"
            snapshot[kind] = self.put_bytes(payload)
        self.ledger.execute(
            "INSERT OR IGNORE INTO segment_snapshots VALUES (?,?)",
            (segment_id, json.dumps(snapshot)),
        )
        return snapshot

    def records(self, sha: str) -> list[dict[str, Any]]:
        path = self.object_path(sha)
        # Always hash the on-disk bytes, including when decoded records are cached.
        if digest(path) != sha:
            raise ValueError("shared metadata integrity failure")
        if sha not in self.records_cache:
            self.records_cache[sha] = json.loads(path.read_bytes())
        self.records_cache.move_to_end(sha)
        result = self.records_cache[sha]
        if len(self.records_cache) > 128:
            self.records_cache.popitem(last=False)
        return result

    def rows(self, recipe: dict[str, Any], kind: str) -> Iterator[dict[str, Any]]:
        if "blob" in recipe:
            yield from self.records(recipe["blob"]["sha256"])
            return
        streams = [iter(self.records(self.segment(s)[kind]["sha256"])) for s in recipe["segments"]]

        def key(row: dict[str, Any]) -> float:
            value = row["media_seconds"] if kind == "frames" else row["sequence"]
            return float("-inf") if value is None else float(value)

        yield from heapq.merge(*streams, key=key)

    def recipe(self, segment_ids: list[str], kind: str) -> dict[str, Any]:
        key = (kind, tuple(segment_ids))
        if key in self.recipe_cache:
            return self.recipe_cache[key].copy()
        recipe: dict[str, Any] = {"segments": segment_ids}
        h = hashlib.sha256()
        h.update(b"[")
        size = 1
        for index, row in enumerate(self.rows(recipe, kind)):
            data = (b"," if index else b"") + canonical(row).rstrip(b"\n")
            h.update(data)
            size += len(data)
        h.update(b"]\n")
        size += 2
        recipe.update(sha256=h.hexdigest(), size_bytes=size)
        self.recipe_cache[key] = recipe
        if len(self.recipe_cache) > 256:
            self.recipe_cache.popitem(last=False)
        return recipe.copy()

    def register(self, event_id: str, recipes: dict[str, Any]) -> None:
        payload = canonical(recipes)
        folder = self.root / "events" / event_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "storage.json"
        sha = hashlib.sha256(payload).hexdigest()
        existing = self.ledger.query(
            "SELECT sha256 FROM shared_captures WHERE event_id=?", (event_id,)
        )
        if existing and existing[0]["sha256"] != sha:
            raise ValueError("shared capture references are immutable")
        if not path.exists():
            self.budget.check(len(payload) + 4096)
            atomic_write(path, payload)
        elif digest(path) != sha:
            raise ValueError("shared capture reference integrity failure")
        self.ledger.execute(
            "INSERT OR IGNORE INTO shared_captures VALUES (?,?,?)", (event_id, str(path), sha)
        )

    def mapping(self, event_id: str) -> dict[str, Any] | None:
        rows = self.ledger.query(
            "SELECT path,sha256 FROM shared_captures WHERE event_id=?", (event_id,)
        )
        if not rows:
            return None
        p = Path(rows[0]["path"])
        if digest(p) != rows[0]["sha256"]:
            raise ValueError("shared capture reference integrity failure")
        return dict(json.loads(p.read_bytes()))

    def source(self, event_id: str, segment_id: str) -> Path:
        event = self.ledger.query(
            "SELECT manifest_path,manifest_sha256 FROM events WHERE id=?", (event_id,)
        )[0]
        manifest = Path(event["manifest_path"])
        if digest(manifest) != event["manifest_sha256"]:
            raise ValueError("capture integrity failure")
        capture = json.loads(manifest.read_bytes())
        name = f"source_segments/{segment_id}.mkv"
        expected = next(
            (a["sha256"] for a in capture["artifacts"] if a["relative_path"] == name), None
        )
        if expected is None:
            raise ValueError("segment absent from sealed capture")
        if self.mapping(event_id):
            row = self.ledger.query("SELECT path FROM segments WHERE id=?", (segment_id,))[0]
            path = Path(row["path"])
        else:
            path = safe_member(manifest.parent, name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError("source integrity failure")
        return path

    @contextmanager
    def payloads(self, event_id: str) -> Iterator[dict[str, Path]]:
        event = self.ledger.query(
            "SELECT manifest_path,manifest_sha256 FROM events WHERE id=?", (event_id,)
        )[0]
        manifest = Path(event["manifest_path"])
        if digest(manifest) != event["manifest_sha256"]:
            raise ValueError("capture integrity failure")
        capture = json.loads(manifest.read_bytes())
        mapping = self.mapping(event_id)
        temporary_root = self.root / "temporary"
        temporary_root.mkdir(exist_ok=True)
        with TemporaryDirectory(prefix="payload-", dir=temporary_root) as temporary:
            files = {"capture_manifest.json": manifest}
            for artifact in capture["artifacts"]:
                name = artifact["relative_path"]
                if name.startswith("source_segments/"):
                    path = self.source(event_id, Path(name).stem)
                elif mapping and name in ("frames.json", "packets.json"):
                    recipe = mapping[name]
                    self.budget.check(artifact["size_bytes"] + 4096)
                    path = Path(temporary) / name
                    write_json_records(path, self.rows(recipe, name.removesuffix(".json")))
                else:
                    path = safe_member(manifest.parent, name)
                if (
                    not path.is_file()
                    or digest(path) != artifact["sha256"]
                    or path.stat().st_size != artifact["size_bytes"]
                ):
                    raise ValueError("capture artifact integrity failure")
                files[name] = path
            yield files
