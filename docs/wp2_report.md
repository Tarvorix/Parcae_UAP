# WP2 durable evidence and retention

Migration 2 adds events, transactional event-segment references, reviews, analyses,
exports and audit history. Event triggers pin pre-context and future post-context;
segment commits acquire matching open-event references in the same SQLite
transaction. Cleanup serializes with triggers and deletes only unprotected source
segments. Protected bytes exceeding quota remain protected and report a warning.
No evidence deletion or silent clipping is used to satisfy quota.

Closed source files receive a fsynced packet-ledger sidecar before rename. Startup
reconciliation adopts closed orphan files with this ledger; unfinished .partial
files remain explicitly unfinished. Interrupted sessions become gaps and pending
events seal as partial where context is missing. Do not run recovery against an
active recorder; it is a startup operation.

Capture directories contain original remuxed source segments, frame and packet
references, coverage and SHA256 manifests. Capture manifests never include later
review fields. Review records append; each export is a new frozen ZIP with its own
manifest and revision IDs. Existing exports are never appended or overwritten.
`uv run --project backend python scripts/verify_evidence.py PACKAGE.zip` verifies
paths, sizes, hashes, source-frame pixel hashes/PTS and frame/packet/segment links.
This proves internal byte consistency, not independent scene authenticity.

Actual validation on Python3.15.1/Linux: `make test` passed 32 backend tests,
Ruff, strict mypy (21 modules), 2 Vitest and TypeScript/Vite build. Browser regression
also passed. Six new evidence tests exercise complete 30-second pre-context and
45-second post-context for a 30-second scripted event in a 120-second generated
recording, review/re-export invariance, restart interruption, orphan-seal recovery,
corruption, quota/trigger locking, path traversal and simulated ENOSPC. Encoded
source and capture-manifest bytes remain unchanged across review and re-export.

Limits: OS power-loss semantics and real full-disk recovery are not established
by simulated ENOSPC. Quota applies to rolling source bytes; retained event/export
copies also consume space and must be reported in WP5 storage status. Source
copying can fail; failed sealing remains pending and protects source. No detector
triggers yet (WP3–4), no field accuracy claim, and no actual long soak yet.
