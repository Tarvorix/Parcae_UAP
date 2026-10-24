import pytest
from uap_observer.models import DetectionCandidate
from uap_observer.tracking.tracker import Tracker


def candidate(index, x, y=90):
    return DetectionCandidate(
        candidate_id=str(index),
        frame_id=str(index),
        camera_id="camera",
        session_id="session",
        x_px=x,
        y_px=y,
        area_px=5,
        detector_version="test",
    )


def test_two_crossing_tracks_keep_direction_and_raw_observations():
    tracker = Tracker()
    for index in range(80):
        tracker.update(
            index * 0.05,
            [
                candidate(f"a{index}", 30 + 3 * index),
                candidate(f"b{index}", 290 - 3 * index),
            ],
        )
    assert len(tracker.tracks) == 2
    first, second = tracker.tracks
    assert first.observations[-1]["x_px"] > first.observations[0]["x_px"]
    assert second.observations[-1]["x_px"] < second.observations[0]["x_px"]
    assert first.observations[0]["candidate_id"] == "a0"
    assert all("candidate_id" not in point for point in first.fitted)
    assert len(first.fitted) == len(first.observations) == 80


def test_lost_reacquired_and_ended_fragments():
    tracker = Tracker(max_gap=0.5)
    for index in range(6):
        tracker.update(index * 0.05, [candidate(index, 20 + index)])
    track = tracker.tracks[0]
    tracker.update(0.4, [])
    assert track.state == "temporarily_lost"
    tracker.update(0.5, [candidate("return", 30)])
    assert track.state == "confirmed" and len(tracker.tracks) == 1
    assert track.associations[-1]["reacquired"]
    tracker.update(1.2, [candidate("new", 40)])
    assert track.state == "ended" and len(tracker.tracks) == 2
    with pytest.raises(ValueError, match="backward"):
        tracker.update(0.1, [])


def test_long_track_fragments_preserve_all_points_with_bounded_history():
    tracker = Tracker(max_history_points=32)
    saved = []
    for index in range(1000):
        tracker.update(index / 20, [candidate(index, index)])
        saved.extend(tracker.drain_ended())
        assert len(tracker.tracks) == 1
        assert len(tracker.tracks[0].observations) <= 32
    saved.extend(tracker.tracks)
    assert sum(len(track.observations) for track in saved) == 1000
    assert len({track.logical_id for track in saved}) == 1
    assert saved[-1].observation_count == 1000
    assert saved[-1].first_observed == 0
    assert all(b.parent_id == a.id for a, b in zip(saved, saved[1:]))
    assert [p["candidate_id"] for t in saved for p in t.observations] == [
        str(i) for i in range(1000)
    ]
