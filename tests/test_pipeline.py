from datetime import datetime
from pathlib import Path

import pytest

from conftest import NOW, TZ, USER, make_email
from inbox_to_action.ai import make_stub_call
from inbox_to_action.pipeline import Config, process
from inbox_to_action.store import MemoryStore

FIXTURES = Path(__file__).parent / "fixtures" / "ai"
CONFIG = Config(user_email=USER, vips={"priya@acme.example", "dana.reyes@bigclient.example"}, tz=TZ, jira_sites={"acme"})
STUB = make_stub_call(FIXTURES)


def no_ai(request):
    raise AssertionError("AI must not be called")


def run(email, store, ai=STUB):
    return process(email, store, NOW, CONFIG, ai)


def test_newsletter_skipped_without_ai(load):
    out = run(load("21"), MemoryStore(), no_ai)
    assert out.skipped == "newsletter" and not out.created


def test_tool_email_no_ai(load):
    out = run(load("12"), MemoryStore(), no_ai)
    assert [t.type for t in out.created] == ["Assignment"]
    assert out.created[0].priority == "P1"


def test_tool_email_with_list_unsubscribe_is_not_skipped(load):
    e = load("14")
    e.headers["list-unsubscribe"] = "<mailto:unsub@github.com>"
    assert len(run(e, MemoryStore(), no_ai).created) == 1


def test_ai_email_two_tasks(load):
    out = run(load("11"), MemoryStore())
    assert len(out.created) == 2
    assert [t.key for t in out.created] == ["<board-deck-011@acme.example>#0", "<board-deck-011@acme.example>#1"]


def test_call_plus_prep(load):
    out = run(load("08"), MemoryStore())
    assert sorted(t.type for t in out.created) == ["Action", "Call"]


def test_reprocess_same_email_creates_nothing(load):
    store = MemoryStore()
    run(load("11"), store)
    out = run(load("11"), store)
    assert not out.created and len(store.list_open()) == 2


def test_update_moves_call(load):
    store = MemoryStore()
    run(load("02"), store)
    out = run(load("03"), store)
    assert not out.created and len(out.updated) == 1
    calls = [t for t in store.list_open() if t.type == "Call"]
    assert len(calls) == 1 and calls[0].due == datetime(2026, 11, 5, 11, tzinfo=TZ)


def test_update_keeps_locked_priority(load):
    store = MemoryStore()
    task = run(load("02"), store).created[0]
    store.update(task.page_id, priority="P1", priority_locked=True)
    run(load("03"), store)
    t = store.find("uid:calendar-notification@google.com:7k2v9q1northwind@google.com")
    assert t.priority == "P1" and t.due == datetime(2026, 11, 5, 11, tzinfo=TZ)


def test_cancel_sets_status(load):
    store = MemoryStore()
    run(load("04"), store)
    out = run(load("05"), store)
    assert out.updated[0].status == "Cancelled"
    assert store.list_open() == []


def test_cancel_unknown_uid_is_skipped(load):
    store = MemoryStore()
    out = run(load("05"), store)
    assert out.skipped == "cancel for unknown invite" and store.list_open() == []


def test_snippet_limit_and_source_link(load):
    store = MemoryStore()
    e = load("11")
    e.body = "word " * 500
    for t in run(e, store, STUB).created:
        assert len(t.snippet) <= 200
        assert t.source_email.startswith("https://mail.google.com/")


def test_no_task_found(load):
    out = run(make_email("thanks, sounds good"), MemoryStore())
    assert out.skipped == "no task found"


def test_update_from_different_sender_is_rejected(load):
    store = MemoryStore()
    run(load("02"), store)
    spoof = load("03")
    spoof.from_addr = "attacker@evil.example"
    spoof.body = spoof.body.replace("https://meet.google.com/xqv-hkdp-rme", "https://meet.google.com/evil-link")
    out = run(spoof, store)
    assert not out.updated  # a different sender can't reach the real meeting's key
    t = store.find("uid:calendar-notification@google.com:7k2v9q1northwind@google.com")
    assert t.join_link == "https://meet.google.com/xqv-hkdp-rme"
    assert t.due == datetime(2026, 11, 4, 13, tzinfo=TZ)


def test_message_id_prefix_is_exact(load):
    store = MemoryStore()
    a = load("11")
    run(a, store)
    b = load("11")
    b.message_id = "<board-deck-011@acme.example.other>"
    same_answer = lambda req: STUB({**req, "_message_id": a.message_id})
    assert len(run(b, store, same_answer).created) == 2


def test_missing_message_id_gets_stable_synthetic_id():
    from conftest import SAMPLES
    from inbox_to_action.parse import parse_eml
    raw = (SAMPLES / "11_direct_request.eml").read_bytes().replace(b"Message-ID: <board-deck-011@acme.example>\n", b"")
    e1, e2 = parse_eml(raw), parse_eml(raw)
    assert e1.message_id and e1.message_id == e2.message_id and e1.message_id.endswith("@inbox-to-action>")


def test_update_fails_closed_when_original_sender_unknown(load):
    store = MemoryStore()
    created = run(load("02"), store).created[0]
    store.update(created.page_id, from_addr="")
    out = run(load("03"), store)
    assert out.skipped == "invite update from a different sender"


