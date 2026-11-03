# inbox-to-action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn task-related Gmail emails (including call invites) into prioritized tasks in one Notion database, and work that list from an Aurora-styled Today page that syncs both ways with Notion.

**Architecture:** A pure-Python pipeline (parse → skip → calls → task tools → AI → priority → upsert) runs offline against `samples/` and online behind a Zapier webhook. One stdlib WSGI app serves the webhook, a small task API and login. A static page in `public/` calls that API. Notion is the only data store.

**Tech Stack:** Python 3.12 stdlib only (`email`, `re`, `zoneinfo`, `urllib`, `hmac`, `wsgiref`), pytest for tests, plain HTML/CSS/JS for the page, Notion REST API, Anthropic Messages API, Vercel Python runtime, Zapier (Gmail trigger → Webhooks POST).

**Spec:** `docs/superpowers/specs/2026-11-02-inbox-to-action-design.md`. The requirements it cites are in `README.md` (FR-x/NFR-x). The visual reference is `docs/superpowers/specs/assets/today-ui-mockup.html?__variant=aurora`.

## Global Constraints

- Code runs on Python 3.12. The local machine has 3.14, so use no 3.13+ features.
- No third-party runtime dependencies. pytest is the only dev dependency. Ask before adding anything.
- All datetimes are timezone-aware (`zoneinfo`). Default tz is `America/Los_Angeles` via the `TIMEZONE` env var.
- Tests never touch the network or the real clock. "Now" in tests is `2026-11-02T09:30:00-08:00` (from `samples/expected.json` → `context.now`).
- Never store or log full email bodies. Notion gets subject, sender, a snippet of 200 characters or fewer, and extracted fields (`.claude/rules/privacy.md`).
- Secrets are env vars only: `NOTION_TOKEN`, `NOTION_DATABASE_ID`, `ANTHROPIC_API_KEY`, `WEBHOOK_SECRET`, `APP_PASSWORD`, `SESSION_SECRET`. Plus `TIMEZONE`, `USER_EMAIL`, `VIP_EMAILS` (comma-separated).
- Priority rules, verbatim from `expected.json`: P1 = due or starts within 24h, or urgency language (ASAP/URGENT/blocker) from a non-bulk sender. P2 = within 7 days. P3 = later than 7 days or no date. P4 = cc'd only, with the ask aimed at others.
- A date-only due counts from **00:00 local on that date** for the 24h and 7-day windows. This is the plan's call, and it makes every sample match the key.
- Notion property names are exact: `Task`, `Priority`, `Type`, `Due`, `Status`, `Platform`, `Join link`, `From`, `Source email`, `Source item`, `Why this priority`, `Message ID`, `Priority locked`, `Snippet`.
- Allowed values:
  - `Type`: `Action, Call, Assignment, Approval, Follow-up, Deadline, Added`
  - `Status`: `To do, Done, Cancelled`
  - `Platform`: `Zoom, Meet, Teams, Webex, Phone, Other`
- Model: `claude-haiku-5-5` via `POST https://api.anthropic.com/v1/messages`, with header `anthropic-version: 2023-06-01`. Budget: `max_tokens` 800 and email text truncated to 4,000 characters (NFR-8).
- Notion API: header `Notion-Version: 2022-06-28`, endpoints `/v1/databases/{id}/query`, `/v1/pages`, `/v1/pages/{id}`.
- Page UI: the Aurora style only. It must pass keyboard access, visible focus, no clipping at 320px or 200% zoom, WCAG AA text contrast and `prefers-reduced-motion` gating.

## Review Focus

1. **Duplicate webhook delivery.** Zapier retries or the same email arrives twice. Expect no second task. Test in Task 7: `test_reprocess_same_email_creates_nothing`.
2. **Invite update arrives for a task the user re-prioritized.** Expect the time to change and the manual priority to stay. Test in Task 7: `test_update_keeps_locked_priority`.
3. **Cancellation for an invite never seen before** (for example, the original was skipped). Expect no task created and the skip logged as `cancel for unknown invite`. Test in Task 7: `test_cancel_unknown_uid_is_skipped`.
4. **Notion save fails during a page action** (network down, 429). Expect the row to revert and an error toast with Retry, never a silent success. Test in Task 10: manual check step with the API stubbed to 500.
5. **Webhook called without or with a wrong secret, or with a malformed body.** Expect 401 or 400, with nothing written and nothing echoed back. Tests in Task 9: `test_webhook_rejects_bad_secret` and `test_webhook_rejects_malformed_payload`.

