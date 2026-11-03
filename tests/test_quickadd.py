from datetime import date, datetime

import pytest

from conftest import NOW, TZ
from inbox_to_action.quickadd import parse_quick_add


def test_call_with_day_time_priority():
    t = parse_quick_add("Call Priya Fri 3pm p1", NOW)
    assert (t.title, t.due, t.priority, t.type, t.platform) == (
        "Call Priya", datetime(2026, 11, 6, 15, tzinfo=TZ), "P1", "Call", "Phone")
    assert t.priority_locked and t.key.startswith("added-")


@pytest.mark.parametrize("text,due,prio", [
    ("Draft memo", None, "P3"),
    ("Fix deck p1", date(2026, 11, 2), "P1"),
    ("Fix deck p2", date(2026, 11, 6), "P2"),
    ("Plan offsite tomorrow", date(2026, 11, 3), "P3"),
    ("Review budget monday", date(2026, 11, 9), "P3"),
])
def test_defaults(text, due, prio):
    t = parse_quick_add(text, NOW)
    assert (t.due, t.priority, t.type) == (due, prio, "Added")


def test_blank_is_rejected():
    with pytest.raises(ValueError):
        parse_quick_add("  p1  ", NOW)
