"""Run journal: every stage's outcome is recorded, statuses match what the
requester saw, and the journal can never affect a run."""
import io
import json
import sys
import types
import urllib.error
from pathlib import Path

import pytest

from src import slack_run
from src.run_journal import (RunJournal, Timeline, classify, invariants, published_assets, run_id_from_env,
                             stage_records)


# ── Classification: each Needs-attention / fixes line belongs to a capability ──

@pytest.mark.parametrize("item, stage", [
    ("Image description failed for x.png; its alt text was built from the file name", "vision"),
    ("Fact check: not supported by the source material: [doc] 40 m", "checks"),
    ("The material names 10 file(s) that were never received: a.png", "assets"),
    ("Could not download from Slack: a.png (HTTP 403)", "assets"),
    ("Dropped go-to-location-from-video-2.gif: it is the same file as go-to-location-from-video.gif.", "assets"),
    ("Placement unconfirmed: the page is under **Multi View Dashboard**", "placement"),
    ("The sidebar (SUMMARY.md) was NOT updated, because the edit failed its check", "placement"),
    ('The section you asked for, "Counter-UAS", is not in the sidebar', "placement"),
    ('docs/x.md: link "Zones" pointed to /zones, which does not exist; it is now plain text', "links"),
    ("Cross-link not added: a.md does not exist.", "links"),
    ("Release note doc link changed to https://docs.flytbase.com/x (where the page actually landed).", "links"),
    ("Creating the docs PR failed: 502", "publish"),
])
def test_classify(item, stage):
    assert classify(item) == stage


def test_run_id():
    assert run_id_from_env({"GITHUB_RUN_ID": "39", "GITHUB_RUN_ATTEMPT": "2"}) == "gh-39-2"
    assert run_id_from_env({"GITHUB_RUN_ID": "39"}) == "gh-39-1"
    assert run_id_from_env({}).startswith("local-")


def test_timeline_durations():
    t = iter([0.0, 1.5, 4.0, 10.0])
    tl = Timeline(clock=lambda: next(t))
    tl.mark("material"), tl.mark("assets"), tl.mark("material"), tl.mark("source")
    tl.close()
    assert tl.order == ["material", "assets", "source"]
    assert [tl.duration_ms(s) for s in tl.order] == [1500, 2500, 6000]
    assert tl.duration_ms("publish") is None


def _timeline(*stages):
    tl = Timeline(clock=lambda: 0.0)
    for s in stages:
        tl.mark(s)
    tl.close()
    return tl


def test_stage_statuses_come_from_the_same_lists_the_pr_shows():
    tl = _timeline("material", "assets", "source", "analysis", "drafting", "checks", "publish")
    attention = ["Image description failed for a.png; check it.", "Placement unconfirmed: under X"]
    fixes = ["Dropped b.gif: it is the same file as a.gif.", 'docs/x.md: link "Y": /y -> /z']
    recs = {r["stage"]: r for r in stage_records(tl, attention=attention, fixes=fixes, mode="both", images=2)}
    assert {s: r["status"] for s, r in recs.items()} == {
        "material": "ok", "assets": "repaired", "source": "ok", "analysis": "ok", "vision": "flagged",
        "drafting": "ok", "checks": "ok", "publish": "ok", "placement": "flagged", "links": "repaired"}
    assert recs["vision"]["detail"].startswith("Image description failed")
    assert recs["assets"]["detail"].startswith("Dropped b.gif")


def test_a_failed_run_marks_its_stage_and_skips_what_it_never_reached():
    tl = _timeline("material", "assets", "source", "analysis")
    recs = stage_records(tl, failed_stage="analysis", failure_reason="research agent failed", images=3)
    assert [(r["stage"], r["status"]) for r in recs] == [
        ("material", "ok"), ("assets", "ok"), ("source", "ok"), ("analysis", "failed")]
    assert recs[-1]["detail"] == "research agent failed"


def test_release_only_has_no_placement_and_no_images_means_no_vision():
    tl = _timeline("material", "assets", "source", "analysis", "drafting", "checks", "publish")
    stages = [r["stage"] for r in stage_records(tl, mode="release_only", images=0)]
    assert "placement" not in stages and "vision" not in stages and "links" in stages


