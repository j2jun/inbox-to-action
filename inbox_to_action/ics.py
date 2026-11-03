"""Minimal iCalendar (VEVENT) reader: just the fields a call task needs."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import CallEvent


# Outlook/Exchange invites use Windows zone names. ponytail: common zones only; unknown names fall back to the user's tz.
WINDOWS_TZ = {
    "Pacific Standard Time": "America/Los_Angeles", "Mountain Standard Time": "America/Denver",
    "Central Standard Time": "America/Chicago", "Eastern Standard Time": "America/New_York",
    "GMT Standard Time": "Europe/London", "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris", "India Standard Time": "Asia/Kolkata",
    "China Standard Time": "Asia/Shanghai", "Tokyo Standard Time": "Asia/Tokyo",
    "Korea Standard Time": "Asia/Seoul", "AUS Eastern Standard Time": "Australia/Sydney",
    "UTC": "UTC",
}


def _zone(name: str, fallback: ZoneInfo) -> ZoneInfo:
    try:
        return ZoneInfo(WINDOWS_TZ.get(name, name))
    except (ZoneInfoNotFoundError, ValueError):
        return fallback


def _dt(prop: str, value: str, tz: ZoneInfo) -> datetime:
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(tz)
    m = re.search(r"TZID=([^;:]+)", prop)
    zone = _zone(m.group(1).strip('"'), tz) if m else tz
    fmt = "%Y%m%dT%H%M%S" if "T" in value else "%Y%m%d"
    return datetime.strptime(value, fmt).replace(tzinfo=zone).astimezone(tz)


def parse_ics(text: str, tz: ZoneInfo) -> CallEvent | None:
    text = re.sub(r"\r?\n[ \t]", "", text or "")  # unfold continuation lines
    props: dict[str, tuple[str, str]] = {}
    method = ""
    in_event = False
    for line in text.splitlines():
        if line == "BEGIN:VEVENT":
            in_event = True
        elif line == "END:VEVENT":
            break
        elif ":" in line:
            prop, value = line.split(":", 1)
            name = prop.split(";", 1)[0].upper()
            if name == "METHOD":
                method = value.strip().upper()
            elif in_event:
                props.setdefault(name, (prop, value.strip()))
    if "UID" not in props or "DTSTART" not in props:
        return None
    organizer = props.get("ORGANIZER", ("", ""))[1]
    try:
        start = _dt(*props["DTSTART"], tz)
        end = _dt(*props["DTEND"], tz) if "DTEND" in props else None
    except ValueError:
        return None
    seq = props.get("SEQUENCE", ("", "0"))[1]
    return CallEvent(
        uid=props["UID"][1],
        start=start,
        end=end,
        summary=props.get("SUMMARY", ("", ""))[1],
        organizer=organizer.removeprefix("mailto:").lower(),
        method=method or ("CANCEL" if props.get("STATUS", ("", ""))[1] == "CANCELLED" else "REQUEST"),
        sequence=int(seq) if seq.isdigit() else 0,
        location=props.get("LOCATION", ("", ""))[1],
    )
