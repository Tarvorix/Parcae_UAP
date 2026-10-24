"""One application owner per data directory; prevents false restart recovery."""

import fcntl
import os
from pathlib import Path


class DataLease:
    def __init__(self, directory: Path) -> None:
        descriptor = os.open(directory / ".owner.lock", os.O_CREAT | os.O_RDWR, 0o600)
        self.handle = os.fdopen(descriptor, "a+b")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            raise RuntimeError("Data directory is already in use by another application") from exc

    def close(self) -> None:
        if not self.handle.closed:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
