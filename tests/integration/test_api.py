import shutil
from pathlib import Path

from fastapi.testclient import TestClient
from uap_observer.main import create_app

ROOT = Path(__file__).resolve().parents[2]


def test_api_preview_and_bounds(fixture_video, tmp_path):
    shutil.copytree(ROOT / "config", tmp_path / "config")
    target = tmp_path / "artifacts/fixtures"
    target.mkdir(parents=True)
    shutil.copy(fixture_video, target / "sky.mkv")
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        assert "preview" in client.get("/api/v1/health").json()["capabilities"]
        documentation = client.get("/docs")
        assert documentation.status_code == 200
        assert (
            "<script" not in documentation.text and "https://" not in documentation.text
        )
        assert client.get("/openapi.json").status_code == 200
        assert len(client.get("/api/v1/sources").json()) == 2
        for source in ("synthetic", "video-file"):
            packet = client.get(f"/api/v1/preview/{source}/20").json()
            assert packet["relative_seconds"] == 1
            assert packet["captured_at_utc"] is None
            image = client.get(f"/api/v1/preview/{source}/20/image")
            assert image.headers["content-type"] == "image/jpeg"
            assert image.content.startswith(b"\xff\xd8")
            assert client.get(f"/api/v1/preview/{source}/999").status_code == 404
            assert client.get(f"/api/v1/preview/{source}/-1").status_code == 404
        assert client.get("/api/v1/preview/rtsp/0").status_code == 422


def test_browser_workflow_api_and_origin_guards(fixture_video, tmp_path):
    import io
    import time
    import zipfile

    shutil.copytree(ROOT / "config", tmp_path / "config")
    target = tmp_path / "artifacts/fixtures"
    target.mkdir(parents=True)
    shutil.copy(fixture_video, target / "sky.mkv")
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        assert (
            client.post(
                "/api/v1/demo/start", headers={"origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert (
            client.get("/api/v1/events", headers={"host": "evil.example"}).status_code
            == 400
        )
        assert client.get("/api/v1/events/%2E%2E%2Fsecrets").status_code == 404
        started = client.post("/api/v1/demo/start").json()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            job = client.get("/api/v1/jobs/" + started["job_id"]).json()
            if job["state"] != "running":
                break
            time.sleep(0.1)
        assert job["state"] == "complete", job
        events = client.get("/api/v1/events").json()
        assert events
        event_id = events[0]["id"]
        detail = client.get("/api/v1/events/" + event_id).json()
        assert detail["capture"]["complete"] is False
        assert detail["calibration"]["state"] == "unavailable"
        assert "manifest_path" not in detail
        review = client.post(
            f"/api/v1/events/{event_id}/reviews",
            json={"assessment": "likely_conventional", "note": "Generated source"},
        )
        assert review.status_code == 200
        result = client.post(f"/api/v1/events/{event_id}/export").json()
        assert result["verification"]["verified"]
        downloaded = client.get(result["download_url"])
        assert downloaded.status_code == 200
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert "capture_manifest.json" in archive.namelist()
        segment = detail["segments"][0]["id"]
        playback = client.get(
            f"/api/v1/events/{event_id}/segments/{segment}/preview.mp4"
        )
        assert (
            playback.status_code == 200
            and playback.headers["content-type"] == "video/mp4"
        )
        assert (
            client.get(
                f"/api/v1/events/{event_id}/segments/not-linked/source"
            ).status_code
            == 404
        )
        assert client.get("/api/v1/events?limit=100000").status_code == 422
        settings = client.get("/api/v1/settings").json()
        bad = client.put(
            "/api/v1/settings",
            json={"detector": settings["detector"], "rectangles": [[0, 0, 999, 999]]},
        )
        assert bad.status_code == 422
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8000") as reopened:
        assert reopened.get("/api/v1/events/" + event_id).json()["reviews"]


def test_websocket_status_and_cross_origin_rejection(tmp_path):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    shutil.copytree(ROOT / "config", tmp_path / "config")
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        with client.websocket_connect(
            "ws://127.0.0.1:8000/api/v1/ws/updates"
        ) as socket:
            assert socket.receive_json()["type"] == "health"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "ws://127.0.0.1:8000/api/v1/ws/updates",
                headers={"origin": "https://evil.example"},
            ):
                pass


def test_persisted_exclusion_mask_is_applied_to_next_analysis(fixture_video, tmp_path):
    import time

    shutil.copytree(ROOT / "config", tmp_path / "config")
    target = tmp_path / "artifacts/fixtures"
    target.mkdir(parents=True)
    shutil.copy(fixture_video, target / "sky.mkv")
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8000") as client:
        settings = client.get("/api/v1/settings").json()
        result = client.put(
            "/api/v1/settings",
            json={"detector": settings["detector"], "rectangles": [[0, 0, 320, 180]]},
        )
        assert result.status_code == 200 and result.json()["revision"] == 1
        job_id = client.post("/api/v1/demo/start").json()["job_id"]
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            job = client.get("/api/v1/jobs/" + job_id).json()
            if job["state"] != "running":
                break
            time.sleep(0.1)
        assert job["state"] == "complete"
        assert job["result"]["events"] == []
        assert job["result"]["candidates"] == 0
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8000") as reopened:
        assert reopened.get("/api/v1/settings").json()["rectangles"] == [
            [0, 0, 320, 180]
        ]
