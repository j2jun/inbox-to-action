"""Minimal iCalendar (VEVENT) reader: just the fields a call task needs."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .models import CallEvent


def _dt(prop: str, value: str, tz: ZoneInfo) -> datetime:
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(tz)
    m = re.search(r"TZID=([^;:]+)", prop)
    zone = ZoneInfo(m.group(1)) if m else tz
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
    return CallEvent(
        uid=props["UID"][1],
        start=_dt(*props["DTSTART"], tz),
        end=_dt(*props["DTEND"], tz) if "DTEND" in props else None,
        summary=props.get("SUMMARY", ("", ""))[1],
        organizer=organizer.removeprefix("mailto:").lower(),
        method=method or ("CANCEL" if props.get("STATUS", ("", ""))[1] == "CANCELLED" else "REQUEST"),
        sequence=int(props.get("SEQUENCE", ("", "0"))[1] or 0),
        location=props.get("LOCATION", ("", ""))[1],
    )
