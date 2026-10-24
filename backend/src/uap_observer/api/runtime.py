import hashlib
import json
import os
import shutil
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from ..capture.analysis import ArchiveAnalysis
from ..capture.recorder import Recorder
from ..capture.tools import utc_now
from ..config import load_station
from ..detection.detector import DetectorConfig
from ..events.processor import OfflineProcessor
from ..evidence.files import canonical
from ..evidence.service import EvidenceService
from ..storage.lease import DataLease
from ..storage.ledger import Ledger


class Runtime:
    def __init__(self, root: Path, data_directory: Path | None = None) -> None:
        self.root = root
        self.station, self.cameras = load_station(root / "config/station.example.yaml")
        self.camera = next(c for c in self.cameras if c.enabled)
        self.data = (
            data_directory or Path(os.environ.get("UAP_DATA_DIR") or str(root / "artifacts/state"))
        ).resolve()
        self.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lease = DataLease(self.data)
        self.ledger = Ledger(self.data / "ledger.sqlite")
        self.evidence = EvidenceService(self.ledger, self.data / "evidence")
        self.evidence.recover(self.data / "archive")
        self.ledger.execute(
            "UPDATE jobs SET state='interrupted',error='application_restarted' WHERE state='running'"
        )
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="offline-job")
        self.future: Future[None] | None = None
        self.recorder: Recorder | None = None
        self.lock = threading.Lock()

    def settings(self) -> dict[str, Any]:
        rows = self.ledger.query(
            "SELECT revision,data_json FROM settings ORDER BY revision DESC LIMIT 1"
        )
        if not rows:
            return {"revision": 0, "detector": DetectorConfig().model_dump(), "rectangles": []}
        return {"revision": rows[0]["revision"], **json.loads(rows[0]["data_json"])}

    def save_settings(
        self, detector: DetectorConfig, rectangles: list[list[int]]
    ) -> dict[str, Any]:
        if len(rectangles) > 50:
            raise ValueError("too many exclusion rectangles")
        for rect in rectangles:
            if len(rect) != 4:
                raise ValueError("rectangle must contain x,y,width,height")
            x, y, w, h = rect
            if min(x, y) < 0 or min(w, h) <= 0 or x + w > 320 or y + h > 180:
                raise ValueError("exclusion rectangle exceeds 320x180 fixture")
        if detector.max_area < detector.min_area:
            raise ValueError("maximum area is below minimum")
        mask_version = hashlib.sha256(canonical(rectangles)).hexdigest()
        data = {
            "detector": detector.model_copy(update={"mask_version": mask_version}).model_dump(),
            "rectangles": rectangles,
        }
        self.ledger.execute(
            "INSERT INTO settings(created_utc,data_json) VALUES (?,?)",
            (utc_now(), json.dumps(data)),
        )
        return self.settings()

    def start_fixture(self) -> str:
        with self.lock:
            if self.future and not self.future.done():
                raise ValueError("offline processing already running")
            if shutil.disk_usage(self.data).free < self.station.safety_reserve_bytes:
                raise ValueError("storage safety reserve is low; capture is paused")
            fixture = self.root / "artifacts/fixtures/sky.mkv"
            if not fixture.is_file():
                raise ValueError("run make fixtures first")
            job_id = uuid4().hex
            self.ledger.execute(
                "INSERT INTO jobs(id,created_utc,state) VALUES (?,?,?)",
                (job_id, utc_now(), "running"),
            )
            settings = self.settings()
            self.future = self.executor.submit(self._process, job_id, fixture, settings)
            return job_id

    def _process(self, job_id: str, fixture: Path, settings: dict[str, Any]) -> None:
        recorder: Recorder | None = None
        try:
            recorder = Recorder(
                self.ledger,
                self.data / "archive",
                str(fixture),
                camera_id=self.camera.id,
                segment_seconds=self.camera.recording.nominal_segment_seconds,
            )
            self.recorder = recorder
            recorder.start()
            deadline = time.monotonic() + 5
            while not self.ledger.query(
                "SELECT id FROM sessions WHERE id=?", (recorder.session_id,)
            ):
                if time.monotonic() > deadline:
                    raise RuntimeError("capture startup timeout")
                time.sleep(0.01)
            self.ledger.execute(
                "UPDATE jobs SET session_id=? WHERE id=?", (recorder.session_id, job_id)
            )
            analysis = ArchiveAnalysis(self.ledger)
            analysis.start()
            mask = np.zeros((180, 320), dtype=np.uint8)
            for x, y, w, h in settings["rectangles"]:
                mask[y : y + h, x : x + w] = 255
            config = DetectorConfig.model_validate(settings["detector"])
            processor = OfflineProcessor(self.ledger, self.evidence, config, mask)
            try:
                metrics = processor.process(
                    recorder.session_id,
                    pre=self.camera.recording.preserve_pre_seconds,
                    post=self.camera.recording.preserve_post_seconds,
                    until=recorder.done,
                )
                recorder.wait()
                if analysis.errors:
                    raise ValueError("source frame indexing failed")
            finally:
                analysis.stop()
            metrics["retention"] = self.evidence.cleanup(
                self.station.rolling_quota_bytes, self.station.safety_reserve_bytes
            )
            self.ledger.execute(
                "UPDATE jobs SET state='complete',result_json=? WHERE id=?",
                (json.dumps(metrics), job_id),
            )
        except Exception as exc:
            self.ledger.execute(
                "UPDATE jobs SET state='failed',error=? WHERE id=?", (type(exc).__name__, job_id)
            )
        finally:
            if recorder:
                recorder.stop()
            self.recorder = None

    def close(self) -> None:
        if self.recorder:
            self.recorder.stop()
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.lease.close()
