import json
from pathlib import Path

from uap_observer.capture.analysis import ArchiveAnalysis
from uap_observer.capture.recorder import Recorder
from uap_observer.events.processor import OfflineProcessor, reanalyze_capture
from uap_observer.evidence.service import EvidenceService
from uap_observer.evidence.verify import verify_package
from uap_observer.storage.ledger import Ledger


def test_detector_events_replay_export_and_restart(fixture_video, tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    recorder = Recorder(ledger, tmp_path / "archive", str(fixture_video))
    recorder.start()
    recorder.wait()
    index = ArchiveAnalysis(ledger)
    index.process_pending()
    evidence = EvidenceService(ledger, tmp_path / "evidence")
    metrics = OfflineProcessor(ledger, evidence).process(recorder.session_id)
    assert metrics["frames_processed"] == 120
    assert metrics["events"]
    assert metrics["candidates"] > 100
    for event_id in metrics["events"]:
        event = evidence.event(event_id)
        assert (
            event["state"] == "incomplete_source_gap"
        )  # six-second fixture cannot supply pre/post
        package = evidence.export(event_id)
        assert verify_package(package)["verified"]
        import zipfile

        from uap_observer.models import CaptureManifest, ExportManifest

        with zipfile.ZipFile(package) as bundle:
            cap = CaptureManifest.model_validate_json(
                bundle.read("capture_manifest.json")
            )
            exported = ExportManifest.model_validate_json(bundle.read("manifest.json"))
            assert cap.schema_version == "1.1" and cap.stream_info.application_build
            assert exported.processing_metadata == "complete"
            saved = json.loads(bundle.read("analyses.json"))
            assert (
                json.loads(saved[0]["data_json"])["parameters"]["detector"]["contrast"]
                == 18
            )
        analysis = ledger.query("SELECT * FROM analyses WHERE event_id=?", (event_id,))[
            0
        ]
        content = json.loads(analysis["data_json"])
        assert content["class"] == "unresolved_insufficient_data"
        assert content["track"]["observations"]
        manifest = Path(event["manifest_path"]).read_bytes()
        restored = EvidenceService(Ledger(ledger.path), evidence.root)
        reanalyze_capture(ledger, restored, event_id)
        restored.review(event_id, "likely_conventional", "Known generated scene")
        assert verify_package(restored.export(event_id))["verified"]
        assert Path(event["manifest_path"]).read_bytes() == manifest
        from uap_observer.detection.detector import DetectorConfig

        empty_revision = reanalyze_capture(
            ledger, restored, event_id, DetectorConfig(contrast=255)
        )
        empty = json.loads(
            ledger.query(
                "SELECT data_json FROM analyses WHERE id=?", (empty_revision,)
            )[0]["data_json"]
        )
        assert empty["tracks"] == [] and empty["reason_codes"]
        assert empty["class"] == "unresolved_insufficient_data"
        assert Path(event["manifest_path"]).read_bytes() == manifest


def test_encoded_crossing_targets_keep_observable_tracks(tmp_path):
    import subprocess

    import av
    import cv2
    import numpy as np
    from uap_observer.detection.detector import Detector
    from uap_observer.tracking.tracker import Tracker

    video = tmp_path / "crossing.mkv"
    encoder = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pixel_format",
            "bgr24",
            "-video_size",
            "320x180",
            "-framerate",
            "20",
            "-i",
            "pipe:0",
            "-c:v",
            "ffv1",
            "-g",
            "20",
            str(video),
        ],
        stdin=subprocess.PIPE,
    )
    assert encoder.stdin
    for index in range(80):
        image = np.full((180, 320, 3), 12, dtype=np.uint8)
        for x, y in ((20, 20), (70, 30), (120, 15), (170, 30), (220, 20), (270, 30)):
            cv2.circle(image, (x, y), 1, (100, 100, 100), -1)
        cv2.circle(image, (30 + 3 * index, 90), 2, (240, 240, 240), -1)
        cv2.circle(image, (290 - 3 * index, 90), 2, (240, 240, 240), -1)
        encoder.stdin.write(image.tobytes())
    encoder.stdin.close()
    assert encoder.wait(20) == 0
    detector = Detector()
    tracker = Tracker()
    with av.open(str(video)) as source:
        for index, frame in enumerate(source.decode(video=0)):
            result = detector.process(
                frame.to_ndarray(format="bgr24"), str(index), "cam", "session"
            )
            tracker.update(index / 20, result.candidates)
    long = [track for track in tracker.tracks if len(track.observations) > 50]
    assert len(long) == 2
    directions = [
        track.observations[-1]["x_px"] - track.observations[0]["x_px"] for track in long
    ]
    assert min(directions) < -150 and max(directions) > 150


def test_long_encoded_track_keeps_one_event_and_every_fragment(tmp_path):
    import subprocess
    import zipfile

    import cv2
    import numpy as np

    video = tmp_path / "long-track.mkv"
    encoder = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            "960x180",
            "-r",
            "20",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "ffv1",
            "-g",
            "20",
            str(video),
        ],
        stdin=subprocess.PIPE,
    )
    assert encoder.stdin
    for index in range(600):
        image = np.full((180, 960, 3), 12, dtype=np.uint8)
        for x, y in ((20, 20), (170, 30), (320, 20), (470, 30), (620, 20), (770, 30)):
            cv2.circle(image, (x, y), 1, (100, 100, 100), -1)
        cv2.circle(image, (40 + index, 90), 2, (240, 240, 240), -1)
        encoder.stdin.write(image.tobytes())
    encoder.stdin.close()
    assert encoder.wait(20) == 0
    ledger = Ledger(tmp_path / "ledger.sqlite")
    recorder = Recorder(ledger, tmp_path / "archive", str(video))
    recorder.start()
    recorder.wait()
    ArchiveAnalysis(ledger).process_pending()
    evidence = EvidenceService(ledger, tmp_path / "evidence")
    metrics = OfflineProcessor(ledger, evidence).process(
        recorder.session_id, pre=0, post=0
    )
    assert len(metrics["events"]) == 1
    event_id = metrics["events"][0]
    fragments = [
        json.loads(row["data_json"])
        for row in ledger.query(
            "SELECT * FROM analyses WHERE event_id=? ORDER BY created_utc", (event_id,)
        )
    ]
    points = [
        point for fragment in fragments for point in fragment["track"]["observations"]
    ]
    assert len(fragments) >= 2
    assert len(points) == 599
    assert len({point["frame_id"] for point in points}) == 599
    assert max(len(fragment["track"]["observations"]) for fragment in fragments) <= 512
    assert len({f["track"]["logical_track_id"] for f in fragments}) == 1
    event = evidence.event(event_id)
    assert event["start_seconds"] == points[0]["time_seconds"]
    assert event["end_seconds"] == points[-1]["time_seconds"]
    capture = Path(event["manifest_path"]).read_bytes()
    package = evidence.export(event_id)
    assert verify_package(package)["verified"]
    with zipfile.ZipFile(package) as archive:
        saved = json.loads(archive.read("analyses.json"))
        assert (
            sum(
                len(json.loads(row["data_json"])["track"]["observations"])
                for row in saved
            )
            == 599
        )
    reanalyze_capture(ledger, evidence, event_id)
    assert Path(event["manifest_path"]).read_bytes() == capture
    assert verify_package(evidence.export(event_id))["verified"]
