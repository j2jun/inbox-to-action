from datetime import date, datetime

import pytest

from conftest import TZ
from inbox_to_action.models import Task
from inbox_to_action.notion import PROPERTY_NAMES, NotionError, NotionStore, page_to_task, setup_database, task_to_props

CALL = Task(title="Vendor sync - Northwind", type="Call", due=datetime(2026, 11, 4, 13, tzinfo=TZ), platform="Meet",
            join_link="https://meet.google.com/xqv-hkdp-rme", from_name="Marcus Webb", from_addr="marcus@northwind.example",
            source_email="https://mail.google.com/x", key="7k2v9q1northwind@google.com", uid="7k2v9q1northwind@google.com",
            priority="P2", reason="P2: due within 7 days", snippet="Agenda", page_id="p1")
PHONE = Task(title="Board prep call", type="Call", due=datetime(2026, 11, 3, 8, tzinfo=TZ), platform="Phone",
             phone="+16465550110", key="<b@x>", page_id="p3", priority="P1")
ACTION = Task(title="Send Q3 numbers", type="Action", due=date(2026, 11, 6), from_name="Priya Shah",
              from_addr="priya@acme.example", source_item=None, key="<m@x>#0", priority="P2", priority_locked=True,
              status="To do", page_id="p2")
FIELDS = ["title", "type", "due", "platform", "join_link", "phone", "from_name", "from_addr", "source_email",
          "source_item", "key", "uid", "status", "priority", "reason", "priority_locked", "snippet", "page_id"]


def fake_page(props, page_id):
    """Shape a create payload the way Notion returns a page."""
    out = {}
    for name, value in props.items():
        kind = next(iter(value))
        v = value[kind]
        if kind in ("title", "rich_text"):
            v = [{"plain_text": r["text"]["content"]} for r in v]
        out[name] = {"type": kind, kind: v}
    return {"id": page_id, "properties": out}


@pytest.mark.parametrize("task", [CALL, PHONE, ACTION])
def test_props_round_trip(task):
    back = page_to_task(fake_page(task_to_props(task), task.page_id))
    for f in FIELDS:
        assert getattr(back, f) == getattr(task, f), f


def test_property_names_exact():
    assert set(task_to_props(CALL)) == set(PROPERTY_NAMES) == {
        "Task", "Priority", "Type", "Due", "Status", "Platform", "Join link", "From", "Source email",
        "Source item", "Why this priority", "Message ID", "Priority locked", "Snippet"}


class FakeHttp:
    def __init__(self, *responses):
        self.calls, self.responses = [], list(responses)

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        return self.responses.pop(0) if self.responses else (200, {"results": [], "has_more": False}, {})


def test_find_and_prefix_filters():
    http = FakeHttp()
    s = NotionStore("t", "db1", http=http)
    assert s.find("abc") is None
    assert http.calls[0][:2] == ("POST", "https://api.notion.com/v1/databases/db1/query")
    assert http.calls[0][2]["filter"] == {"property": "Message ID", "rich_text": {"equals": "abc"}}
    s.has_prefix("<m@x>")
    assert http.calls[1][2]["filter"] == {"property": "Message ID", "rich_text": {"starts_with": "<m@x>"}}


def test_list_open_paginates_and_filters():
    page = fake_page(task_to_props(ACTION), "p2")
    http = FakeHttp((200, {"results": [page], "has_more": True, "next_cursor": "c2"}, {}),
                    (200, {"results": [page], "has_more": False}, {}))
    tasks = NotionStore("t", "db1", http=http).list_open()
    assert len(tasks) == 2
    body = http.calls[0][2]
    assert body["filter"] == {"property": "Status", "select": {"equals": "To do"}}
    assert body["sorts"] == [{"property": "Due", "direction": "ascending"}]
    assert http.calls[1][2]["start_cursor"] == "c2"


def test_update_sends_only_changed_properties():
    http = FakeHttp((200, fake_page(task_to_props(ACTION), "p2"), {}))
    NotionStore("t", "db1", http=http).update("p2", status="Done")
    method, url, body = http.calls[0]
    assert (method, url) == ("PATCH", "https://api.notion.com/v1/pages/p2")
    assert body == {"properties": {"Status": {"select": {"name": "Done"}}}}


def test_retry_on_429():
    slept = []
    http = FakeHttp((429, {}, {"Retry-After": "2"}), (200, {"results": [], "has_more": False}, {}))
    NotionStore("t", "db1", http=http, sleep=slept.append).find("x")
    assert slept == [2.0] and len(http.calls) == 2


def test_gives_up_after_retries():
    http = FakeHttp(*[(500, {}, {})] * 4)
    with pytest.raises(NotionError):
        NotionStore("t", "db1", http=http, sleep=lambda s: None).find("x")
    assert len(http.calls) == 4


def test_setup_schema():
    http = FakeHttp((200, {"id": "newdb"}, {}))
    assert setup_database("t", "parent1", http=http) == "newdb"
    method, url, body = http.calls[0]
    assert (method, url) == ("POST", "https://api.notion.com/v1/databases")
    props = body["properties"]
    assert set(props) == set(PROPERTY_NAMES)
    opts = lambda name: [o["name"] for o in props[name]["select"]["options"]]
    assert opts("Type") == ["Action", "Call", "Assignment", "Approval", "Follow-up", "Deadline", "Added"]
    assert opts("Status") == ["To do", "Done", "Cancelled"]
    assert opts("Platform") == ["Zoom", "Meet", "Teams", "Webex", "Phone", "Other"]
    assert opts("Priority") == ["P1", "P2", "P3", "P4"]