---

## File Structure

```
inbox_to_action/
  __init__.py
  models.py      Email, Task, CallEvent dataclasses
  parse.py       parse_eml(bytes) / email_from_payload(dict, ics) -> Email
  dates.py       parse_datetime_text / parse_due_text -> aware datetime or date
  ics.py         parse_ics(text) -> CallEvent | None
  skip.py        skip_reason(email, now) -> str | None
  calls.py       PLATFORMS table, detect_call(email, now) -> Task | None
  tools.py       TASK_TOOLS table, detect_tool_task(email, now) -> list[Task] | None
  priority.py    prioritize(task, email, now, user_email) -> (str, str)
  ai.py          extract_with_ai(email, now, call_fn) -> list[Task]; anthropic_call; stub_call
  store.py       MemoryStore (same interface as NotionStore)
  pipeline.py    process(email, store, now, config, ai_call) -> Outcome
  eval.py        python3 -m inbox_to_action.eval samples/ [--live]
  notion.py      NotionStore, task_to_props, page_to_task, setup_database
  quickadd.py    parse_quick_add(text, now) -> Task
  auth.py        make_session / check_session / check_password
  web.py         WSGI app: /login /logout /api/tasks /webhook/email
  dev.py         python3 -m inbox_to_action.dev  (serves public/ + app on :8000)
api/index.py     Vercel entry: from inbox_to_action.web import app
public/index.html, public/today.css, public/today.js
vercel.json
tests/test_*.py, tests/fixtures/ai/*.json
pyproject.toml   (pytest config only)
```

---

### Task 1: Repo, models and email parsing

**Files:**
- Create: `pyproject.toml`, `inbox_to_action/__init__.py`, `inbox_to_action/models.py`, `inbox_to_action/parse.py`, `tests/test_parse.py`

**Interfaces:**
- Produces:
  - `Email(message_id: str, subject: str, from_name: str, from_addr: str, to: list[str], cc: list[str], date: datetime, headers: dict[str, str], body: str, ics: str | None, thread_id: str | None = None)`. `headers` keys are lowercase. `body` is the text/plain part, or HTML stripped to text if that's all there is.
  - `Task(title: str, type: str, due: datetime | date | None = None, platform: str | None = None, join_link: str | None = None, phone: str | None = None, from_name: str = "", from_addr: str = "", source_email: str | None = None, source_item: str | None = None, message_id: str = "", key: str = "", uid: str | None = None, sequence: int = 0, status: str = "To do", priority: str = "P3", reason: str = "", priority_locked: bool = False, snippet: str = "", page_id: str | None = None)`
  - `CallEvent(uid: str, start: datetime, end: datetime | None, summary: str, organizer: str, method: str, sequence: int, location: str)`
  - `parse_eml(raw: bytes) -> Email`
  - `email_from_payload(payload: dict, ics: str | None) -> Email`. Payload keys: `message_id, thread_id, from, to, cc, subject, date, body, list_unsubscribe, precedence`.

- [ ] **Step 1:** `git init` in `/Users/jjun/inbox-to-action`. Then create `pyproject.toml` with `[tool.pytest.ini_options] testpaths = ["tests"]`.
- [ ] **Step 2: Write failing tests** in `tests/test_parse.py`:
  - `test_parse_plain_eml`: `samples/11_direct_request.eml` gives `message_id == "<board-deck-011@acme.example>"`, `from_addr == "priya@acme.example"`, `"retention numbers" in body`, `ics is None`.
  - `test_parse_ics_part`: `samples/02_google_meet_invite.eml` gives `"UID:7k2v9q1northwind@google.com" in ics`.
  - `test_headers_lowercase`: `samples/21_newsletter.eml` gives `headers["precedence"] == "bulk"`.
  - `test_cc_parsed`: `samples/20_cc_fyi.eml` gives `cc == ["alex@acme.example"]` and `to == ["eng-leads@acme.example"]`.
  - `test_payload_round_trip`: `email_from_payload({...fields for 11...}, None)` matches `parse_eml` on `message_id`, `subject`, `from_addr` and `to`.
  - `test_all_samples_parse`: every `samples/*.eml` parses, and each has a non-empty `message_id` and an aware `date`.
