import json
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from uap_observer.capture.analysis import ArchiveAnalysis
from uap_observer.capture.recorder import Recorder
from uap_observer.evidence.service import EvidenceService
from uap_observer.evidence.verify import verify_package
from uap_observer.storage.ledger import Ledger


def setup_capture(tmp_path, video):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    archive = tmp_path / "archive"
    recorder = Recorder(ledger, archive, str(video))
    recorder.start()
    recorder.wait()
    analysis = ArchiveAnalysis(ledger)
    analysis.process_pending()
    assert not analysis.errors
    service = EvidenceService(ledger, tmp_path / "evidence")
    return ledger, recorder, service


def test_full_pre_post_evidence_and_review_invariance(tmp_path):
    video = tmp_path / "long.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=5:duration=120",
            "-c:v",
            "ffv1",
            "-g",
            "5",
            "-an",
            str(video),
        ],
        check=True,
        timeout=30,
    )
    ledger, recorder, service = setup_capture(tmp_path, video)
    event = service.trigger(recorder.session_id, 35, 65)
    assert service.seal(event)
    capture = service.event(event)
    manifest = Path(capture["manifest_path"])
    before = manifest.read_bytes()
    data = json.loads(before)
    assert data["complete"] and data["requested_interval"] == [5, 110]
    first = service.export(event)
    first_bytes = first.read_bytes()
    service.review(event, "likely_conventional", "Generated test pattern")
    second = service.export(event)
    assert verify_package(first)["verified"] and verify_package(second)["verified"]
    assert manifest.read_bytes() == before and first.read_bytes() == first_bytes
    protected = service.cleanup(0)
    assert protected["quota_exceeded"]
    assert all(Path(segment["path"]).exists() for segment in service.segments(event))
    assert len(ledger.query("SELECT * FROM reviews")) == 1


def test_partial_recovery_and_corruption(fixture_video, tmp_path):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    event = service.trigger(recorder.session_id, 2, 4)
    ledger.execute(
        "UPDATE sessions SET state='running' WHERE id=?", (recorder.session_id,)
    )
    assert not service.seal(event)
    restored = EvidenceService(Ledger(ledger.path), service.root)
    result = restored.recover(tmp_path / "archive")
    assert result["recovered_events"] == 1
    assert restored.event(event)["state"] == "incomplete_source_gap"
    package = restored.export(event)
    verification = verify_package(package)
    assert verification["verified"] and not verification["complete"]
    folder = Path(restored.event(event)["manifest_path"]).parent
    source = next((folder / "source_segments").glob("*.mkv"))
    source.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        restored.export(event)


def test_crash_after_source_rename_before_sql_commit(fixture_video, tmp_path):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    segment = ledger.query("SELECT * FROM segments LIMIT 1")[0]
    ledger.execute("DELETE FROM frames WHERE segment_id=?", (segment["id"],))
    ledger.execute("DELETE FROM packets WHERE segment_id=?", (segment["id"],))
    ledger.execute("DELETE FROM segments WHERE id=?", (segment["id"],))
    recovered = service.recover(tmp_path / "archive")
    assert recovered["recovered_segments"] == 1
    assert ledger.query("SELECT * FROM packets WHERE segment_id=?", (segment["id"],))


def test_trigger_and_cleanup_serialize(fixture_video, tmp_path):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    # Trigger wins its transaction first: cleanup must preserve every referenced segment.
    event = service.trigger(recorder.session_id, 1, 4, pre=0, post=0)
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(service.cleanup, 0)
        b = pool.submit(service.seal, event)
        a.result()
        assert b.result()
    assert all(Path(segment["path"]).exists() for segment in service.segments(event))


