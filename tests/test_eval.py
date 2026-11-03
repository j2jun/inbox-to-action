from inbox_to_action import eval as ev


def test_eval_passes_offline(capsys):
    metrics = ev.evaluate("samples")
    assert metrics == {"call_recall": 100.0, "miss_invent": 0.0, "priority": 100.0}, capsys.readouterr().out
    assert ev.main(["samples"]) == 0
    assert "call recall 100% · action miss/invent 0% · priority 100%" in capsys.readouterr().out