- [ ] **Step 3:** Run `python3 -m pytest tests/test_parse.py -q`. Expected: FAIL (module missing).
- [ ] **Step 4:** Implement `models.py` and `parse.py`. Use `email.message_from_bytes(raw, policy=email.policy.default)`. Get addresses with `email.utils.getaddresses` and lowercase them. Read `ics` from the first `text/calendar` part.
- [ ] **Step 5:** Run `python3 -m pytest -q`. Expected: all pass.
- [ ] **Step 6:** Commit with `git add -A && git commit -m "feat: email model and parsing (FR-1, FR-2)"`. Include all existing project files in this first commit, and confirm `.gitignore` excludes `.superpowers/` and `.env`.

### Task 2: Date and calendar parsing

**Files:**
- Create: `inbox_to_action/dates.py`, `inbox_to_action/ics.py`, `tests/test_dates.py`, `tests/test_ics.py`

**Interfaces:**
- Consumes: `CallEvent` (Task 1)
- Produces:
  - `parse_datetime_text(text: str, now: datetime) -> datetime | None`. Returns the **first** explicit date and time in the text, aware in `now.tzinfo`.
  - `parse_due_text(text: str, now: datetime) -> date | datetime | None`. Handles relative phrases.
  - `parse_ics(text: str, tz: ZoneInfo) -> CallEvent | None`

