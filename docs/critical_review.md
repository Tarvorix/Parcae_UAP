# Core offline code review — 2026-10-24

A separate pass inspected the implementation of source/PTS/segment mapping,
recording and queue boundaries, SQLite transactions and retention, interrupted
capture recovery, manifests and export verification, source-serving paths and
HTTP origin/host handling. This was a single-agent review of actual code; it is
not an independent external audit. Review preceded the fresh final soak.

Findings corrected before that run:

- Exported analysis originally carried parameter hashes but omitted the full
  detector settings and mask. Captures/exports now validate against their actual
  versioned Pydantic schemas. Exports carry reconstructible detector/mask settings,
  tracker/event parameters, source configuration and hashes, and software/build/
  dependency provenance. A regression reconstructs settings after JSON round-trip.
- A crash after source rename but before SQLite commit could adopt bytes without
  a pre-crash checksum. The fsynced sidecar now records size and SHA256 before
  rename. Recovery quarantines mismatched or unverifiable orphans and records a
  source gap. It never silently rehashes corrupt bytes as valid evidence.
- A sealed capture directory left before database commit could be rebuilt using
  later restart gaps. Recovery now verifies/adopts that exact sealed snapshot;
  the capture hash and bytes stay unchanged.
- Cleanup previously unlinked files while holding SQLite's writer transaction.
  It now claims unprotected segments in a short transaction, releases it before
  filesystem deletion, and records completion. A blocked-unlink regression proves
  another source still records all 120 packets. Existing transactional event pins
  remain protected; if deletion claims a segment first, later events report the
  missing interval rather than claim coverage.
- Source exports and verification previously buffered whole videos and metadata.
  ZIP source reads/writes and frame/packet arrays are now streamed. The verifier
  uses a temporary disk-backed SQLite metadata index and decodes one image at a
  time. Analysis/review export selection uses a single read transaction. A test
  rejects whole-source `read_bytes`/ZIP `read` operations.
- Long tracks accumulated unbounded histories. Kalman continuation now uses
  explicit storage fragments of at most 512 observed points, preserving the same
  logical track/event, parent lineage, every raw point and separate fitted paths.
  Ended fragments are persisted and released. An encoded 600-frame test retains
  all 599 post-warmup observations in one event, exports and reanalyzes them without
  changing capture bytes. A 1000-point unit case checks the working-set bound.
- A pathological stream without usable keyframe boundaries could accumulate an
  unbounded packet ledger for a segment. A declared 10,000-packet safety bound now
  fails with a gap and preserves available bytes. It does not silently transcode
  or discard source to pretend the stream is healthy. The bound is exported.
- Large forward source timestamp discontinuities now create explicit gap records;
  a generated 20-second gap keeps its 20 source frames without fabricated fill.
- Local HTTP services now validate Host as well as mutation/WebSocket origins;
  hostile Host, traversal and cross-origin requests are rejected. Runtime error
  cleanup stops the source worker rather than losing its handle.

A follow-up scaled check at three hours found that the claim query itself still
scanned all event/segment relationships while holding the writer transaction.
On an isolated copy of the actual synthetic metadata, cleanup took7.8141s and
paused packet draining6.612s. This is a real recorder-independence defect even
though the paced file eventually retained all120 packets. The active soak was
intentionally stopped before changing code; it is not a completed reliability gate.

Migration5 adds a segment-first lookup index. On the same isolated metadata,
cleanup took0.0313s and preserved all120 new source packets. A regression with
10,000 protected segment records/20,000 references verifies indexed lookup and
concurrent packet-reader progress. Another test upgrades schema4 to5 without
rewriting existing camera records. Full timing evidence is in
retention_review_results.json. Source footage from the interrupted run was not
modified. A fresh full eight-hour run is required on this corrected code.

Verification on the corrected candidate: `make setup` passed with locked dependencies;
`make test` passed 56 pytest tests, Ruff, strict mypy on 33 modules, 2 Vitest tests,
and TypeScript/Vite build. `make browser-test` passed 3 Chromium tests, including
actual source playback, persisted reviews, verified export and forced WebSocket
reconnection. The actual eight-hour gate is separately recorded in wp5_report.md;
these tests are not a substitute for it.

Remaining limits: Linux x86_64 only is tested; physical-camera commissioning and
field accuracy remain untested. Source decoding is tested for FFV1/H264, not all
codecs or damaged streams. Verifier packages are limited to 2 GB uncompressed and
8 MiB per manifest. Immutable-by-application snapshots are not cryptographic
proof of scene authenticity or guarantees against OS administrator modification.
No crash/power-loss test establishes universal filesystem durability. Rolling
quota never deletes protected evidence; protected data can exceed it.
