"""Replay the Cockpit 3.0 release through the whole pipeline, on every CI run.

The material is the real one sent to Son of Anton on 2026-10-19 (the MD
verbatim, the 22 media files as tiny stand-ins of the same names). The model
is replaced by recorded answers that reflect a correct draft; everything else
is the real code: bundle validation, dedupe, drafting templates and checks,
verification repair, placement, link repair, atomic publishing. The result is
read back from a fake GitHub with the same reader the weekly real run uses
(src/e2e_check.view_of) and checked against src/invariants.py.

Variant 2 replays the actual incident: nine screenshots never arrived, the
GIFs were routed to the release note only, and nobody confirmed a section.
It must still publish, with each problem flagged precisely.
"""
import json
import re
import shutil
import sys
import types
from pathlib import Path

import pytest

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
sys.modules.setdefault("requests", types.SimpleNamespace(Session=lambda: types.SimpleNamespace(headers={}), HTTPError=Exception))
sys.modules.setdefault("sentence_transformers", types.SimpleNamespace(SentenceTransformer=None))
sys.modules.setdefault("pinecone", types.SimpleNamespace(Pinecone=None, ServerlessSpec=None))

from tests.test_p0_self_heal import CLOUD, FakeGitHub  # noqa: E402

FIX = Path(__file__).parent / "fixtures"
BUNDLE = FIX / "cockpit_3_0"
REC = FIX / "cockpit_3_0_recorded"
LIVE = (FIX / "cloud_SUMMARY_live.md").read_text()
DOCS, RELS = "FlytBaseAILabs/flytbase-docs", "FlytBaseAILabs/flytbase-releases"
COCKPIT_DIR = "in-flight-modules/how-to-manage-your-flight-operations/cockpit-3-0"
GIFS = ["go-to-location-from-video.gif", "live-annotation.gif", "zone-overlays.gif", "zone-proximity-warnings.gif"]
MISSING_9 = [f"live-annotation-0{i}-" for i in range(1, 9)] + ["go-to-location-04-"]


def _rec(name):
    return json.loads((REC / name).read_text())


def _seeded(summary):
    """A fake GitHub whose docs main holds every page the sidebar lists."""
    gh = FakeGitHub(docs_summary=summary)
    for link in re.findall(r"\]\(([^)\s]+\.md)\)", summary):
        gh.files[(DOCS, link)] = f"# {link}\n"
    return gh


