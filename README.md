# inbox-to-action

Turns emails into a prioritized to-do list in Notion, so less time goes to inbox triage and more goes to strategy.

**Stack:** Gmail → Zapier → Python → Notion

```
Gmail (new email) ──► Zapier trigger ──► Python (classify, extract, prioritize) ──► Notion task database
```

> Status: built and tested locally (133 tests, offline eval 100%). Deployment needs your accounts: see **Setup**.

---

## What it does

1. Reads incoming emails, or sample emails when testing locally.
2. Pulls out action items: what needs doing, by when, and for whom.
3. Detects call and meeting invites (Zoom, Google Meet, Microsoft Teams, Webex, phone calls, and others) and turns each one into a task.
4. Gives every task a priority.
5. Writes the tasks to a Notion database, with a link back to the original email.

---

## Functional requirements

### Email intake
- **FR-1** Trigger on new Gmail messages that match a configurable label or search query (for example `label:inbox -category:promotions`).
- **FR-2** Run locally against a folder of sample emails (`.eml` or JSON) with no Gmail or Notion connection (dry-run mode).
- **FR-3** Skip non-actionable mail such as newsletters, marketing, and receipts, and log why each one was skipped. Automated mail is **not** skipped if it is task-related (see FR-4a).

### Task-related emails
- **FR-4a** Any email that asks for, assigns, or tracks work becomes a task, including:
  | Kind | Examples |
  |---|---|
  | Direct requests | "Can you send…", "Please review…", "Need your input by…" |
  | Assignments from task tools | Asana, Jira, Trello, Linear, Monday, ClickUp, GitHub/GitLab "assigned to you" / "review requested" |
  | Approvals and sign-offs | Expense, PTO, doc approval, DocuSign / e-signature requests |
  | Deadlines and reminders | "Due tomorrow", "Reminder:", form or submission deadlines |
  | Follow-ups | "Following up on…", "Any update?", replies awaiting your answer |
  | Shared docs with an ask | Google Docs/Sheets/Slides or Office comment mentions and "assigned you" notifications |
- **FR-4b** Notifications from task tools link to the original item (Jira ticket, Asana task, PR) as well as the email, and reuse that item's due date when the notification includes one.

### Action-item extraction
- **FR-4** Pull zero or more action items from each email. Each item has a title, a due date if one is stated, the requester, and a short context snippet.
- **FR-5** Convert relative deadlines ("by Friday", "EOD tomorrow") into absolute dates in the user's timezone.

### Call and meeting invites
- **FR-6** Detect a call invite from any of these signals:
  | Platform | Signals |
  |---|---|
  | Zoom | `zoom.us/j/`, `zoom.us/my/`, "Join Zoom Meeting" |
  | Google Meet | `meet.google.com/`, Google Calendar "Invitation:" emails |
  | Microsoft Teams | `teams.microsoft.com/l/meetup-join`, "Join Microsoft Teams Meeting" |
  | Webex | `webex.com/meet`, `*.webex.com/*/j.php` |
  | Others | Slack huddle links, GoTo/GoToMeeting, Whereby, Around, Chime |
  | Phone | Dial-in numbers plus a meeting ID or passcode, or "call me at …" with a time |
  | Any calendar invite | `.ics` attachment or `text/calendar` MIME part |
- **FR-7** For each invite, create a task titled `Call: <meeting title>` with:
  - the start time as the due date and time, plus the end time if known
  - the platform (Zoom, Meet, Teams, Webex, Phone, Other)
  - the join link, or the dial-in number and passcode
  - the organizer
- **FR-8** When an invite is updated or cancelled (same iCal `UID`, or the same thread with "Updated invitation" or "Canceled"), update the existing task instead of creating a duplicate. A cancellation marks the task **Cancelled**.
- **FR-9** Ignore invites that are already in the past when processed.

### Prioritization
- **FR-10** Give every task a priority of **P1 (urgent)**, **P2 (this week)**, **P3 (later)**, or **P4 (FYI)**, based on:
  - how close the deadline or meeting time is
  - sender importance (a configurable VIP list)
  - urgency language ("ASAP", "blocker", "today")
  - whether the user is asked directly or only CC'd
- **FR-11** Store a one-line reason with each priority, for example "P1: meeting in 3h, sender on VIP list".

### Notion output
- **FR-12** Write every task (actions, assignments, approvals, follow-ups, and calls) to **one** Notion database, so it works as a single to-do list. Filtered views such as "Calls today" or "P1 only" are Notion views, not separate databases. Properties:
  | Property | Type |
  |---|---|
  | Task | Title |
  | Priority | Select (P1–P4) |
  | Type | Select (Action, Call, Assignment, Approval, Follow-up, Deadline) |
  | Due | Date (with time for calls) |
  | Status | Status (To do, Done, Cancelled) |
  | Platform | Select (Zoom, Meet, Teams, Webex, Phone, Other) |
  | Join link | URL |
  | From | Text |
  | Source email | URL (Gmail permalink) |
| Source item | URL (Jira / Asana / PR link, if any) |
  | Why this priority | Text |
  | Message ID | Text (used for deduplication) |
