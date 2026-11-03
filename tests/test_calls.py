from datetime import datetime

import pytest

from conftest import NOW, TZ, as_due, expected
from inbox_to_action.calls import detect_call


@pytest.mark.parametrize("prefix", ["01", "02", "04", "06", "08", "09"])
def test_detect_calls(load, prefix):
    want = next(t for t in expected(prefix)["tasks"] if t["type"] == "Call")
    task = detect_call(load(prefix), NOW)
    assert task.type == "Call"
    assert task.platform == want["platform"]
    assert task.due == as_due(want["due"])
    for word in want["title_has"]:
        assert word.lower() in task.title.lower()


def test_title_strips_invitation_prefix(load):
    assert detect_call(load("02"), NOW).title == "Vendor sync - Northwind"


def test_join_link_and_phone(load):
    assert detect_call(load("01"), NOW).join_link.startswith("https://acme.zoom.us/j/84512397720")
    board = detect_call(load("08"), NOW)
    assert (board.phone, board.platform) == ("+16465550110", "Phone")


def test_ics_uid_and_sequence(load):
    t = detect_call(load("03"), NOW)
    assert (t.uid, t.sequence, t.key) == ("7k2v9q1northwind@google.com", 1, "7k2v9q1northwind@google.com")
    assert t.due == datetime(2026, 11, 5, 11, 0, tzinfo=TZ)


def test_cancel_marks_status(load):
    t = detect_call(load("05"), NOW)
    assert t.status == "Cancelled"
    assert t.uid == "040000008200E00074C5B7101A82E00800000000D1A2B3C4"


def test_past_invite(load):
    assert detect_call(load("10"), NOW) == "invite already in the past"


@pytest.mark.parametrize("prefix", ["11", "12", "07"])
def test_not_a_call(load, prefix):
    assert detect_call(load(prefix), NOW) is None
