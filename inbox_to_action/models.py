from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Email:
    message_id: str
    subject: str
    from_name: str
    from_addr: str
    to: list[str]
    cc: list[str]
    date: datetime
    headers: dict[str, str]
    body: str
    ics: str | None
    thread_id: str | None = None


OPEN = ("To do", "Review")  # Review: suggested by AI from an outside sender, waiting for your OK


@dataclass
class Task:
    title: str
    type: str
    due: datetime | date | None = None
    platform: str | None = None
    join_link: str | None = None
    phone: str | None = None
    from_name: str = ""
    from_addr: str = ""
    source_email: str | None = None
    source_item: str | None = None
    message_id: str = ""
    key: str = ""
    uid: str | None = None
    sequence: int = 0
    status: str = "To do"
    priority: str = "P3"
    reason: str = ""
    priority_locked: bool = False
    snippet: str = ""
    page_id: str | None = None


@dataclass
class CallEvent:
    uid: str
    start: datetime
    end: datetime | None
    summary: str
    organizer: str
    method: str
    sequence: int
    location: str
