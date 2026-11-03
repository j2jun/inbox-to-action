"""WSGI app: login, task API for the Today page, and the Zapier webhook (contract in the plan, Task 9)."""
import json
import logging
import os
import re
from datetime import date, datetime
from email.parser import BytesParser
from email.policy import default as default_policy
from http.cookies import SimpleCookie

from . import auth
from .models import Task
from .parse import email_from_payload
from .pipeline import Config, process
from .quickadd import parse_quick_add

log = logging.getLogger(__name__)
MAX_BODY = 2_000_000
PAGE_ID = re.compile(r"^([0-9a-fA-F-]{32,36}|mem-\d+)$")
PRIORITIES = {"P1", "P2", "P3", "P4"}


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def _task_json(t: Task, vips: set[str]) -> dict:
    due = t.due.isoformat() if t.due is not None else None
    return {
        "id": t.page_id, "title": t.title, "type": t.type, "priority": t.priority, "reason": t.reason,
        "due": due, "has_time": isinstance(t.due, datetime), "platform": t.platform, "join_link": t.join_link,
        "phone": t.phone, "from": t.from_name or t.from_addr, "vip": t.from_addr.lower() in vips, "status": t.status,
    }


def _parse_due(value, tz) -> date | datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPError(400, "bad due")
    try:
        if "T" in value:
            d = datetime.fromisoformat(value)
            return d if d.tzinfo else d.replace(tzinfo=tz)
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPError(400, "bad due") from None


