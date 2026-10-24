"""Encoded synthetic sampling benchmark. Reports misses; never field accuracy."""

import json
import resource
import subprocess
import time
from pathlib import Path

import av
import cv2
import numpy as np
from uap_observer.detection.detector import Detector, DetectorConfig
from uap_observer.evidence.files import atomic_write, canonical

ROOT = Path(__file__).resolve().parents[1]


def run() -> dict:
    destination = ROOT / "artifacts/benchmark"
    destination.mkdir(parents=True, exist_ok=True)
    cases = [
        (diameter, contrast, duration)
        for diameter in (1, 3, 5)
        for contrast in (12, 64)
        for duration in (1, 4, 20)
    ]
    videos = []
    for crf in (18, 32):
        video = destination / f"sampling-crf{crf}.mkv"
        encoder = subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "gray",
                "-s",
                "1280x720",
                "-r",
                "20",
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                str(crf),
                "-g",
                "20",
                str(video),
            ],
            stdin=subprocess.PIPE,
        )
        assert encoder.stdin is not None
        for case, (diameter, contrast, duration) in enumerate(cases):
            for local in range(48):
                image = np.full((720, 1280), 16, dtype=np.uint8)
                for x, y in (
                    (100, 100),
                    (300, 120),
                    (600, 80),
                    (850, 100),
                    (1100, 130),
                ):
                    cv2.circle(image, (x, y), 1, 100, -1)
                # Fixed independent seeds per frame, repeatable across codecs.
                rng = np.random.default_rng(1000 + case * 48 + local)
                noise = rng.integers(-2, 3, image.shape, dtype=np.int16)
                image = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)
                visible = 20 <= local < 20 + duration
                if visible:
                    center = (200 + 8 * (local - 20), 350)
                    if diameter == 1:
                        image[center[1], center[0]] = 16 + contrast
                    else:
                        cv2.circle(image, center, diameter // 2, 16 + contrast, -1)
                encoder.stdin.write(image.tobytes())
        encoder.stdin.close()
        if encoder.wait(timeout=30):
            raise RuntimeError("benchmark encoding failed")
        videos.append((crf, video))
    results = []
    for crf, video in videos:
        for width, step in ((1280, 1), (960, 4), (1280, 4)):
            for phase in range(step):
                detector = Detector(DetectorConfig())
                hits = set()
                false_candidates = 0
                observations = 0
                started = time.perf_counter()
                cpu = time.process_time()
                with av.open(str(video)) as source:
                    for index, frame in enumerate(source.decode(video=0)):
                        if index % step != phase:
                            continue
                        image = frame.to_ndarray(format="bgr24")
                        if width != 1280:
                            image = cv2.resize(
                                image,
                                (width, int(720 * width / 1280)),
                                interpolation=cv2.INTER_AREA,
                            )
                        result = detector.process(
                            image, str(index), "benchmark", "session"
                        )
                        case, local = divmod(index, 48)
                        diameter, contrast, duration = cases[case]
                        visible = 20 <= local < 20 + duration
                        observations += int(visible)
                        for candidate in result.candidates:
                            dx = candidate.x_px * 1280 / width - (
                                200 + 8 * (local - 20)
                            )
                            dy = candidate.y_px * 1280 / width - 350
                            if visible and dx * dx + dy * dy < max(4, diameter) ** 2:
                                hits.add(case)
                            else:
                                false_candidates += 1
                rows = [
                    {
                        "diameter_px": d,
                        "contrast_levels": c,
                        "duration_seconds": n / 20,
                        "detected": i in hits,
                    }
                    for i, (d, c, n) in enumerate(cases)
                ]
                results.append(
                    {
                        "codec": "h264",
                        "crf": crf,
                        "width": width,
                        "analysis_fps": 20 / step,
                        "phase_source_frames": phase,
                        "passes": len(cases),
                        "detected_passes": len(hits),
                        "misses": len(cases) - len(hits),
                        "false_candidates": false_candidates,
                        "sampled_target_frames": observations,
                        "wall_seconds": time.perf_counter() - started,
                        "cpu_seconds": time.process_time() - cpu,
                        "cases": rows,
                        "parameter_hash": detector.parameter_hash,
                        "detector_settings": detector.config.model_dump(),
                    }
                )
    report = {
        "schema_version": "1.0",
        "purpose": "software-only sensitivity benchmark; no field accuracy claim",
        "source": {
            "width": 1280,
            "height": 720,
            "fps": 20,
            "frames": len(cases) * 48,
            "seed": 1000,
        },
        "results": results,
        "peak_process_rss_platform_units": resource.getrusage(
            resource.RUSAGE_SELF
        ).ru_maxrss,
    }
    atomic_write(destination / "results.json", canonical(report))
    print(
        json.dumps(
            [{k: v for k, v in result.items() if k != "cases"} for result in results],
            indent=2,
        )
    )
    return report


if __name__ == "__main__":
    run()
