from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from .models import Identifier, Model, SourceKind


class RecordingConfig(Model):
    mode: Literal["rolling_segments"] = "rolling_segments"
    nominal_segment_seconds: int = Field(default=10, gt=0)
    preserve_pre_seconds: int = Field(default=30, ge=0)
    preserve_post_seconds: int = Field(default=45, ge=0)


class CameraConfig(Model):
    id: Identifier
    name: str
    type: SourceKind
    enabled: bool = False
    url_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$")
    path: str | None = None
    transport: Literal["tcp", "udp"] = "tcp"
    analysis_fps: float = Field(default=5, gt=0)
    analysis_width: int = Field(default=960, gt=0)
    record_audio: Literal[False] = False
    masks_file: str | None = None
    profile: str = "fixture"
    recording: RecordingConfig = Field(default_factory=RecordingConfig)

    @model_validator(mode="after")
    def valid_source(self) -> CameraConfig:
        if self.type == SourceKind.rtsp and not self.url_env:
            raise ValueError("rtsp config requires url_env (never a literal URL)")
        if self.type == SourceKind.rtsp and self.enabled:
            raise ValueError("RTSP is unavailable in WP0; leave enabled false")
        if self.type == SourceKind.video_file and (not self.path or "://" in self.path):
            raise ValueError("video_file requires a local path")
        return self


class StationConfig(Model):
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8000, ge=1024, le=65535)
    cameras: list[str] = Field(min_length=1)
    rolling_quota_bytes: int = Field(default=2147483648, ge=0)
    safety_reserve_bytes: int = Field(default=134217728, ge=0)


def load_station(path: Path) -> tuple[StationConfig, list[CameraConfig]]:
    station = StationConfig.model_validate(yaml.safe_load(path.read_text()))
    cameras = [
        CameraConfig.model_validate(yaml.safe_load((path.parent / p).read_text()))
        for p in station.cameras
    ]
    if len({c.id for c in cameras}) != len(cameras):
        raise ValueError("camera IDs must be unique")
    return station, cameras
