"""Hidden local entry for one authorized ten-minute LAN commissioning run."""

import getpass
import json
import time
from pathlib import Path

from uap_observer.api.runtime import Runtime


def main() -> int:
    runtime = Runtime(Path(__file__).resolve().parents[1])
    try:
        source = getpass.getpass(
            "Actual private LAN RTSP URL including login (hidden): "
        )
        job_id = runtime.start_live(source)
        source = ""
        print("Ten-minute commissioning started. Ctrl-C stops camera access early.")
        print("Evidence stays in the local application data directory.")
        live = runtime.live
        assert live is not None
        try:
            while not live.done.wait(1):
                if int(time.monotonic()) % 30 == 0:
                    status = live.status()
                    print(f"{status['state']}: {status['packets']} packets")
        except KeyboardInterrupt:
            live.stop()
            print("Stopping capture and finishing evidence indexing…")
        runtime.future.result()  # type: ignore[union-attr]
        print(
            json.dumps({"job_id": job_id, "state": live.state, "reason": live.reason})
        )
        return 0 if live.state == "complete" else 1
    except Exception as exc:
        # Never emit connection strings or library exception text.
        print(f"Commissioning could not finish: {type(exc).__name__}")
        return 1
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
