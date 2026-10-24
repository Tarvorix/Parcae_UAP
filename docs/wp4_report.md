# WP4 image-plane tracking and detector-triggered events

Migration3 stores versioned processing runs, detections, tracks, event-track
relationships and per-frame quality/suppression records. Constant-velocity Kalman
prediction and gated Hungarian assignment associate candidates. Four observations
confirm a track; promotion additionally requires .15s observed span. Short gaps
up to .75s can reacquire within a 30-pixel gate, with an explicit association
record. Longer gaps end a fragment. Raw observations and fitted paths are separate;
ambiguous close associations are flagged. No physical speed or range is emitted.

Each promoted independent track opens a durable event with the configured pre/post
window. Later observations extend postroll and pins transactionally. Simultaneous
tracks can produce independent events. The schema supports multiple track links
per event; automatic semantic grouping or analyst merge/split UI is not provided.
No long-gap association silently joins fragments. All source references remain
available for later reassessment.

OfflineProcessor reads indexed archived source frames exhaustively at native
cadence, emits candidates/tracks and conservative unresolved assessments with
reason codes. This exhaustive replay is distinct from the bounded live analysis
queue, whose dropped deliveries remain recorded. A new reanalysis function reads
sealed capture bytes into a new analysis revision; it never reseals or overwrites
capture. Reviews and export snapshots preserve prior versions and source hashes.

Validation: `make test` passed 40 pytest tests, Ruff, strict mypy27 modules, 2 Vitest
and TS/Vite build; Chromium browser regression passed. Tests include actual
encoded crossing-target footage retaining two observable directions, ideal
crossing association, temporary loss/reacquisition/long-gap fragments, backward
clock refusal, detector-triggered event persistence, partial event export,
restart/review and reanalysis/export invariance.

The short six-second demo cannot supply 30s pre/45s post; resulting events are
explicitly incomplete. A separate 120-second scripted-trigger fixture in WP2
verifies full pre/post coverage. Tests prove software behavior, not real-world
tracking accuracy, origin classification, hardware reliability or completed soak.
