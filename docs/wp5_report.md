# WP5 offline review UI and hardening — verified offline acceptance

The browser now starts generated-footage analysis, lists/searches/paginates
persisted events, plays a per-segment derived MP4 while offering unmodified source,
shows observed trajectory overlays and integrity/timing limitations, appends human
reviews and exports independently verified evidence. Detector settings and native
pixel rectangle exclusions persist by revision and affect the next analysis.
Local health/event-count WebSocket updates reconnect after transport loss.

FastAPI now has source/job/event/review/export/settings endpoints, local static
API documentation/OpenAPI schema and explicit integrity errors. There is no
arbitrary path-serving/upload endpoint. Cross-origin changes and sockets are
rejected. Application binding stays 127.0.0.1. A data owner lease prevents false
restart recovery; capture/database failure signals completion rather than hanging.
Concurrent sealing/export is serialized. Rolling quota/reserve values are validated
in station YAML; protected evidence is never sacrificed to satisfy quota.

Verified pre-soak on Linux x86_64, CPython3.15.1, PyAV19.0.2, FFmpeg executable7.1.5:
- `make setup`: locked Python/npm setup; repeated offline against populated caches.
- `make test`: 56 backend tests, Ruff, strict mypy33 source modules, 2 Vitest and
  TypeScript/Vite build passed. Final metadata/docs changes additionally passed
  21 capture/contract tests, 4 API integration tests and strict typing.
- Fresh extracted reviewed source with `UV_OFFLINE=1 npm_config_offline=true make setup test`
  passed54 pytest in45.64s,Ruff,mypy33,2 Vitest and frontend build.
- `make browser-test`: 3 passed, covering timestamped preview, actual analysis/
  source playback/review/reload/export, connected socket and forced-close reconnect.
- Six-second final harness smoke: 60 frames, 1 event, 6.1047s wall, verified export,
  searchable event API and unchanged code hash. This is not a long soak.
- Separate 120-second paced concurrent smoke: 1200 frames, 2 sealed complete events,
  verified source export, 120.8451s wall. This is not a long soak.

Default-setting benchmark: `docs/detection_benchmark_default.json`. Settings are
contrast18/change10/min_area2, distinct from the original WP3 sensitivity settings.

| CRF | Width | FPS | Detected passes | Misses | False candidates |
|---|---:|---:|---:|---:|---:|
|18|1280|20|6/18|12|0|
|18|1280|5|18/72|54|0|
|18|960|5|18/72|54|0|
|32|1280|20|6/18|12|9|
|32|1280|5|18/72|54|39|
|32|960|5|18/72|54|7|

One-pixel and low-contrast cases remain in the denominator. These results measure
candidate-level signals, not promoted event recall or real-world accuracy. Native
cadence remains the default. No inference of physical range/speed or extraordinary
origin is made; absent calibration/enrichment is explicit.

## Eight-hour gate: corrected attempt3 passed

Attempt1 (artifacts/soak-core-8h) was intentionally interrupted for missing export
settings/provenance after1002.5416 wall seconds. It is not an eight-hour pass.

Attempt2 (artifacts/soak-core-8h-v2) ran from2026-10-24T03:23:27.804974Z to
2026-10-24T06:27:53.460856Z. It was intentionally interrupted after a separate
scaled retention test demonstrated6.612s of blocked packet draining. Its actual
terminal report is preserved;10800 media seconds,108000 frames,180 events.
It is **not** an eight-hour pass. No earlier attempt is counted toward the final gate.

The missing relationship lookup index was repaired by migration5. On the same
isolated metadata, cleanup fell from7.8141s to0.0313s;all120 source packets were
preserved. See [critical_review.md](critical_review.md) and
[retention_review_results.json](retention_review_results.json). New scaled-archive
and migration regressions passed. Full corrected suite passed 56 pytest tests in 49.64s, Ruff, mypy on 33 modules, 2 Vitest tests and build;
browser 3 tests passed in 15.1s. Final-code smoke passed60frames,1event,6.1397wall,
verified export,code unchanged.

Attempt3 **passed**,from **2026-10-24T06:31:45.348241Z** to
**2026-10-24T14:36:05.186791Z**,exit0. Actual wall time **29059.8386s**
(8h4m19.84s),media time **28800s**. It completed240 epochs,288000 frames,
480 sealed complete events,240 independently verified exports/reviews and240
searchable event API checks. Errors were empty;the frozen code hash and complete
core file inventory were unchanged.

Measured peak RSS:249148KiB (243.31MiB),growth after epoch1:
12116KiB (11.83MiB),below the declared128MiB allowance.
Python/PyAV process CPU:5845.8556s,about20.12% of one CPU averaged
across wall time. This is not a camera-CPU or whole-host utilization measurement.
Final data directory:1337261164bytes (about1.25GiB).
The deliberately small8MiB rolling quota was exceeded by protected footage;
protected source remained intact and free-space reserve stayed healthy.

Post-run acceptance verified SQLite integrity/foreign keys,all288000 exact
frame/packet mappings,zero source gaps,zero unsealed events,all25440 protected
rolling segments,47280 capture artifacts and240 export hashes. Every export had
already undergone independent decode/frame/PTS verification during its epoch.
Full results:[final_acceptance.json](validation/final_acceptance.json),
[terminal status](validation/soak_final_status.json),
[code manifest](validation/soak_final_code_manifest.json) and
[epoch history](validation/soak_final_history.jsonl).
The code-manifest SHA256 is
`7ed43046f4a42ac6228aac50ca62fae57a6c9d9a08c429b95c369fba486a223e`.
Original runtime evidence remains in `artifacts/soak-core-8h-v3`;it is excluded
from the portable source ZIP along with dependencies,caches and secrets.

The full workload requires >=28800 media seconds and >=28800 actual wall seconds.
It uses native320×180/10fps generated H264, paced recording, concurrent independent
archive processing, detector events, context locks, exports/frame-hash verification,
append-only review, searchable event API, protected retention and a declared128MiB
peak-growth allowance after epoch1. Each120s file loop is a separate source epoch;
inter-epoch processing is not represented as camera coverage. Delivery queue drops
are explicitly distinct from exhaustive detector-frame coverage. Core files are
hashed and remain unchanged during the final run; docs may be updated.

Source publication/merge requires passing final-commit CI in GitHub in addition
to these completed local acceptance gates. The PR/commit checks carry that result.
Production deployment and real-camera commissioning remain outside scope.
