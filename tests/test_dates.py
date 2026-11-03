from datetime import date, datetime

import pytest

from conftest import NOW, TZ
from inbox_to_action.dates import parse_datetime_text, parse_due_text


def dt(*a):
    return datetime(*a, tzinfo=TZ)


@pytest.mark.parametrize("text,expected", [
    ("Time: Nov 4, 2026 10:00 AM Pacific Time", dt(2026, 11, 4, 10)),
    ("When: Friday, November 6, 2026 1:00 PM - 2:00 PM (UTC-08:00)", dt(2026, 11, 6, 13)),
    ("Tuesday, November 3, 2026, 8:00 - 8:45 AM PT", dt(2026, 11, 3, 8)),
    ("Thu, Nov 12, 2026 9:00 AM - 10:00 AM (PST)", dt(2026, 11, 12, 9)),
    ("Time: Oct 30, 2026 03:00 PM", dt(2026, 10, 30, 15)),
    ("no date here", None),
])
def test_parse_datetime_text(text, expected):
    assert parse_datetime_text(text, NOW) == expected


@pytest.mark.parametrize("text,expected", [
    ("by Friday", date(2026, 11, 6)),
    ("by tomorrow", date(2026, 11, 3)),
    ("Due date: 03/Nov/26", date(2026, 11, 3)),
    ("Due: Nov 10", date(2026, 11, 10)),
    ("approve by Nov 4", date(2026, 11, 4)),
    ("expires on November 13, 2026", date(2026, 11, 13)),
    ("closes this Friday, November 6 at 5pm PT", dt(2026, 11, 6, 17)),
])
def test_parse_due_text(text, expected):
    got = parse_due_text(text, NOW)
    assert got == expected and type(got) is type(expected)
