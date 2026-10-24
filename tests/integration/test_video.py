import hashlib
import json

import numpy as np
import pytest
from uap_observer.fixtures import generate
from uap_observer.sources import VideoFileSource
from uap_observer.sources.synthetic import COUNT, render


def test_encoded_round_trip(fixture_video):
    source = VideoFileSource(fixture_video)
    info = source.probe()
    assert (info.width, info.height, info.codec, info.frame_count) == (
        320,
        180,
        "ffv1",
        COUNT,
    )
    assert not info.has_audio
    source.start()
    session = source.session_id
    source.start()
    assert source.session_id == session
    frames = list(source.frames())
    assert source.get_health().state == "ended"
    assert source.capture is None
    for index, frame in enumerate(frames):
        np.testing.assert_array_equal(frame.pixels, render(index))
        assert frame.packet.relative_seconds == pytest.approx(index / 20)
        assert frame.packet.source_pts == index * 50
        assert frame.packet.timestamp_basis == "file_best_effort_pts"
    source.stop()
    source.stop()


def test_encoded_reproducibility(fixture_video, tmp_path):
    other = generate(tmp_path)
    assert fixture_video.read_bytes() == other.read_bytes()
    truth = json.loads(other.with_name("sky.truth.json").read_text())
    assert truth["video_sha256"] == hashlib.sha256(other.read_bytes()).hexdigest()
    assert len(truth["frames"]) == COUNT


def test_bad_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        VideoFileSource(tmp_path / "absent")
    broken = tmp_path / "broken.mkv"
    broken.write_bytes(b"not video")
    source = VideoFileSource(broken)
    with pytest.raises(ValueError):
        source.start()
    assert source.get_health().state == "failed"
