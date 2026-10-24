# Data contracts and evidence boundary

Pydantic models reject unknown fields and carry schema version 1.0. Stream/frame
contracts identify source, camera and capture session; frame IDs combine session
and decoded index. Detection/track/event models use image-plane identifiers only.
Capture manifests and export snapshots are separate contracts: review/analysis
revisions belong to exports, never capture seals. No sealing or verification
service is implemented in WP0. Frozen Pydantic fields are not an immutable
filesystem or database guarantee.

Fixture ground truth contains known generated target centers/visibility and
frame-level jitter/exposure metadata. Its SHA256 identifies encoded fixture bytes.
It is not a claim that a detector recovered those labels. Audio is absent.

WP1 must establish exact source-frame/PTS/archive mapping, epochs and uncertainty
with migration-backed records. WP2 must seal immutable capture artifacts and
transactionally preserve pre/post footage across failures before export. Keep
stream-copy source evidence distinct from derived annotations and previews.
