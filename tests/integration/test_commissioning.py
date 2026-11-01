import json
import shutil
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from uap_observer.api.camera import ConnectionInput, connection_url
from uap_observer.capture.commission import CommissioningCapture
from uap_observer.capture.recorder import Recorder
from uap_observer.capture.tools import validate_input
from uap_observer.evidence.service import EvidenceService
from uap_observer.evidence.verify import verify_package
from uap_observer.main import create_app
from uap_observer.storage.ledger import Ledger

from tests.support.rtsp_fixture import RTSPFixture

ROOT = Path(__file__).resolve().parents[2]


def test_private_rtsp_is_explicit_and_credentials_are_encoded(tmp_path):
    source = "rtsp://192.168.1.42:554/camera"
    with pytest.raises(ValueError, match="loopback"):
        validate_input(source)
    assert validate_input(source, allow_remote=True) == source
    for invalid in (
        "rtsp://8.8.8.8/a",
        "rtsp://example.org/a",
        "rtsp://224.0.0.1/a",
        "rtsp://192.168.1.42:0/a",
        "rtsp:///a",
        "rtsp://192.168.1.42/a\n",
    ):
        with pytest.raises(ValueError):
            validate_input(invalid, allow_remote=True)
    data = ConnectionInput(
        url=SecretStr(source),
        username=SecretStr("user@cam"),
        password=SecretStr("private:p@ss/word"),
    )
    assert "private" not in repr(data)
    result = connection_url(data)
    assert result == "rtsp://user%40cam:private%3Ap%40ss%2Fword@192.168.1.42:554/camera"
    with pytest.raises(ValueError):
        Recorder(Ledger(tmp_path / "ledger.sqlite"), tmp_path, source)
    assert (
        Recorder(
            Ledger(tmp_path / "ledger.sqlite"), tmp_path, source, allow_lan=True
        ).source
        == source
    )


def test_camera_entry_rejects_without_echoing_private_input(tmp_path, monkeypatch):
    shutil.copytree(ROOT / "config", tmp_path / "config")
    secret = "rtsp://private-user:private-password@8.8.8.8/private-stream"
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        origin = {"origin": "http://127.0.0.1:8000"}
        for response in (
            client.post("/api/v1/camera/start", json={"url": secret}),
            client.post("/api/v1/camera/start", headers=origin, json={"url": secret}),
            client.post(
                "/api/v1/camera/start", headers=origin, json={"url": {"secret": secret}}
            ),
            client.post(
                "/api/v1/camera/start", headers=origin, json={"url": "private-password"}
            ),
            client.post("/api/v1/camera/start", headers=origin, content="x" * 10000),
        ):
            assert response.status_code in (403, 422)
            assert "private-" not in response.text
        runtime = client.app.state.runtime
        monkeypatch.setattr(runtime, "start_live", lambda source: "test-job")
        response = client.post(
            "/api/v1/camera/start",
            headers=origin,
            json={
                "url": "rtsp://192.168.1.42/actual",
                "username": "user",
                "password": "test-secret",
            },
        )
        assert response.json() == {"job_id": "test-job"}
        assert "test-secret" not in client.get("/api/v1/camera/status").text


def test_finite_live_pipeline_preserves_source_and_verified_export(
    fixture_video, tmp_path
):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    evidence = EvidenceService(ledger, tmp_path / "evidence")
    ledger.execute(
        "INSERT INTO jobs(id,created_utc,state) VALUES ('test','now','running')"
    )
    runner = CommissioningCapture(ledger, evidence, tmp_path, 0, 2**30)
    with RTSPFixture(fixture_video, frame_limit=10000, delay=0.01) as server:
        source = server.url.replace("rtsp://", "rtsp://test-user:test-password@")
        runner.run(source, "test", seconds=4)
    assert runner.done.is_set() and runner.result, runner.status()
    assert runner.reason == "commissioning_time_limit"
    assert runner.result["frames"] > 50
    assert runner.result["frames"] == runner.result["exact_mapped_frames"]
    assert runner.result["streams"][0]["codec"] == "h264"
    assert runner.result["capture_wall_seconds"] < 8
    assert runner.result["observed_fps"] == pytest.approx(20, rel=0.01)
    assert runner.result["independent_export"]["verification"]["verified"]
    exported_event = runner.result["independent_export"]["event_id"]
    source_segments = evidence.segments(exported_event)
    assert source_segments and all(
        s["start_seconds"] is not None for s in source_segments
    )
    capture = json.loads(
        Path(evidence.event(exported_event)["manifest_path"]).read_text()
    )
    assert capture["coverage"] and any(
        a["relative_path"].endswith(".mkv") for a in capture["artifacts"]
    )
    assert ledger.query(
        "SELECT count(*) AS n FROM frames WHERE segment_id=?",
        (source_segments[0]["id"],),
    )[0]["n"]
    assert ledger.query(
        "SELECT * FROM gaps WHERE reason='initial_keyframe_timestamp_unavailable'"
    )
    assert runner.supervisor.source == ""
    assert all(r.source == "" for r in runner.supervisor.recorders)
    stored = json.dumps(ledger.query("SELECT * FROM sessions"))
    stored += (tmp_path / "commissioning/test/report.json").read_text()
    stored += json.dumps(ledger.query("SELECT * FROM jobs"))
    assert "test-password" not in stored and server.url not in stored
    # Durable evidence survives a fresh service and verifies independently of capture state.
    reopened = Ledger(ledger.path)
    export = EvidenceService(reopened, tmp_path / "evidence").export(
        runner.result["processing"][0]["events"][0]
    )
    assert verify_package(export)["verified"]


