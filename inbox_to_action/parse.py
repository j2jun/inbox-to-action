"""Turn a raw .eml or a Zapier webhook payload into an Email."""
import email
import email.policy
import hashlib
import html
import re
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from .models import Email


def _addrs(value: str | None) -> list[str]:
    return [a.lower() for _, a in getaddresses([value or ""]) if a]


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def _angle(message_id: str) -> str:
    # Message-IDs always look like "<…>", so they can never collide with "uid:" / "link:" call keys.
    if len(message_id) > 300:  # oversized IDs would be truncated by Notion (2000 chars) and never match again
        return f"<{hashlib.sha256(message_id.encode()).hexdigest()[:32]}@long-id>"
    return message_id if message_id.startswith("<") and message_id.endswith(">") else f"<{message_id.strip('<>')}>"


def _synthetic_id(raw: bytes) -> str:
    # Stable stand-in when an email has no Message-ID, so dedupe never matches on an empty key.
    return f"<{hashlib.sha256(raw).hexdigest()[:32]}@inbox-to-action>"


def parse_eml(raw: bytes) -> Email:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    text, htm, ics = None, None, None
    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = part.get_content_type()
        if ctype == "text/calendar" and ics is None:
            ics = part.get_content()
        elif ctype == "text/plain" and text is None and part.get_content_disposition() != "attachment":
            text = part.get_content()
        elif ctype == "text/html" and htm is None:
            htm = part.get_content()
    name, addr = parseaddr(msg.get("From", ""))
    return Email(
        message_id=_angle((msg.get("Message-ID") or "").strip()) if (msg.get("Message-ID") or "").strip() else _synthetic_id(raw),
        subject=str(msg.get("Subject", "")),
        from_name=name,
        from_addr=addr.lower(),
        to=_addrs(msg.get("To")),
        cc=_addrs(msg.get("Cc")),
        date=parsedate_to_datetime(msg.get("Date")),
        headers={k.lower(): str(v) for k, v in msg.items()},
        body=text if text is not None else _html_to_text(htm or ""),
        ics=ics,
        thread_id=None,
    )


def email_from_payload(payload: dict, ics: str | None) -> Email:
    """Build an Email from the JSON the Zapier webhook sends (see plan Task 9)."""
    name, addr = parseaddr(payload.get("from") or "")
    headers = {}
    for key, header in (("list_unsubscribe", "list-unsubscribe"), ("precedence", "precedence")):
        if payload.get(key):
            headers[header] = str(payload[key])
    body = payload.get("body") or ""
    if "<" in body and re.search(r"(?i)</(p|div|br|html|body)>", body):
        body = _html_to_text(body)
    return Email(
        message_id=_angle(str(payload["message_id"]).strip()),  # web.py rejects an empty one with 400
        subject=str(payload.get("subject") or ""),
        from_name=name,
        from_addr=addr.lower(),
        to=_addrs(payload.get("to")),
        cc=_addrs(payload.get("cc")),
        date=parsedate_to_datetime(payload["date"]),
        headers=headers,
        body=body,
        ics=ics,
        thread_id=payload.get("thread_id"),
    )
