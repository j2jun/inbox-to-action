---
paths:
  - "tests/**"
  - "samples/**"
---
# Testing

- `samples/*.eml` + `samples/expected.json` is the answer key. The eval must pass offline, with no network.
- Freeze "now" to `context.now` in expected.json; never depend on the real clock.
- New behavior → add or update a sample email and its expected entry, not a mock.
- AI extraction is stubbed in tests with recorded outputs; a separate opt-in run hits the real API.
- Changing expected.json needs a reason in the commit message (it is the spec).
