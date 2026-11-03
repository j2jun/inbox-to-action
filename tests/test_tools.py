import pytest

from conftest import NOW, as_due, expected
from inbox_to_action.tools import detect_tool_task

TITLES = {
    "12": "PLAT-2291 Sev-2 billing sync failing for EU accounts",
    "13": "Draft launch blog post",
    "14": "Review PR #482: Add pricing page experiment flag",
    "15": "Approve expense report: Ravi Menon - Customer visits Oct",
    "16": "Sign Northwind MSA Amendment 3",
    "19": "Reply to Ravi Menon in 2027 Pricing Proposal",
}


@pytest.mark.parametrize("prefix", sorted(TITLES))
def test_tool_tasks(load, prefix):
    want = expected(prefix)["tasks"][0]
    tasks = detect_tool_task(load(prefix), NOW)
    assert len(tasks) == 1
    t = tasks[0]
    assert t.title == TITLES[prefix]
    assert t.type == want["type"]
    assert t.due == as_due(want["due"])
    for word in want["title_has"]:
        assert word.lower() in t.title.lower()
    if "source_item" in want:
        assert t.source_item == want["source_item"]
    assert t.key == load(prefix).message_id + "#0"


def test_unknown_sender(load):
    assert detect_tool_task(load("11"), NOW) is None
