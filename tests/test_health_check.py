"""Daily health check: each check in ok, warn and fail; isolation; retries;
the Slack alert only when something is wrong; a failed alert still recorded."""
import base64
import io
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

import src.health_check as hc
from src.health_check import FAIL, OK, UNKNOWN, WARN

NOW = datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)


def iso(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class Fake:
    """fetch(method, url, headers, body, raw) answered from (method, regex) routes."""

    def __init__(self, routes=None):
        self.routes = list((routes or {}).items())
        self.calls = []

    def add(self, key, value):
        self.routes.insert(0, (key, value))
        return self

    def __call__(self, method, url, headers=None, body=None, raw=False):
        self.calls.append((method, url, body))
        for (m, pattern), value in self.routes:
            if m == method and re.search(pattern, url):
                if isinstance(value, Exception):
                    raise value
                return value(url) if callable(value) else value
        return 404, {"message": "Not Found"}


def logzip(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("refresh-corpus/5_Run corpus refresh.txt", text)
    return buf.getvalue()


def wf_file(cron=None):
    body = "name: x\non:\n" + (f"  schedule:\n    - cron: '{cron}'\n" if cron else "  push:\n")
    return 200, {"content": base64.b64encode(body.encode()).decode()}


# ── OpenRouter ───────────────────────────────────────────────────────

@pytest.mark.parametrize("data,status,expect", [
    ({"limit": 10, "usage": 2, "limit_remaining": 8}, OK, "$8.00 left of $10.00"),
    ({"limit": 10, "usage": 7.5, "limit_remaining": 2.5}, WARN, "$2.50 left"),
    ({"limit": 100, "usage": 85, "limit_remaining": 15}, WARN, "$15.00 left of $100.00"),   # under 20%
    ({"limit": 10, "usage": 10, "limit_remaining": 0}, FAIL, "every run will fail"),
    ({"limit": 10, "usage": 4.5}, OK, "$5.50 left"),                                       # no limit_remaining field
    ({"limit": None, "usage": 40}, OK, "no spending limit"),
])
def test_openrouter_credit(data, status, expect):
    r = hc.check_openrouter(Fake({("GET", "openrouter.ai/api/v1/key"): (200, {"data": data})}), "k")
    assert r.status == status and expect in r.detail
    if status != OK:
        assert "openrouter.ai/settings/credits" in r.fix


def test_openrouter_bad_or_missing_key():
    assert hc.check_openrouter(Fake({("GET", "openrouter"): (401, {"error": "x"})}), "k").status == FAIL
    assert hc.check_openrouter(Fake(), "").detail == "OPENROUTER_API_KEY is not set"


def test_openrouter_thresholds_are_configurable():
    f = Fake({("GET", "openrouter"): (200, {"data": {"limit": 10, "usage": 5, "limit_remaining": 5}})})
    assert hc.check_openrouter(f, "k", min_usd=6).status == WARN
    assert hc.check_openrouter(f, "k", min_usd=1, min_fraction=0.6).status == WARN
    assert hc.check_openrouter(f, "k", min_usd=1, min_fraction=0.1).status == OK


# ── GitHub, Slack, worker ────────────────────────────────────────────

def test_github_write_access():
    f = Fake({
        ("GET", r"/repos/cs-flytbase/Documentation-revamp$"): (200, {"permissions": {"push": True}}),
        ("GET", r"/repos/FlytBaseAILabs/flytbase-docs$"): (200, {"permissions": {"push": False}}),
    })
    ok, ro, missing = hc.check_github_repos(f, "t")
    assert ok.status == OK and ro.status == FAIL and "read-only" in ro.detail
    assert missing.status == FAIL and "HTTP 404" in missing.detail
    assert all(r.status == FAIL for r in hc.check_github_repos(Fake(), ""))


def test_slack_token():
    good = Fake({("POST", "auth.test"): (200, {"ok": True, "user": "son_of_anton", "team": "FlytBase"})})
    assert hc.check_slack(good, "x").status == OK
    bad = hc.check_slack(Fake({("POST", "auth.test"): (200, {"ok": False, "error": "invalid_auth"})}), "x")
    assert bad.status == FAIL and "invalid_auth" in bad.detail
    assert hc.check_slack(Fake(), "").status == FAIL


def test_worker_up_and_content_locked():
    f = Fake({("GET", r"/content\?"): (401, "Unauthorized"), ("GET", r"workers\.dev/$"): (200, "Son of Anton is running")})
    up, locked = hc.check_worker(f, "")
    assert up.status == OK and locked.status == OK


def test_worker_content_open_is_a_security_failure():
    f = Fake({("GET", r"/content\?"): (200, '{"content":"x"}'), ("GET", r"/$"): (200, "Son of Anton is running")})
    assert hc.check_worker(f, "https://w")[1].status == FAIL


def test_worker_down():
    up, locked = hc.check_worker(Fake({("GET", "."): (0, "timed out")}), "https://w")
    assert up.status == FAIL and locked.status == UNKNOWN


# ── Workflows ────────────────────────────────────────────────────────

def test_cron_interval():
    assert hc.cron_interval("0 12 * * 0") == timedelta(days=7)
    assert hc.cron_interval("30 3 * * *") == timedelta(days=1)
    assert hc.cron_interval("0 */6 * * *") == timedelta(hours=6)
    assert hc.cron_interval("0 0 1 * *") == timedelta(days=31)
    assert hc.cron_interval("garbage") == timedelta(days=1)


def _workflows(repo_wfs):
    f = Fake()
    for repo, wfs in repo_wfs.items():
        f.add(("GET", rf"/repos/{repo}/actions/workflows\?"), (200, {"workflows": [w[0] for w in wfs]}))
        for meta, last, cron, sched_last in wfs:
            f.add(("GET", rf"/actions/workflows/{meta['id']}/runs\?status=completed"),
                  (200, {"workflow_runs": [last] if last else []}))
            f.add(("GET", rf"/repos/{repo}/contents/{re.escape(meta['path'])}"), wf_file(cron))
            f.add(("GET", rf"/actions/workflows/{meta['id']}/runs\?event=schedule"),
                  (200, {"workflow_runs": [sched_last] if sched_last else []}))
    return f


def test_workflows_failed_stale_disabled_and_skipped():
    repo = "o/r"
    f = _workflows({repo: [
        ({"id": 1, "path": ".github/workflows/tests.yml", "state": "active"}, {"conclusion": "success", "created_at": iso(0)}, None, None),
        ({"id": 2, "path": ".github/workflows/feedback.yml", "state": "active"},
         {"conclusion": "failure", "created_at": iso(3), "html_url": "https://gh/run/2"}, None, None),
        ({"id": 3, "path": ".github/workflows/corpus-refresh.yml", "state": "active"},
         {"conclusion": "success", "created_at": iso(20)}, "0 12 * * 0", {"created_at": iso(20)}),
        ({"id": 4, "path": ".github/workflows/old.yml", "state": "disabled_manually"}, None, None, None),
        ({"id": 5, "path": "dynamic/pages/pages-build-deployment", "state": "active"}, None, None, None),
        ({"id": 6, "path": ".github/workflows/health.yml", "state": "active"}, None, "30 3 * * *", None),
    ]})
    out = {r.check.split("/")[-1]: r for r in hc.check_workflows(f, "t", NOW, repos=[repo])}
    assert out["tests.yml"].status == OK
    assert out["feedback.yml"].status == FAIL and "https://gh/run/2" in out["feedback.yml"].fix
    assert out["corpus-refresh.yml"].status == WARN and "last ran" in out["corpus-refresh.yml"].detail
    assert out["old.yml"].status == WARN
    assert out["health.yml"].status == WARN and "never run on schedule" in out["health.yml"].detail
    assert "pages-build-deployment" not in out


def test_workflows_on_time_schedule_is_ok():
    f = _workflows({"o/r": [({"id": 3, "path": ".github/workflows/c.yml", "state": "active"},
                             {"conclusion": "success", "created_at": iso(5)}, "0 12 * * 0", {"created_at": iso(5)})]})
    assert hc.check_workflows(f, "t", NOW, repos=["o/r"])[0].status == OK


def test_workflows_repo_not_visible_is_unknown():
    r = hc.check_workflows(Fake(), "t", NOW, repos=["o/hidden"])
    assert r[0].status == UNKNOWN and "HTTP 404" in r[0].detail


# ── Corpus ───────────────────────────────────────────────────────────

def _corpus(recent_log, older_log, commits=3, pinecone=None):
    f = Fake({
        ("GET", r"corpus-refresh\.yml/runs"): (200, {"workflow_runs": [
            {"id": 10, "created_at": iso(1)}, {"id": 9, "created_at": iso(8)}]}),
        ("GET", r"/runs/10/logs"): (200, logzip(recent_log)),
        ("GET", r"/runs/9/logs"): (200, logzip(older_log)),
        ("GET", r"flytbase-docs/commits"): (200, [{}] * commits),
        ("GET", r"flytbase-releases/commits"): (200, []),
    })
    if pinecone is not None:
        f.add(("GET", "api.pinecone.io/indexes/flytbase-docs"), (200, {"host": "idx.pinecone.io"}))
        f.add(("POST", "idx.pinecone.io/describe_index_stats"), (200, {"totalVectorCount": pinecone}))
    return f


def test_corpus_frozen_while_docs_change_warns():
    r = hc.check_corpus(_corpus("  Total vectors: 971", "  Total vectors: 971"), "t", NOW)
    assert r.status == WARN and "971 vectors, unchanged" in r.detail and "3 commit" in r.detail


def test_corpus_rate_limited_warns_and_says_why():
    r = hc.check_corpus(_corpus("429 Client Error: Too Many Requests\n  Total vectors: 1000", "  Total vectors: 971"), "t", NOW)
    assert r.status == WARN and "429" in r.detail and "GitHub API" in r.fix


def test_corpus_growing_is_ok():
    r = hc.check_corpus(_corpus("  Total vectors: 1,050", "  Total vectors: 971"), "t", NOW)
    assert r.status == OK and "+79" in r.detail


def test_corpus_unchanged_but_no_content_commits_is_ok():
    assert hc.check_corpus(_corpus("Total vectors: 971", "Total vectors: 971", commits=0), "t", NOW).status == OK


def test_corpus_pinecone_count_wins_over_log():
    r = hc.check_corpus(_corpus("Total vectors: 971", "Total vectors: 971", pinecone=1200), "t", NOW, pinecone_key="p")
    assert r.status == OK and "1200 vectors" in r.detail


def test_corpus_undeterminable_is_unknown_not_ok():
    r = hc.check_corpus(Fake(), "t", NOW)
    assert r.status == UNKNOWN
    r = hc.check_corpus(_corpus("no count here", "no count here"), "t", NOW)
    assert r.status == UNKNOWN


# ── HTTP: timeouts and retries ───────────────────────────────────────

def test_http_retries_transient_failures_then_succeeds(monkeypatch):
    answers = iter([(0, "timeout"), (503, "busy"), (200, {"ok": True})])
    sleeps = []
    monkeypatch.setattr(hc, "_http_once", lambda *a: next(answers))
    monkeypatch.setattr(hc, "_sleep", sleeps.append)
    assert hc.http("GET", "https://x") == (200, {"ok": True})
    assert sleeps == [1, 3]


def test_http_gives_up_after_retries_and_does_not_retry_4xx(monkeypatch):
    calls = []
    monkeypatch.setattr(hc, "_sleep", lambda s: None)
    monkeypatch.setattr(hc, "_http_once", lambda *a: calls.append(1) or (429, "slow down"))
    assert hc.http("GET", "https://x")[0] == 429 and len(calls) == hc.RETRIES
    calls.clear()
    monkeypatch.setattr(hc, "_http_once", lambda *a: calls.append(1) or (404, "nope"))
    assert hc.http("GET", "https://x")[0] == 404 and len(calls) == 1


def test_http_timeout_becomes_status_zero(monkeypatch):
    class Opener:
        def open(self, req, timeout):
            assert timeout == 20
            raise TimeoutError("timed out")
    monkeypatch.setattr(hc.urllib.request, "build_opener", lambda *a: Opener())
    assert hc._http_once("GET", "https://x", None, None, False, 20) == (0, "timed out")


# ── Isolation, message, main ─────────────────────────────────────────

ALL_OK = {
    ("GET", "openrouter.ai"): (200, {"data": {"limit": 10, "usage": 1, "limit_remaining": 9}}),
    ("GET", r"api\.github\.com/repos/[^/]+/[^/]+$"): (200, {"permissions": {"push": True}}),
    ("POST", "auth.test"): (200, {"ok": True}),
    ("GET", r"/content\?"): (401, "Unauthorized"),
    ("GET", r"workers\.dev/$"): (200, "Son of Anton is running"),
    ("GET", r"/actions/workflows\?"): (200, {"workflows": []}),
    ("GET", r"corpus-refresh\.yml/runs"): (200, {"workflow_runs": [{"id": 10, "created_at": iso(1)}, {"id": 9, "created_at": iso(8)}]}),
    ("GET", r"/runs/10/logs"): (200, logzip("Total vectors: 1000")),
    ("GET", r"/runs/9/logs"): (200, logzip("Total vectors: 971")),
    ("GET", r"/commits"): (200, []),
}
ENV = {"GH_TOKEN": "g", "OPENROUTER_API_KEY": "o", "SLACK_BOT_TOKEN": "s"}


def test_one_crashing_check_does_not_stop_the_others():
    f = Fake(ALL_OK).add(("POST", "auth.test"), RuntimeError("socket exploded"))
    results = hc.run_checks(ENV, f, NOW)
    slack = next(r for r in results if r.check == "Slack bot token")
    assert slack.status == FAIL and "the check itself crashed: RuntimeError: socket exploded" in slack.detail
    assert {r.check for r in results} >= {"OpenRouter credit", "Worker reachable", "Vector corpus freshness"}
    assert all(r.status == OK for r in results if r.check != "Slack bot token")


def test_no_message_when_everything_is_ok(tmp_path):
    f = Fake(ALL_OK)
    summary = tmp_path / "summary.md"
    assert hc.main({**ENV, "GITHUB_STEP_SUMMARY": str(summary)}, f) == 0
    assert not any("chat.postMessage" in c[1] for c in f.calls)
    text = summary.read_text()
    assert "| OpenRouter credit | :white_check_mark: ok |" in text and "Slack alert" not in text


def test_problems_are_posted_once_with_fixes(tmp_path):
    f = Fake(ALL_OK).add(("GET", "openrouter.ai"), (200, {"data": {"limit": 10, "usage": 10, "limit_remaining": 0}})) \
                    .add(("POST", "chat.postMessage"), (200, {"ok": True}))
    summary = tmp_path / "s.md"
    hc.main({**ENV, "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_RUN_ID": "7", "GITHUB_REPOSITORY": "o/r"}, f)
    posts = [c for c in f.calls if "chat.postMessage" in c[1]]
    assert len(posts) == 1
    body = posts[0][2]
    assert body["channel"] == hc.DEFAULT_CHANNEL
    assert "will break the next run" in body["text"] and "Fix: Top up" in body["text"]
    assert "https://github.com/o/r/actions/runs/7" in body["text"]
    assert "Slack alert posted" in summary.read_text()


def test_alert_channel_is_configurable():
    f = Fake(ALL_OK).add(("GET", "openrouter.ai"), (401, {})).add(("POST", "chat.postMessage"), (200, {"ok": True}))
    hc.main({**ENV, "SLACK_ALERT_CHANNEL": "C123"}, f)
    assert next(c for c in f.calls if "chat.postMessage" in c[1])[2]["channel"] == "C123"


def test_failed_slack_post_is_recorded_in_the_summary(tmp_path):
    f = Fake(ALL_OK).add(("GET", "openrouter.ai"), (401, {})).add(("POST", "chat.postMessage"), (200, {"ok": False, "error": "channel_not_found"}))
    summary = tmp_path / "s.md"
    assert hc.main({**ENV, "GITHUB_STEP_SUMMARY": str(summary)}, f) == 0
    assert "The Slack alert could not be posted (chat.postMessage did not return ok)" in summary.read_text()


def test_crashing_slack_post_and_missing_token_are_recorded(tmp_path):
    f = Fake(ALL_OK).add(("GET", "openrouter.ai"), (401, {})).add(("POST", "chat.postMessage"), ConnectionError("down"))
    summary = tmp_path / "s.md"
    hc.main({**ENV, "GITHUB_STEP_SUMMARY": str(summary)}, f)
    assert "could not be posted (ConnectionError: down)" in summary.read_text()
    summary2 = tmp_path / "s2.md"
    hc.main({**ENV, "SLACK_BOT_TOKEN": "", "GITHUB_STEP_SUMMARY": str(summary2)}, Fake(ALL_OK))
    assert "could not be posted (no SLACK_BOT_TOKEN)" in summary2.read_text()


def test_warnings_only_message_tone():
    msg = hc.build_message([hc.Result("Vector corpus freshness", WARN, "971 vectors, unchanged", "Fix it")])
    assert msg.startswith(":warning: *Daily health check:* 1 thing(s) are drifting")
    assert hc.build_message([hc.Result("x", UNKNOWN, "?")]) is None


def test_workflow_file_is_daily_with_manual_trigger_and_no_inline_python():
    import yaml
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / ".github/workflows/health-check.yml").read_text()
    on = yaml.safe_load(src)[True]
    assert on["schedule"] == [{"cron": "30 3 * * *"}] and "workflow_dispatch" in on
    assert "python3 -m src.health_check" in src and "python3 -c" not in src
    assert "secrets.FLYTBASE_GITHUB_TOKEN" in src


def test_network_errors_are_reported_as_such_not_as_permission_problems():
    down = Fake({("GET", "."): (0, "SSL: CERTIFICATE_VERIFY_FAILED"), ("POST", "."): (0, "timed out")})
    r = hc.check_github_repos(down, "t")[0]
    assert r.status == UNKNOWN and "no response after 3 attempts (SSL: CERTIFICATE_VERIFY_FAILED)" in r.detail
    assert hc.check_openrouter(down, "k").status == UNKNOWN
    assert hc.check_slack(down, "x").status == UNKNOWN
    assert "no response" in hc.check_worker(down, "https://w")[0].detail
    assert "no response" in hc.check_workflows(down, "t", NOW, repos=["o/r"])[0].detail
