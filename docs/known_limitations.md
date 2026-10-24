# Current limits and verification boundaries

- The generated offline eight-hour gate passed:28800 media seconds over
  29059.8386 actual wall seconds. This does not validate physical-camera uptime
  or field detector performance. Earlier interrupted runs were not counted.
- All footage is synthetic. Camera reliability, field sensitivity, accuracy,
  weather/insects, privacy framing and the owner's Mac are not validated.
- Default candidate thresholds miss one-pixel/low-contrast cases. Native cadence
  is retained. The original WP3 benchmark used more sensitive 8/6/1 settings;
  WP5 separately measured the application defaults 18/10/2. See both reports.
  Candidate-level success is distinct from confirmed-track/event sensitivity.
- Compact bright-source mode has sampling/compression profiling. Difference and
  background modes have controlled dark-motion tests, not equivalent envelopes.
- A single stationary camera supplies pixels and source media time, not physical
  range/speed or calibrated sky coordinates. Exposure UTC remains unknown.
- Exact packet/frame/segment mapping is tested on FFV1 and H264 fixtures including
  VFR/B-frames, resets and duplicate PTS. Arbitrary codecs/broken streams are not
  universally supported. Missing/ambiguous mapping is never guessed from FPS.
- Segment packet ledgers have a declared 10,000-packet safety bound. A stream
  without usable keyframes fails with a gap at that limit rather than growing
  memory indefinitely. Verifier input is limited to 2 GB uncompressed per ZIP and
  8 MiB per manifest. Long tracks retain all points in bounded storage fragments.
- Closed-segment analysis introduces segment/GOP/backlog latency. Offline processing
  is exhaustive; the independently bounded delivery queue can drop frames. The
  current live physical-camera commissioning path is gated until WP6.
- The UI processes the included fixture. Local-file adapters/diagnostic APIs exist,
  but the browser does not accept arbitrary path uploads. The mask editor uses
  validated native-pixel rectangles. Manual semantic merge/split is not provided.
- MP4/JPEG playback is derived; canonical Matroska source is separately downloadable.
  Segment playback does not join gaps into a fictional continuous clip. Detection
  masks do not redact original pixels. Audio is off.
- Disk quota applies to rolling source bytes; protected source/event/export data
  can exceed it and remains protected. Storage use/free reserve is displayed.
  No implicit deletion of retained evidence occurs. Simulated ENOSPC and restart
  tests are not proof of all filesystem/power-loss behavior.
- The owner's reported Apple Silicon Mac Python is 3.15.0; this cloud uses isolated
  Python3.15.1. No Mac changes were made. Linux x86_64 is the tested platform.
- Original Library ZIP transport failed twice, so its ZIP hash is unverified.
  Complete plan/prompt/changelog bytes were later supplied and matched parent
  originals' sizes/SHA256 exactly; the documentation-preservation gap is resolved.
- A proxy403 blocked MediaMTX GitHub metadata retrieval; it was not retried or
  bypassed. Repository-local loopback RTSP fixture tests passed independently.
