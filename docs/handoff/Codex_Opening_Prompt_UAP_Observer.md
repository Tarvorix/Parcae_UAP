# Codex Opening Prompt — UAP Observer Stationary v1.1

**Planning revision: 2026-10-24. This prompt authorizes WP0 only when the owner gives it to the coding agent as an implementation request. Updating these documents does not itself start a build.**

Read **UAP_Observer_Stationary_Implementation_Plan.md** in full and use version 1.1 as the source of truth.

**Vision:** Build our own affordable, local-first sky observation software around the **Aqara G100 we already own** and the **computer we already have**. Keep Apple Home working. Make a reusable, modular foundation, not a throwaway demo or an expensive observatory. Verify the actual host’s OS, architecture, memory and free storage; do not assume a particular Mac model. No new hardware, dedicated server, paid AI API, cloud service or subscription is required to begin.

**First useful product:** One-camera live view, motion/point detection and image-plane tracking, original event footage with pre/post context, and a basic review/export screen. Advanced sky calibration, aircraft/satellite matching and multiple cameras are optional later extensions. A detection is not evidence of extraordinary origin.

**No footage prerequisite:** The owner has no recorded sky video. Generate deterministic encoded fixtures with ground-truth IDs/tracks, moving dots, blinking lights, noise, jitter and exposure changes. Test offline first, then collect real G100 footage during separately authorized commissioning. Synthetic tests establish software behavior, not real-world accuracy.

**Stack:** FFmpeg/ffprobe for capture/remux/decode; Python/OpenCV/NumPy for analysis and FastAPI; SQLite with migrations; TypeScript/React/Vite for the browser UI. Use tested dependency lockfiles. Keep heavy pixel work in compiled library operations. Benchmark before optimizing; do not silently sacrifice detection quality to reduce load.

**Implement Work Package 0 now:** Create the repository structure, README, AGENTS.md, example configs, typed source/frame/event/manifest models, synthetic and video-file adapters, generated fixtures, minimal browser/API preview with timestamps, setup/demo/test commands and initial tests. Keep this slice small and runnable. It is not yet the complete detector or a validated live-camera system.

**Architecture requirements for later packages:** Introduce minimal SQLite migrations and capture mapping in WP1, durable event locks/recovery in WP2, before dependent event features. Every analyzed frame must retain a tested mapping to original source frames/PTS and archived segments, including reconnects and timestamp resets. Keep draining frame/metadata pipes even when analysis stalls, and prove recording survives. Treat 960-pixel/5-FPS analysis as a provisional benchmark, not validated sensitivity; test native-resolution/cadence alternatives and short transients. Preserve stream-copy source evidence separately from derivatives. Seal capture manifests independently of versioned analyses/reviews/export snapshots. Detection-exclusion masks do not redact original footage; audio is off by default.

**Boundaries:** Stationary only; no PTZ. No fabricated RTSP URL, credentials, range, true physical speed, measured accuracy or hardware validation. No reset/unpair of G100. Never leak secrets into code, logs, browser responses, screenshots or exports. Do not connect to the real camera or change device/network settings during WP0.

**Stop after WP0:** Report what works, changed files, exact commands and actual test results, host/environment tested, limitations and the proposed WP1. Wait for the owner’s go-ahead before continuing. Do not automatically execute WP1–8, launch a long soak, push, deploy, buy hardware or enable paid services.
