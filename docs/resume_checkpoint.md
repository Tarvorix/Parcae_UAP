# Core offline checkpoint — final local acceptance passed

Workspace `/workspace/Parcae_UAP`,branch `work`,base
`abae96f9464a789c87d862c766eba62419169af3`. Source is uncommitted at this checkpoint.
Verified remote `https://github.com/Tarvorix/Parcae_UAP.git`;only main at the base
was present on the final read. Do not overwrite unrelated work or force-push.
The owner authorized scoped commit,push,draft PR,final-commit CI,ready/merge and
remote-main verification after acceptance. Deployment,physical camera access,
WP7–8,purchases,paid APIs and new credentials remain outside scope. Preserve the
user-selected model settings;no runtime identity claim is made.

## Completed local gates

- Isolated CPython3.15.1,uv0.13.5,Node24.19/npm11.9,FFmpeg7.1.5,PyAV19.0.2,
  Chromium154,Linux x86_64. Mac/hardware not tested.
- Locked setup including offline cached setup passed.
- 56pytest49.64s,Ruff,strict mypy33 modules,2Vitest,TypeScript/Vite build passed.
- 3Chromium tests15.1s:preview,actual review/playback/export,and socket reconnect.
- Real repository-local loopback RTSP,PTS/VFR/B-frame/reset/ambiguity,blocked
  consumer,corruption,quota/pinning,disk-full,restart and source gap checks passed.
- Critical review in docs/critical_review.md. The late retention-query defect was
  reproduced on isolated metadata:7.8141s cleanup,6.612s packet-reader pause.
  Migration5 indexes segment relationships. Same metadata cleanup0.0313s;new
  scaled concurrency and schema-upgrade regressions pass. No bypass of the
  previously denied MediaMTX metadata target was attempted.

## Actual final eight-hour pass

Attempt3,status passed,exit0:
- Start2026-10-24T06:31:45.348241Z;finish2026-10-24T14:36:05.186791Z.
- 29059.838558actual wall seconds;28800 media seconds.
- 240epochs,288000exact source frames/packets,480complete sealed events,
  240independently verified exports/reviews/event-API checks,zero errors.
- Peak249148KiB,growth12116KiB;core hashes and inventory unchanged.
- Frozen code manifest SHA7ed43046f4a42ac6228aac50ca62fae57a6c9d9a08c429b95c369fba486a223e.
- Runtime output artifacts/soak-core-8h-v3. PID257276 ended normally;no soak active.
- Post-check docs/validation/final_acceptance.json:SQLite/FK integrity,
  zero gaps/unsealed events,25440protected rolling segments,47280capture
  artifacts and240export hashes verified.

Attempt1 and2 were interrupted for confirmed repairs and do not count toward the
pass. Their exact terminal status/code manifests are in docs/validation.
Original runtime directories remain intact. All footage is generated;these are
software tests,not real-world detector accuracy or G100 reliability claims.

## Library and handoff

Complete handoff document bytes match all parent-supplied sizes/SHA256;both plan
copies are exact. Original ZIP transport failed twice;its ZIP hash is unverified.
See docs/wp0_report.md. Preserve authoritative handoff bytes.

Milestone Library IDs:
- WP0 corrected:libfile_9368d703e1188191bb30116aee179e14 version1.
- WP1:libfile_82370d6cf13881919eec8af7df165980 version1.
- WP2:libfile_9f003a713c608191893e7d54f2ef3475 version0.
- WP3:libfile_bf55df9b2da48191baf869f666a89f30 version0.
- WP4:libfile_10a7f0d6084481918d55779ad9d412d6 version0.
- Core beta:libfile_927ec6ecc63881919f5b62358f3a8d62 version2 currently;the next
  replacement must guard expected_current_version=2 and retain returned identity.

## Publication follow-through

Commit scoped source/docs,then push work branch and create a draft PR. Verify CI
on the exact final head,mark ready,merge only when all gates pass,and verify remote
main contains it. Record confirmed URLs/SHAs in the final delivery report. Preserve
these acceptance facts;do not alter core files merely to update publication metadata.
Package final source and exact validation reports,excluding dependencies,caches,
secrets and runtime footage;only the explicitly public fixture is included.

Tool prefixes:UV_CACHE_DIR=/tmp/uap-uv-cache,
UV_PYTHON_INSTALL_DIR=/workspace/python,npm_config_cache=/tmp/uap-npm-cache,
UV=/workspace/uap-tooling/bin/uv,CHROMIUM_PATH=/usr/bin/chromium.
Library helper:/tmp/uap-library-helpers/library_file_transfer.py;apply returned
xattrs in the same functions.exec as direct replacement. No subagents used.