def test_verifier_rejects_traversal_and_changed_bytes(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        archive.writestr("../escape", "bad")
    assert not verify_package(bad)["verified"]


def test_disk_full_never_seals_or_unpins_existing_source(
    fixture_video, tmp_path, monkeypatch
):
    import shutil

    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    event = service.trigger(recorder.session_id, 1, 3, pre=0, post=0)

    def full(*args):
        raise OSError(28, "disk full")

    monkeypatch.setattr(shutil, "copyfile", full)
    with pytest.raises(OSError):
        service.seal(event)
    assert service.event(event)["manifest_sha256"] is None
    assert service.event(event)["state"] == "capturing_postroll"
    service.cleanup(0)
    assert all(Path(segment["path"]).exists() for segment in service.segments(event))


def test_recover_sealed_folder_without_database_seal_does_not_rewrite_capture(
    fixture_video, tmp_path
):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    event = service.trigger(recorder.session_id, 1, 3, pre=0, post=0)
    assert service.seal(event)
    manifest = Path(service.event(event)["manifest_path"])
    original = manifest.read_bytes()
    ledger.execute(
        "UPDATE events SET state='capturing_postroll',manifest_path=NULL,manifest_sha256=NULL WHERE id=?",
        (event,),
    )
    ledger.execute(
        "UPDATE sessions SET state='running' WHERE id=?", (recorder.session_id,)
    )
    service.recover(tmp_path / "archive")
    assert service.event(event)["state"] == "sealed"
    assert manifest.read_bytes() == original


def test_corrupt_orphan_is_preserved_but_never_adopted(fixture_video, tmp_path):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    segment = ledger.query("SELECT * FROM segments LIMIT 1")[0]
    ledger.execute("DELETE FROM frames WHERE segment_id=?", (segment["id"],))
    ledger.execute("DELETE FROM packets WHERE segment_id=?", (segment["id"],))
    ledger.execute("DELETE FROM segments WHERE id=?", (segment["id"],))
    path = Path(segment["path"])
    path.write_bytes(b"corrupt after source rename, before database commit")
    assert service.recover(tmp_path / "archive")["recovered_segments"] == 0
    assert path.is_file()
    assert not ledger.query("SELECT id FROM segments WHERE id=?", (segment["id"],))
    assert ledger.query("SELECT * FROM health WHERE kind='orphan_quarantined'")


def test_blocked_unlink_does_not_block_source_recording(
    fixture_video, tmp_path, monkeypatch
):
    import threading

    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    entered, release = threading.Event(), threading.Event()
    original = Path.unlink
    blocked = {Path(row["path"]) for row in ledger.query("SELECT path FROM segments")}

    def unlink(path, *args, **kwargs):
        if path in blocked:
            entered.set()
            assert release.wait(10)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    with ThreadPoolExecutor(1) as pool:
        cleanup = pool.submit(service.cleanup, 0)
        try:
            assert entered.wait(3)
            other = Recorder(ledger, tmp_path / "second", str(fixture_video))
            other.start()
            other.wait(5)
            assert other.packet_count == 120
        finally:
            release.set()
        cleanup.result(timeout=10)


def test_export_and_verify_stream_source_bytes(fixture_video, tmp_path, monkeypatch):
    ledger, recorder, service = setup_capture(tmp_path, fixture_video)
    event = service.trigger(recorder.session_id, 1, 3, pre=0, post=0)
    original = Path.read_bytes
    original_zip = zipfile.ZipFile.read

    def bounded(path):
        assert path.suffix not in (".mkv", ".zip"), "source should be streamed"
        return original(path)

    def bounded_zip(archive, name, *args, **kwargs):
        assert not str(name).endswith(".mkv"), "archived source should be streamed"
        return original_zip(archive, name, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", bounded)
    monkeypatch.setattr(zipfile.ZipFile, "read", bounded_zip)
    assert verify_package(service.export(event))["verified"]


def test_large_protected_archive_cleanup_does_not_stall_packet_reader(
    fixture_video, tmp_path
):
    import time

    ledger = Ledger(tmp_path / "ledger.sqlite")
    # Metadata-only protected history represents long observation without writing private video.
    with ledger.connect() as db:
        db.execute("INSERT INTO cameras VALUES ('history','video_file')")
        db.execute(
            "INSERT INTO sessions(id,camera_id,started_utc,state,stream_json) VALUES ('history','history','2026-10-24T00:00:00Z','ended','{}')"
        )
        db.executemany(
            "INSERT INTO segments VALUES (?,?,?,?,?,?,?,?,?)",
            (
                (
                    str(index),
                    "history",
                    str(tmp_path / "unused" / str(index)),
                    "0" * 64,
                    1024,
                    index,
                    index + 1,
                    "sealed",
                    1,
                )
                for index in range(10000)
            ),
        )
        db.executemany(
            "INSERT INTO events(id,session_id,start_seconds,end_seconds,requested_start,requested_end,state,created_utc) VALUES (?,?,?,?,?,?,?,?)",
            (
                (
                    str(index),
                    "history",
                    0,
                    10000,
                    0,
                    10000,
                    "sealed",
                    "2026-10-24T00:00:00Z",
                )
                for index in range(200)
            ),
        )
        db.executemany(
            "INSERT INTO event_segments VALUES (?,?)",
            (
                (str(index // 100 + offset), str(index))
                for offset in (0, 100)
                for index in range(10000)
            ),
        )
    plan = ledger.query(
        "EXPLAIN QUERY PLAN SELECT 1 FROM event_segments es WHERE es.segment_id=?",
        ("5000",),
    )
    assert any("SEARCH es" in row["detail"] for row in plan), plan
    evidence = EvidenceService(ledger, tmp_path / "evidence")
    recorder = Recorder(ledger, tmp_path / "archive", str(fixture_video), realtime=True)
    recorder.start()
    deadline = time.monotonic() + 5
    while recorder.packet_count < 2:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    with ThreadPoolExecutor(1) as pool:
        started = time.monotonic()
        cleanup = pool.submit(evidence.cleanup, 0)
        previous_count = recorder.packet_count
        previous_change = started
        longest_pause = 0.0
        try:
            while not cleanup.done():
                current = time.monotonic()
                if recorder.packet_count != previous_count:
                    previous_change = current
                    previous_count = recorder.packet_count
                longest_pause = max(longest_pause, current - previous_change)
                assert current - started < 20
                time.sleep(0.02)
            result = cleanup.result()
            recorder.wait(10)
        finally:
            recorder.stop()
    assert longest_pause < 1.5
    assert recorder.packet_count == 120 and not recorder.error
    assert result["quota_exceeded"]  # Protected history must survive over quota.
    assert (
        len(
            ledger.query(
                "SELECT id FROM segments WHERE session_id='history' AND state='sealed'"
            )
        )
        == 10000
    )


def test_existing_schema_upgrades_retention_lookup_without_rewriting_evidence(tmp_path):
    import sqlite3

    from uap_observer.storage.ledger import MIGRATIONS

    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        for migration in MIGRATIONS[:4]:
            db.executescript(migration)
        db.execute("PRAGMA user_version=4")
        db.execute("INSERT INTO cameras VALUES ('preserved','video_file')")
    upgraded = Ledger(path)
    assert upgraded.query("PRAGMA user_version")[0]["user_version"] == len(MIGRATIONS)
    assert upgraded.query("SELECT id FROM cameras")[0]["id"] == "preserved"
    assert any(
        row["name"] == "event_segments_segment"
        for row in upgraded.query("PRAGMA index_list(event_segments)")
    )
