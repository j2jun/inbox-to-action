from datetime import datetime

from conftest import TZ
from inbox_to_action.ics import parse_ics


def test_invite(load):
    ev = parse_ics(load("02").ics, TZ)
    assert ev.uid == "7k2v9q1northwind@google.com"
    assert ev.start == datetime(2026, 11, 4, 13, 0, tzinfo=TZ)
    assert (ev.method, ev.sequence) == ("REQUEST", 0)
    assert ev.summary == "Vendor sync - Northwind"


def test_update(load):
    ev = parse_ics(load("03").ics, TZ)
    assert ev.start == datetime(2026, 11, 5, 11, 0, tzinfo=TZ)
    assert ev.sequence == 1


def test_cancel(load):
    assert parse_ics(load("05").ics, TZ).method == "CANCEL"


def test_garbage():
    assert parse_ics("hello world", TZ) is None


def _ics(dtstart, extra=""):
    return f"BEGIN:VCALENDAR\nMETHOD:REQUEST\nBEGIN:VEVENT\nUID:x1\n{dtstart}\n{extra}SUMMARY:S\nEND:VEVENT\nEND:VCALENDAR"


def test_windows_tzid_maps_to_iana():
    ev = parse_ics(_ics("DTSTART;TZID=Eastern Standard Time:20261104T100000"), TZ)
    assert ev.start == datetime(2026, 11, 4, 7, 0, tzinfo=TZ)


def test_unknown_tzid_falls_back():
    ev = parse_ics(_ics("DTSTART;TZID=Mars/Olympus:20261104T100000"), TZ)
    assert ev.start == datetime(2026, 11, 4, 10, 0, tzinfo=TZ)


def test_bad_dtstart_or_sequence():
    assert parse_ics(_ics("DTSTART:2026-11-04"), TZ) is None
    assert parse_ics(_ics("DTSTART:20261104T100000Z", "SEQUENCE:abc\n"), TZ).sequence == 0