- [ ] **Step 1: Write failing tests** (`now = 2026-11-02T09:30-08:00`):
  - `parse_datetime_text` must return these values:
    | Input | Expected |
    |---|---|
    | `"Time: Nov 4, 2026 10:00 AM Pacific Time"` | `2026-11-04 10:00` |
    | `"When: Friday, November 6, 2026 1:00 PM - 2:00 PM (UTC-08:00)"` | `2026-11-06 13:00` |
    | `"Tuesday, November 3, 2026, 8:00 - 8:45 AM PT"` | `2026-11-03 08:00` |
    | `"Thu, Nov 12, 2026 9:00 AM - 10:00 AM (PST)"` | `2026-11-12 09:00` |
    | `"Time: Oct 30, 2026 03:00 PM"` | `2026-10-30 15:00` |
    | `"no date here"` | `None` |
  - `parse_due_text` must return these values:
    | Input | Expected |
    |---|---|
    | `"by Friday"` | `date(2026,11,6)` |
    | `"by tomorrow"` | `date(2026,11,3)` |
    | `"Due date: 03/Nov/26"` | `date(2026,11,3)` |
    | `"Due: Nov 10"` | `date(2026,11,10)` |
    | `"approve by Nov 4"` | `date(2026,11,4)` |
    | `"expires on November 13, 2026"` | `date(2026,11,13)` |
    | `"closes this Friday, November 6 at 5pm PT"` | `datetime 2026-11-06 17:00` |
  - `parse_ics`: `02` gives uid `7k2v9q1northwind@google.com`, start `2026-11-04 13:00` local, method `REQUEST` and sequence 0. `03` gives start `2026-11-05 11:00` and sequence 1. `05` gives method `CANCEL`. Garbage text gives `None`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement the parsers. The approach is one regex per supported shape, then `datetime.strptime` on the normalized match. An "H:MM - H:MM AM" range takes the meridiem from the end of the range. Year-less dates take `now.year`, rolling to next year if more than 60 days in the past. ICS `DTSTART` handles `...Z` (UTC) and `TZID=` forms. Unfold continuation lines first.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat: date and ics parsing (FR-5, NFR-7)`.

### Task 3: Skip filter and call detection

**Files:**
- Create: `inbox_to_action/skip.py`, `inbox_to_action/calls.py`, `tests/test_skip.py`, `tests/test_calls.py`

**Interfaces:**
- Consumes: `Email`, `Task`, `parse_ics`, `parse_datetime_text`
- Produces:
  - `skip_reason(email: Email, now: datetime) -> str | None`. Returns one of `"newsletter"`, `"marketing"`, `"receipt"` or `None`. The past-invite skip lives in `detect_call`.
  - `PLATFORMS: list[tuple[str, re.Pattern]]`, in this order: Zoom `zoom\.us/(j|my)/`, Meet `meet\.google\.com/`, Teams `teams\.microsoft\.com/l/meetup-join`, Webex `webex\.com/.+?(j\.php|/meet)`, Other `meet\.goto\.com/|gotomeeting\.com/|whereby\.com/|chime\.aws/|slack\.com/.*huddle`
  - `detect_call(email: Email, now: datetime) -> Task | str | None`. Returns a call Task, or `"invite already in the past"`, or `None` when the email isn't a call.

- [ ] **Step 1: Write failing tests:**
  - `test_skip_reasons`:
    | Sample | Expected |
    |---|---|
    | `21` | `"newsletter"` |
    | `23` | `"marketing"` |
    | `22` | `"receipt"` |
    | `12`, `13` and `14` (task tools) | `None` |
    | `11` | `None` |
  - Newsletter vs marketing: both have `List-Unsubscribe`. Subject or body words like `off|sale|deal|offer|upgrade|promo` mean marketing; otherwise newsletter. Receipts match `receipt|your trip|order confirmation` from a `noreply`/`no-reply` sender with no ask.
  - `test_detect_calls` is parametrized over samples 01, 02, 04, 06, 08 and 09. It checks `platform`, `due` and `title`, all using the values in `samples/expected.json`.
  - Title rules: the ics `SUMMARY`, else the subject, with `Invitation:`/`Updated invitation:`/`Fwd:` and the trailing `@ …` stripped.
  - `test_join_link_and_phone`:
    - `01`: `join_link` starts with `https://acme.zoom.us/j/84512397720`
    - `08`: `phone == "+16465550110"`, `platform == "Phone"`
  - `test_ics_uid_and_sequence`: `03` has `uid == "7k2v9q1northwind@google.com"`, `sequence == 1`, `due` = Nov 5 11:00.
  - `test_cancel_marks_status`: `05` gives `status == "Cancelled"` and the Teams uid.
  - `test_past_invite`: `10` gives `"invite already in the past"`.
  - `test_not_a_call`: `11`, `12` and `07` give `None`. `07` is a phone *request* in free text, so the AI handles it.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement the detection order:
  1. Read the ics, if present, for time, uid, method and organizer.
  2. Match a platform link in the body.
  3. Detect a dial-in: a phone number plus `Conference ID|Meeting ID|Access Code|PIN`, with no platform link, gives Phone.
  4. Get the time from the ics, else `parse_datetime_text(body)`.
  5. If there's no time and no ics, it's not a call.

  Set `type="Call"` and `key = uid or message_id`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat: skip filter and call invite detection (FR-3, FR-6..FR-9)`.

### Task 4: Task-tool emails

**Files:**
- Create: `inbox_to_action/tools.py`, `tests/test_tools.py`

**Interfaces:**
- Consumes: `Email`, `Task`, `parse_due_text`
- Produces:
  - `TASK_TOOLS: list[dict]`. Each row has `name`, `sender` regex, `type`, `title` (a function of the Email returning str) and `link` regex.
  - `detect_tool_task(email: Email, now: datetime) -> list[Task] | None`. Returns `None` when the sender matches no tool.

- [ ] **Step 1: Write failing tests:** `test_tool_tasks` is parametrized over samples 12, 13, 14, 15, 16 and 19. It asserts `type`, `due`, every `title_has` word and `source_item` exactly as in `samples/expected.json`.
  - Title shapes:
    - Jira: `"PLAT-2291 Sev-2 billing sync failing for EU accounts"`
    - Asana: the line after "assigned a task to you"
    - GitHub: `"Review PR #482: Add pricing page experiment flag"`
    - Expensify: `"Approve expense report: Ravi Menon - Customer visits Oct"`
    - DocuSign: `"Sign Northwind MSA Amendment 3"`, with underscores turned into spaces and the extension dropped
    - Google Docs comment: `"Reply to Ravi Menon in 2027 Pricing Proposal"`, type `Action`
  - `test_unknown_sender`: `11` gives `None`.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement the table rows for Jira (`@.*\.atlassian\.net`), Asana (`@asana\.com`), GitHub (`notifications@github\.com`, only when `x-github-reason` is `review_requested|assign|mention`), Expensify, DocuSign (`@docusign\.net`), Google Docs (`comments-noreply@docs\.google\.com`), and also Trello, Linear, Monday and ClickUp sender patterns. Untested rows are allowed; keep them to one line each. Set `key = message_id + "#0"`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat: task-tool email detection (FR-4a, FR-4b)`.

### Task 5: Priority

**Files:**
- Create: `inbox_to_action/priority.py`, `tests/test_priority.py`

**Interfaces:**
- Consumes: `Task`, `Email`
- Produces: `prioritize(task: Task, email: Email, now: datetime, user_email: str, vips: set[str]) -> tuple[str, str]`

