"""The final Slack message for a pipeline run.

Moved out of the workflow YAML so it can be tested. The live progress
message (src/slack_run.py) already shows the stages; this posts the outcome:
the PR links, where the doc page landed, what the material did not cover,
and anything to check before merging. Written in Son of Anton's voice,
without dropping a single fact.

Run as: python3 -m src.slack_notify
"""
import json
import os
import urllib.request
from pathlib import Path

from src.slack_run import RESULTS_FILE, STATUS_FILE

TICK = chr(96)


def _code(s: str) -> str:
    return f"{TICK}{s}{TICK}"


def build_message(pr: dict, status: dict | None, run_url: str) -> str | None:
    """The outcome message, or None when the progress message already said it all."""
    status = status or {}
    lines = []
    if pr.get("releases_pr"):
        lines.append(f":white_check_mark: *Release note PR:* {pr['releases_pr']}")
        if pr.get("release_note_file"):
            lines.append(f"  File: {_code(pr['release_note_file'])} in FlytBaseAILabs/flytbase-releases")
    if pr.get("docs_pr"):
        lines.append(f":white_check_mark: *Doc page PR:* {pr['docs_pr']}")
        if pr.get("docs_section"):
            lines.append(f"  Placed under: *{pr['docs_section']}*")
        docs_file = pr.get("docs_file") or pr.get("doc_page_file")
        if docs_file:
            lines.append(f"  File: {_code(docs_file)} in FlytBaseAILabs/flytbase-docs")
        if pr.get("docs_impacted"):
            files = ", ".join(_code(f) for f in pr["docs_impacted"])
            lines.append(f"  Also adds cross-links to {len(pr['docs_impacted'])} existing page(s): {files}")
    if pr.get("feature_flag"):
        lines.append(f"  Access section added for feature flag {_code(pr['feature_flag'])}.")

    if pr.get("doc_gaps"):
        lines += ["", ":jigsaw: *Your material didn't cover these sections, so I left them off the doc page instead of inventing them:*"]
        lines += [f"  • {g}" for g in pr["doc_gaps"]]
        lines.append("  Send me the details and I'll add them to the PR.")
    attention = list(pr.get("needs_attention") or [])
    if attention:
        lines += ["", f":warning: *Needs your eyes before merging ({len(attention)}).* "
                      "I fixed what I could; these I won't guess at:"] + [f"  • {w}" for w in attention]
        lines.append("  Same list is at the top of the PR, with a needs-attention label so nobody can pretend they missed it.")
    others = [w for w in pr.get("warnings") or [] if w not in attention]
    if others:
        lines += ["", ":warning: *Check before merging:*"] + [f"  • {w}" for w in others]
    if pr.get("fixes"):
        lines += ["", f":wrench: Fixed {len(pr['fixes'])} thing(s) myself on the way. Details are in the PR, if you enjoy reading."]
    if pr.get("errors"):
        lines += ["", ":x: *Errors:*"] + [f"  • {e}" for e in pr["errors"]]

    has_pr = bool(pr.get("releases_pr") or pr.get("docs_pr"))
    if not has_pr:
        if status.get("status") == "failed":
            return None  # the progress message already names the stage and the reason
        if not status:
            lines = [":x: The run died before it could say anything useful, which is impressive in its own way.",
                     "Ask me why it failed and I'll read the logs."]

    if has_pr:
        lines.insert(0, "Done. Try not to look so surprised.")
    lines += ["", f"Run: {run_url}"]
    if has_pr:
        lines.append("Want me to change anything?")
    return "\n".join(lines)


def main() -> int:
    env = os.environ
    token = env.get("SLACK_BOT_TOKEN", "")
    channel, thread_ts = env.get("PL_SLACK_CHANNEL", ""), env.get("PL_SLACK_THREAD_TS", "")
    run_url = "{}/{}/actions/runs/{}".format(env.get("GITHUB_SERVER_URL", "https://github.com"),
                                            env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_RUN_ID", ""))
    pr = json.loads(Path(RESULTS_FILE).read_text()) if Path(RESULTS_FILE).exists() else {}
    status = json.loads(Path(STATUS_FILE).read_text()) if Path(STATUS_FILE).exists() else None
    message = build_message(pr, status, run_url)
    if message is None:
        print("Nothing to add: the progress message already reports the failure.")
        return 0
    if not (token and channel and thread_ts):
        print(message)
        return 0
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps({"channel": channel, "thread_ts": thread_ts, "text": message, "unfurl_links": False}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    if not data.get("ok"):
        print(f"Slack rejected the message: {data.get('error')}")
        return 1
    print(f"Posted to Slack: {channel}/{thread_ts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
