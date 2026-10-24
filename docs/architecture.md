# Current offline architecture

CameraSource has synthetic, finite video-file and configured loopback RTSP
implementations. Physical-camera URLs remain gated. All current footage is
programmatically generated. A single PyAV/FFmpeg input remuxes encoded video into
keyframe-aligned Matroska segments without audio. Packet PTS/DTS, source/archive
time bases, receipt UTC/monotonic values and payload hashes are recorded with
stable camera/session/packet IDs. Stream-copy containers are not raw transport.

A separate worker indexes decoded frames from closed, hashed segments. Frame
references record segment/display index, packet association, PTS and pixel hash.
Duplicate or absent PTS joins are explicitly ambiguous/unavailable. A bounded
queue drops old delivery frames without blocking recording. The offline detector
uses exhaustive native-cadence archive processing, independently of that delivery
queue; delivery drops are not silently described as detector drops. Source
recording remains independent while the detector catches up from disk.

Compact-source/difference/background detectors emit versioned candidates and
quality reasons. Kalman prediction plus gated Hungarian assignment tracks raw
observations separately from fits. Histories are persisted in bounded storage
fragments with logical identity and explicit parent lineage; no observations are
discarded at fragment boundaries. Confirmed persistent tracks trigger durable
pre/post windows and segment pins; observations extend the window transactionally.
Quiet tracks can seal after postroll coverage arrives. Multiple independent events
are supported; automatic semantic merging/analyst split UI is not implemented.

SQLite ordered migrations introduced durable source/frame records in WP1, evidence
locks/reviews/exports in WP2, and versioned processing/track records in WP4. WP5
adds durable jobs/settings. A data-directory lease prevents two application
owners from running startup recovery concurrently. Sealing/export operations
serialize; recorder writes remain independent. Retention claims unprotected
segments transactionally and unlinks them outside the SQLite writer lock. Interrupted sessions and partial
captures retain honest gap states.

Sealed capture directories contain immutable-by-application source snapshots and
capture manifests. Reviews/analyses append new revisions. Each export is a frozen
ZIP with selected revisions and hashes; the verifier additionally decodes source
frames and checks their recorded pixel hashes and packet/segment references.
Source bytes and metadata arrays are streamed, with a disk-backed verification
index. Full detector/mask, tracker/event settings, source configuration and build
provenance travel with exports, separately from immutable capture metadata.
These checks establish internal consistency, not scene authenticity or field
accuracy. OS power-loss guarantees are outside the current tests.

FastAPI serves only local endpoints. The React UI uses native bundled assets,
local frame/derived playback, review/export, paginated search, settings and live
health/event-count notifications with reconnect. Original source downloads remain
separate from MP4/JPEG derivatives. Cross-origin mutations/sockets are rejected.
No external enrichment, camera reset, paid API, public exposure or deployment
is part of this slice.
