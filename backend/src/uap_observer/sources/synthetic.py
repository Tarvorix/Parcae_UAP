from collections.abc import Iterator
from uuid import uuid4

import cv2
import numpy as np
import numpy.typing as npt

from ..models import FramePacket, SourceHealth, SourceKind, StreamInfo
from .base import CameraSource, DecodedFrame

WIDTH, HEIGHT, FPS, COUNT, SEED = 320, 180, 20, 120, 20261024


def ground_truth(index: int) -> dict[str, object]:
    jitter = 1 if 40 <= index < 60 else 0
    return {
        "source_frame_index": index,
        "relative_seconds": index / FPS,
        "jitter_px": [jitter, 0],
        "exposure_offset": 12 if index >= 80 else 0,
        "objects": [
            {
                "id": "dot-a",
                "x_px": 20 + 2 * index + jitter,
                "y_px": 55,
                "radius_px": 2,
                "visible": True,
            },
            {
                "id": "blink-b",
                "x_px": 290 - 2 * index + jitter,
                "y_px": 110,
                "radius_px": 3,
                "visible": (index // 5) % 2 == 0,
            },
        ],
    }


def render(index: int) -> npt.NDArray[np.uint8]:
    if not 0 <= index < COUNT:
        raise IndexError(index)
    rng = np.random.default_rng(SEED + index)
    gray = np.clip(rng.normal(12, 2, (HEIGHT, WIDTH)), 0, 255).astype(np.uint8)
    image = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    jitter = 1 if 40 <= index < 60 else 0
    for x, y in [(25, 30), (70, 22), (130, 45), (190, 20), (265, 38)]:
        cv2.circle(image, (x + jitter, y), 1, (100, 100, 100), -1)
    cv2.circle(image, (20 + 2 * index + jitter, 55), 2, (230, 230, 230), -1)
    if (index // 5) % 2 == 0:
        cv2.circle(image, (290 - 2 * index + jitter, 110), 3, (180, 180, 180), -1)
    if index >= 80:
        image = cv2.add(image, np.full_like(image, 12))
    return np.asarray(image, dtype=np.uint8)


class SyntheticSource(CameraSource):
    def __init__(self, source_id: str = "synthetic", camera_id: str = "fixture-sky") -> None:
        self.source_id, self.camera_id = source_id, camera_id
        self.session_id = ""
        self.health = SourceHealth(source_id=source_id, state="stopped")

    def probe(self) -> StreamInfo:
        return StreamInfo(
            source_id=self.source_id,
            camera_id=self.camera_id,
            kind=SourceKind.synthetic,
            codec="raw-bgr24",
            width=WIDTH,
            height=HEIGHT,
            fps=FPS,
            frame_count=COUNT,
            time_base=f"1/{FPS}",
        )

    def start(self) -> None:
        if self.health.state != "running":
            self.session_id = uuid4().hex
            self.health = SourceHealth(source_id=self.source_id, state="running")

    def stop(self) -> None:
        self.health = self.health.model_copy(update={"state": "stopped"})

    def get_health(self) -> SourceHealth:
        return self.health

    def frames(self) -> Iterator[DecodedFrame]:
        if self.health.state != "running":
            raise RuntimeError("start source before reading")
        for index in range(COUNT):
            if self.health.state != "running":
                return
            packet = FramePacket(
                source_id=self.source_id,
                camera_id=self.camera_id,
                session_id=self.session_id,
                frame_id=f"{self.session_id}:{index}",
                source_frame_index=index,
                source_pts=index,
                time_base=f"1/{FPS}",
                relative_seconds=index / FPS,
                timestamp_basis="generated",
                timestamp_uncertainty_seconds=0,
                width=WIDTH,
                height=HEIGHT,
            )
            self.health = self.health.model_copy(
                update={"decoded_frames": index + 1, "last_relative_seconds": index / FPS}
            )
            yield DecodedFrame(packet, render(index))
        self.health = self.health.model_copy(update={"state": "ended"})
