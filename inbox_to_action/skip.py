"""Decide which emails are not worth a task. Task-tool emails are checked before this (see pipeline)."""
import re
from datetime import datetime

from .models import Email

_MARKETING = re.compile(r"\b(\d+% off|sale|deal|offer|upgrade|promo)\b", re.I)
_RECEIPT = re.compile(r"\breceipt\b|order confirmation", re.I)


def skip_reason(email: Email, now: datetime) -> str | None:
    text = f"{email.subject}\n{email.body}"
    bulk = "list-unsubscribe" in email.headers or email.headers.get("precedence", "").lower() == "bulk"
    if bulk:
        return "marketing" if _MARKETING.search(text) else "newsletter"
    if re.search(r"no-?reply", email.from_addr) and _RECEIPT.search(text):
        return "receipt"
    return None