- [ ] **Step 1: Write failing tests:**
  - `test_rules`, a table of `(due, flags) -> priority`:
    | Due / flags | Priority |
    |---|---|
    | Nov 2 15:00 | P1 |
    | `date(2026,11,3)` | P1 |
    | Nov 3 08:00 | P1 |
    | `date(2026,11,4)` | P2 |
    | Nov 6 17:00 | P2 |
    | `date(2026,11,10)` | P3 |
    | None | P3 |
    | "ASAP" in body, due None | P1 |
    | user only in Cc, due None | P4 |
    | urgency words + `precedence: bulk` | not P1 from urgency |
  - `test_reason_text`:
    - due Nov 3 gives reason `"P1: due in under 24h"`
    - urgency gives `"P1: urgent wording (ASAP)"`, naming the first word matched
    - Cc gives `"P4: cc'd only"`
    - no date gives `"P3: no due date"`
    - 7-day gives `"P2: due within 7 days"`
    - later gives `"P3: due in more than 7 days"`
    - a VIP sender appends `", VIP sender"`
  - `test_check_order`: urgency first, then cc-only, then the due windows.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. Treat a date-only due as 00:00 local (see Global Constraints). The urgency regex is `\b(ASAP|URGENT|blocker)\b`, case-insensitive, over subject + body.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat: priority rules with reasons (FR-10, FR-11)`.

### Task 6: AI extraction

**Files:**
- Create: `inbox_to_action/ai.py`, `tests/test_ai.py`, `tests/fixtures/ai/{01,07,08,11,17,18,20,24}.json`

**Interfaces:**
- Consumes: `Email`, `Task`, `parse_due_text`
- Produces:
  - `extract_with_ai(email: Email, now: datetime, call_fn: Callable[[dict], dict]) -> list[Task]`
  - `anthropic_call(request: dict) -> dict`, a live POST via urllib with a 20 s timeout
  - `make_stub_call(fixture_dir: Path) -> Callable[[dict], dict]`. It looks up the fixture by the `Message-ID` embedded in the request's metadata.
  - `build_request(email: Email, now: datetime) -> dict`

- [ ] **Step 1: Write failing tests:**
  - `test_build_request`:
    - The model is `claude-haiku-5-5` and `max_tokens == 800`.
    - It forces one tool, `record_tasks`. Its input schema is `{"tasks": [{"title": str, "type": enum(Action, Call, Approval, Follow-up, Deadline), "due": str|null (ISO date or datetime), "platform": enum|null, "phone": str|null}]}`.
    - The system prompt states today's date and timezone, says to exclude "attend this meeting" when the email is itself an invite, and says to return `[]` when there is no ask for the user.
    - The user text is at most 4,000 characters, with quoted replies (`> ` lines and `On … wrote:` onward) and signatures stripped.
  - `test_parse_tool_response`: given the fixture `11.json` → 2 Tasks with titles containing "Q3 retention" and "competitive", due `date(2026,11,6)` and `None`.
  - `test_bad_response_returns_empty_and_logs`: a response missing `tool_use` gives `[]` and logs a warning naming the message id. That's the message id only, never the body.
  - `test_body_never_logged`: use `caplog` while running `extract_with_ai` on `11`, and check that `"retention numbers"` isn't in the log text.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement. Fixtures are hand-written Anthropic responses (a `content` list with one `tool_use` block). Their contents mirror `samples/expected.json` for the 8 emails that need AI: `07, 08` (prep action only), `11, 17, 18, 20, 24`, and `01` (prep action, see Task 7 Step 1).
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat: Claude extraction for free-form emails (FR-4, NFR-5, NFR-8)`.

### Task 7: Pipeline, memory store and eval

**Files:**
- Create: `inbox_to_action/store.py`, `inbox_to_action/pipeline.py`, `inbox_to_action/eval.py`, `tests/test_pipeline.py`, `tests/test_eval.py`
- Modify: `samples/expected.json`. Add an `01` Action for "top 3 Q1 bets", due `2026-11-04T10:00:00-08:00`, P2. Reason for the change: the email asks for prep, the same as `08`, and the key was inconsistent.

**Interfaces:**
- Consumes: everything from Tasks 1–6
- Produces:
  - The store interface, implemented by `MemoryStore` here and `NotionStore` in Task 8:
    - `find(key: str) -> Task | None`
    - `has_prefix(prefix: str) -> bool`
    - `create(task: Task) -> Task` (sets `page_id`)
    - `update(page_id: str, **fields) -> Task`
    - `list_open() -> list[Task]`
  - `Config(user_email: str, vips: set[str], tz: ZoneInfo)` and `Config.from_env()`
  - `Outcome(created: list[Task], updated: list[Task], skipped: str | None)`
  - `process(email: Email, store, now: datetime, config: Config, ai_call) -> Outcome`
  - CLI: `python3 -m inbox_to_action.eval samples/ [--live]`. It exits 0 when every target is met, else 1.

