import hashlib
import subprocess
import time
from fractions import Fraction
from pathlib import Path

import av
import pytest
from uap_observer.capture.analysis import ArchiveAnalysis
from uap_observer.capture.recorder import Recorder
from uap_observer.capture.supervisor import CaptureSupervisor
from uap_observer.capture.tools import dependency_versions, redact, validate_input
from uap_observer.storage.ledger import MIGRATIONS, Ledger


def record(path, tmp_path, reset=False):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    recorder = Recorder(
        ledger, tmp_path / "archive", str(path), reset_segment_timestamps=reset
    )
    recorder.start()
    recorder.wait()
    analysis = ArchiveAnalysis(ledger, capacity=2)
    analysis.process_pending()
    assert not analysis.errors
    return ledger, recorder, analysis


@pytest.mark.parametrize("reset", [False, True])
def test_exact_archive_mapping_and_stalled_analysis(fixture_video, tmp_path, reset):
    ledger, recorder, analysis = record(fixture_video, tmp_path, reset)
    rows = ledger.query("SELECT * FROM frames ORDER BY source_sequence")
    assert len(rows) == 120 and recorder.packet_count == 120
    assert analysis.dropped == 118
    assert analysis.queue.qsize() == 2
    assert len(ledger.query("SELECT * FROM segments")) >= 4
    with av.open(str(fixture_video)) as original:
        for row, source in zip(rows, original.decode(video=0), strict=True):
            assert row["mapping_state"] == "exact"
            assert (
                row["source_pts"] * Fraction(row["source_time_base"])
                == source.pts * source.time_base
            )
            assert (
                row["pixel_sha256"]
                == hashlib.sha256(
                    source.to_ndarray(format="bgr24").tobytes()
                ).hexdigest()
            )
    for row in rows[::19]:
        assert analysis.lookup(row["id"]).metadata["source_pts"] == row["source_pts"]
    assert Ledger(ledger.path).query("PRAGMA user_version")[0]["user_version"] == len(
        MIGRATIONS
    )
    if reset:
        assert any(row["archive_pts"] != row["source_pts"] for row in rows)


def test_b_frames_and_variable_cadence(fixture_video, tmp_path):
    video = tmp_path / "vfr.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(fixture_video),
            "-vf",
            "select='not(eq(mod(n,7),0))'",
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-g",
            "20",
            "-bf",
            "2",
            "-an",
            str(video),
        ],
        check=True,
        timeout=20,
    )
    ledger, _, _ = record(video, tmp_path / "capture")
    rows = ledger.query("SELECT * FROM frames ORDER BY media_seconds")
    with av.open(str(video)) as original:
        originals = list(original.decode(video=0))
    assert len(rows) == len(originals)
    assert any(row["source_dts"] != row["source_pts"] for row in rows)
    for row, source in zip(rows, originals, strict=True):
        assert row["mapping_state"] == "exact"
        assert (
            row["source_pts"] * Fraction(row["source_time_base"])
            == source.pts * source.time_base
        )
        assert (
            row["pixel_sha256"]
            == hashlib.sha256(source.to_ndarray(format="bgr24").tobytes()).hexdigest()
        )


def test_failure_retry_sessions_and_redaction(tmp_path):
    bad = tmp_path / "bad.mkv"
    bad.write_bytes(b"broken")
    ledger = Ledger(tmp_path / "ledger.sqlite")
    supervisor = CaptureSupervisor(
        ledger, tmp_path / "archive", str(bad), retries=2, backoff_seconds=0.02
    )
    start = time.monotonic()
    supervisor.run()
    assert supervisor.state == "failed"
    assert len(set(supervisor.sessions)) == 3
    assert len(ledger.query("SELECT * FROM gaps")) == 3
    assert time.monotonic() - start >= 0.06
    assert "secret" not in redact("error rtsp://user:secret@127.0.0.1:8554/test")
    with pytest.raises(ValueError, match="loopback"):
        validate_input("rtsp://user:secret@example.com/sky")
    assert "ffmpeg" in dependency_versions()


