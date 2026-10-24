"""Non-secret capture/processing build provenance."""

import hashlib
import platform
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def build_info() -> dict[str, Any]:
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
    try:
        software = version("uap-observer")
    except PackageNotFoundError:
        software = "unpackaged"
    dependencies = {}
    for name in ("numpy", "opencv-python-headless", "scipy", "av", "pydantic", "fastapi"):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = "not_installed"
    return {
        "dependencies": dependencies,
        "software_version": software,
        "python_version": platform.python_version(),
        "system": platform.system(),
        "architecture": platform.machine(),
        "backend_source_sha256": digest.hexdigest(),
    }
