# UAP Observer — offline beta

Local stationary-camera sky observation software using generated footage first.
The offline workflow now records source segments, maps frames/PTS, detects and
tracks image-plane candidates, preserves event context, and supports browser
review and verified evidence export. The existing G100 remains untouched.

**The core offline acceptance gates passed on generated footage**, including an
actual eight-hour media replay over8h4m19.84s wall time:288000 frames,480 events,
zero errors and unchanged core code. See the [validation report](docs/wp5_report.md).
Camera commissioning (WP6) and optional calibration/enrichment/multiple cameras
remain separately gated. Synthetic tests establish software behavior,not field accuracy.

## Run locally

Prerequisites: stable CPython **3.15.1**, uv **0.13.5+**, Node **24+**, npm and
FFmpeg/ffprobe on PATH. `uv python install 3.15.1` can install an isolated managed
interpreter. Linux x86_64 is tested; macOS/Apple Silicon remains untested. No paid
API, subscription, developer key, new hardware or owner-supplied footage is needed.

```sh
make setup
make test
make demo
```

Open **http://127.0.0.1:8000** on the running host. `make demo` generates the fixture,
builds the UI and starts the local-only FastAPI factory; Ctrl-C stops it. This is
not a public cloud deployment. Dependencies install once; after installation the
app, UI, API schema and tests use local resources. `/docs` is a local static guide
and `/openapi.json` is the generated schema; neither needs external JavaScript.

In the browser: play/seek the preview, select **Analyze generated footage**, choose
an event, inspect its source segment and observed trajectory, append a review,
then **Export and verify evidence**. The six-second fixture cannot supply 30s
before and 45s after; its events are explicitly partial. A separate generated
120-second fixture verifies full context. The MP4 player is a derivative; the
unmodified archived Matroska segment has a separate download. No overlay is burned
into canonical evidence. Detection masks affect analysis, not original pixels.

For browser tests:

```sh
cd frontend && npx playwright install chromium && cd ..
make browser-test
# Or: CHROMIUM_PATH=/usr/bin/chromium make browser-test
```

The browser harness starts/stops its own demo on port8000. Stop any other demo
using that port first. It tests preview, analysis/review/export and WebSocket
reconnection. The GitHub Actions workflow repeats unit,integration,type/build and
browser checks on pushes and pull requests. Deployment is separate from source publication.

Read-only-home sandbox setup uses writable caches:

```sh
export UV_CACHE_DIR=/tmp/uap-uv-cache
export npm_config_cache=/tmp/uap-npm-cache
# This cloud workspace also uses:
export UV_PYTHON_INSTALL_DIR=/workspace/python
export UV=/workspace/uap-tooling/bin/uv
```

`make setup` uses `uv sync --locked` and `npm ci`. Both were repeated with
`UV_OFFLINE=1 npm_config_offline=true` against populated caches. Versions are in
`backend/uv.lock` and `frontend/package-lock.json`.

## Data and diagnostics

Application data lives in `artifacts/state`, ignored by git. `UAP_DATA_DIR` selects
a different local workspace. One application process owns each data directory;
stop it cleanly before opening that directory elsewhere. Startup recovery is not
safe against a simultaneously running recorder; the owner lease prevents this.
Never commit raw private footage, location coordinates or credentials.

Station/camera YAML examples validate at boot. The offline pipeline uses the
fixture camera's segment/pre/post settings; analysis stays at native fixture
cadence/resolution. The disabled G100 example is not a live integration. Settings
and masks edited in the UI apply to the next analysis and are revisioned. Rolling
source quota and disk reserve are configured in `config/station.example.yaml`.
Protected events/exports are never automatically deleted to satisfy quota.

```sh
# Regenerate the explicitly public fixture and ground truth:
make fixtures
# Diagnostic accepts a local file, or a hidden loopback-only RTSP URL:
uv run --project backend python scripts/probe_camera.py --file artifacts/fixtures/sky.mkv
# Verify a self-contained evidence export:
uv run --project backend python scripts/verify_evidence.py /path/to/event.zip
# Reproduce the encoded sensitivity benchmark:
uv run --project backend python scripts/benchmark_detection.py
```

The full eight-hour harness uses actual paced replay, independent source recording
and analysis, event locking, exports/hash/frame verification, append-only reviews,
searchable event API checks, retention and memory monitoring:

```sh
uv run --project backend python scripts/soak_offline.py \
  --seconds 28800 --output artifacts/soak-core-8h-v3
```

Choose a fresh output directory for each run. Status, heartbeat, history and code
hashes persist there. It requires at least eight media hours **and** eight wall
hours; processing can extend its duration. Short smoke runs are not long soaks.
Each finite file loop is a separate epoch; inter-epoch processing time is not
claimed as camera coverage. After the soak stops, its event history can be opened
with `UAP_DATA_DIR=/absolute/path/to/soak-output make demo`.

See [current WP5 report](docs/wp5_report.md), [operations](docs/operations.md),
[known limits](docs/known_limitations.md), [default-setting benchmark](docs/detection_benchmark_default.json),
and milestone reports [WP0](docs/wp0_report.md), [WP1](docs/wp1_report.md),
[WP2](docs/wp2_report.md), [WP3](docs/wp3_report.md), [WP4](docs/wp4_report.md).
The complete v1.1 handoff is preserved with matching original document hashes;
[provenance](docs/wp0_report.md) distinguishes that from the unfetched original ZIP.