def install(monkeypatch, tmp_path, summary):
    """Wire the whole pipeline to a fake GitHub and recorded model answers.
    Returns (fake GitHub, call counters, run_pipeline)."""
    import src.config as config
    import src.orchestrator as orch
    import src.tools.github_publisher as gp
    from src.agents.drafting import DraftingAgent
    from src.summary_placement import parent_chains

    gh = _seeded(summary)
    calls = {"drafting": [], "vision": 0, "verify": 0}
    monkeypatch.setattr(config, "GITHUB_TOKEN", "test")
    monkeypatch.setattr(gp, "GITHUB_TOKEN", "test")
    monkeypatch.setattr(gp.GitHubPublisher, "_api", lambda self_, m, r, p, **kw: gh.api(m, r, p, **kw))
    monkeypatch.setattr(orch, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(orch, "_save_feature_note", lambda *a, **k: None)
    monkeypatch.setattr(orch, "fetch_exemplar", lambda *a, **k: "")
    monkeypatch.setattr(orch, "load_live_tree", lambda scope=None: "Every existing section and page in the live "
                        "sidebar.\n" + "\n".join(f"- {c}" for c in parent_chains(summary)))
    research, vision = _rec("research.json"), _rec("vision.json")

    class Research:
        def __init__(self, doc_scope=None):
            pass

        def research(self, pm_doc, ia):
            assert "Cockpit 3.0" in ia  # the live sidebar reached the agent
            return json.loads(json.dumps(research))

    class Vision:
        def describe_all_assets(self, paths, pm_doc=""):
            calls["vision"] += 1
            by = {v["file_name"]: v for v in vision}
            return [by[Path(p).name] for p in paths if Path(p).name in by]

    class Verify:
        def verify(self, **kw):
            calls["verify"] += 1
            return {"verified": True, "faithfulness_score": 9, "issues": []}

    def drafting():
        agent = DraftingAgent.__new__(DraftingAgent)
        agent.conversation_history = []

        def run(message):
            kind = "release" if "RELEASE NOTE" in agent.system_prompt else "doc"
            calls["drafting"].append((kind, message))
            return json.dumps(_rec(f"{kind}.json"))
        agent.run = run
        return agent

    monkeypatch.setattr(orch, "ResearchAgent", Research)
    monkeypatch.setattr(orch, "VisionAgent", Vision)
    monkeypatch.setattr(orch, "VerificationAgent", Verify)
    monkeypatch.setattr(orch, "DraftingAgent", drafting)
    return gh, calls, orch.run_pipeline


class _Replay:
    def __init__(self, monkeypatch, tmp_path, summary, drop=(), audience=None, target_section="Cockpit 3.0"):
        self.gh, self.calls, run_pipeline = install(monkeypatch, tmp_path, summary)
        bundle = tmp_path / "bundle"
        shutil.copytree(BUNDLE, bundle)
        for f in list((bundle / "assets").iterdir()):
            if any(f.name.startswith(p) for p in drop):
                f.unlink()
        self.received = sorted(p.name for p in (bundle / "assets").iterdir())
        self.md = next(BUNDLE.glob("*.md")).read_text()

        from src.e2e_check import audience_for, missing_attention
        self.attention_in = missing_attention(self.md, self.received)
        self.result = run_pipeline(
            str(bundle), mode="both", slack_thread_ts="1792409125.805209", target_section=target_section,
            asset_audience=audience if audience is not None else audience_for(self.received),
            attention=self.attention_in, doc_scope="cloud")
        self.pr = self.result.get("pr_results") or {}
        self.target_section = target_section

    def branch(self, repo):
        return next(b for (r, b) in self.gh.refs if r == repo and b != "main")

    def published(self, require_clean):
        from src.assets import missing_named
        from src.e2e_check import view_of
        from src.invariants import Published
        api = self.gh.api
        return Published(
            docs=view_of(api, DOCS, self.branch(DOCS), "SUMMARY.md"),
            releases=view_of(api, RELS, self.branch(RELS), "SUMMARY.md"),
            named_media=missing_named(self.md, []), needs_attention=self.pr.get("needs_attention") or [],
            target_section=self.target_section, require_clean=require_clean)


def _check(published):
    from src.invariants import check_all, table
    results = check_all(published)
    assert all(r.ok for r in results), "\n" + table(results)
    return results


# ── The clean run ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("summary", [LIVE, CLOUD], ids=["live sidebar (Cockpit 3.0 has children)",
                                                          "incident sidebar (Cockpit 3.0 is a leaf)"])
def test_clean_cockpit_3_0_run_holds_every_invariant(monkeypatch, tmp_path, summary):
    r = _Replay(monkeypatch, tmp_path, summary)
    assert r.result["status"] == "success"
    assert r.pr["docs_pr"] and r.pr["releases_pr"] and not r.pr["errors"]
    assert r.pr["docs_file"].startswith(f"{COCKPIT_DIR}/")
    assert r.pr["needs_attention"] == []
    _check(r.published(require_clean=True))
    # The model was asked once per output: the recorded draft passed every check.
    assert [k for k, _ in r.calls["drafting"]] == ["release", "doc"]
    assert r.calls["vision"] == 1 and r.calls["verify"] == 1


def test_all_22_named_files_land_once_in_the_right_repos(monkeypatch, tmp_path):
    r = _Replay(monkeypatch, tmp_path, LIVE)
    p = r.published(require_clean=True)
    doc = next(iter(p.docs.pages.values()))
    note = next(iter(p.releases.pages.values()))
    assert len(p.named_media) == 22
    for name in p.named_media:
        assert doc.count(f"](assets/{name})") == 1, name
        assert f"{COCKPIT_DIR}/assets/{name}" in p.docs.paths
    for gif in GIFS:
        assert note.count(f"](assets/{gif})") == 1
    assert not re.search(r"\]\(assets/[^)]+\.png\)", note)  # screenshots stay on the doc page


