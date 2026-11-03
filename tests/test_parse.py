from conftest import SAMPLES
from inbox_to_action.parse import email_from_payload, parse_eml


def test_parse_plain_eml(load):
    e = load("11")
    assert e.message_id == "<board-deck-011@acme.example>"
    assert e.from_addr == "priya@acme.example"
    assert "retention numbers" in e.body
    assert e.ics is None


def test_parse_ics_part(load):
    assert "UID:7k2v9q1northwind@google.com" in load("02").ics


def test_headers_lowercase(load):
    assert load("21").headers["precedence"] == "bulk"


def test_cc_parsed(load):
    e = load("20")
    assert e.cc == ["alex@acme.example"]
    assert e.to == ["eng-leads@acme.example"]


def test_payload_round_trip(load):
    e = load("11")
    p = email_from_payload({
        "message_id": e.message_id, "thread_id": None,
        "from": "Priya Shah <priya@acme.example>", "to": "Alex Kim <alex@acme.example>", "cc": "",
        "subject": e.subject, "date": "Mon, 02 Nov 2026 08:45:00 -0800", "body": e.body,
        "list_unsubscribe": "", "precedence": "",
    }, None)
    assert (p.message_id, p.subject, p.from_addr, p.to) == (e.message_id, e.subject, e.from_addr, e.to)
    assert p.date == e.date


def test_all_samples_parse():
    for f in sorted(SAMPLES.glob("*.eml")):
        e = parse_eml(f.read_bytes())
        assert e.message_id, f
        assert e.date.tzinfo is not None, f
