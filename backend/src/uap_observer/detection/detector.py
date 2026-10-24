import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

import cv2
import numpy as np
import numpy.typing as npt
from pydantic import Field

from ..models import DetectionCandidate, Model


class DetectorConfig(Model):
    mode: Literal["compact", "difference", "background"] = "compact"
    contrast: float = Field(default=18, gt=0)
    change: float = Field(default=10, gt=0)
    min_area: int = Field(default=2, ge=1)
    max_area: int = Field(default=2000, ge=1)
    exposure_jump: float = Field(default=8, gt=0)
    jitter_px: float = Field(default=0.7, gt=0)
    profile: Literal["day", "night"] = "night"
    mask_version: str = "none"


@dataclass(frozen=True)
class DetectionResult:
    candidates: list[DetectionCandidate]
    flags: list[str]
    suppressed: str | None = None


class Detector:
    version = "baseline-1"

    def __init__(
        self, config: DetectorConfig | None = None, exclusion: npt.NDArray[np.uint8] | None = None
    ) -> None:
        self.config = config or DetectorConfig()
        self.exclusion = (
            None
            if exclusion is None or not np.any(exclusion)
            else np.asarray(exclusion, dtype=np.uint8).copy()
        )
        self.mask_parameters: dict[str, Any] | None = None
        if self.exclusion is not None:
            self.exclusion.setflags(write=False)
            bits = np.packbits(self.exclusion.reshape(-1) > 0, bitorder="big").tobytes()
            self.mask_parameters = {
                "encoding": "packbits-big-endian",
                "shape": list(self.exclusion.shape),
                "bits_base64": base64.b64encode(bits).decode(),
                "sha256": hashlib.sha256(bits).hexdigest(),
            }
        self.previous: npt.NDArray[np.uint8] | None = None
        self.background = cv2.createBackgroundSubtractorMOG2(
            history=100, varThreshold=25, detectShadows=False
        )
        self.seen = 0
        hash_parameters = self.config.model_dump()
        if self.mask_parameters is not None:
            hash_parameters["_exclusion_mask"] = self.mask_parameters

        def normalize(value: Any) -> Any:
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            return value

        self.parameter_hash = hashlib.sha256(
            json.dumps(normalize(hash_parameters), sort_keys=True).encode()
        ).hexdigest()

    def parameters(self) -> dict[str, Any]:
        return {"detector": self.config.model_dump(), "exclusion_mask": self.mask_parameters}

    @classmethod
    def from_parameters(cls, parameters: dict[str, Any]) -> Detector:
        config = DetectorConfig.model_validate(parameters["detector"])
        mask = parameters.get("exclusion_mask")
        exclusion = None
        if mask is not None:
            if mask["encoding"] != "packbits-big-endian":
                raise ValueError("unsupported mask encoding")
            shape = tuple(int(value) for value in mask["shape"])
            if len(shape) != 2 or min(shape) <= 0 or shape[0] * shape[1] > 10_000_000:
                raise ValueError("invalid mask dimensions")
            bits = base64.b64decode(mask["bits_base64"], validate=True)
            if (
                len(bits) != (shape[0] * shape[1] + 7) // 8
                or hashlib.sha256(bits).hexdigest() != mask["sha256"]
            ):
                raise ValueError("mask integrity failure")
            exclusion = np.unpackbits(
                np.frombuffer(bits, dtype=np.uint8), bitorder="big", count=shape[0] * shape[1]
            ).reshape(shape)
        return cls(config, exclusion)

    def process(
        self, pixels: npt.NDArray[np.uint8], frame_id: str, camera_id: str, session_id: str
    ) -> DetectionResult:
        gray = np.asarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
        if self.exclusion is not None and self.exclusion.shape != gray.shape:
            raise ValueError("mask dimensions do not match analysis frame")
        old = self.previous
        self.previous = gray.copy()
        self.seen += 1
        foreground = self.background.apply(gray)
        if old is None:
            return DetectionResult([], ["warming_up"], "initial_frame")
        flags = []
        shift = float(np.median(gray.astype(np.int16) - old.astype(np.int16)))
        if abs(shift) >= self.config.exposure_jump:
            return DetectionResult([], ["exposure_transition"], "scene_exposure_change")
        features = cv2.goodFeaturesToTrack(old, maxCorners=40, qualityLevel=0.04, minDistance=6)
        if features is not None and len(features) >= 4:
            moved, status, _ = cv2.calcOpticalFlowPyrLK(old, gray, features, features.copy())
            if moved is not None and status is not None:
                valid = status.reshape(-1) > 0
                if np.count_nonzero(valid) >= 4:
                    vectors = (moved - features).reshape(-1, 2)[valid]
                    jitter = float(np.linalg.norm(np.median(vectors, axis=0)))
                    if jitter > self.config.jitter_px:
                        return DetectionResult([], ["camera_jitter"], "global_frame_shift")
        else:
            flags.append("jitter_reference_insufficient")
        contrast = gray.astype(np.float32) - cv2.GaussianBlur(gray, (0, 0), 3).astype(np.float32)
        difference = cv2.absdiff(gray, old)
        if self.config.mode == "compact":
            # Bright point path; new bright signal excludes static stars and trailing ghosts.
            changed = cv2.subtract(gray, old)
            binary = (
                np.asarray(
                    (contrast >= self.config.contrast) & (changed >= self.config.change),
                    dtype=np.uint8,
                )
                * 255
            )
        elif self.config.mode == "difference":
            binary = np.asarray(difference >= self.config.change, dtype=np.uint8) * 255
        else:
            if self.seen < 10:
                return DetectionResult([], ["warming_up"], "background_warmup")
            binary = (
                np.asarray((foreground > 0) & (difference >= self.config.change), dtype=np.uint8)
                * 255
            )
        if self.exclusion is not None:
            binary[self.exclusion > 0] = 0
        count, labels, stats, centers = cv2.connectedComponentsWithStats(binary, connectivity=8)
        candidates = []
        for index in range(1, count):
            x, y, w, h, area = [int(v) for v in stats[index]]
            if not self.config.min_area <= area <= self.config.max_area:
                continue
            selection = labels == index
            candidates.append(
                DetectionCandidate(
                    candidate_id=uuid5(
                        NAMESPACE_URL, f"{frame_id}:{self.parameter_hash}:{index}"
                    ).hex,
                    frame_id=frame_id,
                    camera_id=camera_id,
                    session_id=session_id,
                    x_px=float(centers[index][0]),
                    y_px=float(centers[index][1]),
                    area_px=area,
                    detector_version=self.version,
                    bbox_px=[x, y, w, h],
                    contrast=float(np.mean(contrast[selection])),
                    parameter_hash=self.parameter_hash,
                    mask_version=self.config.mask_version,
                    profile=self.config.profile,
                    quality_flags=flags,
                )
            )
        return DetectionResult(candidates, flags)