def test_sidebar_entry_nests_under_cockpit_3_0_and_adds_no_group(monkeypatch, tmp_path):
    r = _Replay(monkeypatch, tmp_path, LIVE)
    summary = r.gh.tree_of(DOCS, r.branch(DOCS))["SUMMARY.md"]
    lines = summary.splitlines()
    i = lines.index(f"  * [Cockpit 3.0]({COCKPIT_DIR}.md)")
    entry = f"    * [Cockpit 3.0 Video Overlays]({COCKPIT_DIR}/cockpit-3-0-video-overlays-update.md)"
    assert entry in lines[i + 1:i + 7]
    assert summary.count("\n## ") == LIVE.count("\n## ")


def test_release_links_the_published_doc_page(monkeypatch, tmp_path):
    r = _Replay(monkeypatch, tmp_path, LIVE)
    note = next(iter(r.published(True).releases.pages.values()))
    assert f"(https://docs.flytbase.com/{COCKPIT_DIR}/cockpit-3-0-video-overlays-update)" in note
    assert "{{DOC_PAGE_URL}}" not in note and "Image asset" not in note


def test_one_commit_per_repo_and_no_pull_request_twice(monkeypatch, tmp_path):
    r = _Replay(monkeypatch, tmp_path, LIVE)
    assert [repo for repo, *_ in r.gh.commit_log] == [DOCS, RELS]  # docs first
    assert len(r.gh.prs) == 2


# ── The incident, replayed ─────────────────────────────────────────────────

def test_the_incident_still_publishes_and_flags_each_problem(monkeypatch, tmp_path):
    routing = {g: "release" for g in GIFS}  # what Son of Anton stored on 2026-10-19
    r = _Replay(monkeypatch, tmp_path, LIVE, drop=MISSING_9, audience=routing, target_section="")
    assert r.result["status"] == "success"
    assert r.pr["docs_pr"] and r.pr["releases_pr"] and not r.pr["errors"]
    att = r.pr["needs_attention"]

    # 1. Nine screenshots never arrived: named, in one precise item.
    missing = [a for a in att if a.startswith("The material names 9 file(s) that were never received")]
    assert len(missing) == 1
    gone = sorted(n for n in r.published(False).named_media if not (BUNDLE / "assets" / n).exists() or
                  any(n.startswith(p) for p in MISSING_9))
    assert len(gone) == 9 and all(n in missing[0] for n in gone)

    # 2. The GIFs were routed release-only: honored, so they are on the release
    #    note and not on the doc page (the requester's routing is caught before
    #    the run, in Son of Anton's confirmation summary, not overridden here).
    p = r.published(False)
    doc = next(iter(p.docs.pages.values()))
    note = next(iter(p.releases.pages.values()))
    for gif in GIFS:
        assert f"](assets/{gif})" in note and f"](assets/{gif})" not in doc

    # 3. No confirmed section: placed under the research pick from the live
    #    sidebar, never a new group, and flagged as unconfirmed.
    assert r.pr["docs_file"].startswith(f"{COCKPIT_DIR}/")
    assert any(a.startswith("Placement unconfirmed") and "Cockpit 3.0" in a for a in att)

    # Everything named is used or flagged; nothing else breaks.
    _check(p)
    assert len(att) == 2, att


def test_an_invariant_catches_a_silently_lost_file(monkeypatch, tmp_path):
    """The check must fail when a named file is neither published nor flagged:
    that is exactly what happened to the nine screenshots."""
    from src.invariants import named_files_accounted
    r = _Replay(monkeypatch, tmp_path, LIVE, drop=MISSING_9)
    p = r.published(False)
    p.needs_attention = []  # pretend nobody said anything
    res = named_files_accounted(p)
    assert not res.ok and "silently lost" in res.detail and "live-annotation-05-symbol-menu.png" in res.detail
