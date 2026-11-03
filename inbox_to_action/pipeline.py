"""One email in, tasks out: tools → skip → calls → AI → priority → upsert."""
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .ai import _trim, extract_with_ai
from .calls import detect_call
from .models import Email, Task
from .priority import prioritize
from .skip import skip_reason
from .tools import detect_tool_task


@dataclass
class Config:
    user_email: str
    vips: set[str]
    tz: ZoneInfo

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        return cls(
            user_email=env.get("USER_EMAIL", "").lower(),
            vips={v.strip().lower() for v in env.get("VIP_EMAILS", "").split(",") if v.strip()},
            tz=ZoneInfo(env.get("TIMEZONE", "America/Los_Angeles")),
        )


@dataclass
class Outcome:
    created: list[Task] = field(default_factory=list)
    updated: list[Task] = field(default_factory=list)
    skipped: str | None = None


def _finish(task: Task, email: Email, now: datetime, config: Config) -> Task:
    level, reason = prioritize(task, email, now, config.user_email, config.vips)
    snippet = re.sub(r"\s+", " ", _trim(email.body))[:200]
    link = "https://mail.google.com/mail/u/0/#search/rfc822msgid%3A" + quote(email.message_id.strip("<>"), safe="")
    return replace(task, priority=level, reason=reason, snippet=snippet, source_email=link)


def _create_all(tasks: list[Task], email, store, now, config, out: Outcome) -> None:
    for i, t in enumerate(tasks):
        t = _finish(replace(t, key=t.key or f"{email.message_id}#{i}"), email, now, config)
        out.created.append(store.create(t))


def _seen(email: Email, store) -> bool:
    # Exact key or "<id>#n": a bare prefix would let "<a@x>" match "<a@x.other>#0".
    return store.find(email.message_id) is not None or store.has_prefix(email.message_id + "#")


def process(email: Email, store, now: datetime, config: Config, ai_call) -> Outcome:
    out = Outcome()

    # Task-tool notifications first: they often carry List-Unsubscribe and must never be skipped.
    tool_tasks = detect_tool_task(email, now)
    if tool_tasks is not None:
        if _seen(email, store):
            out.skipped = "already processed"
        else:
            _create_all(tool_tasks, email, store, now, config, out)
        return out

    if reason := skip_reason(email, now):
        out.skipped = reason
        return out

    call = detect_call(email, now)
    if isinstance(call, str):
        out.skipped = call
        return out
    if call is not None:
        existing = store.find(call.key)
        if existing:
            # Only the original sender may move, cancel or relink a meeting (blocks spoofed updates by UID).
            if not existing.from_addr or email.from_addr.lower() != existing.from_addr.lower():
                out.skipped = "invite update from a different sender"
                return out
            if call.sequence >= existing.sequence:
                fields = dict(title=call.title, due=call.due, status=call.status, sequence=call.sequence)
                if call.join_link or call.phone:  # an update without a link keeps the stored one
                    fields.update(join_link=call.join_link, phone=call.phone)
                if not existing.priority_locked:
                    fields["priority"], fields["reason"] = prioritize(
                        replace(existing, due=call.due), email, now, config.user_email, config.vips)
                out.updated.append(store.update(existing.page_id, **fields))
            return out
        if call.status == "Cancelled":
            out.skipped = "cancel for unknown invite"
            return out
        out.created.append(store.create(_finish(call, email, now, config)))
        # Invites can also ask for prep work ("bring your top 3 bets").
        prep = [t for t in extract_with_ai(email, now, ai_call) if t.type != "Call"]
        _create_all([replace(t, key=f"{email.message_id}#{i + 1}") for i, t in enumerate(prep)],
                    email, store, now, config, out)
        return out

    if _seen(email, store):
        out.skipped = "already processed"
        return out
    tasks = extract_with_ai(email, now, ai_call)
    if not tasks:
        out.skipped = "no task found"
        return out
    _create_all(tasks, email, store, now, config, out)
    return out
