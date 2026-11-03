"""Notifications from task tools (Jira, Asana, GitHub, …). One table row per tool."""
import re
from datetime import datetime

from .dates import parse_due_text
from .models import Email, Task

_DUE_LINE = re.compile(r"^.*\b(due|by|expires?|deadline)\b.*$", re.I | re.M)


def _after(pattern: str, text: str, default: str) -> str:
    m = re.search(pattern, text, re.I | re.S)
    return m.group(1).strip() if m else default


def _jira(e: Email) -> str:
    m = re.search(r"\(([A-Z][A-Z0-9]+-\d+)\)\s*(.+?)(?:\s+-\s+assigned to you)?$", e.subject)
    return f"{m.group(1)} {m.group(2)}" if m else e.subject


def _github(e: Email) -> str:
    m = re.search(r"\]\s*(.+?)\s*\((?:PR|Issue) #(\d+)\)", e.subject)
    if not m:
        return e.subject
    verb = "Review PR" if e.headers.get("x-github-reason") == "review_requested" else "Respond on #"
    return f"{verb} #{m.group(2)}: {m.group(1)}" if verb == "Review PR" else f"Respond on #{m.group(2)}: {m.group(1)}"


def _docusign(e: Email) -> str:
    name = _after(r"DocuSign:\s*(.+)$", e.subject, e.subject)
    return "Sign " + re.sub(r"\.(pdf|docx?)$", "", name, flags=re.I).replace("_", " ")


# sender: regex on the From address; when: extra condition; due: read a due date from the body
TASK_TOOLS: list[dict] = [
    {"name": "Jira", "sender": r"@[\w.-]*\.atlassian\.net$", "type": "Assignment", "title": _jira,
     "link": r"https://[\w-]+\.atlassian\.net/browse/[A-Z][A-Z0-9]+-\d+\b", "due": True},
    {"name": "Asana", "sender": r"@asana\.com$", "type": "Assignment",
     "title": lambda e: _after(r"assigned a task to you\s*\n+\s*([^\n]+)", e.body, e.subject),
     "link": r"https://app\.asana\.com/[^\s<>\"]+", "due": True},
    {"name": "GitHub", "sender": r"^notifications@github\.com$", "type": "Assignment", "title": _github,
     "link": r"https://github\.com/[\w.-]+/[\w.-]+/(?:pull|issues)/\d+", "due": False,
     "when": lambda e: e.headers.get("x-github-reason") in {"review_requested", "assign", "mention"}},
    {"name": "Expensify", "sender": r"@expensify\.com$", "type": "Approval",
     "title": lambda e: "Approve expense report: " + _after(r"approval:\s*(.+)$", e.subject, e.subject),
     "link": r"https://www\.expensify\.com/report[^\s<>\"]*", "due": True},
    {"name": "DocuSign", "sender": r"@docusign\.net$", "type": "Approval", "title": _docusign,
     "link": None, "due": True},
    {"name": "Google Docs", "sender": r"^comments-noreply@docs\.google\.com$", "type": "Action",
     "title": lambda e: re.sub(r'^(.+?) mentioned you in "(.+)"$', r"Reply to \1 in \2", e.subject),
     "link": r"https://docs\.google\.com/[^\s<>\"]+", "due": False},
    {"name": "Trello", "sender": r"@trello\.com$", "type": "Assignment", "title": lambda e: e.subject, "link": r"https://trello\.com/c/[^\s<>\"]+", "due": True},
    {"name": "Linear", "sender": r"@linear\.app$", "type": "Assignment", "title": lambda e: e.subject, "link": r"https://linear\.app/[^\s<>\"]+", "due": True},
    {"name": "Monday", "sender": r"@monday\.com$", "type": "Assignment", "title": lambda e: e.subject, "link": r"https://[\w-]+\.monday\.com/[^\s<>\"]+", "due": True},
    {"name": "ClickUp", "sender": r"@(tasks\.)?clickup\.com$", "type": "Assignment", "title": lambda e: e.subject, "link": r"https://app\.clickup\.com/[^\s<>\"]+", "due": True},
]


def detect_tool_task(email: Email, now: datetime) -> list[Task] | None:
    for row in TASK_TOOLS:
        if not re.search(row["sender"], email.from_addr, re.I):
            continue
        if "when" in row and not row["when"](email):
            continue
        # Links must start at a URL boundary so "https://evil.example/x.atlassian.net/..." can't match mid-string.
        link = re.search(r"(?<![\w./-])" + row["link"], email.body) if row["link"] else None
        due_line = _DUE_LINE.search(email.body) if row["due"] else None
        return [Task(
            title=row["title"](email),
            type=row["type"],
            due=parse_due_text(due_line.group(0), now) if due_line else None,
            source_item=link.group(0) if link else None,
            from_name=email.from_name,
            from_addr=email.from_addr,
            message_id=email.message_id,
            key=email.message_id + "#0",
        )]
    return None
