from pathlib import Path

import pytest
from pydantic import ValidationError
from uap_observer.config import CameraConfig, load_station
from uap_observer.models import ArtifactDigest, CaptureManifest, ExportManifest

ROOT = Path(__file__).resolve().parents[2]


def test_example_configs():
    station, cameras = load_station(ROOT / "config/station.example.yaml")
    assert station.host == "127.0.0.1"
    assert len(cameras) == 2
    assert not cameras[1].enabled


@pytest.mark.parametrize(
    "extra",
    [
        {"url": "rtsp://secret"},
        {"analysis_fps": 0},
        {"record_audio": True},
        {"enabled": True},
    ],
)
def test_invalid_camera_config(extra):
    with pytest.raises(ValidationError):
        CameraConfig.model_validate(
            {
                "id": "g100",
                "name": "camera",
                "type": "rtsp",
                "url_env": "UAP_URL",
                **extra,
            }
        )


@pytest.mark.parametrize("path", ["../secret", "/private", "a/../b", "a\\b"])
def test_manifest_rejects_nonportable_paths(path):
    with pytest.raises(ValidationError):
        ArtifactDigest(relative_path=path, sha256="a" * 64, size_bytes=1)


def test_manifest_round_trip_and_revision_separation():
    artifact = ArtifactDigest(
        relative_path="source/segment.mkv", sha256="a" * 64, size_bytes=3
    )
    capture = CaptureManifest(
        schema_version="1.0",
        capture_id="c",
        camera_id="cam",
        session_id="session",
        source_id="s",
        artifacts=[artifact],
        complete=False,
        gaps=["scripted gap"],
    )
    assert CaptureManifest.model_validate_json(capture.model_dump_json()) == capture
    export = ExportManifest(
        schema_version="1.0",
        export_id="e",
        capture_id="c",
        capture_manifest_sha256="b" * 64,
        review_revision="r2",
        artifacts=[artifact],
    )
    assert "review_revision" not in capture.model_dump()
    assert export.review_revision == "r2"


def test_data_lease_rejects_second_owner_and_releases(tmp_path):
    from uap_observer.storage.lease import DataLease

    owner = DataLease(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            DataLease(tmp_path)
    finally:
        owner.close()
    replacement = DataLease(tmp_path)
    replacement.close()
