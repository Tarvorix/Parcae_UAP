import numpy as np
import pytest
from uap_observer.sources.rtsp import RTSPSource
from uap_observer.sources.synthetic import COUNT, SyntheticSource, ground_truth, render


def test_synthetic_determinism_and_truth():
    np.testing.assert_array_equal(render(42), render(42))
    assert not np.array_equal(render(42), render(43))
    assert ground_truth(42)["jitter_px"] == [1, 0]
    assert ground_truth(80)["exposure_offset"] == 12
    assert render(0)[55, 20, 0] == 230
    assert render(0)[110, 290, 0] == 180
    assert render(5)[110, 280, 0] < 30


def test_lifecycle_and_identity():
    source = SyntheticSource()
    with pytest.raises(RuntimeError):
        next(source.frames())
    source.start()
    session = source.session_id
    source.start()
    assert source.session_id == session
    frames = list(source.frames())
    assert len(frames) == COUNT
    assert source.get_health().state == "ended"
    assert len({f.packet.frame_id for f in frames}) == COUNT
    assert frames[-1].packet.relative_seconds == 5.95
    source.stop()
    source.stop()
    source.start()
    assert session != source.session_id
    iterator = source.frames()
    next(iterator)
    source.stop()
    assert list(iterator) == []


def test_rtsp_explicitly_unavailable():
    source = RTSPSource("camera", "camera")
    assert source.get_health().state == "unsupported"
    with pytest.raises(NotImplementedError):
        source.start()
