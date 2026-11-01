import json
import shutil
from pathlib import Path

import pytest
from uap_observer.evidence.files import canonical, digest, write_json_records
from uap_observer.evidence.migrate import migrate_legacy, remove_verified_redundancy
from uap_observer.evidence.shared import StorageBudgetExceeded
from uap_observer.evidence.verify import verify_package

from tests.integration.test_evidence import setup_capture


def legacy_copy(service, event):
    folder = Path(service.event(event)["manifest_path"]).parent
    with service.shared.payloads(event) as files:
        for name, path in files.items():
            if name == "capture_manifest.json":
                continue
            target = folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    service.ledger.execute("DELETE FROM shared_captures WHERE event_id=?", (event,))
    (folder / "storage.json").unlink()
    return folder


def test_overlap_stores_each_video_and_metadata_payload_once(fixture_video, tmp_path):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    ids = []
    for _ in range(25):
        event = service.trigger(recorder.session_id, 2, 3, pre=1, post=1)
        assert service.seal(event)
        ids.append(event)
    assert not list((service.root / "events").rglob("*.mkv"))
    assert not list((service.root / "events").rglob("frames.json"))
    assert not list((service.root / "events").rglob("packets.json"))
    segments = service.segments(ids[0])
    assert len(list(service.shared.objects.glob("*/*"))) == len(segments) * 2
    assert len(ledger.query("SELECT * FROM segment_snapshots")) == len(segments)
    assert verify_package(service.export(ids[0]))["verified"]
    assert verify_package(service.export(ids[-1]))["verified"]
    # A later mutable DB row cannot rewrite frozen evidence metadata.
    ledger.execute("UPDATE frames SET receipt_utc='changed-after-seal'")
    assert verify_package(service.export(ids[0]))["verified"]


def test_shared_object_corruption_and_total_budget(fixture_video, tmp_path):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    event = service.trigger(recorder.session_id, 1, 3, pre=0, post=0)
    service.seal(event)
    original = list(service.shared.objects.glob("*/*"))[0]
    original.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        service.export(event)
    unrelated = tmp_path / "large-existing-metadata"
    unrelated.write_bytes(b"x" * 100000)
    usage = service.budget.usage()
    service.budget.limit = usage["allocated_bytes"] + 100
    with pytest.raises(StorageBudgetExceeded):
        service.shared.put_bytes(b"z" * 10000)
    assert all(Path(s["path"]).exists() for s in service.segments(event))


def test_legacy_migration_preserves_hashes_and_deletes_only_verified_duplicates(
    fixture_video, tmp_path
):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    event = service.trigger(recorder.session_id, 1, 3, pre=0, post=0)
    service.seal(event)
    folder = legacy_copy(service, event)
    before = (folder / "capture_manifest.json").read_bytes()
    keep = tmp_path / "unrelated"
    keep.write_text("keep")
    source_hashes = {
        s["path"]: digest(Path(s["path"])) for s in service.segments(event)
    }
    plan = migrate_legacy(service)
    assert (folder / "capture_manifest.json").read_bytes() == before
    assert not plan["corrections"] and not plan["fallback_metadata_objects"]
    assert verify_package(service.export(event))["verified"]
    removed = remove_verified_redundancy(service, plan)
    assert removed["files_removed"] == len(plan["inventory"])
    assert keep.read_text() == "keep"
    assert all(digest(Path(p)) == h for p, h in source_hashes.items())
    assert verify_package(service.export(event))["verified"]
    assert ledger.query("PRAGMA integrity_check")[0]["integrity_check"] == "ok"
    assert not ledger.query("PRAGMA foreign_key_check")


def test_boundary_correction_is_revisioned_without_timestamp_changes(
    fixture_video, tmp_path
):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    all_segments = ledger.query("SELECT * FROM segments ORDER BY start_seconds")
    event = service.trigger(recorder.session_id, 0.1, 0.5, pre=0, post=0)
    service.seal(event)
    folder = legacy_copy(service, event)
    manifest = folder / "capture_manifest.json"
    capture = json.loads(manifest.read_bytes())
    covered = {s["id"] for s in capture["coverage"]}
    extra = next(s for s in all_segments if s["id"] not in covered)
    capture["requested_interval"][1] = extra["start_seconds"]
    ledger.execute(
        "UPDATE events SET requested_end=? WHERE id=?", (extra["start_seconds"], event)
    )
    ledger.execute("INSERT INTO event_segments VALUES (?,?)", (event, extra["id"]))
    write_json_records(
        folder / "packets.json",
        ledger.query(
            "SELECT p.* FROM packets p JOIN event_segments e ON p.segment_id=e.segment_id WHERE e.event_id=? ORDER BY sequence",
            (event,),
        ),
    )
    for artifact in capture["artifacts"]:
        if artifact["relative_path"] == "packets.json":
            artifact.update(
                sha256=digest(folder / "packets.json"),
                size_bytes=(folder / "packets.json").stat().st_size,
            )
    manifest.write_bytes(canonical(capture))
    old = manifest.read_bytes()
    ledger.execute(
        "UPDATE events SET manifest_sha256=? WHERE id=?", (digest(manifest), event)
    )
    plan = migrate_legacy(service)
    assert len(plan["corrections"]) == 1
    new = json.loads(Path(service.event(event)["manifest_path"]).read_bytes())
    assert manifest.read_bytes() == old
    assert new["requested_interval"] == capture["requested_interval"]
    assert new["trigger_interval"] == capture["trigger_interval"]
    assert set(s["id"] for s in service.segments(event)) == covered
    remove_verified_redundancy(service, plan)
    assert verify_package(service.export(event))["verified"]
    assert (folder / "legacy-storage.json").is_file()


def test_recovery_after_snapshot_rename_and_boundary_pin_freeze(
    fixture_video, tmp_path, monkeypatch
):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    event = service.trigger(recorder.session_id, 1, 2, pre=0, post=0)
    original = service.shared.segment

    def check_frozen(segment):
        assert service.event(event)["state"] == "sealing"
        return original(segment)

    monkeypatch.setattr(service.shared, "segment", check_frozen)
    assert service.seal(event)
    monkeypatch.setattr(service.shared, "segment", original)
    ledger.execute("DELETE FROM shared_captures WHERE event_id=?", (event,))
    ledger.execute(
        "UPDATE events SET state='sealing',manifest_path=NULL,manifest_sha256=NULL WHERE id=?",
        (event,),
    )
    assert service.recover(tmp_path / "archive")["recovered_events"] == 1
    assert verify_package(service.export(event))["verified"]


def test_cleanup_refuses_changed_payload_or_shared_replacement(fixture_video, tmp_path):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    event = service.trigger(recorder.session_id, 1, 3, pre=0, post=0)
    service.seal(event)
    folder = legacy_copy(service, event)
    plan = migrate_legacy(service)
    paths = [tmp_path / r["path"] for r in plan["inventory"]]
    metadata = folder / "frames.json"
    original = metadata.read_bytes()
    metadata.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        remove_verified_redundancy(service, plan)
    assert all(p.exists() for p in paths)
    metadata.write_bytes(original)
    shared = next(service.shared.objects.glob("*/*"))
    shared.write_bytes(b"changed shared replacement")
    with pytest.raises(ValueError, match="shared metadata"):
        remove_verified_redundancy(service, plan)
    assert all(p.exists() for p in paths)
