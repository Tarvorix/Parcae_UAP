from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from ..models import FramePacket, SourceHealth, StreamInfo


@dataclass(frozen=True)
class DecodedFrame:
    packet: FramePacket
    pixels: npt.NDArray[np.uint8]


class CameraSource(ABC):
    """Finite offline iterator now; live bounded delivery/recording belongs to WP1."""

    @abstractmethod
    def probe(self) -> StreamInfo: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def get_health(self) -> SourceHealth: ...

    @abstractmethod
    def frames(self) -> Iterator[DecodedFrame]: ...
