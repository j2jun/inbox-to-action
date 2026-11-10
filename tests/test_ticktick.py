import itertools
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from conftest import NOW, SAMPLES, TZ, USER
from inbox_to_action.ai import make_stub_call
from inbox_to_action.models import Task
from inbox_to_action.parse import parse_eml
from inbox_to_action.pipeline import Config, process
from inbox_to_action.store import MemoryStore
from inbox_to_action.ticktick import (API, MARK, TickTickError, TickTickStore, json_to_task, save_env,
                                      task_to_json)
from test_notion import ACTION, CALL, FIELDS, PHONE

PID = "p" * 24


class FakeTickTick:
    """Just enough of the Open API: create, get, update, complete, project data."""

    def __init__(self):
        self.tasks, self.calls, self.ids = {}, [], (f"{i:024x}" for i in itertools.count(1))

    def __call__(self, method, url, body):
        self.calls.append((method, url[len(API):]))
        path = url[len(API):].split("/")[1:]
        if method == "GET" and path == ["project", PID, "data"]:
            return 200, {"tasks": [t for t in self.tasks.values() if t.get("status", 0) == 0]}, {}
        if method == "POST" and path == ["task"]:
            t = {**body, "id": next(self.ids), "status": 0, "dueDate": (body.get("dueDate") or "").replace("+", ".000+") or None}
            self.tasks[t["id"]] = t
            return 200, t, {}
        if len(path) == 4 and path[:3] == ["project", PID, "task"] and method == "GET":
            return (200, self.tasks[path[3]], {}) if path[3] in self.tasks else (404, {}, {})
        if method == "POST" and len(path) == 2 and path[0] == "task":
            self.tasks[path[1]] = {**self.tasks[path[1]], **body}
            return 200, self.tasks[path[1]], {}
        if method == "POST" and path[-1] == "complete":
            self.tasks[path[3]]["status"] = 2
            return 200, {}, {}
        raise AssertionError((method, url))


def store(http=None):
    return TickTickStore("t", PID, TZ, http=http or FakeTickTick(), sleep=lambda s: None)


@pytest.mark.parametrize("task", [CALL, PHONE, ACTION])
def test_json_round_trip(task):
    back = json_to_task({**task_to_json(task, PID, TZ), "id": task.page_id, "status": 0}, TZ)
    for f in FIELDS:
        assert getattr(back, f) == getattr(task, f), f


def test_all_day_is_local_midnight_and_priority_maps():
    body = task_to_json(ACTION, PID, TZ)
    assert body["dueDate"] == "2026-11-06T08:00:00+0000" and body["isAllDay"] is True  # PST midnight
    assert body["priority"] == 3 and body["timeZone"] == "America/Los_Angeles"
    assert task_to_json(PHONE, PID, TZ)["priority"] == 5


def test_task_made_in_ticktick_app_without_metadata():
    t = json_to_task({"id": "a" * 24, "title": "Buy milk", "priority": 0, "dueDate": "2026-11-03T16:00:00.000+0000"}, TZ)
    assert (t.title, t.type, t.priority, t.status, t.key) == ("Buy milk", "Added", "P4", "To do", "")
    assert t.due == datetime(2026, 11, 3, 8, tzinfo=TZ)


def test_hand_edited_metadata_keeps_task():
    t = json_to_task({"id": "a" * 24, "title": "x", "content": "my note" + MARK + "{broken"}, TZ)
    assert t.title == "x" and t.type == "Added"


def test_done_completes_and_undo_reopens():
    s = store()
    t = s.create(ACTION)
    s.update(t.page_id, status="Done")
    assert s.http.calls[-1] == ("POST", f"/project/{PID}/task/{t.page_id}/complete")
    assert s.list_open() == [] and s.get(t.page_id).status == "Done"
    s.update(t.page_id, status="To do")
    assert s.http.tasks[t.page_id]["status"] == 0 and [x.title for x in s.list_open()] == ["Send Q3 numbers"]


def test_cancelled_stays_cancelled():
    s = store()
    t = s.create(CALL)
    s.update(t.page_id, status="Cancelled")
    assert s.get(t.page_id).status == "Cancelled"


def test_find_prefix_and_sorted_open():
    s = store()
    for t in (ACTION, PHONE, Task(title="Someday", type="Added", key="k")):
        s.create(t)
    assert s.find("<b@x>").title == "Board prep call" and s.find("nope") is None
    assert s.has_prefix("<m@x>#") and not s.has_prefix("<z@x>#")
    assert [t.title for t in s.list_open()] == ["Board prep call", "Send Q3 numbers", "Someday"]


def test_retries_429_then_raises_on_4xx():
    responses = [(429, {}, {"Retry-After": "0"}), (200, {"tasks": []}, {}), (401, {}, {})]
    s = store(http=lambda m, u, b: responses.pop(0))
    assert s.list_open() == []
    with pytest.raises(TickTickError, match="401"):
        s.list_open()


def test_pipeline_matches_memory_store():
    """Every sample email produces the same open tasks through TickTick as through MemoryStore."""
    stub = make_stub_call(Path(__file__).parent / "fixtures" / "ai")
    config = Config(USER, {"priya@acme.example"}, TZ)
    tt, mem = store(), MemoryStore()
    for f in sorted(SAMPLES.glob("*.eml")):
        for s in (tt, mem):
            process(parse_eml(f.read_bytes()), s, NOW, config, stub)
    view = lambda s: sorted((t.title, t.type, t.priority, str(t.due), t.key, t.join_link) for t in s.list_open())
    assert view(tt) == view(mem) and view(tt)


def test_save_env_replaces_keys_and_is_private(tmp_path):
    p = tmp_path / ".env.local"
    p.write_text("WEBHOOK_SECRET=w\nTICKTICK_TOKEN=old\n")
    save_env(p, {"TICKTICK_TOKEN": "new", "TICKTICK_PROJECT_ID": "pid"})
    assert p.read_text() == "WEBHOOK_SECRET=w\nTICKTICK_TOKEN=new\nTICKTICK_PROJECT_ID=pid\n"
    assert p.stat().st_mode & 0o777 == 0o600


def test_app_picks_ticktick_when_configured():
    from inbox_to_action.web import make_store
    config = Config(USER, set(), TZ)
    assert isinstance(make_store({"TICKTICK_TOKEN": "t", "TICKTICK_PROJECT_ID": PID}, config), TickTickStore)
    assert type(make_store({"NOTION_TOKEN": "t", "NOTION_DATABASE_ID": "d"}, config)).__name__ == "NotionStore"
