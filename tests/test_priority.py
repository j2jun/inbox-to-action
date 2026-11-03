from datetime import date, datetime

import pytest

from conftest import NOW, TZ, USER, make_email
from inbox_to_action.models import Task
from inbox_to_action.priority import prioritize


def dt(*a):
    return datetime(*a, tzinfo=TZ)


def run(due=None, email=None, vips=frozenset()):
    return prioritize(Task(title="t", type="Action", due=due), email or make_email(), NOW, USER, set(vips))


@pytest.mark.parametrize("due,want", [
    (dt(2026, 11, 2, 15), "P1"), (date(2026, 11, 3), "P1"), (dt(2026, 11, 3, 8), "P1"),
    (date(2026, 11, 4), "P2"), (dt(2026, 11, 6, 17), "P2"),
    (date(2026, 11, 10), "P3"), (None, "P3"),
])
def test_due_windows(due, want):
    assert run(due)[0] == want


def test_urgency_words():
    assert run(None, make_email("Need your go/no-go ASAP")) == ("P1", "P1: urgent wording (ASAP)")


def test_cc_only_is_p4():
    assert run(None, make_email("please skim", to=("team@acme.example",), cc=(USER,)))[0] == "P4"


def test_bulk_sender_urgency_ignored():
    e = make_email("Act now, URGENT offer", headers={"precedence": "bulk"})
    assert run(None, e)[0] == "P3"


@pytest.mark.parametrize("due,email,reason", [
    (date(2026, 11, 3), None, "P1: due in under 24h"),
    (None, make_email(to=("team@acme.example",), cc=(USER,)), "P4: cc'd only"),
    (None, None, "P3: no due date"),
    (date(2026, 11, 4), None, "P2: due within 7 days"),
    (date(2026, 11, 10), None, "P3: due in more than 7 days"),
])
def test_reason_text(due, email, reason):
    assert run(due, email)[1] == reason


def test_vip_suffix():
    e = make_email(from_addr="priya@acme.example")
    assert run(None, e, {"priya@acme.example"})[1] == "P3: no due date, VIP sender"


def test_check_order_urgency_beats_cc():
    e = make_email("blocker!", to=("team@acme.example",), cc=(USER,))
    assert run(None, e)[0] == "P1"
