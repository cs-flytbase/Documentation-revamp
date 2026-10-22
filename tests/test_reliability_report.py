"""Weekly reliability report: per-capability rates from the run journal,
posted every week, never silently skipped."""
from datetime import datetime, timedelta, timezone

from src import reliability_report as rr

NOW = datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)


def rec(stage, status, days_ago=1, run="gh-1-1", detail=""):
    ts = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"run_id": run, "channel": "C1", "thread_ts": "1.2", "stage": stage, "status": status,
            "detail": detail, "duration_ms": None, "ts": ts}


WEEK = [
    rec("drafting", "ok"), rec("drafting", "ok"), rec("drafting", "repaired"), rec("drafting", "failed", detail="model 502"),
    rec("vision", "flagged", detail="Image description failed for a.png; check it."),
    rec("vision", "flagged", detail="Image description failed for b-02.png; check it."),
    rec("vision", "ok"),
    rec("file_manifest", "flagged", run="thread:C1:1.2", detail="named 22, received 12, missing 10"),
    rec("invariant:prs", "ok"), rec("invariant:prs", "failed", detail="missing: doc page PR"),
    rec("tool:trigger_pipeline", "failed", run="thread:C1:1.2", detail="GitHub 502"),
]
LAST_WEEK = [rec("drafting", "ok", days_ago=9), rec("drafting", "failed", days_ago=10)]


def test_rates_add_up_and_good_is_ok_plus_repaired():
    r = rr.rates(WEEK)["drafting"]
    assert r["n"] == 4 and r["ok"] == 50 and r["repaired"] == 25 and r["failed"] == 25 and r["good"] == 75
    assert sum(r[s] for s in rr.STATUSES) == 100


def test_weeks_are_split_and_malformed_records_are_counted_not_crashed_on():
    junk = [{"stage": "x"}, rec("drafting", "great"), {**rec("drafting", "ok"), "ts": "nope"}, "string", None]
    this, last, bad = rr.split_weeks(WEEK + LAST_WEEK + junk + [rec("drafting", "ok", days_ago=20)], NOW)
    assert len(this) == len(WEEK) and len(last) == 2 and bad == 5


def test_report_math_and_week_over_week():
    rep = rr.build(WEEK + LAST_WEEK, NOW, malformed_api=1)
    assert rep["runs"] == 1 and rep["threads"] == 1 and rep["malformed"] == 1
    caps = {s: (r, d) for s, r, d in rep["caps"]}
    assert list(caps) == ["file_manifest", "vision", "drafting"]  # display order, tools and invariants apart
    assert caps["drafting"][1] == "+25"  # 75% good vs 50% last week
    assert caps["vision"][1] == "new"
    # overall: 4 good out of 8 capability records (tool and invariant records excluded)
    assert rep["overall"] == 50 and rep["overall_before"] == 50
    assert [s for s, _, _ in rep["invariants"]] == ["invariant:prs"]
    assert rep["tools"] == [("tool:trigger_pipeline", 1)]


def test_recurring_reasons_group_by_shape_not_by_file_name():
    reasons = rr.top_reasons(WEEK)
    assert reasons[0][1] == 2 and reasons[0][2] == "vision"
    assert reasons[0][0].startswith("Image description failed for")
    assert all(not s.startswith("invariant:") for _, _, s in reasons)


def test_slack_message_has_the_table_reasons_and_voice():
    msg = rr.slack_message(rr.build(WEEK + LAST_WEEK, NOW), "https://run")
    assert msg.startswith(":bar_chart: *Reliability report, 2026-10-19 to 2026-10-26.*")
    assert "Stages delivered without a person: *50%*. Same as last week." in msg
    caps_table, inv_table = msg.split("```")[1], msg.split("```")[3]
    assert "Image descriptions" in caps_table and "Drafting" in caps_table and "PRs expected" not in caps_table
    assert "PRs expected = created" in inv_table
    assert "*Tool errors:* Tool trigger_pipeline x1" in msg
    assert "Most common reasons" in msg and "Full table: https://run" in msg


