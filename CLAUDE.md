# inbox-to-action

Turns Gmail emails into prioritized tasks in one Notion database. Requirements are in README.md (FR-x / NFR-x IDs); cite them in commits and PRs.

## Stack
- Python 3.12, standard library first (`email`, `re`, `zoneinfo`, `json`)
- Gmail → Zapier (trigger only, sends a webhook) → Python function → Notion API
- AI is used only for free-form human emails; everything else is rules

## Pipeline (order matters)
1. **Skip filter**: `List-Unsubscribe` / `Precedence: bulk` headers, receipts, past invites
2. **Call invites**: `.ics` (`text/calendar`) first, then link and dial-in patterns
3. **Task-tool emails**: sender and format patterns (Jira, Asana, GitHub, DocuSign, …)
4. **AI extraction**: only what steps 1–3 didn't handle
5. **Priority + dedupe**: pure functions; dedupe key = iCal UID for calls, else Gmail Message-ID

## Commands
- Eval against samples: `python3 -m inbox_to_action.eval samples/` (compares to `samples/expected.json`)
- Tests: `python3 -m pytest -q`

## Rules
See `.claude/rules/`: python-style, testing, privacy. Privacy rules are non-negotiable.
