import io
import json
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pytest

from conftest import NOW, SAMPLES, TZ, USER
from inbox_to_action.ai import make_stub_call
from inbox_to_action.models import Task
from inbox_to_action.pipeline import Config
from inbox_to_action.store import MemoryStore
from inbox_to_action.web import make_app

ENV = {"APP_PASSWORD": "correct horse", "SESSION_SECRET": "s" * 32, "WEBHOOK_SECRET": "hook-secret"}
STUB = make_stub_call(Path(__file__).parent / "fixtures" / "ai")


def call(app, method, path, body=None, headers=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    env = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(data)),
           "CONTENT_TYPE": ctype, "wsgi.input": io.BytesIO(data)}
    for k, v in (headers or {}).items():
        env["HTTP_" + k.upper().replace("-", "_")] = v
    out = {}

    def start(status, hdrs):
        out["status"], out["headers"] = int(status.split()[0]), hdrs
    payload = b"".join(app(env, start))
    out["json"] = json.loads(payload) if payload else None
    out["raw"] = payload
    return out


@pytest.fixture
def store():
    s = MemoryStore()
    s.create(Task(title="Send Q3 numbers", type="Action", key="<m@x>#0", priority="P2", from_addr="priya@acme.example"))
    return s


@pytest.fixture
def app(store):
    cfg = Config(user_email=USER, vips={"priya@acme.example"}, tz=TZ)
    return make_app(store, cfg, STUB, now_fn=lambda: NOW, env=ENV)


def login(app):
    r = call(app, "POST", "/login", {"password": "correct horse"})
    cookie = next(v for k, v in r["headers"] if k == "Set-Cookie")
    return {"Cookie": cookie.split(";")[0]}, cookie


def test_login_and_session(app):
    assert call(app, "POST", "/login", {"password": "nope"})["status"] == 401
    hdr, cookie = login(app)
    for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Max-Age=2592000"):
        assert flag in cookie
    assert call(app, "GET", "/api/tasks", headers=hdr)["status"] == 200
    assert call(app, "GET", "/api/tasks")["status"] == 401
    tampered = {"Cookie": hdr["Cookie"][:-2] + "00"}
    assert call(app, "GET", "/api/tasks", headers=tampered)["status"] == 401


def test_expired_session(app, store):
    from inbox_to_action.auth import make_session
    from inbox_to_action.auth import session_key
    old = make_session(session_key(ENV["SESSION_SECRET"], ENV["APP_PASSWORD"]), NOW.timestamp() - 31 * 86400)
    assert call(app, "GET", "/api/tasks", headers={"Cookie": f"session={old}"})["status"] == 401


def test_no_password_configured_fails_closed(store):
    a = make_app(store, Config(USER, set(), TZ), STUB, now_fn=lambda: NOW, env={**ENV, "APP_PASSWORD": ""})
    assert call(a, "POST", "/login", {"password": ""})["status"] == 401


def test_list_tasks_json(app):
    hdr, _ = login(app)
    t = call(app, "GET", "/api/tasks", headers=hdr)["json"]["tasks"][0]
    assert set(t) == {"id", "title", "type", "priority", "reason", "due", "has_time", "platform", "join_link",
                      "phone", "from", "from_addr", "vip", "status"}
    assert t["vip"] is True


def test_add_task(app):
    hdr, _ = login(app)
    r = call(app, "POST", "/api/tasks", {"text": "Call Priya Fri 3pm p1"}, headers=hdr)
    assert r["status"] == 201
    assert (r["json"]["title"], r["json"]["priority"], r["json"]["due"], r["json"]["has_time"]) == (
        "Call Priya", "P1", "2026-11-06T15:00:00-08:00", True)
    assert call(app, "POST", "/api/tasks", {"text": "  "}, headers=hdr)["status"] == 400


def test_patch_done_priority_snooze(app, store):
    hdr, _ = login(app)
    pid = store.list_open()[0].page_id
    r = call(app, "PATCH", f"/api/tasks/{pid}", {"priority": "P1"}, headers=hdr)
    assert r["status"] == 200 and r["json"]["priority"] == "P1" and store.find("<m@x>#0").priority_locked
    assert call(app, "PATCH", f"/api/tasks/{pid}", {"due": "2026-11-09"}, headers=hdr)["json"]["due"] == "2026-11-09"
    assert call(app, "PATCH", f"/api/tasks/{pid}", {"due": None}, headers=hdr)["json"]["due"] is None
    assert call(app, "PATCH", f"/api/tasks/{pid}", {"done": True}, headers=hdr)["json"]["status"] == "Done"
    assert call(app, "PATCH", f"/api/tasks/{pid}", {"done": False}, headers=hdr)["json"]["status"] == "To do"
    assert call(app, "PATCH", "/api/tasks/mem-999", {"done": True}, headers=hdr)["status"] == 404
    assert call(app, "PATCH", f"/api/tasks/{pid}", {"priority": "P9"}, headers=hdr)["status"] == 400
    assert call(app, "PATCH", f"/api/tasks/{pid}", {"due": "soon"}, headers=hdr)["status"] == 400


def test_api_requires_json_content_type(app, store):
    hdr, _ = login(app)
    pid = store.list_open()[0].page_id
    r = call(app, "PATCH", f"/api/tasks/{pid}", raw=b"done=true", headers=hdr, ctype="application/x-www-form-urlencoded")
    assert r["status"] == 415


