"""Turn a raw .eml or a Zapier webhook payload into an Email."""
import email
import email.policy
import hashlib
import html
from html.parser import HTMLParser
import re
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from .models import Email


def _addrs(value: str | None) -> list[str]:
    return [a.lower() for _, a in getaddresses([value or ""]) if a]


_HIDING = re.compile(r"display\s*:\s*none|visibility\s*:\s*(hidden|collapse)|font-size\s*:\s*0|opacity\s*:\s*[0.]+\s*%?\s*(;|!|$)"
                     r"|max-height\s*:\s*0|(?<![\w-])(height|width)\s*:\s*0", re.I)
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# Elements whose content browsers read as raw text (so "</div>" inside doesn't close anything); dropped whole.
_RAW = re.compile(r"(?is)<(script|style|textarea|title|xmp|noembed|noframes|noscript|iframe)\b.*?</\1\s*>|<plaintext\b.*")


def _css(value: str) -> str:
    """Undo CSS comments and escapes so "displ\\61y:/**/none" reads as "display:none"."""
    value = re.sub(r"(?s)/\*.*?\*/", "", value)
    value = re.sub(r"\\([0-9a-fA-F]{1,6})\s?", lambda m: chr(min(int(m.group(1), 16), 0x10FFFF)), value)
    return re.sub(r"\\(.)", r"\1", value)


def _hides(tag: str, attrs: list) -> bool:
    # Every occurrence counts: a duplicate style= must not let one copy slip past the check.
    return tag not in _VOID and any(n == "hidden" or (n == "style" and _HIDING.search(_css(v or ""))) for n, v in attrs)


class _VisibleHTML(HTMLParser):
    """Rebuilds the HTML without subtrees the reader can't see, so hidden text never reaches the model."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out, self.hidden_tag, self.depth = [], None, 0

    def handle_starttag(self, tag, attrs):
        if self.hidden_tag:
            self.depth += tag == self.hidden_tag
            return
        if _hides(tag, attrs):
            self.hidden_tag, self.depth = tag, 1
            return
        self.out.append(self.get_starttag_text())

    # Browsers ignore the "/" in "<div hidden/>": the element stays open, so treat it as a plain start tag.
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.hidden_tag:
            self.depth -= tag == self.hidden_tag
            if self.depth == 0:
                self.hidden_tag = None
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.hidden_tag:
            self.out.append(data)

    handle_entityref = lambda self, name: self.handle_data(f"&{name};")
    handle_charref = lambda self, name: self.handle_data(f"&#{name};")


def _visible(s: str) -> str:
    # ponytail: inline styles and the hidden attribute only; class-based CSS and white-on-white text still pass.
    p = _VisibleHTML()
    p.feed(s)
    # No close(): it would flush an unfinished trailing tag ("<div style=...secret") as visible text.
    return "".join(p.out)


def _html_to_text(s: str) -> str:
    s = _RAW.sub("", s)
    s = _visible(s)
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


def _payload_date(value) -> datetime:
    """Zapier may send RFC 2822, ISO 8601 or a Unix timestamp; never reject an email over its date."""
    text = str(value or "").strip()
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        if text.isdigit():
            return datetime.fromtimestamp(int(text[:10]), timezone.utc)
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


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
        date=_payload_date(payload.get("date")),
        headers=headers,
        body=body,
        ics=ics,
        thread_id=payload.get("thread_id"),
    )
