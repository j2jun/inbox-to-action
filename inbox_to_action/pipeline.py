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
    jira_sites: set[str] = field(default_factory=set)
    trusted_domains: set[str] = field(default_factory=set)  # e.g. your company; never a public provider like gmail.com

    @classmethod
    def from_env(cls, env=os.environ) -> "Config":
        return cls(
            user_email=env.get("USER_EMAIL", "").lower(),
            vips={v.strip().lower() for v in env.get("VIP_EMAILS", "").split(",") if v.strip()},
            tz=ZoneInfo(env.get("TIMEZONE", "America/Los_Angeles")),
            jira_sites={s.strip().lower() for s in env.get("JIRA_SITES", "").split(",") if s.strip()},
            trusted_domains={d.strip().lower() for d in env.get("TRUSTED_DOMAINS", "").split(",") if d.strip()},
        )


@dataclass
class Outcome:
    created: list[Task] = field(default_factory=list)
    updated: list[Task] = field(default_factory=list)
    skipped: str | None = None


def _finish(task: Task, email: Email, now: datetime, config: Config) -> Task:
    level, reason = prioritize(task, email, now, config.user_email, config.vips)
    snippet = re.sub(r"\s+", " ", _trim(email.body))[:200]
    raw_id = email.message_id.strip("<>")
    if "@" in raw_id:  # a real RFC 822 Message-ID
        link = "https://mail.google.com/mail/u/0/#search/rfc822msgid%3A" + quote(raw_id, safe="")
    else:  # Zapier's Gmail trigger sends Gmail's own message id
        link = "https://mail.google.com/mail/u/0/#all/" + quote(email.thread_id or raw_id, safe="")
    return replace(task, priority=level, reason=reason, snippet=snippet, source_email=link)


def _trusted(email: Email, config: Config) -> bool:
    """AI tasks go straight to your list only from VIPs or TRUSTED_DOMAINS, and only from authenticated mail."""
    sender = email.from_addr.lower()
    domain = sender.rsplit("@", 1)[-1]
    if sender not in config.vips and domain not in config.trusted_domains:
        return False
    auth = email.headers.get("authentication-results")
    if auth is None:
        # ponytail: Zapier payloads carry no auth headers, so a spoofed VIP / trusted-domain From is trusted
        # there; Gmail's spam filter is the backstop. Pass the header through Zapier to close this.
        return True
    # Header present: require a positive pass aligned with the sender's domain, not merely "no fail".
    aligned_dkim = re.search(r"\bdkim=pass\b[^;]*header\.d=(?:[\w-]+\.)*" + re.escape(domain) + r"\b", auth, re.I)
    return bool(re.search(r"\bdmarc=pass\b", auth, re.I) or aligned_dkim)


def _for_review(tasks: list[Task], email: Email, config: Config) -> list[Task]:
    # A model reading attacker text can be talked into anything, so outside senders' tasks wait for your OK.
    return tasks if _trusted(email, config) else [replace(t, status="Review") for t in tasks]


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
    tool_tasks = detect_tool_task(email, now, config.jira_sites)
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
        prep = _for_review([t for t in extract_with_ai(email, now, ai_call) if t.type != "Call"], email, config)
        _create_all([replace(t, key=f"{email.message_id}#{i + 1}") for i, t in enumerate(prep)],
                    email, store, now, config, out)
        return out

    if _seen(email, store):
        out.skipped = "already processed"
        return out
    tasks = _for_review(extract_with_ai(email, now, ai_call), email, config)
    if not tasks:
        out.skipped = "no task found"
        return out
    _create_all(tasks, email, store, now, config, out)
    return out
