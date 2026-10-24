"""Versioned contracts. Event/manifests are schemas, not persistence services."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]+$", min_length=1)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0"] = "1.0"


class SourceKind(StrEnum):
    synthetic = "synthetic"
    video_file = "video_file"
    rtsp = "rtsp"


class StreamInfo(Model):
    source_id: Identifier
    camera_id: Identifier
    kind: SourceKind
    codec: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: float | None = Field(default=None, gt=0)
    frame_count: int | None = Field(default=None, ge=0)
    has_audio: bool = False
    time_base: str | None = None


class FramePacket(Model):
    source_id: Identifier
    camera_id: Identifier
    session_id: Identifier
    frame_id: Identifier
    source_frame_index: int | None = Field(default=None, ge=0)
    analysis_frame_index: int | None = Field(default=None, ge=0)
    source_packet_sequence: int | None = Field(default=None, ge=0)
    source_pts: int | None = None
    time_base: str | None = None
    relative_seconds: float = Field(ge=0)
    timestamp_basis: Literal[
        "generated", "file_best_effort_pts", "frame_rate_estimate", "archived_source_pts"
    ]
    timestamp_uncertainty_seconds: float | None = Field(default=None, ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    # Offline file media time is not a measured UTC exposure timestamp.
    captured_at_utc: str | None = None
    segment_id: Identifier | None = None
    source_packet_id: Identifier | None = None
    mapping_state: Literal["exact", "ambiguous", "unavailable"] | None = None
    receipt_utc: str | None = None


class DetectionCandidate(Model):
    candidate_id: Identifier
    frame_id: Identifier
    camera_id: Identifier
    session_id: Identifier
    x_px: float = Field(ge=0)
    y_px: float = Field(ge=0)
    area_px: float = Field(gt=0)
    detector_version: str
    bbox_px: list[int] = Field(default_factory=list)
    contrast: float | None = None
    parameter_hash: str | None = None
    mask_version: str = "none"
    profile: str = "night"
    quality_flags: list[str] = Field(default_factory=list)


class Track(Model):
    track_id: Identifier
    camera_id: Identifier
    session_id: Identifier
    candidate_ids: list[Identifier]
    state: Literal["tentative", "confirmed", "lost", "ended"]


class Event(Model):
    event_id: Identifier
    camera_id: Identifier
    session_id: Identifier
    track_ids: list[Identifier]
    state: Literal["open", "complete", "partial"]
    interpretation: Literal["unresolved"] = "unresolved"
    limitations: list[str] = Field(default_factory=list)


class ArtifactDigest(Model):
    relative_path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def safe_path(self) -> ArtifactDigest:
        if self.relative_path.startswith("/") or "\\" in self.relative_path:
            raise ValueError("artifact path must be portable and relative")
        if any(p in ("", ".", "..") for p in self.relative_path.split("/")):
            raise ValueError("artifact path must not traverse directories")
        return self


class ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1.0", "1.1"] = "1.1"


class BuildInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    software_version: str
    dependencies: dict[str, str] = Field(default_factory=dict)
    python_version: str
    system: str
    architecture: str
    backend_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RecordedStream(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    codec: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    fps: str | None = None
    time_base: str | None = None
    audio_recorded: Literal[False] = False
    pyav_version: str | None = None
    libraries: dict[str, tuple[int, ...]] = Field(default_factory=dict)
    source_id: Identifier | None = None
    source_kind: Literal["video_file", "rtsp"] | None = None
    source_configuration: dict[str, str | float | bool | int] | None = None
    source_file_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_config_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    application_build: BuildInfo | None = None


class SegmentCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: Identifier
    session_id: Identifier
    start_seconds: float | None
    end_seconds: float | None


class CaptureManifest(ManifestModel):
    capture_id: Identifier
    camera_id: Identifier
    session_id: Identifier
    source_id: Identifier | None = None
    artifacts: list[ArtifactDigest]
    complete: bool
    gaps: list[dict[str, str] | str]
    requested_interval: tuple[float, float] | None = None
    trigger_interval: tuple[float, float] | None = None
    stream_info: RecordedStream | None = None
    limitations: list[str] = Field(default_factory=list)
    coverage: list[SegmentCoverage] = Field(default_factory=list)
    timestamp_basis: Literal["original_source_pts"] = "original_source_pts"
    exposure_utc_quality: Literal["unknown"] = "unknown"
    audio_recorded: Literal[False] = False
    source_preservation: str | None = None

    @model_validator(mode="after")
    def complete_contract(self) -> CaptureManifest:
        if self.schema_version == "1.1":
            if not self.source_id or not self.stream_info or not self.stream_info.application_build:
                raise ValueError("capture provenance is required for schema1.1")
            if self.stream_info.source_id != self.source_id:
                raise ValueError("source identity mismatch")
            if (
                not self.stream_info.source_configuration
                or not self.stream_info.source_config_sha256
            ):
                raise ValueError("full source configuration is required")
            if self.requested_interval is None or self.trigger_interval is None:
                raise ValueError("capture intervals are required")
            if self.complete and (self.limitations or self.gaps or not self.coverage):
                raise ValueError("complete capture cannot have missing context")
        if any(item.session_id != self.session_id for item in self.coverage):
            raise ValueError("coverage crosses a source session")
        if len({a.relative_path for a in self.artifacts}) != len(self.artifacts):
            raise ValueError("duplicate capture artifact")
        return self


class ExportManifest(ManifestModel):
    export_id: Identifier
    capture_id: Identifier
    capture_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    analysis_revision: str | None = None
    review_revision: str | int | None = None
    artifacts: list[ArtifactDigest]
    processing_metadata: Literal["complete", "not_analyzed", "legacy_partial"] = "not_analyzed"
    limitations: list[str] = Field(default_factory=list)
    export_build: BuildInfo | None = None

    @model_validator(mode="after")
    def export_contract(self) -> ExportManifest:
        names = [artifact.relative_path for artifact in self.artifacts]
        if len(set(names)) != len(names) or "manifest.json" in names:
            raise ValueError("export artifact list must be unique and exclude itself")
        if self.schema_version == "1.1" and self.export_build is None:
            raise ValueError("export build provenance required")
        return self


class SourceHealth(Model):
    source_id: Identifier
    state: Literal["stopped", "running", "ended", "failed", "unsupported"]
    decoded_frames: int = Field(default=0, ge=0)
    decode_errors: int = Field(default=0, ge=0)
    last_relative_seconds: float | None = None
    detail: str | None = None