- **FR-13** Never create duplicates. The same Gmail message ID, or the same iCal UID for calls, updates the existing task.

---

## Non-functional requirements

| ID | Area | Requirement |
|---|---|---|
| NFR-1 | Latency | A task shows up in Notion within one Zapier polling interval of the email arriving: 1–15 minutes, depending on the Zapier plan. |
| NFR-2 | Accuracy | Call-invite detection catches at least 95% of invites in the sample set. Action-item extraction misses or invents no more than 10% of items. Both are measured on a labeled set of sample emails. |
| NFR-3 | Reliability | No email is silently dropped. Failures are retried, then logged with the message ID so they can be replayed. |
| NFR-4 | Idempotency | Processing the same email twice gives the same Notion state. |
| NFR-5 | Privacy | Only the subject, sender, a short snippet, and the extracted fields go to Notion. Full email bodies are never stored. Any external AI call receives only the minimum text it needs. |
| NFR-6 | Security | API keys (Notion, any AI provider) live in environment variables or Zapier secrets, never in the repo. |
| NFR-7 | Time zones | All dates are stored with an explicit timezone. Invite times come from the `.ics` timezone, not from guesses in the email body. |
| NFR-8 | Cost | Usage stays within the Zapier plan's monthly task limit and a set AI-token budget per email. |
| NFR-9 | Testability | Every rule can be tested offline against sample emails, with no network calls. |
| NFR-10 | Maintainability | A single small Python package. Adding a new call platform means adding one pattern, not writing new code paths. |
| NFR-11 | Observability | Each run logs: emails seen, skipped (with reason), tasks created or updated, and errors. |

---

## Out of scope for v1

- Sending replies or accepting invites on the user's behalf
- Writing to Google Calendar or Outlook calendars (Notion is the single to-do list)
- Email providers other than Gmail
- A daily digest email (possible later)

---

## Try it locally (no accounts needed)

```bash
python3 -m pytest -q                       # 133 tests, no network
python3 -m inbox_to_action.eval samples/   # accuracy vs samples/expected.json
python3 -m inbox_to_action.dev             # Today page on http://localhost:8000, password: inbox-to-action-dev
```

## Setup

### Quick setup (recommended)
After `npx vercel login`, run **`python3 -m inbox_to_action.setup`** in your own terminal. It asks for each value with hidden input, creates the Notion database and saves every setting to Vercel. The manual steps below do the same thing.

### 1. Notion
1. Create an integration at notion.so/my-integrations and copy its token.
2. Share an empty Notion page with the integration and copy the page's id from its URL.
3. Create the database: `NOTION_TOKEN=… python3 -m inbox_to_action.notion setup <page_id>`. It prints the database id.
4. In Notion, add a **Calls** calendar view (filter Type = Call) and an **All tasks** table view.

### 2. Environment variables (Vercel project settings)
| Name | Value |
|---|---|
| `NOTION_TOKEN` | integration token |
| `NOTION_DATABASE_ID` | printed by step 1.3 |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `WEBHOOK_SECRET` | long random string (also used in Zapier) |
| `APP_PASSWORD` | your Today page password, 12+ characters |
| `SESSION_SECRET` | long random string (`python3 -c "import secrets;print(secrets.token_hex(32))"`) |
| `USER_EMAIL` | your Gmail address |
| `VIP_EMAILS` | comma-separated important senders |
| `TIMEZONE` | e.g. `America/Los_Angeles` |

### 3. Deploy
`npx vercel` for a preview, then `npx vercel --prod`. The function lives in `api/index.py`, and the page is served from `public/`.

### 4. Zapier
1. Trigger: **Gmail → New Email** (or *New Email Matching Search*, e.g. `label:inbox -category:promotions`).
2. Action: **Webhooks by Zapier → POST** to `https://<your-app>/webhook/email`.
   - Header `X-Webhook-Secret: <WEBHOOK_SECRET>`.
   - Payload type **JSON**, data: `message_id`, `thread_id`, `from`, `to`, `cc`, `subject`, `date`, `body` mapped from the Gmail fields (`body` = plain body).
   - Optional: `list_unsubscribe` and `precedence` if your Gmail step exposes those headers.
   - Calendar attachments: if the Gmail step exposes the `.ics` file, use payload type **Form** with a `payload` field (the JSON above) and a file field named `ics`. Without it, calls are still detected from the join link and the time in the email text.
3. Test with an email containing a Zoom link and a time. It should appear in Notion and on the Today page.

### Live accuracy check
`ANTHROPIC_API_KEY=… python3 -m inbox_to_action.eval samples/ --live` runs the samples against the real model.

## Project layout

```
inbox_to_action/   pipeline (parse, dates, ics, skip, calls, tools, priority, ai, pipeline),
                   storage (store, notion), web (auth, quickadd, web, dev), eval
api/index.py       Vercel entry point
public/            Today page (Aurora)
samples/           sample emails + expected.json answer key
tests/             pytest suite + recorded AI responses
docs/superpowers/  design spec, mockup and build plan
```
