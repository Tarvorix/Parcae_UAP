"""Verified, resumable migration of legacy event payloads. Cleanup is a separate phase."""

import json
import re
from pathlib import Path
from typing import Any

from ..capture.tools import utc_now
from ..models import CaptureManifest
from .files import atomic_write, canonical, digest
from .index import items
from .service import EvidenceService


def migrate_legacy(service: EvidenceService, progress: Any = None) -> dict[str, Any]:
    inventory = []
    corrections = []
    fallback = 0
    verified_sources = set()
    for ordinal, event in enumerate(
        service.ledger.query("SELECT * FROM events ORDER BY created_utc,id")
    ):
        folder = service.root / "events" / event["id"]
        original = folder / "capture_manifest.json"
        capture = json.loads(original.read_bytes())
        # The original manifest remains an immutable historical revision if corrected.
        if not service.shared.mapping(event["id"]) and digest(original) != event["manifest_sha256"]:
            raise ValueError("legacy manifest integrity failure")
        coverage = [c["id"] for c in capture["coverage"]]
        recipes = {}
        for artifact in capture["artifacts"]:
            name = artifact["relative_path"]
            path = folder / name
            if (
                not path.is_file()
                or path.is_symlink()
                or digest(path) != artifact["sha256"]
                or path.stat().st_size != artifact["size_bytes"]
            ):
                raise ValueError("legacy artifact integrity failure")
            stat = path.stat()
            inventory.append(
                {
                    "path": str(path.relative_to(service.root.parent)),
                    "sha256": artifact["sha256"],
                    "bytes": stat.st_size,
                    "allocated_bytes": stat.st_blocks * 512,
                }
            )
            if name.startswith("source_segments/"):
                sid = Path(name).stem
                source = service.ledger.query(
                    "SELECT path,sha256 FROM segments WHERE id=?", (sid,)
                )[0]
                if source["sha256"] != artifact["sha256"] or (
                    sid not in verified_sources
                    and digest(Path(source["path"])) != artifact["sha256"]
                ):
                    raise ValueError("canonical source does not match event copy")
                verified_sources.add(sid)
            elif name in ("frames.json", "packets.json"):
                kind = name.removesuffix(".json")
                recipe = service.shared.recipe(coverage, kind)
                if recipe["sha256"] != artifact["sha256"]:
                    with path.open("rb") as handle:
                        ids = list(dict.fromkeys(row["segment_id"] for row in items(handle)))
                    recipe = service.shared.recipe(ids, kind)
                if recipe["sha256"] != artifact["sha256"]:
                    # Preserve unusual legacy ordering exactly rather than guessing it.
                    blob = service.shared.put_bytes(path.read_bytes())
                    recipe = {"blob": blob, **blob}
                    fallback += 1
                if recipe["size_bytes"] != artifact["size_bytes"]:
                    raise ValueError("legacy metadata reconstruction mismatch")
                recipes[name] = recipe
            else:
                raise ValueError("unexpected legacy payload; cleanup halted")
        mapped = service.shared.mapping(event["id"])
        packet_extra = set(recipes["packets.json"].get("segments", coverage)) - set(coverage)
        database_ids = {
            r["segment_id"]
            for r in service.ledger.query(
                "SELECT segment_id FROM event_segments WHERE event_id=?", (event["id"],)
            )
        }
        extra = database_ids - set(coverage)
        if mapped:
            recipes = mapped
        elif extra or packet_extra:
            if extra != packet_extra or recipes["frames.json"].get("segments") != coverage:
                raise ValueError("unexplained event reference inconsistency")
            for sid in extra:
                segment = service.ledger.query(
                    "SELECT start_seconds,end_seconds FROM segments WHERE id=?", (sid,)
                )[0]
                if abs(segment["start_seconds"] - capture["requested_interval"][1]) > 1e-7:
                    raise ValueError("extra packet reference is not a proven end boundary")
            atomic_write(folder / "legacy-storage.json", canonical(recipes))
            old_hash = digest(original)
            fixed = service.shared.recipe(coverage, "packets")
            recipes["packets.json"] = fixed
            for artifact in capture["artifacts"]:
                if artifact["relative_path"] == "packets.json":
                    artifact.update(sha256=fixed["sha256"], size_bytes=fixed["size_bytes"])
            capture["limitations"] = list(
                dict.fromkeys(capture["limitations"] + ["boundary_packet_reference_corrected"])
            )
            capture["complete"] = False
            CaptureManifest.model_validate(capture)
            corrected = folder / "capture_manifest.storage-v2.json"
            atomic_write(corrected, canonical(capture))
            correction = {
                "event_id": event["id"],
                "previous_manifest_sha256": old_hash,
                "manifest_sha256": digest(corrected),
                "removed_boundary_references": sorted(extra),
                "reason": "packet snapshot included the following boundary segment absent from sealed source/frame coverage; timestamps unchanged",
            }
            with service.ledger.connect() as db:
                db.execute(
                    "UPDATE events SET manifest_path=?,manifest_sha256=?,limitations_json=?,state='incomplete_source_gap' WHERE id=?",
                    (
                        str(corrected),
                        digest(corrected),
                        json.dumps(capture["limitations"]),
                        event["id"],
                    ),
                )
                for sid in extra:
                    db.execute(
                        "DELETE FROM event_segments WHERE event_id=? AND segment_id=?",
                        (event["id"], sid),
                    )
                db.execute(
                    "INSERT INTO audit(observed_utc,action,subject_id,detail) VALUES (?,?,?,?)",
                    (utc_now(), "storage_boundary_correction", event["id"], json.dumps(correction)),
                )
            corrections.append(correction)
        service.shared.register(event["id"], recipes)
        if progress and ordinal % 100 == 0:
            progress({"events_verified": ordinal + 1, "payload_files_verified": len(inventory)})
    integrity = service.ledger.query("PRAGMA integrity_check")
    if list(integrity[0].values()) != ["ok"] or service.ledger.query("PRAGMA foreign_key_check"):
        raise ValueError("database integrity check failed")
    return {
        "version": 1,
        "data_directory": str(service.root.parent),
        "inventory": inventory,
        "corrections": corrections,
        "fallback_metadata_objects": fallback,
        "events": ordinal + 1,
        "logical_redundant_bytes": sum(r["bytes"] for r in inventory),
        "allocated_redundant_bytes": sum(r["allocated_bytes"] for r in inventory),
        "verified_utc": utc_now(),
    }


