"""Single-user login: one password, then a signed session cookie."""
import hashlib
import hmac

TTL = 30 * 86400
MIN_PASSWORD = 12  # ponytail: no login rate limit (serverless instances share no memory); length makes guessing impractical


def _sign(secret: str, msg: str) -> str:
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()


def make_session(secret: str, now_ts: float, ttl: int = TTL) -> str:
    msg = f"v1.{int(now_ts) + ttl}"
    return f"{msg}.{_sign(secret, msg)}"


def check_session(cookie: str | None, secret: str, now_ts: float) -> bool:
    if not cookie or not secret:
        return False
    try:
        version, expiry, sig = cookie.split(".")
        expiry_ts = int(expiry)
    except ValueError:
        return False
    return (version == "v1" and expiry_ts > now_ts
            and hmac.compare_digest(sig, _sign(secret, f"v1.{expiry}")))


def check_password(given: str, expected: str) -> bool:
    # Fails closed when no password is configured.
    return len(expected) >= MIN_PASSWORD and hmac.compare_digest(given.encode(), expected.encode())


def session_key(session_secret: str, password: str) -> str:
    """Sessions are signed with the password mixed in, so changing APP_PASSWORD signs out every device."""
    return session_secret + ":" + hashlib.sha256(password.encode()).hexdigest() if session_secret else ""
