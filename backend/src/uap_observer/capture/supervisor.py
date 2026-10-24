import threading
from pathlib import Path
from typing import Any

from ..storage.ledger import Ledger
from .recorder import Recorder


class CaptureSupervisor:
    """Bounded reconnects; every new connection receives a separate session ID."""

    def __init__(
        self,
        ledger: Ledger,
        root: Path,
        source: str,
        retries: int = 3,
        backoff_seconds: float = 1,
        **settings: Any,
    ) -> None:
        if retries < 0 or backoff_seconds <= 0:
            raise ValueError("invalid retry policy")
        self.ledger, self.root, self.source = ledger, root, source
        self.retries, self.backoff_seconds, self.settings = retries, backoff_seconds, settings
        self.stop_requested = threading.Event()
        self.sessions: list[str] = []
        self.current: Recorder | None = None
        self.state = "stopped"

    def run(self) -> None:
        for attempt in range(self.retries + 1):
            if self.stop_requested.is_set():
                break
            self.current = Recorder(self.ledger, self.root, self.source, **self.settings)
            self.sessions.append(self.current.session_id)
            self.state = "running"
            self.current.start()
            while not self.current.done.wait(0.1):
                if self.stop_requested.is_set():
                    self.current.stop()
            is_live = self.source.startswith(("rtsp://", "rtsps://"))
            if not self.current.error and not is_live:
                self.state = "ended"
                return
            self.state = "reconnecting"
            if attempt < self.retries and self.stop_requested.wait(
                min(30, self.backoff_seconds * 2**attempt)
            ):
                break
        self.state = "stopped" if self.stop_requested.is_set() else "failed"

    def stop(self) -> None:
        self.stop_requested.set()
        if self.current:
            self.current.stop()