def test_live_reconnects_have_separate_epochs_and_errors(fixture_video, tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.execute(
        "INSERT INTO jobs(id,created_utc,state) VALUES ('retry','now','running')"
    )
    runner = CommissioningCapture(
        ledger, EvidenceService(ledger, tmp_path / "evidence"), tmp_path, 0, 2**30
    )
    with RTSPFixture(fixture_video, frame_limit=70, delay=0.005) as server:
        runner.run(server.url, "retry", seconds=5)
        assert server.connections >= 2
    assert len(ledger.query("SELECT id FROM sessions")) >= 2
    assert ledger.query("SELECT * FROM gaps")
    assert runner.done.is_set() and runner.result["frames"]


def test_watchdog_is_independent_of_blocked_monitor():
    # stop() signals ingest without joining a blocked reader or waiting on analysis.
    runner = CommissioningCapture(None, None, Path("/tmp"), 0, 1)
    called = threading.Event()

    class Supervisor:
        stop_requested = threading.Event()

        class Current:
            stop_requested = called

        current = Current()

    runner.supervisor = Supervisor()
    timer = threading.Timer(0.05, runner._deadline)
    started = time.monotonic()
    timer.start()
    assert called.wait(0.5)
    timer.join()
    assert time.monotonic() - started < 0.5
    assert runner.stop_requested.is_set()


def test_ingest_deadline_survives_stalled_detection(
    fixture_video, tmp_path, monkeypatch
):
    from uap_observer.events.processor import OfflineProcessor

    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.execute(
        "INSERT INTO jobs(id,created_utc,state) VALUES ('stall','now','running')"
    )
    runner = CommissioningCapture(
        ledger, EvidenceService(ledger, tmp_path / "evidence"), tmp_path, 0, 2**30
    )
    entered, release = threading.Event(), threading.Event()

    def delayed(self, session_id, **kwargs):
        entered.set()
        release.wait(10)
        return {"events": []}

    monkeypatch.setattr(OfflineProcessor, "process", delayed)
    with RTSPFixture(fixture_video, frame_limit=10000, delay=0.01) as server:
        thread = threading.Thread(target=runner.run, args=(server.url, "stall", 1.5))
        thread.start()
        try:
            assert entered.wait(3)
            assert runner.supervisor.current.done.wait(5)
            assert (
                not runner.done.is_set()
            )  # ingest stopped while detection is still blocked
            assert runner.supervisor.current.stop_requested.is_set()
        finally:
            release.set()
            thread.join(15)
        assert not thread.is_alive()
        assert runner.result["stop_reason"] == "commissioning_time_limit"


def test_indexing_failure_stops_camera_and_reports_failure(
    fixture_video, tmp_path, monkeypatch
):
    from uap_observer.capture.analysis import ArchiveAnalysis

    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.execute(
        "INSERT INTO jobs(id,created_utc,state) VALUES ('indexfail','now','running')"
    )
    runner = CommissioningCapture(
        ledger, EvidenceService(ledger, tmp_path / "evidence"), tmp_path, 0, 2**30
    )

    def broken(self, segment):
        raise ValueError("simulated decoder failure")

    monkeypatch.setattr(ArchiveAnalysis, "index_segment", broken)
    with RTSPFixture(fixture_video, frame_limit=10000, delay=0.005) as server:
        runner.run(server.url, "indexfail", seconds=5)
    assert runner.done.is_set() and runner.state == "failed"
    assert runner.supervisor.current.done.is_set()
    assert runner.result["analysis_errors"]
    assert (tmp_path / "commissioning/indexfail/report.json").is_file()
    assert ledger.query("SELECT * FROM health WHERE kind='analysis_failure'")
    assert (
        ledger.query("SELECT state FROM jobs WHERE id='indexfail'")[0]["state"]
        == "failed"
    )
