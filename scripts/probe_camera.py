"""Camera-free WP1 diagnostics. Only loopback RTSP or local files accepted."""

import argparse
import getpass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from uap_observer.capture.recorder import Recorder
from uap_observer.capture.tools import dependency_versions, validate_input
from uap_observer.storage.ledger import Ledger

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    source = (
        str(args.file) if args.file else getpass.getpass("Loopback RTSP URL (hidden): ")
    )
    source = validate_input(source)
    print(json.dumps(dependency_versions(), indent=2))
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = Ledger(root / "ledger.sqlite")
        recorder = Recorder(ledger, root / "archive", source)
        recorder.start()
        recorder.done.wait(5)
        recorder.stop()
        rows = ledger.query("SELECT state,stream_json,reason FROM sessions")
        print(json.dumps(rows, indent=2))
