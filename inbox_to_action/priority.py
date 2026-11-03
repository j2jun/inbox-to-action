"""Priority rules (samples/expected.json → priority_rules_assumed). Pure: same inputs, same answer."""
import re
from datetime import date, datetime, time, timedelta

from .models import Email, Task

_URGENT = re.compile(r"\b(ASAP|URGENT|blocker)\b", re.I)


def _as_datetime(due: date | datetime, now: datetime) -> datetime:
    # A date-only due counts from 00:00 local on that date.
    return due if isinstance(due, datetime) else datetime.combine(due, time(0), now.tzinfo)


def _level(task: Task, email: Email, now: datetime, user_email: str) -> tuple[str, str]:
    bulk = "list-unsubscribe" in email.headers or email.headers.get("precedence", "").lower() == "bulk"
    if not bulk and (m := _URGENT.search(f"{email.subject}\n{email.body}")):
        return "P1", f"P1: urgent wording ({m.group(1)})"
    user = user_email.lower()
    if user in email.cc and user not in email.to:
        return "P4", "P4: cc'd only"
    if task.due is None:
        return "P3", "P3: no due date"
    left = _as_datetime(task.due, now) - now
    if left <= timedelta(hours=24):
        return "P1", "P1: due in under 24h"
    if left <= timedelta(days=7):
        return "P2", "P2: due within 7 days"
    return "P3", "P3: due in more than 7 days"


def prioritize(task: Task, email: Email, now: datetime, user_email: str, vips: set[str]) -> tuple[str, str]:
    level, reason = _level(task, email, now, user_email)
    if email.from_addr.lower() in vips:
        reason += ", VIP sender"
    return level, reason
