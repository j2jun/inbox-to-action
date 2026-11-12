"""Local dev server: the Today page + API on http://localhost:8000 with sample data, no Notion needed.

    python3 -m inbox_to_action.dev            # password: inbox-to-action-dev (or $APP_PASSWORD)
"""
import mimetypes
import os
import secrets
from datetime import datetime
from pathlib import Path
from wsgiref.simple_server import make_server
from zoneinfo import ZoneInfo

from .ai import make_stub_call
from .parse import parse_eml
from .pipeline import Config, process
from .store import MemoryStore
from .web import make_app

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"


def build():
    tz = ZoneInfo("America/Los_Angeles")
    config = Config("alex@acme.example", {"priya@acme.example", "dana.reyes@bigclient.example"}, tz, {"acme"})
    store = MemoryStore()
    stub = make_stub_call(ROOT / "tests" / "fixtures" / "ai")
    sample_now = datetime(2026, 11, 2, 9, 30, tzinfo=tz)
    for f in sorted((ROOT / "samples").glob("*.eml")):
        process(parse_eml(f.read_bytes()), store, sample_now, config, stub)
    env = {"APP_PASSWORD": os.environ.get("APP_PASSWORD", "inbox-to-action-dev"), "SESSION_SECRET": secrets.token_hex(16),
           "WEBHOOK_SECRET": "dev", "DEV_INSECURE_COOKIE": "1"}
    api = make_app(store, config, stub, now_fn=lambda: sample_now, env=env)

    def app(environ, start_response):
        path = environ.get("PATH_INFO", "/")
        if path.startswith(("/api/", "/login", "/logout", "/webhook/")):
            return api(environ, start_response)
        target = (PUBLIC / (path.lstrip("/") or "index.html")).resolve()
        if not target.is_file() or PUBLIC not in target.parents:
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"not found"]
        start_response("200 OK", [("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream"),
                                  ("Cache-Control", "no-store")])
        return [target.read_bytes()]
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Today page: http://localhost:{port}  (password: {os.environ.get('APP_PASSWORD', 'inbox-to-action-dev')})")
    make_server("127.0.0.1", port, build()).serve_forever()