def test_long_detail_is_summarised():
    tl = _timeline("material", "checks")
    recs = stage_records(tl, attention=[f"Fact check: claim {i}" for i in range(7)])
    assert recs[1]["detail"].endswith("(+4 more)")


# ── Invariants ──────────────────────────────────────────────────────────────

def _inv(**kw):
    base = dict(named=["a.gif", "b.png"], missing_named=[], received_files=["a.gif", "b.png"],
                published_refs={"a.gif", "b.png"}, dropped=[], mode="both",
                target_section="In-Flight Modules > Cockpit 3.0", docs_section="How To Manage > Cockpit 3.0",
                missing_prs=[], link_problems=0, needs_attention=0, reached_publish=True)
    base.update(kw)
    return {r["stage"]: r for r in invariants(**base)}


def test_invariants_all_hold():
    inv = _inv()
    assert {s: r["status"] for s, r in inv.items()} == {
        "invariant:files": "ok", "invariant:placement": "ok", "invariant:prs": "ok",
        "invariant:links": "ok", "invariant:needs_attention": "ok"}
    assert inv["invariant:files"]["detail"] == "named 2, received 2, published 2"


def test_invariants_catch_this_weeks_incidents():
    inv = _inv(named=[f"s{i}.png" for i in range(22)], missing_named=[f"s{i}.png" for i in range(10)],
               received_files=[f"s{i}.png" for i in range(10, 22)] + ["dup-2.gif"],
               published_refs={f"s{i}.png" for i in range(10, 21)}, dropped=["dup-2.gif"],
               target_section="", docs_section="Multi View Dashboard", missing_prs=["doc page PR"],
               link_problems=2, needs_attention=4)
    assert inv["invariant:files"]["status"] == "flagged"
    assert inv["invariant:files"]["detail"].startswith("named 22, received 13, published 11; never received: s0.png")
    assert "not used on any page: s21.png" in inv["invariant:files"]["detail"]
    assert inv["invariant:placement"]["status"] == "flagged"
    assert inv["invariant:prs"]["status"] == "failed"
    assert inv["invariant:links"]["detail"] == "2 broken link(s) left"
    assert inv["invariant:needs_attention"]["status"] == "flagged"


def test_placement_mismatch_and_not_in_sidebar_are_flagged():
    assert _inv(docs_section="Live Video Feed")["invariant:placement"]["status"] == "flagged"
    assert _inv(docs_section="(not in the sidebar yet)")["invariant:placement"]["status"] == "flagged"
    assert "invariant:placement" not in _inv(mode="release_only")
    assert "invariant:prs" not in _inv(reached_publish=False)


def test_published_assets_reads_every_page():
    draft = {"release_note": {"content": "![a](assets/a.gif)"}, "doc_page": {"content": "x ![b](assets/b-01.png) y"},
             "_subsections": {"children": [{"content": "![c](assets/c.png)"}]}}
    assert published_assets(draft) == {"a.gif", "b-01.png", "c.png"}


# ── The client never raises ─────────────────────────────────────────────────

class Opener:
    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def __call__(self, req, timeout=0):
        self.calls.append(json.loads(req.data.decode()) | {"_token": req.headers.get("X-content-token"), "_url": req.full_url})
        out = self.outcomes.pop(0) if self.outcomes else "ok"
        if isinstance(out, Exception):
            raise out
        return io.BytesIO(b'{"stored":true}')


def _client(opener):
    return RunJournal("https://w/", "tok", "gh-1-1", "C1", "1.2", opener=opener, sleep=lambda s: None)


def test_posts_the_record_with_the_token():
    o = Opener()
    assert _client(o).post("drafting", "ok", "fine", 1200) is True
    c = o.calls[0]
    assert c["_url"] == "https://w/journal" and c["_token"] == "tok"
    assert {k: c[k] for k in ("run_id", "channel", "thread_ts", "stage", "status", "detail", "duration_ms")} == {
        "run_id": "gh-1-1", "channel": "C1", "thread_ts": "1.2", "stage": "drafting", "status": "ok",
        "detail": "fine", "duration_ms": 1200}


