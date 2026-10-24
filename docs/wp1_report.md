# WP1 capture and mapping verification

WP1 uses a single PyAV input (FFmpeg libraries) to remux encoded video packets
into independently decodable Matroska source segments. No audio is selected.
A separate archive decoder reads completed segments, joins decoded PTS to original
packet PTS/DTS/time bases and UTC/monotonic receipt records, stores display-order
frame references and hashes, and feeds a bounded queue. Duplicate PTS mappings
are ambiguous, not guessed. Container timestamp resets retain original timing.
Recorder progress does not depend on an analysis consumer or decoded-pixel pipe.
This choice adds approximately one segment plus decode backlog to preview latency;
it is a deliberate alternative to simultaneous remux/raw-pipe outputs. Segment
boundaries follow source keyframes. No transcoding or second source reader occurs.

SQLite migration 1 provides cameras, sessions, segments, packets, frames, health
and gaps with foreign keys, WAL and transactions. Reconnect attempts use new
session IDs and bounded exponential backoff. `scripts/probe_camera.py` checks
FFmpeg/ffprobe and captures a local file or hidden-input loopback RTSP diagnostic;
remote physical-camera access remains disabled. Capture health failures are
sanitized by class name, rather than retaining library exception URLs.

Verified on CPython3.15.1/Linux x86_64; PyAV19.0.2 wheel and executable FFmpeg7.1.5.
`make test` with checkpoint's environment prefixes: 26 pytest tests pass (9 new
capture tests including parametrization), Ruff/strict mypy, 2 Vitest tests and
TypeScript/Vite production build pass. `CHROMIUM_PATH=/usr/bin/chromium make
browser-test`: 1 passed. No camera or external server involved.

Capture checks cover pixel/PTS identity through multiple segments, timestamp
resets, VFR and B-frame display ordering, duplicate-PTS ambiguity, missing/corrupt
source refusal, failed reconnect session/gap state, full analysis queue dropping
old frames, and a deliberately blocked decoder while all 120 source packets
continue recording. Loopback RTSP uses test-only repository RTSP/RTP-over-TCP
fixture code and FFmpeg-generated H264; exactly one source connection, archived
frames mapped, intentional disconnect recorded. No live G100 validation claimed.

MediaMTX metadata GET to `https://api.github.com/repos/bluenviron/mediamtx/releases/latest`
was blocked by proxy CONNECT 403. No download, retry or bypass occurred. That
external action would require environment network access approval/allowlisting
for api.github.com:443, if desired; it is unnecessary for the local test server.
No user decision is needed for continued offline development.

Remaining capture limits: missing PTS is explicitly unavailable; arbitrary
malformed streams and all codecs are not validated. Recording can still fail on
host disk/I/O failure; failures are reported as gaps. A crash between filesystem
seal and database commit needs WP2 reconciliation. Segment retention and event
locking are WP2. Source time is not exposure UTC, and source UTC clock accuracy is
unknown. Linux tested; macOS remains untested. No long soak has run yet.


Portable ZIP recheck: a fresh extraction and offline locked setup revealed one
import-order lint defect in the added RTSP adapter test. The isolated copy was
corrected without changing application behavior, then `make setup test` passed
26 pytest tests, Ruff, mypy17 modules, 2 Vitest and the production build. The WP1
Library ZIP was corrected as a new version under the same identity. This did not
change the running WP5 core or its code manifest.
