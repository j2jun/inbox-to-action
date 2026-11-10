"""TickTick list as the task store. Same interface as NotionStore and MemoryStore.

    TICKTICK_CLIENT_ID=… TICKTICK_CLIENT_SECRET=… python3 -m inbox_to_action.ticktick auth

Opens TickTick's consent page, catches the redirect on localhost, creates an "Inbox to Action" list,
and saves TICKTICK_TOKEN + TICKTICK_PROJECT_ID to .env.local (never printed).
Register http://localhost:8765/callback as the redirect URI at developer.ticktick.com.
"""
import base64
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, replace
from datetime import date, datetime, time as dtime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import Task

API = "https://api.ticktick.com/open/v1"
AUTH_URL = "https://ticktick.com/oauth/authorize"
TOKEN_URL = "https://ticktick.com/oauth/token"
REDIRECT = "http://localhost:8765/callback"
SCOPES = "tasks:read tasks:write"
TO_TT = {"P1": 5, "P2": 3, "P3": 1, "P4": 0}
FROM_TT = {v: k for k, v in TO_TT.items()}
MARK = "\n\n— i2a:"  # everything after this line is our metadata; edit the note above it freely
META = ("type", "platform", "join_link", "phone", "from_name", "from_addr", "source_email", "source_item",
        "message_id", "key", "uid", "sequence", "status", "reason", "priority_locked", "snippet")


class TickTickError(Exception):
    pass


def _fmt(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def _parse(s: str) -> datetime:
    return datetime.strptime(s.replace(".000", ""), "%Y-%m-%dT%H:%M:%S%z")  # TickTick returns ".000+0000"


def _note(task: Task) -> str:
    """Human-readable note on top, machine metadata below MARK."""
    sender = f"{task.from_name} <{task.from_addr}>" if task.from_addr else task.from_name
    lines = [task.join_link or (f"tel:{task.phone}" if task.phone else ""), f"From: {sender}" if sender else "",
             task.reason, task.snippet, task.source_email or "", task.source_item or ""]
    meta = {k: v for k, v in asdict(task).items() if k in META and v not in (None, "", 0, False)}
    return "\n".join(l for l in lines if l) + MARK + json.dumps(meta, separators=(",", ":"))


def task_to_json(task: Task, project_id: str, tz: ZoneInfo) -> dict:
    body = {"projectId": project_id, "title": task.title, "content": _note(task),
            "priority": TO_TT.get(task.priority, 1), "timeZone": str(tz)}
    if task.page_id and not task.page_id.startswith("mem-"):
        body["id"] = task.page_id
    if isinstance(task.due, datetime):
        body.update(dueDate=_fmt(task.due), startDate=_fmt(task.due), isAllDay=False)
    elif task.due is not None:  # all-day: midnight in the user's zone
        midnight = _fmt(datetime.combine(task.due, dtime(), tz))
        body.update(dueDate=midnight, startDate=midnight, isAllDay=True)
    return body


def json_to_task(t: dict, tz: ZoneInfo) -> Task:
    content = t.get("content") or ""
    meta = {}
    if MARK in content:
        try:
            meta = json.loads(content.rsplit(MARK, 1)[1])
        except ValueError:
            meta = {}  # metadata hand-edited in TickTick; keep the task, lose the extras
    meta = {k: v for k, v in meta.items() if k in META}
    due = None
    if t.get("dueDate"):
        d = _parse(t["dueDate"]).astimezone(tz)
        due = d.date() if t.get("isAllDay") else d
    status = meta.pop("status", "To do")
    if t.get("status", 0) == 2:
        status = "Cancelled" if status == "Cancelled" else "Done"
    else:
        status = "To do"
    return Task(title=t.get("title", ""), type=meta.pop("type", "Added"), due=due, status=status,
                priority=FROM_TT.get(t.get("priority", 0), "P4"), page_id=t["id"], **meta)


def urllib_http(token: str):
    def call(method: str, url: str, body: dict | None):
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}, dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, {}, dict(e.headers or {})
    return call


