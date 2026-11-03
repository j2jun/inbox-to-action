# inbox-to-action: design

Date: 2026-11-02 · Status: draft for approval

## Intent

Inbox triage eats time that should go to strategy. This turns every task-related email (requests, assignments, approvals, deadlines, follow-ups and call invites from any platform) into one prioritized to-do list. The user works that list from a single page that looks good enough to open first thing every morning.

Success: the user stops reading the inbox to find work. They open the Today page, see what matters first, join calls from it, and trust that nothing task-like was missed.

## Goals

1. Every task-related email becomes a task in one Notion database: README FR-1 to FR-13 and the "Task-related emails" section.
2. Every call invite (Zoom, Meet, Teams, Webex, GoTo, phone/dial-in, any `.ics`) becomes a call task with time, platform and join link. Updates and cancellations change the existing task.
3. Every task has a priority P1–P4 and a one-line reason.
4. A custom **Today page** in the chosen **Aurora** style shows the list as one connected timeline and syncs both ways with Notion.
5. From the Today page the user can: tick done (with undo), change priority, snooze, add a task, and join or dial a call.
6. Accuracy is measured offline against `samples/*.eml` + `samples/expected.json` (NFR-2).

## Non-goals

- Replying to email or accepting invites.
- Writing to Google or Outlook calendars.
- Email providers other than Gmail.
- More than one user, teams or sharing.
- Replacing Notion's own views. The **Calls** calendar and **All tasks** table stay as Notion views *(my call)*.
- Real-time push updates. Polling is enough (see Sync).
- A daily digest email.

## Anti-goals (failure even if it "works")

- A task-tool notification (Jira, Asana, GitHub, DocuSign…) gets skipped as "automated".
- Duplicate tasks for the same email or invite, or a moved or cancelled meeting left at its old time.
- Meeting times off by hours because of time zones.
- Full email bodies stored in Notion or logs, or secrets visible in the browser.
- The page goes back to looking boxed in and segmented. It must read as one surface.
- Ticking a task on the page silently fails to reach Notion.

## Constraints

- Tools: Gmail, Zapier, Notion, Python (user's choice).
- Zapier only triggers. It sends each new Gmail message to a webhook. Its polling interval (1–15 min by plan) bounds latency.
- Python 3.12, standard library first. Project conventions are in `CLAUDE.md` and `.claude/rules/` (python-style, testing, privacy).
- Notion API is rate-limited (about 3 requests/second). One user polling every 30 s is well within it.
- Sample emails use the fixed clock and context in `samples/expected.json` (`now = 2026-11-02T09:30-08:00`, `America/Los_Angeles`).

## How: decided

### Architecture

```
Gmail ─► Zapier ─(POST /webhook/email, shared secret)─► Python app ─► Notion DB
                                                          ▲
Today page (browser) ─(GET/POST/PATCH /api/tasks, login)──┘
```

One Python app, deployed once, serves three things: the Zapier webhook, a small task API for the page, and the page itself.

### Email pipeline (order matters)

1. **Skip:** `List-Unsubscribe` / `Precedence: bulk` headers, receipts, invites already past. Log the reason. Task-tool senders are never skipped.
2. **Calls:** read the `text/calendar` part first (time, organizer, `UID`, `SEQUENCE`, `METHOD:CANCEL`). Fall back to link and dial-in patterns. Platforms live in one pattern table.
3. **Task tools:** sender and format patterns in one table (Jira, Asana, GitHub, Expensify, DocuSign, Google Docs comments…). Extract the title, due date and source link.
4. **AI extraction:** only for human-written emails that steps 1–3 didn't handle. Claude via the Anthropic API (confirmed by user) returns structured fields: zero or more items, each with title, due, requester and type. It only sees subject plus trimmed body.
5. **Priority:** a pure function returning `(priority, reason)`. The rules are in `samples/expected.json` → `priority_rules_assumed` (P1 within 24h or urgency words, P2 within 7 days, P3 later or no date, P4 cc'd only). The VIP list is config.
6. **Dedupe/upsert:** the key is the iCal `UID` for calls and the Gmail `Message-ID` otherwise. Updates change due/time, and cancel sets Status = Cancelled.

### Notion database

The properties in README FR-12, plus:
- `Type` adds **Added** (created on the page).
- `Priority locked` (checkbox): set when the user changes priority by hand. Later email updates never overwrite a locked priority *(my call)*.

### Today page (Aurora)

The reference mockup is `docs/superpowers/specs/assets/today-ui-mockup.html` (`?__variant=aurora`). It's the agreed look and behavior; the Quiet and Editorial styles in it are not to be built.

- **One connected surface:** a single vertical gradient thread through the sections Today / Tomorrow / This week / Later / Someday. No cards or columns. Tasks are dots and calls are diamonds, colored by priority, and the color always comes with a P-label.
- **Header:** large gradient date, counts (open · urgent · calls this week) and a sync status pill.
- **Add box:** natural-language input understanding today, tomorrow, mon–fri, a time like `3pm` and `p1`–`p4`. Defaults: P3 with no date. A P1 with no date goes to today; a P2 with no date goes to Friday.
- **Row:** checkbox and title. Meta line with time, platform/type and sender (★ for VIP). Actions are Join/Call (calls only), the P-label (opens a priority menu) and Snooze (later today / tomorrow / Friday / next week / someday). Snooze shows on hover and focus, and always on touch screens.
- **Feedback:** every change shows "Saving to Notion…", then a toast with **Undo** for 5 s. A failed save reverts the row and says so, with Retry.
- **Style:** near-black background with violet and cyan glow, Geist and Geist Mono (times), violet gradient Join buttons, frosted-glass menus and toast. Pulse and arrival highlight only run under `prefers-reduced-motion: no-preference`.
- **Must hold:** keyboard reaches everything (menus use arrow keys and Esc), visible focus, no clipping at 320px or 200% zoom, text contrast passes WCAG AA.

### Sync

- Notion is the single source of truth, and the app keeps no task store of its own.
- Page → Notion: each action is one API call. The UI updates right away and rolls back on failure.
- Notion → page: refetch every 30 s while visible and on tab focus *(my call)*.
- Conflicts: last write wins *(my call)*.

### Security

- Notion token, AI key and webhook secret are server-side env vars only (`.claude/rules/privacy.md`).
- The webhook rejects requests without the shared secret.
- The page and the API need a login. It's single-user: one password, then a signed, HTTP-only session cookie that keeps each device signed in (confirmed by user).

### Hosting

A Vercel Python function on the user's Vercel account that serves the webhook, the API and the static page from one deploy (confirmed by user).

## Left to the builder

- Web framework, if any (the smallest thing the host supports), and the file layout under `inbox_to_action/`.
- Whether the page is plain HTML/JS or a small build. Plain is preferred if it reproduces the mockup.
- The exact Claude model and prompt, within a per-email token budget the builder sets and documents (NFR-8).
- The AI stub format for offline tests.
- Retry and backoff details, and log format (NFR-3, NFR-11).
- How the Zapier Zap is documented. Setup steps go in README.
