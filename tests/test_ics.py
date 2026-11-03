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