class TickTickStore:
    def __init__(self, token: str, project_id: str, tz: ZoneInfo, http=None, sleep=time.sleep):
        self.pid, self.tz = project_id, tz
        self.http = http or urllib_http(token)
        self.sleep = sleep

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        for attempt in range(4):
            status, payload, headers = self.http(method, API + path, body)
            if status < 300:
                return payload
            if status != 429 and status < 500:
                raise TickTickError(f"TickTick {status}")
            if attempt < 3:
                self.sleep(float(headers.get("Retry-After") or 2 ** attempt))
        raise TickTickError(f"TickTick {status} after retries")

    def _open(self) -> list[Task]:
        # ponytail: one GET of the whole list per lookup; fine for a personal list of a few hundred open tasks.
        # /data returns open tasks only, so a key whose task is already done isn't found (an invite update for a
        # finished call becomes a new task). Add the completed-tasks endpoint if that shows up in practice.
        return [json_to_task(t, self.tz) for t in self._req("GET", f"/project/{self.pid}/data").get("tasks", [])]

    def find(self, key: str) -> Task | None:
        return next((t for t in self._open() if t.key == key), None)

    def has_prefix(self, prefix: str) -> bool:
        return any(t.key.startswith(prefix) for t in self._open())

    def create(self, task: Task) -> Task:
        created = json_to_task(self._req("POST", "/task", task_to_json(replace(task, page_id=None), self.pid, self.tz)),
                               self.tz)
        if task.status != "To do":
            self._req("POST", f"/project/{self.pid}/task/{created.page_id}/complete")
            created = replace(created, status=task.status)
        return created

    def get(self, page_id: str) -> Task:
        return json_to_task(self._req("GET", f"/project/{self.pid}/task/{page_id}"), self.tz)

    def update(self, page_id: str, **fields) -> Task:
        current = self.get(page_id)
        task = replace(current, **fields)
        body = task_to_json(task, self.pid, self.tz)
        if task.status == "To do" and current.status != "To do":
            body["status"] = 0  # reopen (Undo on the Today page)
        self._req("POST", f"/task/{page_id}", body)
        if task.status != "To do" and current.status == "To do":
            self._req("POST", f"/project/{self.pid}/task/{page_id}/complete")
        return task

    def list_open(self) -> list[Task]:
        far = datetime.max.replace(tzinfo=timezone.utc)
        key = lambda t: (t.due is None, t.due if isinstance(t.due, datetime)
                         else datetime.combine(t.due, dtime(), self.tz) if t.due else far)
        return sorted(self._open(), key=key)


# ---- one-time OAuth, run in your own terminal ----

def exchange_code(code: str, client_id: str, client_secret: str, post=None) -> str:
    form = urllib.parse.urlencode({"code": code, "grant_type": "authorization_code", "scope": SCOPES,
                                   "redirect_uri": REDIRECT}).encode()
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    req = urllib.request.Request(TOKEN_URL, data=form, method="POST", headers={
        "Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"})
    with (post or urllib.request.urlopen)(req, timeout=15) as resp:
        token = json.load(resp).get("access_token")
    if not token:
        raise TickTickError("TickTick returned no access token")
    return token


def save_env(path: Path, values: dict[str, str]) -> None:
    lines = [l for l in (path.read_text().splitlines() if path.exists() else [])
             if l.split("=", 1)[0] not in values]
    path.write_text("\n".join(lines + [f"{k}={v}" for k, v in values.items()]) + "\n")
    path.chmod(0o600)


def _wait_for_code(state: str) -> str:
    got = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state") == [state] and "code" in q
            if ok:
                got["code"] = q["code"][0]
            self.send_response(200 if ok else 400)
            self.end_headers()
            self.wfile.write(b"Connected. You can close this tab." if ok else b"State mismatch, run auth again.")

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 8765), Handler)
    while "code" not in got:
        server.handle_request()
    return got["code"]


def auth() -> None:
    import webbrowser
    cid, secret = os.environ.get("TICKTICK_CLIENT_ID"), os.environ.get("TICKTICK_CLIENT_SECRET")
    if not cid or not secret:
        sys.exit("Set TICKTICK_CLIENT_ID and TICKTICK_CLIENT_SECRET (from developer.ticktick.com → Manage Apps).")
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({"client_id": cid, "scope": SCOPES, "state": state,
                                                   "redirect_uri": REDIRECT, "response_type": "code"})
    print("Opening TickTick to approve access…\n" + url)
    webbrowser.open(url)
    token = exchange_code(_wait_for_code(state), cid, secret)
    store = TickTickStore(token, "", ZoneInfo("UTC"))
    project = next((p for p in store._req("GET", "/project") if p.get("name") == "Inbox to Action"), None)
    project = project or store._req("POST", "/project", {"name": "Inbox to Action", "kind": "TASK"})
    save_env(Path(".env.local"), {"TICKTICK_TOKEN": token, "TICKTICK_PROJECT_ID": project["id"]})
    print('Saved TICKTICK_TOKEN and TICKTICK_PROJECT_ID to .env.local. Tasks go to the "Inbox to Action" list.')


if __name__ == "__main__":
    if sys.argv[1:] == ["auth"]:
        auth()
    else:
        sys.exit("usage: python3 -m inbox_to_action.ticktick auth")
