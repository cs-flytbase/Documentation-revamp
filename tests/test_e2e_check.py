"""The weekly end-to-end check and the publisher's sandbox mode.

Sandbox: the full publish flow on disposable branches, never a PR, a label or
an edit to an existing page. The check: run, read back, verify, and delete the
sandbox branches always, including when the run crashes.
"""
import io
import json
import urllib.error

import pytest

from tests.test_p0_self_heal import FakeGitHub, doc_draft, make_publisher
from tests.test_replay_cockpit import BUNDLE, LIVE, install

DOCS, RELS = "FlytBaseAILabs/flytbase-docs", "FlytBaseAILabs/flytbase-releases"
TAG = "e2e-20261028-42"


def _no_writes_outside_sandbox(gh, tag=TAG):
    """No PR, no label; every branch the run touched is under sandbox/<tag>/."""
    assert not [c for c in gh.calls if c[2] in ("pulls",) or "labels" in c[2]]
    touched = {b for (r, b) in gh.refs if b != "main"}
    assert touched and all(b.startswith(f"sandbox/{tag}/") for b in touched), touched


# ── Sandbox mode in the publisher ─────────────────────────────────────────

def test_sandbox_publish_commits_to_sandbox_branches_and_never_opens_a_pr(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    pub.sandbox = TAG
    draft = doc_draft()
    draft["impacted_page_edits"] = [{"source_url": "https://docs.flytbase.com/in-flight-modules/annotations",
                                     "section_heading": "## A", "patch_mode": "append", "patch_content": "x"}]
    r = pub.publish(draft, output_dir="/tmp", feature_slug="zone-overlays", bundle_asset_paths=[],
                    release_month="october-2026", mode="doc_only", target_section="Cockpit 3.0")
    _no_writes_outside_sandbox(gh)
    assert r["docs_pr"] == f"sandbox:{DOCS}@sandbox/{TAG}/zone-overlays"
    assert r["sandbox_branches"] == [[DOCS, f"sandbox/{TAG}/zone-overlays"]]
    assert r["sandbox_skipped_edits"] == 1  # an existing page is never edited
    assert not any(p.startswith("in-flight-modules/annotations") for (_, p) in gh.commit_log[0][2:])


def test_sandbox_can_never_create_a_pr_even_if_called_directly(monkeypatch):
    import src.tools.github_publisher as gp
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    pub.sandbox = TAG
    with pytest.raises(gp.PublishError):
        pub._create_pr(DOCS, "x", "t", "b")
    pub._label_needs_attention(DOCS, "https://github.com/x/y/pull/1")
    assert not gh.calls


def test_sandbox_tag_comes_from_the_environment_and_is_validated(monkeypatch):
    import src.tools.github_publisher as gp
    monkeypatch.setattr(gp, "GITHUB_TOKEN", "test")
    monkeypatch.setenv("PIPELINE_SANDBOX", TAG)
    assert gp.GitHubPublisher().sandbox == TAG
    monkeypatch.setenv("PIPELINE_SANDBOX", "../main")
    with pytest.raises(ValueError):
        gp.GitHubPublisher()
    monkeypatch.delenv("PIPELINE_SANDBOX")
    assert gp.GitHubPublisher().sandbox == ""


# ── The README cross-link fix ─────────────────────────────────────────────

def test_a_cross_link_to_a_folder_page_edits_its_readme(monkeypatch):
    gh = FakeGitHub()
    gh.files[(DOCS, "in-flight-modules/annotations/README.md")] = "# Annotations\n\n## Related\n\n- x\n"
    _, pub = make_publisher(monkeypatch, gh)
    draft = doc_draft()
    draft["impacted_page_edits"] = [{"source_url": "https://docs.flytbase.com/in-flight-modules/annotations",
                                     "section_heading": "## Related", "patch_mode": "append",
                                     "patch_content": "\n- [Zone Overlays](x.md)\n"}]
    r = pub.publish(draft, output_dir="/tmp", feature_slug="zone-overlays", bundle_asset_paths=[],
                    release_month="october-2026", mode="doc_only", slack_thread_ts="1.2", target_section="Cockpit 3.0")
    assert r["docs_impacted"] == ["in-flight-modules/annotations/README.md"]
    assert not any("does not exist" in a for a in r["needs_attention"])
    assert "[Zone Overlays](x.md)" in gh.files[(DOCS, "in-flight-modules/annotations/README.md")]


def test_a_page_file_still_wins_over_a_folder_readme(monkeypatch):
    gh = FakeGitHub()
    gh.files[(DOCS, "a/b.md")] = "# B\n"
    gh.files[(DOCS, "a/b/README.md")] = "# B folder\n"
    _, pub = make_publisher(monkeypatch, gh)
    assert pub._existing_page(DOCS, "a/b.md")[0] == "a/b.md"
    assert pub._existing_page(DOCS, "a/c.md") == ("a/c.md", None, None)


# ── The end-to-end check ──────────────────────────────────────────────────

def test_a_good_week_passes_every_invariant_and_leaves_no_branch(monkeypatch, tmp_path):
    from src.e2e_check import run_check
    gh, _, run_pipeline = install(monkeypatch, tmp_path, LIVE)
    results, outcome = run_check(gh.api, run_pipeline, TAG, fixture=BUNDLE)
    assert all(r.ok for r in results), [(r.name, r.detail) for r in results if not r.ok]
    assert outcome["pr_results"]["sandbox_branches"]
    assert {b for (r, b) in gh.refs} == {"main"}  # everything deleted
    assert not [c for c in gh.calls if c[2] == "pulls" or "labels" in c[2]]
    names = [r.name for r in results]
    assert "run published both outputs" in names and names[-1] == "sandbox branches deleted"


def test_the_branches_are_deleted_even_when_the_run_crashes(monkeypatch, tmp_path):
    from src.e2e_check import run_check
    gh = FakeGitHub()
    gh._init()

    def crashing(bundle, **kw):
        import os
        tag = os.environ["PIPELINE_SANDBOX"]
        gh.refs[(DOCS, f"sandbox/{tag}/half-done")] = gh.refs[(DOCS, "main")]
        raise RuntimeError("model went away")

    results, _ = run_check(gh.api, crashing, TAG, fixture=BUNDLE)
    by = {r.name: r for r in results}
    assert not by["pipeline run"].ok and "model went away" in by["pipeline run"].detail
    assert by["sandbox branches deleted"].ok and "deleted 1" in by["sandbox branches deleted"].detail
    assert (DOCS, f"sandbox/{TAG}/half-done") not in gh.refs
    import os
    assert "PIPELINE_SANDBOX" not in os.environ  # restored


def test_a_branch_that_cannot_be_deleted_is_reported(monkeypatch):
    from src.e2e_check import cleanup
    gh = FakeGitHub()
    gh._init()
    gh.refs[(RELS, f"sandbox/{TAG}/x")] = gh.refs[(RELS, "main")]
    gh.fail[("DELETE", "git/refs/heads/")] = 500
    res = cleanup(gh.api, TAG)
    assert not res.ok and f"{RELS}@sandbox/{TAG}/x" in res.detail


def test_a_failed_invariant_is_reported_in_the_run_not_hidden(monkeypatch, tmp_path):
    """A pipeline that publishes a broken page must fail the check."""
    from src.e2e_check import run_check
    gh, _, run_pipeline = install(monkeypatch, tmp_path, LIVE)

    def broken(bundle, **kw):
        out = run_pipeline(bundle, **kw)
        import os
        tag = os.environ["PIPELINE_SANDBOX"]
        branch = f"sandbox/{tag}/cockpit-3-0-video-overlays-update"
        tree = gh.tree_of(DOCS, branch)
        page = next(p for p in tree if p.endswith("cockpit-3-0-video-overlays-update.md"))
        t = gh._sha("t")
        gh.trees[t] = {**tree, page: tree[page].replace("](assets/zone-overlays-01-video-overlays-panel.png)", "](assets/missing.png)")}
        c = gh._sha("c")
        gh.commits[c] = {"tree": t, "parents": [gh.refs[(DOCS, branch)]]}
        gh.refs[(DOCS, branch)] = c
        return out

    results, _ = run_check(gh.api, broken, TAG, fixture=BUNDLE)
    failed = {r.name for r in results if not r.ok}
    assert {"named files accounted for", "internal links resolve"} <= failed
    assert {b for (r, b) in gh.refs} == {"main"}


# ── Reporting ──────────────────────────────────────────────────────────────

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(*answers):
    calls = []

    def op(req, timeout=None):
        calls.append(req)
        a = answers[len(calls) - 1]
        if isinstance(a, BaseException):
            raise a
        return _Resp(json.dumps(a).encode())
    op.calls = calls
    return op


def _http(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))


