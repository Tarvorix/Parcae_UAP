"""Independent streamed ZIP verification with a bounded disk-backed metadata index."""

import hashlib
import json
import sqlite3
import zipfile
from fractions import Fraction
from pathlib import Path
from typing import Any

import av

from ..models import CaptureManifest, ExportManifest
from .files import canonical, safe_member
from .index import metadata_index


def verify_package(path: Path) -> dict[str, Any]:
    errors: set[str] = set()
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or len(names) > 10000:
                raise ValueError("duplicate or excessive ZIP members")
            if sum(info.file_size for info in archive.infolist()) > 2_000_000_000:
                raise ValueError("evidence ZIP exceeds verifier limit")
            for name in names:
                safe_member(Path("/tmp/verification-root"), name)
            for metadata_name in ("manifest.json", "capture_manifest.json"):
                if archive.getinfo(metadata_name).file_size > 8 * 1024 * 1024:
                    raise ValueError("manifest exceeds verifier size limit")
            manifest = ExportManifest.model_validate_json(archive.read("manifest.json"))
            if set(names) != {"manifest.json", *(a.relative_path for a in manifest.artifacts)}:
                errors.add("unlisted_or_missing_members")
            for artifact in manifest.artifacts:
                with archive.open(artifact.relative_path) as handle:
                    hasher = hashlib.sha256()
                    while block := handle.read(1024 * 1024):
                        hasher.update(block)
                    actual = hasher.hexdigest()
                if (
                    actual != artifact.sha256
                    or archive.getinfo(artifact.relative_path).file_size != artifact.size_bytes
                ):
                    errors.add("artifact_hash:" + artifact.relative_path)
            capture_bytes = archive.read("capture_manifest.json")
            if hashlib.sha256(capture_bytes).hexdigest() != manifest.capture_manifest_sha256:
                errors.add("capture_manifest_hash")
            capture = CaptureManifest.model_validate_json(capture_bytes)
            if capture.capture_id != manifest.capture_id:
                errors.add("capture_identity")
            if capture.schema_version == "1.1" and capture.stream_info:
                info = capture.stream_info
                if (
                    hashlib.sha256(canonical(info.source_configuration)).hexdigest()
                    != info.source_config_sha256
                ):
                    errors.add("source_configuration_hash")
            export_artifacts = {a.relative_path: a for a in manifest.artifacts}
            for artifact in capture.artifacts:
                exported = export_artifacts.get(artifact.relative_path)
                if (
                    not exported
                    or exported.sha256 != artifact.sha256
                    or exported.size_bytes != artifact.size_bytes
                ):
                    errors.add("capture_artifact_reference")
            segments = {segment.id for segment in capture.coverage}
            with (
                archive.open("frames.json") as frames,
                archive.open("packets.json") as packets,
                metadata_index(frames, packets) as db,
            ):
                for stored in db.execute("SELECT * FROM frames"):
                    frame = json.loads(stored["data_json"])
                    if (
                        frame["segment_id"] not in segments
                        or frame["session_id"] != capture.session_id
                    ):
                        errors.add("frame_segment_reference")
                    if frame["mapping_state"] == "exact":
                        packet_row = db.execute(
                            "SELECT * FROM packets WHERE id=?", (frame["packet_id"],)
                        ).fetchone()
                        if packet_row is None:
                            errors.add("missing_packet_reference")
                            continue
                        packet = json.loads(packet_row["data_json"])
                        if (
                            packet["segment_id"] != frame["segment_id"]
                            or packet["session_id"] != frame["session_id"]
                        ):
                            errors.add("packet_segment_reference")
                        if frame["source_pts"] is None or any(
                            frame[key] != packet[key]
                            for key in ("source_pts", "source_dts", "source_time_base")
                        ):
                            errors.add("source_packet_reference")
                        if (
                            packet["archive_pts"] is None
                            or frame["archive_pts"] is None
                            or packet["archive_pts"] * Fraction(packet["archive_time_base"])
                            != frame["archive_pts"] * Fraction(frame["archive_time_base"])
                        ):
                            errors.add("archive_packet_time_reference")
                    elif frame["mapping_state"] not in ("ambiguous", "unavailable"):
                        errors.add("invalid_mapping_state")
                for segment in capture.coverage:
                    count = 0
                    with (
                        archive.open(f"source_segments/{segment.id}.mkv") as media,
                        av.open(media, mode="r", format="matroska") as video,
                    ):
                        references = db.execute(
                            "SELECT data_json FROM frames WHERE segment_id=? ORDER BY ordinal",
                            (segment.id,),
                        )
                        for decoded in video.decode(video=0):
                            row = references.fetchone()
                            if row is None:
                                errors.add("decoded_frame_count")
                                break
                            reference = json.loads(row["data_json"])
                            pixels = decoded.to_ndarray(format="bgr24")
                            if (
                                hashlib.sha256(pixels.tobytes()).hexdigest()
                                != reference["pixel_sha256"]
                            ):
                                errors.add("decoded_frame_hash")
                            if (
                                decoded.pts != reference["archive_pts"]
                                or str(decoded.time_base) != reference["archive_time_base"]
                            ):
                                errors.add("decoded_frame_pts")
                            count += 1
                        if references.fetchone() is not None:
                            errors.add("decoded_frame_count")
                    if count == 0:
                        errors.add("empty_source_segment")
            if manifest.schema_version == "1.1":
                from ..detection.detector import Detector
                from .index import items

                with archive.open("analyses.json") as handle:
                    for analysis in items(handle):
                        content = json.loads(analysis["data_json"])
                        if "parameters" not in content:
                            if manifest.processing_metadata == "complete":
                                errors.add("missing_processing_parameters")
                            continue
                        rebuilt = Detector.from_parameters(content["parameters"])
                        if rebuilt.parameter_hash != content["parameter_hash"]:
                            errors.add("detector_parameter_hash")
            return {
                "verified": not errors,
                "errors": sorted(errors),
                "complete": capture.complete,
                "limitations": capture.limitations,
                "export_id": manifest.export_id,
            }
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        ArithmeticError,
        sqlite3.Error,
        zipfile.BadZipFile,
    ) as exc:
        return {"verified": False, "errors": [type(exc).__name__]}