- [ ] **Step 1:** Edit `samples/expected.json` for `01`, as described under Files.
- [ ] **Step 2: Write failing tests:**
  - `test_flow_order`:
    - `21` is skipped as "newsletter" and the AI is not called (the stub raises if called).
    - `12` gives one Assignment and no AI call.
    - `11` gives 2 tasks via AI.
    - `08` gives the call plus the AI prep task.
  - `test_reprocess_same_email_creates_nothing`: `process(11)` twice leaves the store holding 2 tasks.
  - `test_update_moves_call`: running `02` then `03` leaves one task, due Nov 5 11:00.
  - `test_update_keeps_locked_priority`: run `02`, set `priority="P1", priority_locked=True`, then run `03`. The priority is still P1 and the due has moved.
  - `test_cancel_sets_status`: `04` then `05` gives `status == "Cancelled"`.
  - `test_cancel_unknown_uid_is_skipped`: `05` alone gives `skipped == "cancel for unknown invite"` and creates nothing.
  - `test_snippet_limit`: every created task has `len(snippet) <= 200`.
  - `test_eval_passes_offline`: run `eval.main(["samples/"])` with stub AI. It returns 0, with call-invite recall 100%, action miss/invent rate 0% and priority match 100%.
- [ ] **Step 3:** Run the tests. Expected: FAIL.
- [ ] **Step 4:** Implement `process`:
  1. Return early if `has_prefix(message_id)` is true and the email is not a calendar update.
  2. Call `skip_reason`.
  3. Call `detect_call`. An existing uid means update `due`, `status`, `join_link` and `title`, and keep the priority if it's locked.
  4. Call `detect_tool_task`.
  5. Otherwise, call the AI. For calls, the AI also runs to catch prep tasks.
  6. Run `prioritize` on each new task.
  7. Create each task with `key = f"{message_id}#{i}"`, or the uid for calls.

  `eval.py` processes the files in name order into one `MemoryStore`. It matches tasks to expected entries by type plus `title_has`, and prints the three NFR-2 numbers and one line per mismatch. `--live` uses `anthropic_call`.
- [ ] **Step 5:** Run `python3 -m pytest -q && python3 -m inbox_to_action.eval samples/`. Expected: all pass, then eval prints `call recall 100% · action miss/invent 0% · priority 100%` and exits 0.
- [ ] **Step 6:** Commit `feat: pipeline, dedupe and offline eval (FR-8, FR-13, NFR-2, NFR-4, NFR-9)`.

### Task 8: Notion store and database setup

**Files:**
- Create: `inbox_to_action/notion.py`, `tests/test_notion.py`

**Interfaces:**
- Consumes: `Task` and the store interface (Task 7)
- Produces:
  - `task_to_props(task: Task) -> dict` and `page_to_task(page: dict) -> Task`, both pure
  - `NotionStore(token: str, database_id: str, http=urllib_http)`, implementing the store interface. Its `http(method, url, body) -> dict` is injectable.
  - `setup_database(token: str, parent_page_id: str) -> str`, which returns the new database id
  - CLI: `python3 -m inbox_to_action.notion setup <parent_page_id>`

