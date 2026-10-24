# UAP Observer handoff revision 1.1

**Date:** 2026-10-24  
**Scope:** Planning documents only. No application code was created, no camera was connected and no hardware performance was measured by this revision.

## What changed

- Made the budget constraint explicit: existing G100 and existing computer, no required new hardware or paid services, a useful modular foundation before optional features.
- Clarified that no owner-supplied video is needed. Generated fixtures support development; later real G100 footage establishes field behavior.
- Aligned milestones with work packages. Core offline beta is WP0–5; WP6 commissions one G100. Calibration/enrichment and multi-camera operation in WP7–8 no longer block core v1.
- Moved minimal SQLite migrations/persistence to WP1 and transactional event/segment locks to WP2, before restart recovery and event features depend on them.
- Added source-frame/PTS identity and archived-segment mapping requirements and tests, including timestamp resets, variable cadence and reconnects.
- Required continuous pipe draining and stalled-analysis tests so a slow detector cannot silently block source recording.
- Marked 960-pixel/5-FPS analysis as provisional. Added size, contrast, duration and sampling-phase sensitivity tests against higher/native-resolution and cadence settings.
- Distinguished detection-exclusion masks from capture privacy. Added audio-off default and clearly labeled redacted derivative exports.
- Separated immutable capture manifests from versioned analyses/reviews and frozen export snapshots, with invariance tests across review/re-export.
- Kept core soak, storage, recovery and security testing in the core build rather than deferring them to multi-camera expansion. Retention targets depend on measured bitrate and available disk space.
- Removed unverified Mac model and geographic assumptions. Kept Apple Home pairing and measured hardware acceptance requirements intact.
- Changed the opening prompt to implement WP0, report and stop for review rather than automatically running the entire roadmap.

## Contents

1. UAP_Observer_Stationary_Implementation_Plan.md — complete revised specification.
2. Codex_Opening_Prompt_UAP_Observer.md — bounded first coding assignment.
3. CHANGELOG_v1.1.md — this change summary.

The prior documents remain unchanged. The revised ZIP contains exactly these three UTF-8 Markdown documents. Revision validation checks document structure, consistency and exact ZIP member bytes; it is not a software test or live-camera validation.
