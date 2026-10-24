import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def redact(text: str) -> str:
    # Never retain any RTSP URL components, even a URL without userinfo.
    return re.sub(r"rtsps?://[^\s'\"]+", "[redacted-stream]", text, flags=re.IGNORECASE)


def dependency_versions() -> dict[str, str]:
    result = {}
    for name in ("ffmpeg", "ffprobe"):
        exe = shutil.which(name)
        if exe is None:
            raise RuntimeError(f"{name} is required; install it on PATH")
        result[name] = (
            subprocess.check_output([exe, "-version"], timeout=5).decode().splitlines()[0]
        )
    return result


def validate_input(source: str, allow_remote: bool = False) -> str:
    if source.startswith(("rtsp://", "rtsps://")):
        host = urlsplit(source).hostname
        if not allow_remote and host not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Only loopback RTSP is enabled; real-camera commissioning is gated")
        return source
    if "://" in source:
        raise ValueError("Only local files or explicitly enabled RTSP are supported")
    path = Path(source).resolve(strict=True)
    if not path.is_file():
        raise ValueError("Input must be a regular file")
    return str(path)