def remove_verified_redundancy(
    service: EvidenceService, plan: dict[str, Any], progress: Any = None
) -> dict[str, int]:
    data = service.root.parent.resolve()
    if Path(plan["data_directory"]).resolve() != data:
        raise ValueError("migration inventory belongs to another directory")
    # Verify shared replacements again before any permanent removal.
    for row in service.ledger.query("SELECT snapshot_json FROM segment_snapshots"):
        snapshot = json.loads(row["snapshot_json"])
        source = service.ledger.query("SELECT path FROM segments WHERE id=?", (snapshot["id"],))[0]
        if digest(Path(source["path"])) != snapshot["source_sha256"]:
            raise ValueError("shared canonical source changed; cleanup halted")
        for kind in ("frames", "packets"):
            if (
                digest(service.shared.object_path(snapshot[kind]["sha256"]))
                != snapshot[kind]["sha256"]
            ):
                raise ValueError("shared metadata changed; cleanup halted")
    # Validate the complete scope before removing anything. New mappings/objects must still work.
    for event in service.ledger.query("SELECT id,manifest_path,manifest_sha256 FROM events"):
        if digest(Path(event["manifest_path"])) != event["manifest_sha256"]:
            raise ValueError("capture manifest changed; cleanup halted")
        if not service.shared.mapping(event["id"]):
            raise ValueError("event has no verified shared storage")
    for row in plan["inventory"]:
        relative = row["path"]
        if not re.fullmatch(
            r"evidence/events/[a-f0-9]{32}/(?:source_segments/[a-f0-9]{32}\.mkv|frames\.json|packets\.json)",
            relative,
        ):
            raise ValueError("unsafe redundant-file scope")
        path = data / relative
        if path.is_symlink() or path.resolve() != path or not path.resolve().is_relative_to(data):
            raise ValueError("unsafe cleanup path")
        if path.exists() and (path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]):
            raise ValueError("redundant file changed after migration verification")
    removed = logical = allocated = 0
    for row in plan["inventory"]:
        path = data / row["path"]
        if path.exists():
            path.unlink()
            removed += 1
            logical += row["bytes"]
            allocated += row["allocated_bytes"]
        if progress and removed % 10000 == 0:
            progress({"redundant_files_removed": removed})
    return {
        "files_removed": removed,
        "logical_bytes_removed": logical,
        "allocated_bytes_removed": allocated,
    }
