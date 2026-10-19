"""Every pipeline message in a Slack thread must link its Actions run.

Son of Anton's diagnose_failure finds a thread's runs by reading these links,
and a person reading the thread needs them too. Without one, a failed run was
reported as "the pipeline reported no reason" with nothing to follow.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def test_slack_messages_link_their_run():
    from src.slack_notify import build_message
    from src.slack_run import progress_text
    assert "Run: RUN" in build_message({"docs_pr": "https://github.com/x/pull/1"}, {"status": "success"}, "RUN")
    assert "Run: RUN" in progress_text("F", [], "material", None, {}, "RUN")
    for name in ("slack_run.py", "slack_notify.py"):
        assert "GITHUB_RUN_ID" in (ROOT / "src" / name).read_text()
    bulk = (WORKFLOWS / "bulk-replace.yml").read_text()
    assert "actions/runs/" in bulk and "GITHUB_RUN_ID" in bulk


def test_crash_is_not_reported_as_no_reason():
    from src.slack_notify import build_message
    assert "died before it could say anything useful" in build_message({}, None, "RUN")
    assert build_message({}, {"status": "failed", "stage": "drafting"}, "RUN") is None  # progress message says it
