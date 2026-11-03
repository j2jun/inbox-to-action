# Vercel entry point: every /api, /login, /logout and /webhook request is rewritten here (see vercel.json).
from inbox_to_action.web import app  # noqa: F401
