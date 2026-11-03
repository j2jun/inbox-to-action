"""Pull dates and times out of email text. Each supported shape is one regex."""
import re
from datetime import date, datetime, time, timedelta

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DOW = r"(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\s+)?"

# "Nov 4, 2026 10:00 AM", "Tuesday, November 3, 2026, 8:00 - 8:45 AM", "… 1:00 PM - 2:00 PM"
_DATETIME = re.compile(
    _DOW + _MON + r"\s+(\d{1,2}),?\s+(\d{4}),?\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
    r"(?:\s*[-–]\s*\d{1,2}:\d{2}\s*(am|pm))?", re.I)
_FULL_DATE = re.compile(_DOW + _MON + r"\s+(\d{1,2}),?\s+(\d{4})", re.I)
_SLASH_DATE = re.compile(r"\b(\d{1,2})/([a-z]{3})/(\d{2})\b", re.I)
_SHORT_DATE = re.compile(r"\b" + _MON + r"\s+(\d{1,2})\b(?!\s*,?\s*\d{4})", re.I)
_AT_TIME = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)
_WEEKDAY = re.compile(r"\b(" + "|".join(WEEKDAYS) + r")\b", re.I)


def _month(s: str) -> int:
    return MONTHS[s[:3].lower()]


def _hour(h: str, meridiem: str | None) -> int:
    h = int(h) % 12 if meridiem else int(h)
    return h + 12 if meridiem and meridiem.lower() == "pm" else h


def _with_time(d: date, text: str, now: datetime) -> date | datetime:
    m = _AT_TIME.search(text)
    if not m:
        return d
    try:
        return datetime.combine(d, time(_hour(m.group(1), m.group(3)), int(m.group(2) or 0)), now.tzinfo)
    except ValueError:  # "13pm" and the like: keep the date, drop the time
        return d


def parse_datetime_text(text: str, now: datetime) -> datetime | None:
    """First explicit date + time in the text, in now's timezone."""
    m = next((m for m in _DATETIME.finditer(text) if m.group(5) or m.group(6)), None)  # need :MM or am/pm
    if not m:
        return None
    mon, day, year, hh, mm, ap1, ap2 = m.groups()
    try:
        return datetime(int(year), _month(mon), int(day), _hour(hh, ap1 or ap2), int(mm or 0), tzinfo=now.tzinfo)
    except ValueError:  # "Nov 31", "25:00"
        return None


def parse_due_text(text: str, now: datetime) -> date | datetime | None:
    """A due date from phrases like 'by Friday', 'Due: Nov 10', '03/Nov/26'. Datetime only if a time is given.
    Impossible dates ("Nov 31") give None rather than an exception."""
    try:
        return _parse_due(text, now)
    except ValueError:
        return None


def _parse_due(text: str, now: datetime) -> date | datetime | None:
    if m := _FULL_DATE.search(text):
        return _with_time(date(int(m.group(3)), _month(m.group(1)), int(m.group(2))), text, now)
    if m := _SLASH_DATE.search(text):
        return date(2000 + int(m.group(3)), _month(m.group(2)), int(m.group(1)))
    if m := _SHORT_DATE.search(text):
        d = date(now.year, _month(m.group(1)), int(m.group(2)))
        if d < now.date() - timedelta(days=60):
            d = d.replace(year=now.year + 1)
        return _with_time(d, text, now)
    if m := _WEEKDAY.search(text):
        ahead = (WEEKDAYS.index(m.group(1).lower()) - now.weekday()) % 7 or 7
        return _with_time(now.date() + timedelta(days=ahead), text, now)
    if re.search(r"\btomorrow\b", text, re.I):
        return _with_time(now.date() + timedelta(days=1), text, now)
    if re.search(r"\b(today|EOD)\b", text, re.I):
        return _with_time(now.date(), text, now)
    return None