def _payload(prefix):
    from inbox_to_action.parse import parse_eml
    e = parse_eml(next(SAMPLES.glob(f"{prefix}_*.eml")).read_bytes())
    return e, {"message_id": e.message_id, "thread_id": "t1", "from": f"{e.from_name} <{e.from_addr}>",
               "to": ", ".join(e.to), "cc": ", ".join(e.cc), "subject": e.subject,
               "date": e.headers["date"], "body": e.body}


def test_webhook_rejects_bad_secret(app, store):
    before = len(store.pages)
    _, p = _payload("11")
    assert call(app, "POST", "/webhook/email", p)["status"] == 401
    assert call(app, "POST", "/webhook/email", p, headers={"X-Webhook-Secret": "wrong"})["status"] == 401
    assert len(store.pages) == before


def test_webhook_rejects_malformed_payload(app, store):
    before = len(store.pages)
    h = {"X-Webhook-Secret": "hook-secret"}
    r = call(app, "POST", "/webhook/email", raw=b"{not json <script>", headers=h)
    assert r["status"] == 400 and b"<script>" not in r["raw"]
    assert call(app, "POST", "/webhook/email", {"subject": "x"}, headers=h)["status"] == 400
    assert call(app, "POST", "/webhook/email", {"message_id": " ", "date": "x"}, headers=h)["status"] == 400
    assert len(store.pages) == before


def test_webhook_processes_payload(app):
    _, p = _payload("11")
    r = call(app, "POST", "/webhook/email", p, headers={"X-Webhook-Secret": "hook-secret"})
    assert r["status"] == 200 and r["json"] == {"created": 2, "updated": 0, "skipped": None}


def test_webhook_multipart_ics(app, store):
    e, p = _payload("02")
    msg = MIMEMultipart("form-data")
    part = MIMEText(json.dumps(p), "plain")
    part.add_header("Content-Disposition", "form-data", name="payload")
    msg.attach(part)
    f = MIMEApplication(e.ics.encode(), "octet-stream")
    f.add_header("Content-Disposition", "form-data", name="ics", filename="invite.ics")
    msg.attach(f)
    raw = msg.as_bytes().split(b"\n\n", 1)[1]
    ctype = msg["Content-Type"]
    r = call(app, "POST", "/webhook/email", raw=raw, ctype=ctype, headers={"X-Webhook-Secret": "hook-secret"})
    assert r["status"] == 200 and r["json"]["created"] == 1
    t = store.find("uid:calendar-notification@google.com:7k2v9q1northwind@google.com")
    assert t.platform == "Meet" and t.due.isoformat() == "2026-11-04T13:00:00-08:00"


def test_webhook_body_too_large(app):
    r = call(app, "POST", "/webhook/email", raw=b"x" * 2_000_001, headers={"X-Webhook-Secret": "hook-secret"})
    assert r["status"] == 413


def test_short_password_config_fails_closed(store):
    a = make_app(store, Config(USER, set(), TZ), STUB, now_fn=lambda: NOW, env={**ENV, "APP_PASSWORD": "short"})
    assert call(a, "POST", "/login", {"password": "short"})["status"] == 401


def test_changing_password_revokes_sessions(store):
    cfg = Config(USER, set(), TZ)
    a = make_app(store, cfg, STUB, now_fn=lambda: NOW, env=ENV)
    hdr, _ = login(a)
    b = make_app(store, cfg, STUB, now_fn=lambda: NOW, env={**ENV, "APP_PASSWORD": "a brand new passphrase"})
    assert call(b, "GET", "/api/tasks", headers=hdr)["status"] == 401


def test_webhook_ai_failure_returns_503(store):
    def boom(req):
        raise OSError("down")
    a = make_app(store, Config(USER, set(), TZ), boom, now_fn=lambda: NOW, env=ENV)
    _, p = _payload("11")
    assert call(a, "POST", "/webhook/email", p, headers={"X-Webhook-Secret": "hook-secret"})["status"] == 503


def test_unconfigured_production_app_answers_503(monkeypatch):
    import inbox_to_action.web as web
    for k in ("NOTION_TOKEN", "NOTION_DATABASE_ID", "APP_PASSWORD", "SESSION_SECRET", "WEBHOOK_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(web, "_app", None)
    r = call(web.app, "GET", "/api/tasks")
    assert r["status"] == 503 and r["json"] == {"error": "not configured"}


def test_approve_dismiss_and_undo_review(app, store):
    hdr, _ = login(app)
    t = store.create(Task("Buy gift cards", "Action", status="Review"))
    assert any(x["id"] == t.page_id for x in call(app, "GET", "/api/tasks", headers=hdr)["json"]["tasks"])
    for body, want in [({"approve": True}, "To do"), ({"review": True}, "Review"), ({"approve": False}, "Cancelled")]:
        r = call(app, "PATCH", f"/api/tasks/{t.page_id}", body, headers=hdr)
        assert r["status"] == 200 and r["json"]["status"] == want, body
    assert call(app, "PATCH", f"/api/tasks/{t.page_id}", {"approve": "yes"}, headers=hdr)["status"] == 400