def test_retries_once_on_network_errors_and_5xx_not_on_4xx():
    o = Opener(OSError("reset"))
    assert _client(o).post("a", "ok") is True and len(o.calls) == 2
    o = Opener(urllib.error.HTTPError("u", 503, "x", {}, None), urllib.error.HTTPError("u", 503, "x", {}, None))
    j = _client(o)
    assert j.post("a", "ok") is False and len(o.calls) == 2 and j.failures == ["a: HTTP 503"]
    o = Opener(urllib.error.HTTPError("u", 401, "x", {}, None))
    assert _client(o).post("a", "ok") is False and len(o.calls) == 1


def test_circuit_breaker_stops_after_the_worker_is_unreachable():
    o = Opener(OSError("down"), OSError("down"))
    j = _client(o)
    assert j.post_all([{"stage": f"s{i}", "status": "ok"} for i in range(15)]) == 0
    assert len(o.calls) == 2  # one record's two attempts, then the rest are skipped
    assert len(j.failures) == 15 and j.failures[-1].endswith("skipped, journal unreachable (OSError: down)")
    # A rejected record (4xx) is about that record, not the worker: keep going.
    o = Opener(urllib.error.HTTPError("u", 400, "x", {}, None))
    j = _client(o)
    assert j.post_all([{"stage": "a", "status": "ok"}, {"stage": "b", "status": "ok"}]) == 1


