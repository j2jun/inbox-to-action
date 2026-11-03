---
name: run-eval
description: Run the pipeline on samples/ and score it against samples/expected.json. Use after changing detection, extraction, or priority logic, or when asked "how accurate is it?"
---

1. Run `python3 -m inbox_to_action.eval samples/`.
2. Report three numbers: call-invite recall (target ≥95%), action-item miss/invent rate (target ≤10%), priority match rate.
3. List each failing sample: file, expected vs actual, and the likely cause.
4. Do not edit `samples/expected.json` to make a failure pass unless the user agrees the key was wrong.