def test_corruption_is_not_substituted(fixture_video, tmp_path):
    ledger, _, analysis = record(fixture_video, tmp_path)
    row = ledger.query("SELECT * FROM frames LIMIT 1")[0]
    segment = ledger.query("SELECT * FROM segments WHERE id=?", (row["segment_id"],))[0]
    Path(segment["path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="corrupt"):
        analysis.lookup(row["id"])


def test_real_loopback_rtsp_capture_and_gap(fixture_video, tmp_path):
    from tests.support.rtsp_fixture import RTSPFixture

    with RTSPFixture(fixture_video, frame_limit=100) as server:
        ledger = Ledger(tmp_path / "ledger.sqlite")
        recorder = Recorder(ledger, tmp_path / "archive", server.url, read_timeout=2)
        recorder.start()
        assert recorder.done.wait(15)
        analysis = ArchiveAnalysis(ledger, capacity=2)
        analysis.process_pending()
        assert not analysis.errors
        rows = ledger.query("SELECT * FROM frames")
        assert len(rows) >= 40
        assert all(row["mapping_state"] == "exact" for row in rows)
        assert server.connections == 1
        assert ledger.query("SELECT * FROM gaps")  # fixture intentionally disconnects


def test_duplicate_pts_is_explicitly_ambiguous(fixture_video, tmp_path):
    ledger, recorder, analysis = record(fixture_video, tmp_path)
    packet = ledger.query("SELECT * FROM packets LIMIT 1")[0]
    copy = dict(packet)
    copy["id"] = "duplicate"
    copy["sequence"] = 10000
    ledger.execute(
        f"INSERT INTO packets VALUES ({','.join('?' for _ in copy)})",
        tuple(copy.values()),
    )
    ledger.execute("DELETE FROM frames WHERE segment_id=?", (packet["segment_id"],))
    segment = ledger.query(
        "SELECT * FROM segments WHERE id=?", (packet["segment_id"],)
    )[0]
    analysis.index_segment(segment)
    row = ledger.query(
        "SELECT * FROM frames WHERE segment_id=? ORDER BY segment_frame_index",
        (packet["segment_id"],),
    )[0]
    assert row["mapping_state"] == "ambiguous"
    assert row["source_pts"] is None and row["packet_id"] is None


def test_recording_finishes_during_slow_analysis(fixture_video, tmp_path, monkeypatch):
    import threading

    ledger = Ledger(tmp_path / "ledger.sqlite")
    recorder = Recorder(ledger, tmp_path / "archive", str(fixture_video))
    analysis = ArchiveAnalysis(ledger, capacity=1)
    original = analysis.index_segment
    entered = threading.Event()
    release = threading.Event()

    def slow(segment):
        entered.set()
        release.wait(10)
        return original(segment)

    monkeypatch.setattr(analysis, "index_segment", slow)
    analysis.start()
    recorder.start()
    try:
        assert entered.wait(5)
        recorder.wait(5)
        assert recorder.packet_count == 120
        assert len(ledger.query("SELECT * FROM packets")) == 120
    finally:
        release.set()
        analysis.stop()


def test_rtsp_adapter_finishes_with_durable_frame_references(fixture_video, tmp_path):
    from uap_observer.sources.rtsp import RTSPSource

    from tests.support.rtsp_fixture import RTSPFixture

    with RTSPFixture(fixture_video, frame_limit=60) as server:
        source = RTSPSource("loopback", "fixture", server.url, tmp_path)
        source.start()
        frames = list(source.frames())
        assert frames
        assert all(f.packet.segment_id and f.packet.source_packet_id for f in frames)
        assert all(f.packet.timestamp_basis == "archived_source_pts" for f in frames)
        assert source.get_health().decoded_frames >= len(frames)
        source.stop()


def test_recorder_database_failure_signals_completion(
    fixture_video, tmp_path, monkeypatch
):
    ledger = Ledger(tmp_path / "ledger.sqlite")

    def unavailable(*args):
        raise OSError("simulated database storage failure")

    monkeypatch.setattr(ledger, "execute", unavailable)
    recorder = Recorder(ledger, tmp_path / "archive", str(fixture_video))
    recorder.start()
    assert recorder.done.wait(3)
    assert recorder.error == "capture_failed:OSError"


def test_large_source_timestamp_gap_is_explicit(tmp_path):
    import subprocess

    video = tmp_path / "dropout.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=10:duration=2",
            "-vf",
            "setpts='PTS+20/TB*gte(N,10)'",
            "-fps_mode",
            "vfr",
            "-c:v",
            "ffv1",
            "-g",
            "10",
            "-an",
            str(video),
        ],
        check=True,
        timeout=20,
    )
    ledger = Ledger(tmp_path / "ledger.sqlite")
    recorder = Recorder(ledger, tmp_path / "archive", str(video))
    recorder.start()
    recorder.wait()
    analysis = ArchiveAnalysis(ledger)
    analysis.process_pending()
    gaps = ledger.query(
        "SELECT reason FROM gaps WHERE reason LIKE 'source_timeline_gap:%'"
    )
    assert len(gaps) == 1
    assert len(ledger.query("SELECT * FROM frames")) == 20
    media = [
        row["media_seconds"]
        for row in ledger.query("SELECT * FROM frames ORDER BY media_seconds")
    ]
    assert media[10] - media[9] > 20


def test_missing_keyframes_fail_with_declared_memory_bound(fixture_video, tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    recorder = Recorder(
        ledger,
        tmp_path / "archive",
        str(fixture_video),
        segment_seconds=10,
        max_segment_packets=10,
    )
    recorder.start()
    with pytest.raises(RuntimeError, match="capture_failed"):
        recorder.wait()
    assert recorder.packet_count == 10 and len(recorder.rows) <= 10
    assert ledger.query(
        "SELECT * FROM gaps WHERE reason='keyframe_segment_packet_limit'"
    )
