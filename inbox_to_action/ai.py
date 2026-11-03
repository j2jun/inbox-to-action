"""Claude reads free-form emails and returns structured tasks. Only used when rules don't apply."""
import json
import logging
import os
import re
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from .models import Email, Task

log = logging.getLogger(__name__)
MODEL = "claude-haiku-5-5"
MAX_TOKENS = 800
MAX_CHARS = 4000
API_URL = "https://api.anthropic.com/v1/messages"
TYPES = ["Action", "Call", "Approval", "Follow-up", "Deadline"]
PLATFORMS = ["Zoom", "Meet", "Teams", "Webex", "Phone", "Other"]

TOOL = {
    "name": "record_tasks",
    "description": "Record every task this email asks the user to do. Empty list if none.",
    "input_schema": {
        "type": "object",
        "properties": {"tasks": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short imperative, e.g. 'Send Q3 numbers to Priya'"},
                "type": {"type": "string", "enum": TYPES},
                "due": {"type": ["string", "null"], "description": "ISO date or datetime with offset, or null"},
                "platform": {"type": ["string", "null"], "enum": PLATFORMS + [None]},
                "phone": {"type": ["string", "null"], "description": "E.164 phone number for a requested call"},
            },
            "required": ["title", "type", "due", "platform", "phone"],
        }}},
        "required": ["tasks"],
    },
}


def _trim(body: str) -> str:
    body = re.split(r"(?m)^On .+wrote:\s*$", body, maxsplit=1)[0]
    body = re.split(r"(?m)^-- ?$", body, maxsplit=1)[0]
    return "\n".join(l for l in body.splitlines() if not l.lstrip().startswith(">")).strip()


def build_request(email: Email, now: datetime) -> dict:
    tz = getattr(now.tzinfo, "key", str(now.tzinfo))
    system = (
        f"Today is {now.strftime('%A')} {now.date().isoformat()}, timezone {tz}. "
        "You extract tasks the email asks the user to do. Resolve relative dates ('by Friday') to absolute ISO dates "
        "in that timezone; include a time only if the email states one. "
        "If the email is itself a meeting invite, do not add 'attend this meeting'; only add separate prep work it asks for. "
        "A request to phone someone at a time is type Call with platform Phone. "
        "If there is no ask for the user, return tasks: []."
    )
    text = f"From: {email.from_name} <{email.from_addr}>\nSubject: {email.subject}\n\n{_trim(email.body)}"
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "tools": [TOOL],
        "tool_choice": {"type": "tool", "name": "record_tasks"},
        "messages": [{"role": "user", "content": text[:MAX_CHARS]}],
        "_message_id": email.message_id,
    }


def anthropic_call(request: dict) -> dict:
    body = {k: v for k, v in request.items() if not k.startswith("_")}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(), method="POST", headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def make_stub_call(fixture_dir: Path) -> Callable[[dict], dict]:
    """Offline stand-in for anthropic_call: replays recorded responses keyed by Message-ID."""
    by_id = {}
    for f in Path(fixture_dir).glob("*.json"):
        data = json.loads(f.read_text())
        by_id[data["message_id"]] = data["response"]
    empty = {"content": [{"type": "tool_use", "name": "record_tasks", "input": {"tasks": []}}]}
    return lambda request: by_id.get(request["_message_id"], empty)


def _due(value: str | None, now: datetime) -> date | datetime | None:
    if not value:
        return None
    try:
        if "T" in value:
            d = datetime.fromisoformat(value)
            return d.astimezone(now.tzinfo) if d.tzinfo else d.replace(tzinfo=now.tzinfo)
        return date.fromisoformat(value)
    except ValueError:
        return None


def _phone(value, body: str) -> str | None:
    """Accept a model-supplied number only if it is a plausible E.164 number whose digits appear in the email."""
    if not isinstance(value, str):
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) == 10 and not value.strip().startswith("+"):
        digits = "1" + digits  # ponytail: assume North American numbers without a country code
    candidate = "+" + digits
    if not re.fullmatch(r"\+[1-9]\d{6,14}", candidate):
        return None
    body_digits = re.sub(r"\D", "", body)
    national = digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
    return candidate if national in body_digits else None


def extract_with_ai(email: Email, now: datetime, call_fn: Callable[[dict], dict]) -> list[Task]:
    try:
        response = call_fn(build_request(email, now))
        block = next(b for b in response.get("content", []) if b.get("type") == "tool_use")
        items = block["input"]["tasks"]
    except Exception as exc:  # network, bad JSON, missing tool_use: never crash the pipeline
        log.warning("AI extraction failed for %s: %s", email.message_id, type(exc).__name__)
        return []
    tasks = []
    for item in items:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        tasks.append(Task(
            title=str(item["title"])[:200],
            type=item.get("type") if item.get("type") in TYPES else "Action",
            due=_due(item.get("due"), now),
            platform=item.get("platform") if item.get("platform") in PLATFORMS else None,
            phone=_phone(item.get("phone"), email.body),
            from_name=email.from_name,
            from_addr=email.from_addr,
            message_id=email.message_id,
        ))
    return tasks
