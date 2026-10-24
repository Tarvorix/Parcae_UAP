# UAP Observer repository rules
Implement the currently authorized scope: WP1–5 sequentially, each tested.
Owner authorized the core eight-hour wall-clock offline soak. WP6–8 remain gated. Keep modules focused and offline use
camera-free. Never access hardware, alter networking, push or deploy implicitly.
Keep Apple Home paired. No real credentials, private recordings or coordinates in
source, logs, UI or exports. Audio is off. Synthetic tests prove software behavior,
not sky accuracy. Never infer physical distance or speed from pixels.
Run `make test` and `make browser-test` before reporting a milestone. Preserve
source footage separately from derivatives; don't call preview frames evidence.
WP1 introduces SQLite migrations and tested PTS/segment mapping; WP2 adds durable
evidence locks and immutable capture manifests. Do not substitute schema models
for implemented capabilities. See docs/implementation_plan.md for the full specification and
docs/wp0_report.md for verified handoff provenance.
