# WP0 implementation and verification report

Date: 2026-10-24. Workspace: `/workspace/Parcae_UAP`. Branch: `work`.
Base commit: `abae96f` (`Initial commit`). Existing LICENSE preserved. All WP0
changes are local and uncommitted. No push/deploy/camera access/network setting
change/long soak occurred. Original checkout was clean and contained LICENSE
only; no existing AGENTS.md or local skills were present.

## Result

Runnable offline Python/FastAPI and TypeScript/React/Vite foundation, with
synthetic and video-file source adapters and explicit unsupported RTSP contract.
Typed versioned source/frame/detection/track/event/capture/export/health schemas,
validated YAML examples, deterministic encoded fixture + ground truth, browser
preview with timestamps, source selection, play/pause/restart/seek, tests and CI.
No detection, tracking, recording, persistence or evidence export is claimed.

The WP0 documentation gap is resolved. The original Library ZIP could not be
fetched after two transfer failures (not approval denials), and its ZIP hash
remains unverified. The parent subsequently supplied the complete plan in eight
numbered text chunks, plus the opening prompt/changelog and original document
byte counts/SHA256 values. Concatenation without added separators reproduced
all three original UTF-8 document hashes exactly. The complete plan is preserved
byte-for-byte at both `docs/implementation_plan.md` and its handoff copy.

Original input Library ID: `libfile_e2af887b515081919d9f4bf27df49ca6`.
Expected original ZIP SHA256 (provided, not independently verified):
`44b2b028a57c4ebfc31a755eaae6a781a1f3c139c5be14490ae65a694a4638d6`.

| Verified document | Bytes | SHA256 |
|---|---:|---|
| Implementation plan (both copies) | 72687 | `5c5f4435bf7f17e3306269c0c8493c6b6369ab734df9196d1e95a5e36e3dd6fd` |
| Opening prompt | 3738 | `31dc5e25bf026d46929c68c2e6ac7647e6a5244d41287714cdff8a2436259dec` |
| Changelog | 2554 | `0f094081e62ae299c7b3c58027a6f0424a112104562dab25ed36931e376195e4` |

Documentation-only follow-up: verified all 21 plan sections and nine work-package
headings, matching plan copies, ZIP CRCs and exact packaged document hashes.
Compared the replacement package with the prior package: application source,
tests, dependencies' lockfiles, configs and generated fixture bytes are unchanged.
Functional tests were not rerun unnecessarily; the results below remain the
previous verified WP0 results. No WP1 feature work occurred.
Source ZIP Library identity retained: `libfile_9368d703e1188191bb30116aee179e14`.

## Actual environment

- Linux x86_64, Debian 13 userspace, kernel 6.18.55; cloud container, not owner's Mac.
- Approximately 17 GiB RAM and 30 GiB free workspace storage at initial inspection.
- Isolated CPython 3.15.1, uv 0.13.5; Node 24.19.0, npm 11.9.0.
- FFmpeg/ffprobe 7.1.5; Chromium 154.0.8037.57.
- NumPy 2.5.4, opencv-python-headless 5.0.0.93, FastAPI 0.162.0,
  Pydantic 2.14.1, pytest 9.1.1. Full dependency versions in lockfiles.
- Python 3.15.1 is confirmed by the official release page:
  https://www.python.org/downloads/release/python-3151/ (released Oct 21, 2026).
- Preinstalled uv 0.12.23 could not find Python 3.15.1. Installing uv 0.13.5
  into `/workspace/uap-tooling` solved that. Python installed under
  `/workspace/python`; no system Python replacement. Creating a convenience link
  in read-only home failed, but the managed interpreter and project venv work.
- NumPy/OpenCV wheels installed without compiling those packages. PyYAML built
  successfully in about six seconds total dependency preparation. Imports pass.
- Parent reported owner's Apple Silicon Mac has Homebrew Python 3.14.7; not
  independently inspected here. No macOS compatibility or precise model claim.

