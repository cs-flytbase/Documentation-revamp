"""Every pipeline message in a Slack thread must link its Actions run.

Son of Anton's diagnose_failure finds a thread's runs by reading these links,
and a person reading the thread needs them too. Without one, a failed run was
reported as "the pipeline reported no reason" with nothing to follow.
"""
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github/workflows"


def test_slack_messages_link_their_run():
    for name in ("slack-pipeline.yml", "bulk-replace.yml"):
        src = (WORKFLOWS / name).read_text()
        assert "chat.postMessage" in src
        assert "actions/runs/" in src and "GITHUB_RUN_ID" in src, f"{name} posts to Slack without the run link"


def test_crash_is_not_reported_as_no_reason():
    src = (WORKFLOWS / "slack-pipeline.yml").read_text()
    assert "crashed before it could publish" in src
