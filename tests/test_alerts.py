from src.alerts import shorten
from src.e2e_check import slack_text
from src.invariants import Result


def test_a_long_alert_becomes_a_few_short_lines_with_the_run_link():
    detail = "; ".join(f"docs: Fleet view-{i}.png duplicates Fleet view.png" for i in range(50))
    text = slack_text([Result("no duplicate assets", False, detail), Result("placement", True, "ok")], "https://gh/run/1")
    out = shorten(text)
    assert len(out) < 700
    assert out.splitlines()[-1] == "Run: https://gh/run/1"
    assert all(len(l) <= 300 for l in out.splitlines())


def test_many_lines_are_capped_and_counted():
    text = "\n".join(f"- problem {i}" for i in range(20)) + "\n\nRun: x"
    out = shorten(text)
    assert "...and 12 more line(s) in the run." in out and out.endswith("Run: x")


def test_short_alerts_are_unchanged():
    assert shorten("All good.\n\nRun: x") == "All good.\n\nRun: x"
