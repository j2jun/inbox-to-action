"""Parse the Today page's add box: 'Call Priya Fri 3pm p1'."""
import re
import uuid
from datetime import date, datetime, time, timedelta

from .models import Task

_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_PRIO = re.compile(r"\bp([1-4])\b", re.I)
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_DAY = re.compile(r"\b(today|tomorrow|mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:rs?|rsday)?|fri(?:day)?"
                  r"|sat(?:urday)?|sun(?:day)?)\b", re.I)


def _next_weekday(now: datetime, idx: int) -> date:
    return now.date() + timedelta(days=(idx - now.weekday()) % 7 or 7)


def parse_quick_add(text: str, now: datetime) -> Task:
    priority, when, at = None, None, None
    if m := _PRIO.search(text):
        priority, text = f"P{m.group(1)}", text[:m.start()] + text[m.end():]
    if m := _TIME.search(text):
        hour = int(m.group(1)) % 12 + (12 if m.group(3).lower() == "pm" else 0)
        if hour < 24 and int(m.group(2) or 0) < 60:
            at = time(hour, int(m.group(2) or 0))
        text = text[:m.start()] + text[m.end():]
    if m := _DAY.search(text):
        word = m.group(1).lower()
        when = (now.date() if word == "today" else now.date() + timedelta(days=1) if word == "tomorrow"
                else _next_weekday(now, _DAYS.index(word[:3])))
        text = text[:m.start()] + text[m.end():]
    title = re.sub(r"\s{2,}", " ", text).strip(" ,-")
    if not title:
        raise ValueError("empty task")
    if when is None and priority in ("P1", "P2"):
        when = now.date() if priority == "P1" else _next_weekday(now, 4) if now.weekday() != 4 else now.date()
    if when is None and at is not None:
        when = now.date()
    due = datetime.combine(when, at, now.tzinfo) if (when and at) else when
    is_call = at is not None and re.match(r"(?i)call\b", title)
    return Task(
        title=title,
        type="Call" if is_call else "Added",
        platform="Phone" if is_call else None,
        due=due,
        priority=priority or "P3",
        reason="Set by you" if priority else "P3: added by you",
        priority_locked=priority is not None,
        from_name="You",
        key=f"added-{uuid.uuid4().hex}",
    )