def _no_ics(load, prefix):
    e = load(prefix)
    e.ics = None
    return e


def test_google_invite_without_ics_is_a_call_and_updates_in_place(load):
    store = MemoryStore()
    created = run(_no_ics(load, "02"), store).created
    assert [t.platform for t in created] == ["Meet"]
    out = run(_no_ics(load, "03"), store)
    assert not out.created and len(out.updated) == 1
    calls = [t for t in store.list_open() if t.type == "Call"]
    assert len(calls) == 1 and calls[0].due == datetime(2026, 11, 5, 11, tzinfo=TZ)


def test_cancel_subject_without_ics(load):
    store = MemoryStore()
    e = _no_ics(load, "02")
    run(e, store)
    cancel = _no_ics(load, "02")
    cancel.message_id, cancel.subject = "<cancel@x>", "Canceled: " + e.subject
    out = run(cancel, store)
    assert out.updated and out.updated[0].status == "Cancelled"


def test_update_without_link_keeps_stored_link(load):
    store = MemoryStore()
    run(load("02"), store)
    upd = load("03")
    upd.body = upd.body.replace("https://meet.google.com/xqv-hkdp-rme", "")
    run(upd, store)
    assert store.find("uid:calendar-notification@google.com:7k2v9q1northwind@google.com").join_link == "https://meet.google.com/xqv-hkdp-rme"


def test_ai_failure_raises_so_zapier_retries(load):
    from inbox_to_action.ai import AIError
    def boom(req):
        raise OSError("network down")
    with pytest.raises(AIError):
        run(load("11"), MemoryStore(), boom)


def test_personal_zoom_room_is_not_a_shared_key(load):
    store = MemoryStore()
    a = load("01"); a.body = a.body.replace("https://acme.zoom.us/j/84512397720?pwd=cTFhR2xWb0RkT2x3", "https://acme.zoom.us/my/priya")
    b = load("01"); b.body = a.body.replace("Nov 4, 2026 10:00 AM", "Nov 5, 2026 10:00 AM"); b.message_id = "<other@x>"
    run(a, store, no_ai := (lambda req: {"content": [{"type": "tool_use", "input": {"tasks": []}}]}))
    run(b, store, no_ai)
    assert len([t for t in store.list_open() if t.type == "Call"]) == 2


def test_link_key_cannot_be_squatted_by_another_sender(load):
    store = MemoryStore()
    squat = _no_ics(load, "02")
    squat.from_addr, squat.message_id = "attacker@evil.example", "<squat@x>"
    run(squat, store)
    real = run(_no_ics(load, "02"), store)
    assert [t.platform for t in real.created] == ["Meet"]


def test_uid_cannot_be_squatted_by_another_sender(load):
    store = MemoryStore()
    squat = load("02")
    squat.from_addr, squat.message_id = "attacker@evil.example", "<squat2@x>"
    run(squat, store)
    real = run(load("02"), store)
    assert [t.platform for t in real.created] == ["Meet"]


def test_message_id_cannot_mimic_a_call_key(load):
    from inbox_to_action.parse import email_from_payload
    e = load("11")
    p = {"message_id": "link:calendar-notification@google.com:https://meet.google.com/xqv-hkdp-rme",
         "from": "x <x@y.z>", "to": USER, "subject": "s", "date": "Mon, 02 Nov 2026 08:45:00 -0800", "body": "hi"}
    assert email_from_payload(p, None).message_id.startswith("<")


def test_huge_ids_are_shortened_stably(load):
    from inbox_to_action.calls import detect_call
    from inbox_to_action.parse import email_from_payload
    p = {"message_id": "<" + "a" * 5000 + ">", "from": "x <x@y.z>", "to": USER, "subject": "s",
         "date": "Mon, 02 Nov 2026 08:45:00 -0800", "body": "hi"}
    m1, m2 = email_from_payload(p, None).message_id, email_from_payload(p, None).message_id
    assert m1 == m2 and len(m1) < 100
    e = load("02")
    e.ics = e.ics.replace("UID:7k2v9q1northwind@google.com", "UID:" + "u" * 5000)
    k1, k2 = detect_call(e, NOW).key, detect_call(e, NOW).key
    assert k1 == k2 and len(k1) < 500


def test_source_link_for_gmail_api_id():
    e = make_email("Can you send the deck by Friday?", message_id="<18b9f3c2a1d4e5f6>")
    e.thread_id = "18b9f3c2a1d4e5f6"
    out = run(e, MemoryStore(), lambda req: {"content": [{"type": "tool_use", "input": {"tasks": [
        {"title": "Send deck", "type": "Action", "due": None, "platform": None, "phone": None}]}}]})
    assert out.created[0].source_email == "https://mail.google.com/mail/u/0/#all/18b9f3c2a1d4e5f6"


def test_source_link_for_rfc_message_id(load):
    t = run(load("12"), MemoryStore(), no_ai).created[0]
    assert t.source_email.startswith("https://mail.google.com/mail/u/0/#search/rfc822msgid%3A")
