"""Score the pipeline against samples/expected.json (NFR-2).

    python3 -m inbox_to_action.eval samples/ [--live]
"""
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .ai import anthropic_call, make_stub_call
from .parse import parse_eml
from .pipeline import Config, process
from .store import MemoryStore

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "ai"
TARGETS = {"call_recall": 95.0, "miss_invent": 10.0, "priority": 90.0}


def _due(value, tz):
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(tz) if "T" in value else date.fromisoformat(value)


def _matches(want: dict, got, tz) -> bool:
    return (got.type == want["type"]
            and all(w.lower() in got.title.lower() for w in want["title_has"])
            and got.due == _due(want.get("due"), tz)
            and ("platform" not in want or got.platform == want["platform"]))


def evaluate(samples: str, live: bool = False, out=print) -> dict:
    root = Path(samples)
    spec = json.loads((root / "expected.json").read_text())
    ctx, expected = spec["context"], spec["emails"]
    tz = ZoneInfo(ctx["timezone"])
    now = datetime.fromisoformat(ctx["now"]).astimezone(tz)
    config = Config(user_email=ctx["user"].split("<")[-1].rstrip(">").lower(), vips=set(ctx["vip"]), tz=tz,
                    jira_sites=set(ctx.get("jira_sites", [])), trusted_domains=set(ctx.get("trusted_domains", [])))
    ai = anthropic_call if live else make_stub_call(FIXTURES)
    store = MemoryStore()

    calls_expected = calls_found = 0
    other_expected = missed = invented = 0
    matched = right_priority = 0
    for name in sorted(expected):
        want = expected[name]
        result = process(parse_eml((root / name).read_bytes()), store, now, config, ai)
        produced = result.created + result.updated
        if "skip" in want:
            if result.skipped != want["skip"]:
                out(f"MISS {name}: expected skip '{want['skip']}', got {result.skipped!r} / {len(produced)} task(s)")
            invented += len(produced)
            continue
        if want.get("status") and not any(t.status == want["status"] for t in result.updated):
            out(f"MISS {name}: expected status {want['status']}")
            missed += 1
        unused = list(produced)
        for task in want["tasks"]:
            is_call = task["type"] == "Call"
            calls_expected += is_call
            other_expected += not is_call
            hit = next((t for t in unused if _matches(task, t, tz)), None)
            if not hit:
                out(f"MISS {name}: {task['type']} {task['title_has']} due {task.get('due')}")
                missed += not is_call
                continue
            unused.remove(hit)
            calls_found += is_call
            matched += 1
            if hit.priority == task["priority"]:
                right_priority += 1
            else:
                out(f"PRIORITY {name}: '{hit.title}' expected {task['priority']}, got {hit.priority} ({hit.reason})")
        if not want.get("status"):
            for t in unused:
                out(f"INVENTED {name}: {t.type} '{t.title}' due {t.due}")
            invented += len(unused)

    metrics = {
        "call_recall": round(100 * calls_found / max(calls_expected, 1), 1),
        "miss_invent": round(100 * (missed + invented) / max(other_expected, 1), 1),
        "priority": round(100 * right_priority / max(matched, 1), 1),
    }
    out(f"call recall {metrics['call_recall']:g}% · action miss/invent {metrics['miss_invent']:g}% · "
        f"priority {metrics['priority']:g}%")
    return metrics


def main(argv: list[str]) -> int:
    live = "--live" in argv
    paths = [a for a in argv if not a.startswith("--")]
    m = evaluate(paths[0] if paths else "samples", live)
    ok = (m["call_recall"] >= TARGETS["call_recall"] and m["miss_invent"] <= TARGETS["miss_invent"]
          and m["priority"] >= TARGETS["priority"])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