- [ ] **Step 1: Write failing tests:**
  - `test_props_round_trip`: `page_to_task(fake_page(task_to_props(t)))` equals `t` on every Notion-backed field. Run it for one call task (datetime due, link, uid) and one date-only task.
  - `test_property_names_exact`: the keys of `task_to_props` equal the exact names in Global Constraints.
  - `test_find_uses_message_id_filter`: with a fake `http` recording calls, `find("abc")` POSTs `/v1/databases/{id}/query` with `{"filter": {"property": "Message ID", "rich_text": {"equals": "abc"}}}`.
    - `has_prefix` uses `starts_with`.
    - `list_open` filters `Status == "To do"`, sorts by `Due` ascending and follows `next_cursor` pages.
  - `test_retry_on_429`: the fake returns 429 then 200. There's one retry after the `Retry-After` delay, with sleep injected.
  - `test_setup_schema`: `setup_database` sends exactly the property set and select options from Global Constraints.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement with `urllib.request`, using the headers `Authorization: Bearer`, `Notion-Version: 2022-06-28` and `Content-Type: application/json`. Retry 429 and 5xx up to 3 times with backoff. A date-only `Due` is written as `{"start": "YYYY-MM-DD"}`, a datetime as ISO with offset.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat: Notion store and database setup (FR-12, NFR-3)`.

### Task 9: Web app (auth, task API, webhook)

**Files:**
- Create: `inbox_to_action/quickadd.py`, `inbox_to_action/auth.py`, `inbox_to_action/web.py`, `inbox_to_action/dev.py`, `tests/test_quickadd.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: `process`, `Config`, `email_from_payload`, the store interface, `NotionStore`, `anthropic_call`
- Produces:
  - `parse_quick_add(text: str, now: datetime) -> Task`
  - `make_app(store, config: Config, ai_call, now_fn=lambda: datetime.now(tz), env=os.environ) -> WSGI callable`, and the module-level `app = make_app(NotionStore(...), Config.from_env(), anthropic_call)` created lazily on first request
  - HTTP contract:
    | Route | Request | Response |
    |---|---|---|
    | `POST /login` | `{"password"}` | 204 + `Set-Cookie: session=…; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000`, or 401 |
    | `POST /logout` | | 204, clears the cookie |
    | `GET /api/tasks` | | 200 `{"tasks": [TaskJSON]}` |
    | `POST /api/tasks` | `{"text"}` | 201 `TaskJSON` |
    | `PATCH /api/tasks/<page_id>` | `{"done"?: bool, "priority"?: "P1".."P4", "due"?: ISO\|null}` | 200 `TaskJSON` |
    | `POST /webhook/email` | header `X-Webhook-Secret`; body JSON payload, or multipart with a `payload` JSON field + optional `ics` file | 200 `{"created": n, "updated": n, "skipped": reason\|null}` |
  - `TaskJSON` has the keys `id, title, type, priority, reason, due, has_time, platform, join_link, phone, from, vip, status`. `id` is the Notion page id.
  - `/api/*` routes without a valid session give 401 `{"error": "login required"}`.

- [ ] **Step 1: Write failing tests:**
  - `test_quick_add`:
    | Input | Expected |
    |---|---|
    | `"Call Priya Fri 3pm p1"` | title `"Call Priya"`, due Nov 6 15:00, P1, type `Call`, platform `Phone` |
    | `"Draft memo"` | P3, no due, type `Added` |
    | `"Fix deck p1"` | due `date(2026,11,2)` |
    | `"Fix deck p2"` | due `date(2026,11,6)` |
    | `"Plan offsite tomorrow"` | due `date(2026,11,3)` |
  - `test_login_and_session`:
    - A wrong password gives 401.
    - The right password gives 204 and a cookie.
    - `GET /api/tasks` with that cookie gives 200, and without it 401.
    - A tampered cookie gives 401, and so does an expired one.
    - Passwords are compared with `hmac.compare_digest`.
  - `test_patch_done_priority_snooze`, against a `MemoryStore`:
    - `done: true` sets Status `Done`.
    - `priority: "P1"` sets P1 and `priority_locked`.
    - `due: null` clears due.
    - An unknown id gives 404.
    - A bad priority value gives 400.
  - `test_webhook_rejects_bad_secret`: a missing or wrong `X-Webhook-Secret` gives 401, and the store is unchanged.
  - `test_webhook_rejects_malformed_payload`: non-JSON, or JSON missing `message_id`, gives 400 and the store is unchanged. The response body never echoes the input.
  - `test_webhook_processes_payload`: the payload built from `11` with stub AI gives 200 `{"created": 2, …}`.
  - `test_webhook_multipart_ics`: the payload for `02` plus the ics as a multipart file creates a Meet call due Nov 4 13:00.
- [ ] **Step 2:** Run the tests. Expected: FAIL.
- [ ] **Step 3:** Implement:
  - `web.py` routes with a small dict of `(method, path-regex) -> handler`.
  - Multipart is parsed by feeding `Content-Type` + body to `email.parser.BytesParser`.
  - `auth.py` cookie format: `v1.<expiry_epoch>.<hex hmac_sha256(SESSION_SECRET, "v1.<expiry>")>`.
  - `dev.py` runs `wsgiref.simple_server` on `:8000`. It serves `public/*` for non-API GETs and uses `MemoryStore` pre-loaded from `samples/` when `NOTION_TOKEN` is unset.