def test_report_all_ok_is_quiet_and_exits_zero(tmp_path):
    from src.e2e_check import report
    from src.invariants import Result
    summary = tmp_path / "s.md"
    slack, journal = _opener(), _opener({})
    code = report([Result("a", True, "fine")], env={"GITHUB_STEP_SUMMARY": str(summary), "CONTENT_TOKEN": "t"},
                  slack_opener=slack, journal_opener=journal)
    assert code == 0 and not slack.calls and len(journal.calls) == 1
    assert json.loads(journal.calls[0].data)["status"] == "ok" and journal.calls[0].headers["X-content-token"] == "t"
    assert "| a | pass | fine |" in summary.read_text()


def test_report_failure_posts_once_and_fails_the_job(tmp_path):
    from src.e2e_check import report
    from src.invariants import Result
    summary = tmp_path / "s.md"
    slack, journal = _opener({"ok": True}), _opener(_http(404))
    env = {"GITHUB_STEP_SUMMARY": str(summary), "SLACK_BOT_TOKEN": "x", "CONTENT_TOKEN": "t",
           "GITHUB_RUN_ID": "9", "GITHUB_REPOSITORY": "cs-flytbase/Documentation-revamp"}
    code = report([Result("a", True), Result("placement", False, "under Live Video Feed")], env=env,
                  slack_opener=slack, journal_opener=journal)
    assert code == 1 and len(slack.calls) == 1
    body = json.loads(slack.calls[0].data)
    assert body["channel"] == "C0BGB8F56JX" and "*placement*: under Live Video Feed" in body["text"]
    assert "actions/runs/9" in body["text"]
    text = summary.read_text()
    assert "| placement | FAIL |" in text and "Journal" not in text  # a 404 journal is skipped quietly


