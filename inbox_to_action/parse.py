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


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_INVISIBLE_TEXT = {"script", "style", "title", "iframe", "noembed", "noframes"}  # raw-text content the reader never sees
_COMPUTED = re.compile(r"\b(calc|var|min|max|clamp|env|attr)\s*\(", re.I)
_NUMBER = re.compile(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?)\s*(%|[a-z]*)", re.I)
# Largest size that still counts as unreadable, per unit (relative units are against ~16px).
_TINY = {"%": 10, "em": 0.1, "rem": 0.1, "ex": 0.2, "ch": 0.2, "vw": 0.1, "vh": 0.1, "vmin": 0.1, "vmax": 0.1,
         "in": 0.01, "cm": 0.03, "mm": 0.3, "pc": 0.06, "q": 1}
_SIZE_PROPS = {"font-size", "height", "width", "max-height", "max-width", "line-height"}


def _css(value: str) -> str:
    """Undo CSS comments and escapes so "displ\\61y:/**/none" reads as "display:none"."""
    value = re.sub(r"(?s)/\*.*?\*/", "", value)
    value = re.sub(r"\\([0-9a-fA-F]{1,6})\s?", lambda m: chr(min(int(m.group(1), 16), 0x10FFFF)), value)
    return re.sub(r"\\(.)", r"\1", value)


def _style_hides(style: str) -> bool:
    for decl in _css(style).split(";"):
        prop, _, value = decl.partition(":")
        prop, value = prop.strip().lower(), re.sub(r"!\s*important\s*$", "", value.strip().lower()).strip()
        if prop == "display" and value == "none" or prop == "visibility" and value in {"hidden", "collapse"}:
            return True
        if prop == "opacity" or prop in _SIZE_PROPS:
            if _COMPUTED.search(value):
                return True  # can't evaluate calc()/var(): fail closed
            m = _NUMBER.match(value)
            if not m:
                continue  # keywords like auto / 100% / normal
            n, unit = float(m.group(1)), m.group(2)
            if prop == "opacity":
                hidden = (n / 100 if unit == "%" else n) <= 0.05
            else:  # ponytail: one element only; nested 50% inside 20% compounds past this check
                hidden = n <= _TINY.get(unit, 1)  # px, pt, unitless: ≤ 1
            if hidden:  # negatives count too
                return True
    return False


def _hides(tag: str, attrs: list) -> bool:
    # Every occurrence counts: a duplicate style= must not let one copy slip past the check.
    return tag not in _VOID and any(n == "hidden" or (n == "style" and _style_hides(v or "")) for n, v in attrs)


class _VisibleHTML(HTMLParser):
    """Rebuilds the HTML without subtrees the reader can't see, so hidden text never reaches the model."""
    # Raw-text elements as browsers parse them ("</div>" inside a textarea closes nothing). Set explicitly
    # because Python 3.12's default list lacks textarea/title.
    CDATA_CONTENT_ELEMENTS = ("script", "style", "xmp", "iframe", "noembed", "noframes", "textarea", "title")

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out, self.hidden_tag, self.depth, self.raw = [], None, 0, None

    def handle_starttag(self, tag, attrs):
        if tag in _INVISIBLE_TEXT:
            self.raw = tag
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
        if tag == self.raw:
            self.raw = None
        if self.hidden_tag:
            self.depth -= tag == self.hidden_tag
            if self.depth == 0:
                self.hidden_tag = None
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.hidden_tag and not self.raw:
            self.out.append(data)

    handle_entityref = lambda self, name: self.handle_data(f"&{name};")
    handle_charref = lambda self, name: self.handle_data(f"&#{name};")


def _visible(s: str) -> str:
    # ponytail: inline styles and the hidden attribute only; class-based CSS and white-on-white text still pass.
    # Everything after <plaintext> is raw text to a browser (no tag ever closes again), so a later "</div>"
    # can't end a hidden ancestor. Obsolete tag: drop the rest rather than model it.
    s = re.split(r"(?i)<plaintext\b", s, maxsplit=1)[0]
    p = _VisibleHTML()
    p.feed(s)
    # No close(): it would flush an unfinished trailing tag ("<div style=...secret") as visible text.
    return "".join(p.out)


def _html_to_text(s: str) -> str:
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