- [ ] **Step 4:** Run `python3 -m pytest -q`. Expected: all pass.
- [ ] **Step 5:** Commit `feat: web app with login, task API and Zapier webhook (NFR-6)`.

### Task 10: Today page (Aurora)

**Files:**
- Create: `public/index.html`, `public/today.css`, `public/today.js`
- Reference (read only): `docs/superpowers/specs/assets/today-ui-mockup.html`. Copy the Aurora tokens and structure, and drop the picker plus the Quiet and Editorial styles.

**Interfaces:**
- Consumes: the HTTP contract and `TaskJSON` from Task 9

- [ ] **Step 1:** Build the page to match the mockup's Aurora variant:
  - **Header:** date, counts and the sync pill.
  - **Add box:** posts `{text}` to `/api/tasks`. The hint copy is "Understands today, tomorrow, mon–fri, a time like 3pm, and p1–p4. Enter to add."
  - **Timeline sections:** Today, Tomorrow, This week, Later, Someday.
  - **Rows:** checkbox, title, meta, Join/Call, P-label menu, and Snooze menu with "Later today / Tomorrow / Friday / Next week / Someday (no date)".
  - **Login:** when any API call returns 401, the timeline is replaced by a one-field password form that posts `/login`, then reloads.
- [ ] **Step 2:** Wire the sync behavior:
  - **Updates:** apply each change optimistically, then `PATCH`.
  - **Pill:** reads "Saving to Notion…" during a save, then "Synced with Notion · just now".
  - **Undo:** a toast with Undo stays for 5 s. Undo sends the inverse `PATCH`.
  - **Failures:** a failed request reverts the row and shows the toast "Couldn't save to Notion" with **Retry**.
  - **Polling:** re-fetch `GET /api/tasks` every 30 s while `document.visibilityState === "visible"` and on `visibilitychange` to visible.
- [ ] **Step 3:** Run `python3 -m inbox_to_action.dev` and open `http://localhost:8000`. Verify by hand in a browser:
  1. Log in with `APP_PASSWORD=dev`.
  2. Tick a task, then press Undo.
  3. Change priority from the P-label menu using the keyboard (Tab, Enter, arrows, Esc).
  4. Snooze a task to Next week and confirm it moves to Later.
  5. Add `Call Priya Fri 3pm p1` and confirm it lands under This week.
  6. Check that nothing clips at 320px width or 200% zoom.
  7. Turn on reduced motion and confirm the pulse stops.
  8. Stop the server mid-session, tick a task, and confirm the row reverts and the Retry toast appears (Review Focus 4).
- [ ] **Step 4:** Commit `feat: Aurora Today page with two-way sync`.

### Task 11: Deploy and connect Zapier

**Files:**
- Create: `api/index.py`, `vercel.json`
- Modify: `README.md`. Add a "Setup" section covering Notion integration + `notion setup`, the env vars, Vercel deploy, the Zap, and how to run `eval --live`. Update the "Project layout" section.

- [ ] **Step 1:** `api/index.py` is `from inbox_to_action.web import app`. `vercel.json` sets the Python runtime for `api/index.py` and rewrites `/api/(.*)`, `/login`, `/logout` and `/webhook/(.*)` to `/api/index`. `public/` is served as static output.
- [ ] **Step 2:** Run `npx vercel` (preview) from the repo root and set every env var from Global Constraints in the Vercel project. Verify:
  - `curl -i <preview>/` gives 200 HTML.
  - `curl -i <preview>/api/tasks` gives 401.
  - `curl -i -X POST <preview>/webhook/email` without the secret gives 401.
- [ ] **Step 3:** In Zapier, set up Gmail "New Email" (label filter per FR-1), then a Webhooks by Zapier POST to `<url>/webhook/email`. Use header `X-Webhook-Secret`, Payload Type JSON, and map Gmail fields to `message_id, thread_id, from, to, cc, subject, date, body`. If the Gmail step exposes the `.ics` attachment, switch to the form/multipart payload with `payload` + `ics` file. Record in README which option worked.
- [ ] **Step 4:** Send yourself a test email containing a Zoom link and a time. Within one Zapier polling interval, it appears in Notion and on the Today page. Tick it on the page and confirm Notion shows `Done`.
- [ ] **Step 5:** Run `python3 -m pytest -q && python3 -m inbox_to_action.eval samples/`. Expected: all pass.
- [ ] **Step 6:** Commit `feat: Vercel deploy and Zapier setup docs`. Ask the user before promoting to production.