## Commands and final results

Cloud sandbox-specific prefixes (portable machines can use normal `uv`):

```sh
export UV_CACHE_DIR=/tmp/uap-uv-cache
export UV_PYTHON_INSTALL_DIR=/workspace/python
export npm_config_cache=/tmp/uap-npm-cache
export UV=/workspace/uap-tooling/bin/uv
make setup
make test
CHROMIUM_PATH=/usr/bin/chromium make browser-test
```

- `make setup`: passed, `uv sync --locked`, `npm ci` (109 packages).
  Initial npm invocation failed because its default cache was read-only; writable
  cache fixed it without changing dependency hashes.
- `UV_OFFLINE=1 npm_config_offline=true make setup`: passed using populated caches.
- `UV_OFFLINE=1 npm_config_offline=true make test`: passed after final code formatting.
- pytest: **17 passed**, ~3 seconds. Tests cover source lifecycle/session identity,
  stop behavior, config rejection, manifest paths/schema separation, encoded
  pixel-exact decoding, 120 matched file timestamps, generated truth/hash,
  byte-identical repeated encoding, invalid files, API bounds/JPEGs/metadata.
- Ruff: passed. Strict mypy: passed, **10 source files**.
- Vitest: **2 passed**. TypeScript `tsc --noEmit`: passed.
- Vite production build: passed (29 modules; main JS ~226 kB before gzip).
- Playwright/Chromium: **1 passed** in 7.5 seconds. It runs `make demo`, verifies
  heading, moving timestamp, play/pause, switching to encoded-file source,
  seek to frame 60 = 3.000 seconds, decoded JPEG width = 320, restart and no page
  errors. Server was stopped by the browser harness afterward.
- `make demo` generated 2.9 MiB `sky.mkv` and 56 KiB ground-truth JSON, built UI,
  and served the app on 127.0.0.1:8000 during that test. No public deployment.
- `git diff --check`: passed for tracked changes; new files also linted/typechecked.
- CI workflow written, but no remote CI run claimed.

One non-failing upstream warning remains: Starlette's test client deprecates
httpx in favor of httpx2. Current locked httpx tests pass. No suppression applied.

## Changed files

New root files: `.gitignore`, `.python-version`, `.env.example`, `AGENTS.md`,
`Makefile`, `README.md`; `.github/workflows/ci.yml`.

Backend: `backend/{pyproject.toml,uv.lock,.python-version}`;
`backend/src/uap_observer/{__init__,models,config,fixtures,main}.py`;
`sources/{__init__,base,synthetic,video_file,rtsp}.py`.

Frontend: `frontend/{package.json,package-lock.json,tsconfig.json,vite.config.ts,
playwright.config.ts,index.html}`; `frontend/src/{api.ts,api.test.ts,main.tsx,style.css}`.

Config: `config/station.example.yaml`, `config/cameras/{synthetic,g100}.example.yaml`.
Scripts: `scripts/create_test_videos.py`.
Tests: `tests/conftest.py`, `tests/unit/{test_sources,test_contracts}.py`,
`tests/integration/{test_video,test_api}.py`, `tests/e2e/preview.spec.ts`.
Docs: `docs/{implementation_plan,architecture,data_and_evidence,known_limitations,
wp0_report}.md`, and three files under `docs/handoff`.
Generated artifacts (ignored by git): `artifacts/fixtures/{sky.mkv,sky.truth.json}`.
Dependencies, caches, .git, private data and environment secrets are excluded
from the portable source ZIP. Generated synthetic fixture is included.

## Review gate

Stop here for review. WP1 requires separate authorization: single-input capture,
minimal SQLite migrations, exact source PTS/frame-to-segment mapping, continuously
drained metadata/frame transport and stalled-analysis recording tests. This WP0
preview's file timestamp mapping is tested only on its generated FFV1 fixture;
it is not a substitute for that future work. No real-world detection accuracy,
original evidence integrity, camera reliability or macOS runtime validated.
