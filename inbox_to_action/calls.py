"""Turn a call or meeting invite into a Call task. Platforms are one table row each."""
import hashlib
import re
from datetime import datetime

from .dates import parse_datetime_text
from .ics import parse_ics
from .models import Email, Task

PLATFORMS: list[tuple[str, re.Pattern]] = [
    ("Zoom", re.compile(r"zoom\.us/(j|my)/", re.I)),
    ("Meet", re.compile(r"meet\.google\.com/", re.I)),
    ("Teams", re.compile(r"teams\.microsoft\.com/l/meetup-join", re.I)),
    ("Webex", re.compile(r"webex\.com/.+?(j\.php|/meet)", re.I)),
    ("Other", re.compile(r"meet\.goto\.com/|gotomeeting\.com/|whereby\.com/|chime\.aws/|slack\.com/.*huddle", re.I)),
]
_URL = re.compile(r"https?://[^\s<>\"')]+")
_PHONE = re.compile(r"\+?1?[\s.-]*\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}")
_DIAL_IN = re.compile(r"conference id|meeting id|access code|\bpin\b", re.I)
_TITLE_PREFIX = re.compile(r"^(invitation|updated invitation|canceled|cancelled|fwd|fw)\s*:\s*", re.I)


def _clean_title(s: str) -> str:
    while _TITLE_PREFIX.match(s):
        s = _TITLE_PREFIX.sub("", s, count=1)
    return re.sub(r"\s+@\s+.*$", "", s).strip()


def _phone(text: str) -> str | None:
    m = _PHONE.search(text)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    return "+" + (digits if len(digits) == 11 else "1" + digits)


def _short(value: str) -> str:
    # Keep keys well under Notion's 2000-char text limit so lookups always match what was stored.
    return value if len(value) <= 300 else "sha256-" + hashlib.sha256(value.encode()).hexdigest()


def _link_key(link: str | None, sender: str) -> str | None:
    # Without an .ics, a meeting-specific join link ties updates and cancels to the original task.
    # Personal rooms (zoom.us/my/…) are reused across meetings, so they never become a key.
    if not link or re.search(r"zoom\.us/my/", link, re.I):
        return None
    # Bound to the sender so another sender can't claim a meeting's link first.
    return f"link:{sender.lower()[:254]}:" + _short(link.split("?")[0].split("#")[0])


def detect_call(email: Email, now: datetime) -> Task | str | None:
    ev = parse_ics(email.ics, now.tzinfo) if email.ics else None
    platform, link = None, None
    for url in _URL.findall(email.body):
        for name, pattern in PLATFORMS:
            if pattern.search(url):
                platform, link = name, url
                break
        if platform:
            break
    phone = None
    if not platform:
        phone = _phone(email.body) if _DIAL_IN.search(email.body) else None
        if phone:
            platform = "Phone"
        elif ev:
            platform = "Other"
    if not platform:
        return None
    start = ev.start if ev else parse_datetime_text(email.body, now)
    if start is None:
        return None
    cancelled = bool(ev and ev.method == "CANCEL") or bool(re.match(r"(?i)\s*cancel+ed( event)?\s*:", email.subject))
    if start < now and not cancelled:
        return "invite already in the past"
    return Task(
        title=_clean_title(ev.summary if ev and ev.summary else email.subject),
        type="Call",
        due=start,
        platform=platform,
        join_link=link,
        phone=phone,
        from_name=email.from_name,
        from_addr=email.from_addr,
        message_id=email.message_id,
        key=f"uid:{email.from_addr.lower()[:254]}:{_short(ev.uid)}" if ev else _link_key(link, email.from_addr) or email.message_id,
        uid=ev.uid if ev else None,
        sequence=ev.sequence if ev else 0,
        status="Cancelled" if cancelled else "To do",
    )
