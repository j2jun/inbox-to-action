"""Score how many attack emails get through (attack success rate, lower is better).

    python3 -m inbox_to_action.redteam redteam/ [--live]

Offline, AI attacks run against an obedient model: it follows any planted instruction whose trigger text
reaches it. That is the worst case, so only defenses outside the model (what the model is shown, what the
pipeline accepts back) count offline. --live sends the attacks to the real model (costs API credits).
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .ai import anthropic_call
from .parse import parse_eml
from .pipeline import Config, process
from .store import MemoryStore

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "expected.json"


def obedient_model(attacks: dict):
    def call(request: dict) -> dict:
        text = request["messages"][0]["content"]
        tasks = next((a["obeys"]["tasks"] for a in attacks.values() if "obeys" in a and a["obeys"]["trigger"] in text), [])
        return {"content": [{"type": "tool_use", "name": "record_tasks", "input": {"tasks": tasks}}]}
    return call


def succeeded(rule: dict, tasks: list) -> bool:
    if "tasks_at_least" in rule:
        return len(tasks) >= rule["tasks_at_least"]
    return any(all(re.search(rx, str(getattr(t, field) or "")) for field, rx in rule.items()) for t in tasks)


def run(folder: str, live: bool = False, out=print) -> dict[str, bool]:
    attacks = json.loads((Path(folder) / "attacks.json").read_text())["attacks"]
    ctx = json.loads(SAMPLES.read_text())["context"]
    tz = ZoneInfo(ctx["timezone"])
    now = datetime.fromisoformat(ctx["now"]).astimezone(tz)
    config = Config(user_email=ctx["user"].split("<")[-1].rstrip(">").lower(), vips=set(ctx["vip"]), tz=tz)
    ai = anthropic_call if live else obedient_model(attacks)
    results = {}
    for name, a in sorted(attacks.items()):
        result = process(parse_eml((Path(folder) / name).read_bytes()), MemoryStore(), now, config, ai)
        results[name] = succeeded(a["succeeds_if"], result.created + result.updated)
        out(f"{'BREACH ' if results[name] else 'blocked'}  [{a['layer']:5}] {name}: {a['goal']}")
    hits = sum(results.values())
    out(f"attack success rate {hits}/{len(results)} ({100 * hits / len(results):.0f}%)")
    return results


if __name__ == "__main__":
    args = sys.argv[1:]
    run(next((a for a in args if not a.startswith("--")), "redteam"), "--live" in args)
