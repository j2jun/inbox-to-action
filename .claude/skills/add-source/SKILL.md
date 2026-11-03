---
name: add-source
description: Add support for a new call platform (e.g. Whereby, Chime) or task tool (e.g. ClickUp, Monday). Use when the user names a platform or tool that isn't detected yet.
---

1. Write a realistic sample email for it in `samples/NN_<name>.eml` (fake `*.example` domains except the platform's own link host).
2. Add its expected task(s) to `samples/expected.json`.
3. Add one row to the matching pattern table (call platforms or task tools). No new code paths.
4. Run the `run-eval` skill and confirm nothing else regressed.
5. If it's a call platform, add it to the FR-6 table in README.md.
