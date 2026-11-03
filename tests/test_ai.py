import logging
from datetime import date
from pathlib import Path

from conftest import NOW
from inbox_to_action.ai import build_request, extract_with_ai, make_stub_call

FIXTURES = Path(__file__).parent / "fixtures" / "ai"


def test_build_request(load):
    e = load("18")
    r = build_request(e, NOW)
    assert r["model"] == "claude-haiku-5-5" and r["max_tokens"] == 800
    assert r["tool_choice"] == {"type": "tool", "name": "record_tasks"}
    item = r["tools"][0]["input_schema"]["properties"]["tasks"]["items"]
    assert set(item["properties"]) == {"title", "type", "due", "platform", "phone"}
    assert set(item["properties"]["type"]["enum"]) == {"Action", "Call", "Approval", "Follow-up", "Deadline"}
    assert "2026-11-02" in r["system"] and "America/Los_Angeles" in r["system"]
    assert "invite" in r["system"].lower() and "[]" in r["system"]
    text = r["messages"][0]["content"]
    assert "any update?" in text
    assert "get back to you this week" not in text  # quoted reply stripped
    assert r["_message_id"] == e.message_id


def test_body_truncated(load):
    e = load("11")
    e.body = "x" * 10_000
    assert len(build_request(e, NOW)["messages"][0]["content"]) <= 4000


def test_parse_tool_response(load):
    tasks = extract_with_ai(load("11"), NOW, make_stub_call(FIXTURES))
    assert [t.title for t in tasks] == ["Send Q3 retention numbers to Priya", "Review competitive landscape slide"]
    assert [t.due for t in tasks] == [date(2026, 11, 6), None]


def test_missing_fixture_returns_empty(load):
    assert extract_with_ai(load("02"), NOW, make_stub_call(FIXTURES)) == []


def test_bad_response_returns_empty_and_logs(load, caplog):
    e = load("11")
    with caplog.at_level(logging.WARNING):
        assert extract_with_ai(e, NOW, lambda req: {"content": [{"type": "text", "text": "hi"}]}) == []
    assert e.message_id in caplog.text


def test_body_never_logged(load, caplog):
    with caplog.at_level(logging.DEBUG):
        extract_with_ai(load("11"), NOW, make_stub_call(FIXTURES))
        extract_with_ai(load("11"), NOW, lambda req: {})
    assert "retention numbers" not in caplog.text
