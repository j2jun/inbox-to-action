---
paths:
  - "**/*.py"
---
# Python style

- Standard library first. Ask before adding a dependency.
- Plain functions and dataclasses; no classes with one method, no factories.
- All datetimes are timezone-aware (`zoneinfo`). Never call `datetime.now()` without a tz.
- Call-platform and task-tool detection lives in data tables (pattern → platform), so adding a platform is one line.
- Priority logic is a pure function of extracted fields. It returns `(priority, reason)`.
