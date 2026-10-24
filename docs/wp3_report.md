# WP3 deterministic candidate generation and measured limits

Compact bright-source, temporal-difference and MOG2 background baselines are
independent configurable modes using compiled OpenCV/NumPy operations. Connected
components report centroids, area, bounding boxes, contrast, flags and detector/
parameter/mask/profile versions. Default compact mode uses local contrast and
positive temporal change to reject static stars and trailing ghosts. Day/night
profiles are labels for versioned settings; only the synthetic night-style
compact defaults are profiled here. Static, masked, exposure-jump and global
shift tests pass. Suppression reasons are explicit; no origin inference occurs.

`make test`: 36 pytest, Ruff, strict mypy23 modules, 2 Vitest and TS/Vite build
passed. Browser regression passed. Source/fixture provenance: fixtures.md.
`uv run --project backend python scripts/benchmark_detection.py` completed on the
cloud host, decoding H264 at two CRFs and sampling all 5-FPS phases. Detailed
case-level results, CPU/wall timing and parameter hashes: detection_benchmark.json.
This WP3 benchmark used contrast=8, change=6, min_area=1 (more sensitive than the
application defaults 18/10/2). Its numbers are not a default-setting performance
claim. WP5 reruns the same encoded cases using the application defaults.

| CRF | Width | FPS | Detected scripted passes | Misses | False candidates |
|---|---:|---:|---:|---:|---:|
|18|1280|20|18/18|0|223|
|18|1280|5|54/72|18|257|
|18|960|5|45/72|27|46|
|32|1280|20|7/18|11|272|
|32|1280|5|19/72|53|1072|
|32|960|5|19/72|53|447|

Each native-20FPS run uses 18 cases; each 5FPS configuration aggregates four
sampling phases, hence denominator72. Targets span diameters1/3/5 native pixels,
contrast12/64 gray levels, durations.05/.2/1 seconds, moving160px/s. This includes
one-frame flashes entirely between samples. Peak process RSS was225332KiB on
Linux. Individual configuration runs took about24–31 wall seconds in this cloud
container; this is not camera/owner-Mac performance.

The 18/18 result is detection of at least one candidate near each scripted target,
not tracking accuracy or real-world recall. False candidates are not false events;
tracking/promotion is WP4. No universal sensitivity threshold follows from this
small synthetic set. Below-threshold/missed cases are retained in the report.
Compression demonstrably removes small/dim signals. Native-cadence processing is
the offline default; 960/5 is an explicitly measured optional benchmark setting.
The code must not silently trade away that quality to meet a CPU target.

Remaining limits include dark-object tuning, weather/lens artifacts and field
footage. Difference/background modes passed controlled dark-motion tests but have
not had equivalent sensitivity profiling. No real-world detector performance or
extraordinary-origin conclusion is claimed. No long soak has run.
