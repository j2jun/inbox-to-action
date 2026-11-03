from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 2, 9, 30, tzinfo=TZ)


def sample_path(prefix: str) -> Path:
    return next(SAMPLES.glob(f"{prefix}_*.eml"))


@pytest.fixture
def load():
    from inbox_to_action.parse import parse_eml
    return lambda prefix: parse_eml(sample_path(prefix).read_bytes())

import json
EXPECTED = json.loads((SAMPLES / "expected.json").read_text())["emails"]


def expected(prefix: str) -> dict:
    return next(v for k, v in EXPECTED.items() if k.startswith(prefix + "_"))


def as_due(value):
    """expected.json due string -> date or aware datetime."""
    from datetime import date
    if value is None:
        return None
    if "T" in value:
        return datetime.fromisoformat(value).astimezone(TZ)
    return date.fromisoformat(value)

USER = "alex@acme.example"


def make_email(body="", subject="Hi", to=(USER,), cc=(), headers=None, from_addr="someone@acme.example", message_id="<t@x>"):
    from inbox_to_action.models import Email
    return Email(message_id=message_id, subject=subject, from_name="Someone", from_addr=from_addr,
                 to=list(to), cc=list(cc), date=NOW, headers=headers or {}, body=body, ics=None)