def test_disabled_without_config_and_never_raises():
    j = RunJournal("", "", "gh-1-1", opener=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    assert j.post("a", "ok") is False
    weird = _client(Opener(ValueError("bad"), ValueError("bad")))
    assert weird.post_all([{"stage": "a", "status": "ok"}, {"no": "stage"}]) == 0


# ── slack_run.main: journalled end to end, and the journal can't break a run ──

@pytest.fixture
def journal_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(slack_run.time, "sleep", lambda s: None)
    monkeypatch.setattr("src.run_journal.time.sleep", lambda s: None)
    for k, v in {"PL_FEATURE_NAME": "video-overlays", "PL_MODE": "both", "PL_WORKER_URL": "https://w",
                 "PL_CONTENT_KEY": "k", "PL_CONTENT_TOKEN": "tok", "SLACK_BOT_TOKEN": "", "PL_SLACK_CHANNEL": "C1",
                 "PL_SLACK_THREAD_TS": "1.2", "GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "1",
                 "PL_TARGET_SECTION": "In-Flight Modules > Cockpit 3.0"}.items():
        monkeypatch.setenv(k, v)
    kv = {"content": "Zones. Media: `zones.gif`, `panel.png`.", "assets": [{"url": "https://files/x", "name": "zones.gif"}],
          "intake": {"asset_audience": {"zones.gif": "both"}}}
    posted, state = [], {"journal_down": False}

    def urlopen(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        if url.endswith("/journal"):
            if state["journal_down"]:
                raise OSError("worker unreachable")
            posted.append(json.loads(req.data.decode()))
            return io.BytesIO(b'{"stored":true}')
        if "/content?key=" in url:
            return io.BytesIO(json.dumps(kv).encode())
        return io.BytesIO(b"GIF89a" + b"\0" * 200)

    monkeypatch.setattr(slack_run.urllib.request, "urlopen", urlopen)
    sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "src.agents.source", types.SimpleNamespace(
        SourceAgent=lambda: types.SimpleNamespace(build_pm_doc=lambda **k: {"pm_doc": "# PM"})))

    def install(result, stages=("analysis", "drafting", "checks", "publish"), boom=None):
        def run_pipeline(bundle, **kw):
            for s in stages:
                kw["on_stage"](s)
            if boom:
                raise boom
            return result
        monkeypatch.setitem(sys.modules, "src.orchestrator", types.SimpleNamespace(run_pipeline=run_pipeline))
    return install, posted, state


SUCCESS = {"status": "success", "attention": ["Image description failed for zones.gif; check it."],
           "fixes": [], "draft": {"release_note": {"filename": "v.md", "content": "![z](assets/zones.gif)"},
                                  "doc_page": {"content": "![z](assets/zones.gif)"}},
           "pr_results": {"docs_pr": "d", "releases_pr": "r", "errors": [], "docs_section": "How To Manage > Cockpit 3.0",
                          "needs_attention": ["Image description failed for zones.gif; check it."], "fixes": []}}


def test_a_successful_run_journals_every_stage_and_the_invariants(journal_run):
    install, posted, _ = journal_run
    install(SUCCESS)
    assert slack_run.main() == 0
    got = {r["stage"]: r["status"] for r in posted}
    assert got == {
        # assets is flagged: the material names panel.png, which never arrived.
        "material": "ok", "assets": "flagged", "source": "ok", "analysis": "ok", "vision": "flagged", "drafting": "ok",
        "checks": "ok", "publish": "ok", "placement": "ok", "links": "ok",
        "invariant:files": "flagged", "invariant:placement": "ok", "invariant:prs": "ok",
        "invariant:links": "ok", "invariant:needs_attention": "flagged"}
    files = next(r for r in posted if r["stage"] == "invariant:files")
    assert files["detail"] == "named 2, received 1, published 1; never received: panel.png"
    assert all(r["run_id"] == "gh-77-1" and r["channel"] == "C1" and r["thread_ts"] == "1.2" for r in posted)
    assert next(r for r in posted if r["stage"] == "material")["duration_ms"] is not None


def test_a_failed_run_journals_the_failing_stage(journal_run):
    install, posted, _ = journal_run
    install({"status": "success", "pr_results": {"docs_pr": "d", "errors": ["Releases repo failed: 422"]}, "draft": {}})
    assert slack_run.main() == 1
    got = {r["stage"]: r for r in posted}
    assert got["publish"]["status"] == "failed" and got["publish"]["detail"] == "Releases repo failed: 422"
    assert "placement" not in got and got["drafting"]["status"] == "ok"
    assert "invariant:prs" not in got  # publish never completed, so no PR invariant is claimed


def test_an_exception_is_journalled_once_with_its_stage(journal_run):
    install, posted, _ = journal_run
    install({}, stages=("analysis",), boom=RuntimeError("vision exploded"))
    with pytest.raises(RuntimeError):
        slack_run.main()
    analysis = [r for r in posted if r["stage"] == "analysis"]
    assert len(analysis) == 1 and analysis[0]["status"] == "failed" and "vision exploded" in analysis[0]["detail"]


def test_the_journal_being_down_never_affects_the_run(journal_run):
    install, posted, state = journal_run
    state["journal_down"] = True
    install(SUCCESS)
    assert slack_run.main() == 0
    assert json.loads(Path("pipeline_status.json").read_text())["status"] == "success"
    assert posted == []


def test_no_token_means_no_journal_and_a_normal_run(journal_run, monkeypatch):
    install, posted, _ = journal_run
    monkeypatch.delenv("PL_CONTENT_TOKEN")
    install(SUCCESS)
    assert slack_run.main() == 0 and posted == []


# ── Notify journals whether the requester was told ──────────────────────────

def test_notify_is_journalled(tmp_path, monkeypatch):
    from src import slack_notify
    monkeypatch.chdir(tmp_path)
    for k, v in {"PL_WORKER_URL": "https://w", "PL_CONTENT_TOKEN": "tok", "GITHUB_RUN_ID": "77",
                 "SLACK_BOT_TOKEN": "x", "PL_SLACK_CHANNEL": "C1", "PL_SLACK_THREAD_TS": "1.2"}.items():
        monkeypatch.setenv(k, v)
    Path("pr_results.json").write_text(json.dumps({"docs_pr": "d", "releases_pr": "r"}))
    Path("pipeline_status.json").write_text(json.dumps({"status": "success"}))
    posted, slack = [], {"ok": True}

    def urlopen(req, timeout=0):
        if req.full_url.endswith("/journal"):
            posted.append(json.loads(req.data.decode()))
            return io.BytesIO(b"{}")
        return io.BytesIO(json.dumps({"ok": slack["ok"], "error": "channel_not_found"}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    assert slack_notify.main() == 0
    assert posted[-1]["stage"] == "notify" and posted[-1]["status"] == "ok"
    slack["ok"] = False
    assert slack_notify.main() == 1
    assert posted[-1]["status"] == "failed" and "channel_not_found" in posted[-1]["detail"]

    def down(req, timeout=0):
        if req.full_url.endswith("/journal"):
            posted.append(json.loads(req.data.decode()))
            return io.BytesIO(b"{}")
        raise OSError("slack down")

    monkeypatch.setattr("urllib.request.urlopen", down)
    with pytest.raises(OSError):
        slack_notify.main()
    assert posted[-1]["status"] == "failed" and "slack down" in posted[-1]["detail"]
