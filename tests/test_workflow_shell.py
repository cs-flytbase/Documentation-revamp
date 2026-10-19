"""The pipeline workflow must not embed Python.

Python inside a shell script inside YAML broke the pipeline twice: once when a
`\\n` escape was flattened, and once when an unescaped double quote inside
`python3 -c "..."` closed the shell string early. The second one shipped to
main and broke every run until someone tried to use it.

Since 2026-10-19 the run and the notification live in src/slack_run.py and
src/slack_notify.py, which are tested like any other code. These tests keep it
that way.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/slack-pipeline.yml"
SRC = WORKFLOW.read_text()
RUN = (ROOT / "src/slack_run.py").read_text()


def _steps():
    return {s["name"]: s for s in yaml.safe_load(SRC)["jobs"]["run-pipeline"]["steps"]}


def test_no_inline_python_anywhere():
    assert "python3 <<" not in SRC and "python3 -c" not in SRC and "PYEOF" not in SRC


def test_run_and_notify_are_modules():
    steps = _steps()
    assert steps["Fetch content from Worker KV and run pipeline"]["run"].strip() == "python3 -m src.slack_run"
    assert steps["Notify Slack"]["run"].strip() == "python3 -m src.slack_notify"
    assert steps["Notify Slack"]["if"] == "always()"


def test_a_failed_run_fails_the_job():
    """Every run used to report success, including ones that produced nothing.
    That hid an 11-day outage."""
    steps = _steps()
    assert steps["Fetch content from Worker KV and run pipeline"]["continue-on-error"] is True
    last = list(steps.values())[-1]
    assert last["if"] == "always()" and last["run"].strip() == "python3 -m src.slack_run --check-status"


def test_payload_values_reach_python_through_the_environment():
    env = _steps()["Fetch content from Worker KV and run pipeline"]["env"]
    for name in ("PL_DOC_SCOPE", "PL_MODE", "PL_TARGET_SECTION", "PL_CONTENT_KEY", "PL_SLACK_THREAD_TS"):
        assert name in env, f"{name} is not passed to the pipeline step"
    notify_env = _steps()["Notify Slack"]["env"]
    assert "PL_SLACK_CHANNEL" in notify_env and "PL_SLACK_THREAD_TS" in notify_env


def test_doc_scope_and_routing_reach_run_pipeline():
    """doc_scope crosses five hops; this is the one inside the run module."""
    assert 'doc_scope=env.get("PL_DOC_SCOPE", "")' in RUN
    assert 'asset_audience=m["asset_audience"]' in RUN and 'feature_flag=m["feature_flag"]' in RUN