def test_a_quiet_week_still_posts():
    rep = rr.build([], NOW)
    assert rep["records"] == 0 and rep["overall"] is None
    assert rr.slack_message(rep).startswith(":zzz: *Reliability report, 2026-10-19 to 2026-10-26.* Quiet week")
    assert "Quiet week: no runs." in rr.markdown(rep)


def test_markdown_table():
    md = rr.markdown(rr.build(WEEK, NOW))
    assert "| Drafting | 4 | 50% | 25% | 0% | 25% | new |" in md
    assert "### Invariants" in md and "| PRs expected = created | 2 | 50% | 0% | 0% | 50% | new |" in md


class Fetch:
    def __init__(self, journal=(200, None), slack=(200, {"ok": True}), slack_raises=None):
        self.journal, self.slack, self.slack_raises, self.calls = journal, slack, slack_raises, []

    def __call__(self, method, url, headers=None, body=None, raw=False):
        self.calls.append((method, url, headers, body))
        if "/journal" in url:
            return self.journal
        if self.slack_raises:
            raise self.slack_raises
        return self.slack


def _env(tmp_path, **kw):
    return {"CONTENT_TOKEN": "tok", "SLACK_BOT_TOKEN": "x", "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"), **kw}


def test_main_reads_two_weeks_with_the_token_and_posts_once(tmp_path):
    f = Fetch(journal=(200, {"records": WEEK + LAST_WEEK, "malformed": 0}))
    assert rr.main(_env(tmp_path), f, NOW) == 0
    method, url, headers, _ = f.calls[0]
    assert url == "https://docagent-worker.se-team-charlie.workers.dev/journal?since=2026-10-12T04:00:00Z"
    assert headers == {"X-Content-Token": "tok"}
    slack_posts = [c for c in f.calls if "slack.com" in c[1]]
    assert len(slack_posts) == 1 and slack_posts[0][3]["channel"] == "C0BGB8F56JX"
    assert "## Reliability report" in (tmp_path / "summary.md").read_text()


def test_main_uses_the_configured_channel_and_worker(tmp_path):
    f = Fetch(journal=(200, {"records": [], "malformed": 0}))
    assert rr.main(_env(tmp_path, SLACK_ALERT_CHANNEL="CX", WORKER_URL="https://other/"), f, NOW) == 0
    assert f.calls[0][1].startswith("https://other/journal?since=")
    assert f.calls[1][3]["channel"] == "CX" and "Quiet week" in f.calls[1][3]["text"]


def test_an_unreadable_journal_is_reported_and_fails_the_job(tmp_path):
    for journal in [(401, {"error": "unauthorized"}), (0, "timed out"), (200, {"nope": 1})]:
        f = Fetch(journal=journal)
        assert rr.main(_env(tmp_path), f, NOW) == 1
        text = [c for c in f.calls if "slack.com" in c[1]][0][3]["text"]
        assert text.startswith(":x: *Reliability report: could not read the run journal*")
    assert rr.main(_env(tmp_path, CONTENT_TOKEN=""), Fetch(), NOW) == 1


def test_a_failed_slack_post_is_written_to_the_summary_and_fails_the_job(tmp_path):
    ok_journal = (200, {"records": WEEK, "malformed": 0})
    assert rr.main(_env(tmp_path), Fetch(journal=ok_journal, slack=(200, {"ok": False})), NOW) == 1
    assert "could not be posted to Slack (chat.postMessage did not return ok)" in (tmp_path / "summary.md").read_text()
    assert rr.main(_env(tmp_path), Fetch(journal=ok_journal, slack_raises=OSError("dns")), NOW) == 1
    assert "OSError: dns" in (tmp_path / "summary.md").read_text()
    assert rr.main(_env(tmp_path, SLACK_BOT_TOKEN=""), Fetch(journal=ok_journal), NOW) == 1


def test_reason_keys_keep_http_codes_but_drop_names_and_counts():
    assert rr.reason_key("model 502") == "model 502"
    assert rr.reason_key("GitHub 403 on a.md") != rr.reason_key("GitHub 502 on a.md")
    assert rr.reason_key("named 22, received 12") == rr.reason_key("named 9, received 3")
    assert rr.reason_key("") == "(no detail)"
