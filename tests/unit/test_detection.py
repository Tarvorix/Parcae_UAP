import cv2
import numpy as np
from uap_observer.detection.detector import Detector, DetectorConfig
from uap_observer.sources.synthetic import render


def test_motion_static_masks_and_quality():
    detector = Detector()
    results = [
        detector.process(render(i), str(i), "camera", "session") for i in range(120)
    ]
    assert sum(bool(r.candidates) for r in results) > 100
    assert results[80].suppressed == "scene_exposure_change"
    static = Detector()
    assert not any(
        static.process(render(0), str(i), "camera", "session").candidates
        for i in range(30)
    )
    mask = np.ones((180, 320), dtype=np.uint8)
    masked = Detector(exclusion=mask)
    assert not any(
        masked.process(render(i), str(i), "camera", "session").candidates
        for i in range(30)
    )


def test_repeatable_geometry_and_version_parameters():
    a, b = Detector(), Detector()
    for i in range(20):
        left = a.process(render(i), str(i), "camera", "session")
        right = b.process(render(i), str(i), "camera", "session")
        assert [(d.x_px, d.y_px, d.area_px) for d in left.candidates] == [
            (d.x_px, d.y_px, d.area_px) for d in right.candidates
        ]
    assert a.parameter_hash == b.parameter_hash
    assert a.parameter_hash != Detector(DetectorConfig(change=20)).parameter_hash


def test_independent_baselines_and_dark_motion():
    for mode in ("difference", "background"):
        detector = Detector(DetectorConfig(mode=mode))
        image = np.full((100, 100, 3), 150, dtype=np.uint8)
        for i in range(12):
            detector.process(image, str(i), "cam", "session")
        moved = image.copy()
        cv2.rectangle(moved, (30, 30), (40, 40), (10, 10, 10), -1)
        assert detector.process(moved, "moved", "cam", "session").candidates


def test_jitter_is_quality_flagged():
    detector = Detector()
    image = render(0)
    detector.process(image, "0", "cam", "session")
    shifted = cv2.warpAffine(image, np.float32([[1, 0, 3], [0, 1, 0]]), (320, 180))
    result = detector.process(shifted, "1", "cam", "session")
    assert result.suppressed == "global_frame_shift"
    assert not result.candidates


def test_parameter_snapshot_reconstructs_exact_mask_and_hash():
    import hashlib
    import json

    config = DetectorConfig()
    baseline = Detector(config)
    assert (
        baseline.parameter_hash
        == hashlib.sha256(
            json.dumps(config.model_dump(), sort_keys=True).encode()
        ).hexdigest()
    )
    mask = np.zeros((180, 320), dtype=np.uint8)
    mask[:, 0:100] = 255
    original = Detector(config, mask)
    rebuilt = Detector.from_parameters(json.loads(json.dumps(original.parameters())))
    assert original.parameter_hash == rebuilt.parameter_hash != baseline.parameter_hash
    for index in range(15):
        a = original.process(render(index), str(index), "camera", "session")
        b = rebuilt.process(render(index), str(index), "camera", "session")
        assert [c.model_dump() for c in a.candidates] == [
            c.model_dump() for c in b.candidates
        ]
