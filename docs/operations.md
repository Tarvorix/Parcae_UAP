# Local operation and recovery

Start with `make setup`, `make test`, `make demo`. View the app on the same host at
127.0.0.1:8000. Do not expose it publicly. No camera is required or contacted.
The original Apple Home pairing is never altered. Config examples contain secret
names only; actual camera commissioning remains gated.

Ctrl-C closes the job worker and releases the data lease. On an actual process
crash, the OS releases its advisory lease. Next startup reconciles closed orphan
segments with packet ledgers, marks interrupted sessions as gaps and pending
captures as incomplete where required. Unfinished .partial files are not advertised
as sealed source. Never delete .owner.lock to override a running owner; stop that
process first. Inspect the recorded errors and preserve the data directory before
attempting manual repair. Do not run two owners on one data directory.

The rolling quota can only reclaim unprotected source. Protected event/source and
export bytes can exceed the quota, producing a warning; they are not automatically
deleted. Inspect application size and free reserve. A low reserve prevents a new
fixture job. No purchase is automatically needed or proposed.

Evidence exports are new immutable-by-application ZIP snapshots. Reviews append;
reanalysis produces a new interpretation revision. Neither changes the sealed
capture manifest/source bytes. Run `scripts/verify_evidence.py` against a copied
ZIP to independently check hashes, frame pixels/PTS and source links. A failure
means investigate missing/corrupted evidence; no annotated clip is substituted.

The final soak writes `artifacts/soak-core-8h-v3/{status,heartbeat}.json` and
`history.jsonl`. `status: running` is not success. A completed pass requires both
wall_seconds and media_seconds >=28800 plus `code_unchanged: true`. The code
manifest lists exactly the files monitored. Heartbeats include a PID and source
packet count. If a heartbeat becomes stale, check that PID before deciding whether
an interrupted result needs investigation. Do not restart over the same output
folder or claim elapsed wall time as processed media. To stop a run, interrupt its
process and retain the interrupted report and existing evidence.

After the owner process ends, view persisted soak events through the normal app
using `UAP_DATA_DIR=/absolute/path/to/soak-output make demo`. During a run the soak
checks the event API in-process; a second UI owner is rejected by the lease.

Dependencies are locked and support local offline use once installed. FFmpeg/
ffprobe must remain on PATH. Chromium is needed only for browser verification.
If a read-only home breaks tool caches, use the writable paths in README. The
upstream Starlette/httpx deprecation warning is known and does not fail checks.
The source repository runs the same checks in GitHub Actions. Physical-camera
hardware validation is separate and remains unperformed.