def test_report_says_when_slack_or_the_journal_failed(tmp_path):
    from src.e2e_check import report
    from src.invariants import Result
    summary = tmp_path / "s.md"
    code = report([Result("x", False, "bad")], env={"GITHUB_STEP_SUMMARY": str(summary), "SLACK_BOT_TOKEN": "x",
                                                     "CONTENT_TOKEN": "t", "SLACK_ALERT_CHANNEL": "C1"},
                  slack_opener=_opener({"ok": False, "error": "channel_not_found"}), journal_opener=_opener(_http(500)))
    text = summary.read_text()
    assert code == 1 and "Slack alert NOT sent: Slack said: channel_not_found" in text
    assert "Journal record not stored: journal answered HTTP 500" in text


def test_missing_token_is_a_reported_failure_not_a_crash(monkeypatch, tmp_path):
    from src import e2e_check
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(e2e_check, "post_slack", lambda *a, **k: "")
    monkeypatch.setattr(e2e_check, "post_journal", lambda *a, **k: "")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))
    assert e2e_check.main([]) == 1
    assert "GITHUB_TOKEN is not set" in (tmp_path / "s.md").read_text()


def test_github_client_retries_server_errors_and_not_404():
    from src.e2e_check import GitHubAPI
    op = _opener(_http(502), {"object": {"sha": "s"}})
    api = GitHubAPI("t", sleep=lambda s: None, opener=op)
    assert api("GET", DOCS, "git/ref/heads/main") == {"object": {"sha": "s"}} and len(op.calls) == 2
    op = _opener(_http(404))
    with pytest.raises(urllib.error.HTTPError):
        GitHubAPI("t", sleep=lambda s: None, opener=op)("GET", DOCS, "contents/x")
    assert len(op.calls) == 1
    op = _opener(urllib.error.URLError("down"), urllib.error.URLError("down"), {"a": 1})
    assert GitHubAPI("t", sleep=lambda s: None, opener=op)("GET", DOCS, "x", params={"ref": "a/b"}) == {"a": 1}
    assert op.calls[-1].full_url.endswith("x?ref=a/b")


def test_workflow_is_weekly_manual_and_uses_existing_secrets():
    import yaml
    from pathlib import Path
    wf = yaml.safe_load(Path(".github/workflows/e2e-check.yml").read_text())
    on = wf.get("on") or wf.get(True)
    assert on["schedule"][0]["cron"] == "0 5 * * 3" and "workflow_dispatch" in on
    text = Path(".github/workflows/e2e-check.yml").read_text()
    for secret in ("FLYTBASE_GITHUB_TOKEN", "OPENROUTER_API_KEY", "PINECONE_API_KEY", "SLACK_BOT_TOKEN", "CONTENT_TOKEN"):
        assert f"secrets.{secret}" in text
    assert "python3 -m src.e2e_check" in text and "python3 -c" not in text
