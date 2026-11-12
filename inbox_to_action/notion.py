"""Notion database as the task store. Same interface as MemoryStore.

    python3 -m inbox_to_action.notion setup <parent_page_id>    # creates the database, prints its id
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from email.utils import parseaddr

from .models import OPEN, Task

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"
OPTIONS = {
    "Priority": ["P1", "P2", "P3", "P4"],
    "Type": ["Action", "Call", "Assignment", "Approval", "Follow-up", "Deadline", "Added"],
    "Status": ["To do", "Review", "Done", "Cancelled"],
    "Platform": ["Zoom", "Meet", "Teams", "Webex", "Phone", "Other"],
}
SCHEMA = {
    "Task": {"title": {}}, "Due": {"date": {}}, "Join link": {"url": {}}, "From": {"rich_text": {}},
    "Source email": {"url": {}}, "Source item": {"url": {}}, "Why this priority": {"rich_text": {}},
    "Message ID": {"rich_text": {}}, "Priority locked": {"checkbox": {}}, "Snippet": {"rich_text": {}},
    **{name: {"select": {"options": [{"name": o} for o in opts]}} for name, opts in OPTIONS.items()},
}
PROPERTY_NAMES = list(SCHEMA)
# Task field -> Notion property, for partial updates.
FIELD_PROPS = {
    "title": "Task", "priority": "Priority", "type": "Type", "due": "Due", "status": "Status",
    "platform": "Platform", "join_link": "Join link", "phone": "Join link", "from_name": "From", "from_addr": "From",
    "source_email": "Source email", "source_item": "Source item", "reason": "Why this priority",
    "key": "Message ID", "uid": "Message ID", "priority_locked": "Priority locked", "snippet": "Snippet",
}


class NotionError(Exception):
    pass


def _text(s: str | None) -> list:
    return [{"type": "text", "text": {"content": s[:2000]}}] if s else []


def _plain(prop: dict) -> str:
    return "".join(r.get("plain_text", "") for r in prop.get(prop.get("type"), []) or [])


def _format_sender(name: str, addr: str) -> str:
    if not addr:
        return name
    if re.search(r'[,;:<>@()"\\]', name):  # quote so parseaddr reads "Okafor, Ben" back intact
        name = '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"{name} <{addr}>" if name else addr


def task_to_props(task: Task) -> dict:
    due = None
    if task.due is not None:
        due = {"start": task.due.isoformat() if isinstance(task.due, datetime) else task.due.isoformat()}
    # Phone calls keep their number as a tel: link so the page can dial it.
    link = task.join_link or (f"tel:{task.phone}" if task.phone else None)
    sender = _format_sender(task.from_name, task.from_addr)
    select = lambda v: {"select": {"name": v} if v else None}
    return {
        "Task": {"title": _text(task.title)},
        "Priority": select(task.priority),
        "Type": select(task.type),
        "Due": {"date": due},
        "Status": select(task.status),
        "Platform": select(task.platform),
        "Join link": {"url": link},
        "From": {"rich_text": _text(sender)},
        "Source email": {"url": task.source_email},
        "Source item": {"url": task.source_item},
        "Why this priority": {"rich_text": _text(task.reason)},
        "Message ID": {"rich_text": _text(task.key)},
        "Priority locked": {"checkbox": task.priority_locked},
        "Snippet": {"rich_text": _text(task.snippet)},
    }


def page_to_task(page: dict) -> Task:
    p = page["properties"]
    sel = lambda name: ((p.get(name) or {}).get("select") or {}).get("name")
    due = None
    if (d := (p.get("Due") or {}).get("date")) and d.get("start"):
        due = datetime.fromisoformat(d["start"]) if "T" in d["start"] else date.fromisoformat(d["start"])
    link = (p.get("Join link") or {}).get("url")
    name, addr = parseaddr(_plain(p.get("From", {})))
    key = _plain(p.get("Message ID", {}))
    return Task(
        title=_plain(p.get("Task", {})),
        type=sel("Type") or "Action",
        due=due,
        platform=sel("Platform"),
        join_link=None if (link or "").startswith("tel:") else link,
        phone=link[4:] if (link or "").startswith("tel:") else None,
        from_name=name if addr else _plain(p.get("From", {})),
        from_addr=addr.lower(),
        source_email=(p.get("Source email") or {}).get("url"),
        source_item=(p.get("Source item") or {}).get("url"),
        key=key,
        uid=parts[2] if key.startswith("uid:") and len(parts := key.split(":", 2)) == 3 else None,
        status=sel("Status") or "To do",
        priority=sel("Priority") or "P3",
        reason=_plain(p.get("Why this priority", {})),
        priority_locked=bool((p.get("Priority locked") or {}).get("checkbox")),
        snippet=_plain(p.get("Snippet", {})),
        page_id=page["id"],
    )


def urllib_http(token: str):
    def call(method: str, url: str, body: dict | None):
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": f"Bearer {token}", "Notion-Version": VERSION,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.load(resp), dict(resp.headers)
        except urllib.error.HTTPError as e:
            try:
                payload = json.load(e)
            except ValueError:
                payload = {}
            return e.code, payload, dict(e.headers or {})
    return call


class NotionStore:
    def __init__(self, token: str, database_id: str, http=None, sleep=time.sleep):
        self.db = database_id
        self.http = http or urllib_http(token)
        self.sleep = sleep

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        for attempt in range(4):
            status, payload, headers = self.http(method, API + path, body)
            if status < 300:
                return payload
            if status != 429 and status < 500:
                raise NotionError(f"Notion {status}: {payload.get('code', '')}")
            if attempt < 3:
                self.sleep(float(headers.get("Retry-After") or 2 ** attempt))
        raise NotionError(f"Notion {status} after retries")

    def _query(self, filter_: dict, page_size: int = 100, sort: bool = False, all_pages: bool = True) -> list[Task]:
        body, tasks = {"filter": filter_, "page_size": page_size}, []
        if sort:
            body["sorts"] = [{"property": "Due", "direction": "ascending"}]
        while True:
            res = self._req("POST", f"/databases/{self.db}/query", body)
            tasks += [page_to_task(p) for p in res.get("results", [])]
            if not (all_pages and res.get("has_more")):
                return tasks
            body = {**body, "start_cursor": res["next_cursor"]}

    def find(self, key: str) -> Task | None:
        hits = self._query({"property": "Message ID", "rich_text": {"equals": key}}, page_size=1, all_pages=False)
        return hits[0] if hits else None

    def has_prefix(self, prefix: str) -> bool:
        return bool(self._query({"property": "Message ID", "rich_text": {"starts_with": prefix}},
                                page_size=1, all_pages=False))

    def create(self, task: Task) -> Task:
        return page_to_task(self._req("POST", "/pages", {"parent": {"database_id": self.db},
                                                          "properties": task_to_props(task)}))

    def update(self, page_id: str, **fields) -> Task:
        full = task_to_props(Task(**{"title": "", "type": "Action", **fields}))
        props = {FIELD_PROPS[f]: full[FIELD_PROPS[f]] for f in fields if f in FIELD_PROPS}
        return page_to_task(self._req("PATCH", f"/pages/{page_id}", {"properties": props}))

    def list_open(self) -> list[Task]:
        return self._query({"or": [{"property": "Status", "select": {"equals": s}} for s in OPEN]}, sort=True)

    def get(self, page_id: str) -> Task:
        return page_to_task(self._req("GET", f"/pages/{page_id}"))


def setup_database(token: str, parent_page_id: str, http=None) -> str:
    store = NotionStore(token, "", http=http)
    res = store._req("POST", "/databases", {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": _text("Inbox to Action"),
        "properties": SCHEMA,
    })
    return res["id"]


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "setup":
        print(setup_database(os.environ["NOTION_TOKEN"], sys.argv[2]))
    else:
        sys.exit("usage: python3 -m inbox_to_action.notion setup <parent_page_id>")
