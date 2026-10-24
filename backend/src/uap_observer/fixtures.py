"""Reproducible encoded software fixtures, not observational evidence."""

import hashlib
import json
import subprocess
from pathlib import Path

from .sources.synthetic import COUNT, FPS, HEIGHT, SEED, WIDTH, ground_truth, render


def generate(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    video = destination / "sky.mkv"
    process = subprocess.Popen(
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
            f"{WIDTH}x{HEIGHT}",
            "-framerate",
            str(FPS),
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-threads",
            "1",
            "-fflags",
            "+bitexact",
            "-flags:v",
            "+bitexact",
            "-map_metadata",
            "-1",
            str(video),
        ],
        stdin=subprocess.PIPE,
    )
    try:
        assert process.stdin is not None
        for index in range(COUNT):
            process.stdin.write(render(index).tobytes())
        process.stdin.close()
        if process.wait(timeout=30):
            raise RuntimeError("fixture encoding failed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    truth = {
        "schema_version": "1.0",
        "generator_version": "wp0-1",
        "seed": SEED,
        "description": "Generated test data; not real-world detector validation",
        "codec": "ffv1",
        "fps": FPS,
        "width": WIDTH,
        "height": HEIGHT,
        "video_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "frames": [ground_truth(i) for i in range(COUNT)],
    }
    (destination / "sky.truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    return video