def make_app(store, config: Config, ai_call, now_fn=None, env=os.environ):
    now_fn = now_fn or (lambda: datetime.now(config.tz))
    # Secure cookies everywhere except the local dev server over plain http (dev.py sets DEV_INSECURE_COOKIE=1).
    secure = "" if env.get("DEV_INSECURE_COOKIE") == "1" else " Secure;"

    def body_bytes(environ) -> bytes:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            raise HTTPError(400, "bad length") from None
        if length > MAX_BODY:
            raise HTTPError(413, "too large")
        return environ["wsgi.input"].read(length)

    def json_body(environ) -> dict:
        if not environ.get("CONTENT_TYPE", "").startswith("application/json"):
            raise HTTPError(415, "send application/json")
        try:
            data = json.loads(body_bytes(environ) or b"{}")
        except ValueError:
            raise HTTPError(400, "bad json") from None
        if not isinstance(data, dict):
            raise HTTPError(400, "bad json")
        return data

    def logged_in(environ) -> bool:
        cookie = SimpleCookie(environ.get("HTTP_COOKIE", ""))
        value = cookie["session"].value if "session" in cookie else None
        return auth.check_session(value, env.get("SESSION_SECRET", ""), now_fn().timestamp())

    def login(environ):
        data = json_body(environ)
        if not auth.check_password(str(data.get("password", "")), env.get("APP_PASSWORD", "")):
            raise HTTPError(401, "wrong password")
        token = auth.make_session(env["SESSION_SECRET"], now_fn().timestamp())
        return 204, None, [("Set-Cookie", f"session={token}; HttpOnly;{secure} SameSite=Lax; Path=/; Max-Age={auth.TTL}")]

    def logout(environ):
        return 204, None, [("Set-Cookie", f"session=; HttpOnly;{secure} SameSite=Lax; Path=/; Max-Age=0")]

    def list_tasks(environ):
        return 200, {"tasks": [_task_json(t, config.vips) for t in store.list_open()]}, []

    def add_task(environ):
        text = json_body(environ).get("text")
        if not isinstance(text, str) or len(text) > 500:
            raise HTTPError(400, "text required")
        try:
            task = parse_quick_add(text, now_fn())
        except ValueError:
            raise HTTPError(400, "text required") from None
        return 201, _task_json(store.create(task), config.vips), []

    def patch_task(environ, page_id):
        if not PAGE_ID.match(page_id):
            raise HTTPError(404, "not found")
        data = json_body(environ)
        fields = {}
        if "done" in data:
            if not isinstance(data["done"], bool):
                raise HTTPError(400, "bad done")
            fields["status"] = "Done" if data["done"] else "To do"
        if "priority" in data:
            if data["priority"] not in PRIORITIES:
                raise HTTPError(400, "bad priority")
            fields.update(priority=data["priority"], priority_locked=True, reason="Set by you")
        if "due" in data:
            fields["due"] = _parse_due(data["due"], config.tz)
        if not fields:
            raise HTTPError(400, "nothing to change")
        try:
            task = store.update(page_id, **fields)
        except KeyError:
            raise HTTPError(404, "not found") from None
        except Exception as exc:
            if "404" in str(exc) or "object_not_found" in str(exc):
                raise HTTPError(404, "not found") from None
            raise
        return 200, _task_json(task, config.vips), []

    def webhook(environ):
        secret = env.get("WEBHOOK_SECRET", "")
        given = environ.get("HTTP_X_WEBHOOK_SECRET", "")
        if not secret or not auth.hmac.compare_digest(given.encode(), secret.encode()):
            raise HTTPError(401, "bad secret")
        raw, ctype, ics = body_bytes(environ), environ.get("CONTENT_TYPE", ""), None
        try:
            if ctype.startswith("multipart/form-data"):
                msg = BytesParser(policy=default_policy).parsebytes(
                    f"Content-Type: {ctype}\r\n\r\n".encode() + raw)
                parts = {p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()}
                payload = json.loads(parts["payload"].get_content())
                if "ics" in parts:
                    content = parts["ics"].get_content()
                    ics = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
            else:
                payload = json.loads(raw)
            if not isinstance(payload, dict) or not str(payload.get("message_id") or "").strip():
                raise ValueError
            email = email_from_payload(payload, ics or payload.get("ics"))
        except (ValueError, KeyError, TypeError, AttributeError):
            raise HTTPError(400, "bad payload") from None
        out = process(email, store, now_fn(), config, ai_call)
        log.info("webhook %s: created=%d updated=%d skipped=%s",
                 email.message_id, len(out.created), len(out.updated), out.skipped)
        return 200, {"created": len(out.created), "updated": len(out.updated), "skipped": out.skipped}, []

    routes = [
        ("POST", re.compile(r"^/login$"), login, False),
        ("POST", re.compile(r"^/logout$"), logout, False),
        ("GET", re.compile(r"^/api/tasks$"), list_tasks, True),
        ("POST", re.compile(r"^/api/tasks$"), add_task, True),
        ("PATCH", re.compile(r"^/api/tasks/([^/]+)$"), patch_task, True),
        ("POST", re.compile(r"^/webhook/email$"), webhook, False),
    ]

    def app(environ, start_response):
        method, path = environ.get("REQUEST_METHOD", "GET"), environ.get("PATH_INFO", "/")
        try:
            for m, pattern, handler, needs_login in routes:
                match = pattern.match(path)
                if match and m == method:
                    if needs_login and not logged_in(environ):
                        raise HTTPError(401, "login required")
                    status, body, headers = handler(environ, *match.groups())
                    break
            else:
                raise HTTPError(404, "not found")
        except HTTPError as e:
            status, body, headers = e.status, {"error": e.message}, []
        except Exception as exc:  # never leak details
            log.exception("unhandled %s on %s %s", type(exc).__name__, method, path)
            status, body, headers = 500, {"error": "server error"}, []
        payload = json.dumps(body).encode() if body is not None else b""
        reasons = {200: "OK", 201: "Created", 204: "No Content", 400: "Bad Request", 401: "Unauthorized",
                   404: "Not Found", 413: "Payload Too Large", 415: "Unsupported Media Type", 500: "Internal Server Error"}
        start_response(f"{status} {reasons.get(status, '')}", [
            ("Content-Type", "application/json"), ("Content-Length", str(len(payload))),
            ("Cache-Control", "no-store"), *headers])
        return [payload]

    return app


_app = None


def app(environ, start_response):
    """Production entry point: built on first request from env vars."""
    global _app
    if _app is None:
        from .ai import anthropic_call
        from .notion import NotionStore
        _app = make_app(NotionStore(os.environ["NOTION_TOKEN"], os.environ["NOTION_DATABASE_ID"]),
                        Config.from_env(), anthropic_call)
    return _app(environ, start_response)
