"""Fixes for the adversarial review of 2026-10-22 (findings 2, 4, 5, 6, 8, 9,
10) and the corpus cleanup bug found live the same day. Each test is the
failure the review described, run against the real code."""
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
import yaml

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from tests.test_replay_cockpit import BUNDLE, COCKPIT_DIR, LIVE, install  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


# ── #2 the fact check, the re-check and research never fail a finished draft ──

def test_safe_verify_turns_an_exception_into_a_flag():
    from src.repair import safe_verify
    attention = []
    v = safe_verify(lambda: (_ for _ in ()).throw(RuntimeError("429 rate limited")), attention)
    assert v["verified"] is False and v["issues"] == [] and v["_unavailable"]
    assert "could not run (RuntimeError: 429 rate limited)" in attention[0]
    attention = []
    assert safe_verify(lambda: "not json", attention)["_unavailable"] and "no usable result" in attention[0]
    ok = {"verified": True, "issues": []}
    attention = []
    assert safe_verify(lambda: ok, attention) is ok and attention == []


def test_a_failed_recheck_keeps_the_first_draft():
    from src.repair import repair_after_verification
    crit = {"issues": [{"severity": "critical", "claim": "made up"}]}
    attention = []

    def recheck(_):
        raise TimeoutError("model timed out")
    d, v = repair_after_verification(lambda claims: {"repaired": True}, recheck, {"orig": 1}, crit, attention)
    assert d == {"orig": 1} and v is crit
    assert any("could not re-run on the repaired draft (TimeoutError" in a for a in attention)
    assert any("not supported by the source material: made up" in a for a in attention)


def test_verification_outage_still_publishes_and_says_so(monkeypatch, tmp_path):
    import src.orchestrator as orch
    install(monkeypatch, tmp_path, LIVE)

    class Down:
        def verify(self, **kw):
            raise RuntimeError("credits exhausted")
    monkeypatch.setattr(orch, "VerificationAgent", Down)
    bundle = tmp_path / "cockpit-3-0-video-overlays-update"
    shutil.copytree(BUNDLE, bundle)
    result = orch.run_pipeline(str(bundle), mode="both", slack_thread_ts="1", target_section="Cockpit 3.0", doc_scope="cloud")
    assert result["status"] == "success"
    pr = result["pr_results"]
    assert pr["docs_pr"] and pr["releases_pr"]
    assert any("fact check could not run (RuntimeError: credits exhausted)" in a for a in pr["needs_attention"])


def test_a_verifier_that_cannot_start_is_flagged_not_fatal(monkeypatch, tmp_path):
    import src.orchestrator as orch
    install(monkeypatch, tmp_path, LIVE)

    def broken():
        raise ValueError("no API key")
    monkeypatch.setattr(orch, "VerificationAgent", broken)
    bundle = tmp_path / "x-feature"
    shutil.copytree(BUNDLE, bundle)
    result = orch.run_pipeline(str(bundle), mode="both", slack_thread_ts="1", target_section="Cockpit 3.0", doc_scope="cloud")
    assert result["status"] == "success"
    assert any("verification agent could not start" in a for a in result["pr_results"]["needs_attention"])


def test_research_failure_degrades_to_a_flag(monkeypatch, tmp_path):
    import src.orchestrator as orch
    install(monkeypatch, tmp_path, LIVE)

    class Down:
        def __init__(self, doc_scope=None):
            pass

        def research(self, *a):
            raise ConnectionError("OpenRouter 502")
    monkeypatch.setattr(orch, "ResearchAgent", Down)
    bundle = tmp_path / "cockpit-3-0-video-overlays-update"
    shutil.copytree(BUNDLE, bundle)
    result = orch.run_pipeline(str(bundle), mode="both", slack_thread_ts="1", target_section="Cockpit 3.0", doc_scope="cloud")
    assert result["status"] == "success"
    pr = result["pr_results"]
    assert pr["docs_pr"] and pr["docs_file"].startswith(f"{COCKPIT_DIR}/")  # confirmed placement still holds
    assert any("research step failed" in a and "OpenRouter 502" in a for a in pr["needs_attention"])


# ── #8 a complete vision failure is flagged ─────────────────────────────────

def test_vision_outage_is_flagged(monkeypatch, tmp_path):
    import src.orchestrator as orch
    install(monkeypatch, tmp_path, LIVE)

    class Down:
        def describe_all_assets(self, paths, pm_doc=""):
            raise RuntimeError("ffmpeg missing")
    monkeypatch.setattr(orch, "VisionAgent", Down)
    bundle = tmp_path / "cockpit-3-0-video-overlays-update"
    shutil.copytree(BUNDLE, bundle)
    result = orch.run_pipeline(str(bundle), mode="both", slack_thread_ts="1", target_section="Cockpit 3.0", doc_scope="cloud")
    assert result["status"] == "success"
    assert any("Image description failed for every file" in a and "ffmpeg missing" in a
               for a in result["pr_results"]["needs_attention"])


# ── #4 the branch comes from the requested feature, not the model's file name ─

def test_branch_slug_is_stable_and_never_the_literal_feature():
    from src.orchestrator import stable_feature_slug
    draft = {"release_note": {"filename": "video-overlays-update.md"}, "doc_page": {"filename": "zone.md"}}
    assert stable_feature_slug("cockpit-3-0-video-overlays", draft) == "cockpit-3-0-video-overlays"
    other = {"release_note": {"filename": "something-else-entirely.md"}}
    assert stable_feature_slug("cockpit-3-0-video-overlays", other) == "cockpit-3-0-video-overlays"
    assert stable_feature_slug("Zone Overlays!", {}) == "zone-overlays"
    # doc-only runs with no feature name use the doc file, not "feature"
    assert stable_feature_slug("feature", {"doc_page": {"filename": "smart-flight-logs.md"}}) == "smart-flight-logs"
    assert stable_feature_slug("", {"release_note": {}, "doc_page": {}}) == "feature"
    assert len(stable_feature_slug("x" * 200, {})) <= 60


def test_a_rerun_reuses_the_branch_and_two_features_in_one_thread_do_not_collide(monkeypatch, tmp_path):
    import src.orchestrator as orch
    gh, _, _ = install(monkeypatch, tmp_path, LIVE)
    for name in ("cockpit-3-0-video-overlays-update", "cockpit-3-0-video-overlays-update", "another-feature"):
        bundle = tmp_path / "runs" / name
        if bundle.exists():
            shutil.rmtree(bundle)
        shutil.copytree(BUNDLE, bundle)
        orch.run_pipeline(str(bundle), mode="doc_only", slack_thread_ts="1792409125.805209",
                          target_section="Cockpit 3.0", doc_scope="cloud")
    branches = sorted(b for (r, b) in gh.refs if r.endswith("flytbase-docs") and b != "main")
    assert branches == ["docs/another-feature-1792409125805209", "docs/cockpit-3-0-video-overlays-update-1792409125805209"]


# ── #5 a requested new section is never fuzzy-matched to an existing page ───

def test_new_section_request_matches_existing_sections_only_exactly():
    from src.summary_placement import resolve_section
    assert resolve_section(LIVE, "Alerts").label.endswith("Alerts and Notifications")  # fuzzy, by default
    assert resolve_section(LIVE, "Alerts", exact_only=True) is None
    assert resolve_section(LIVE, "Cockpit 3.0", exact_only=True).label.endswith("Cockpit 3.0")


def test_requested_new_section_alerts_is_created_not_merged_into_alerts_and_notifications(monkeypatch):
    from tests.test_p0_self_heal import FakeGitHub, doc_draft, docs_summary, make_publisher, publish
    gh = FakeGitHub(docs_summary=LIVE)
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, doc_draft(), target_section="Alerts", allow_new_section=True)
    lines = docs_summary(gh).splitlines()
    assert "## Alerts" in lines
    assert r["docs_section"] == "Alerts" and r["docs_file"].startswith("alerts/")


# ── #6 payload values are data, never shell ─────────────────────────────────

def _run_blocks():
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for job in (yaml.safe_load(wf.read_text()).get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if "run" in step:
                    yield wf.name, step.get("name", "?"), step["run"]


def test_no_workflow_interpolates_dispatch_payload_into_shell():
    bad = [(wf, name) for wf, name, run in _run_blocks() if "${{ github.event.client_payload" in run]
    assert bad == [], f"client_payload interpolated into a run block: {bad}"


@pytest.mark.skipif(shutil.which("bash") is None or shutil.which("openssl") is None, reason="needs bash and openssl")
def test_extract_payload_writes_hostile_values_verbatim(tmp_path):
    steps = yaml.safe_load((WORKFLOWS / "slack-pipeline.yml").read_text())["jobs"]["run-pipeline"]["steps"]
    step = next(s for s in steps if s.get("name") == "Extract payload")
    hostile = 'Alerts"; echo PWNED > pwned.txt; $(touch pwned2) `id` \\\nsecond line'
    env = {k: "" for k in step["env"]}
    env.update(P_TARGET_SECTION=hostile, P_FEATURE_NAME="zone overlays", P_MODE="both")
    out = tmp_path / "out"
    proc = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=tmp_path, capture_output=True, text=True,
                          env={**os.environ, **env, "GITHUB_OUTPUT": str(out)})
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "pwned.txt").exists() and not (tmp_path / "pwned2").exists()
    text = out.read_text()
    m = re.search(r"target_section<<(EOF_[0-9a-f]+)\n(.*?)\n\1\n", text, re.S)
    assert m and m.group(2) == hostile
    assert re.search(r"feature_name<<(EOF_[0-9a-f]+)\nzone overlays\n\1\n", text)


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_subsections_with_quotes_are_written_verbatim(tmp_path):
    steps = yaml.safe_load((WORKFLOWS / "slack-pipeline.yml").read_text())["jobs"]["run-pipeline"]["steps"]
    step = next(s for s in steps if s.get("name") == "Extract subsections")
    payload = json.dumps({"parent_title": "Operator's \"guide\"", "children": [{"title": "$(id)"}]})
    out = tmp_path / "out"
    script = step["run"].replace("/tmp/subsections.json", str(tmp_path / "subsections.json"))
    proc = subprocess.run(["bash", "-e", "-c", script], cwd=tmp_path, capture_output=True, text=True,
                          env={**os.environ, "P_SUBSECTIONS": payload, "GITHUB_OUTPUT": str(out)})
    assert proc.returncode == 0, proc.stderr
    assert json.loads((tmp_path / "subsections.json").read_text())["children"][0]["title"] == "$(id)"
    assert "has_subsections=true" in out.read_text()


# ── #9 Slack wording ────────────────────────────────────────────────────────

def test_slack_says_availability_and_only_formats_real_flag_names():
    from src.slack_notify import build_message
    base = {"docs_pr": "https://github.com/x/y/pull/1"}
    named = build_message({**base, "feature_flag": "video_overlays"}, "success", "")
    assert "Availability section added (feature flag `video_overlays`)" in named and "Access section" not in named
    sentence = build_message({**base, "feature_flag": "contact FlytBase team for enablement"}, "success", "")
    assert "Availability section added (behind a feature flag)" in sentence
    assert "contact FlytBase team for enablement" not in sentence


# ── #10 follow-up answers do not create "never received" flags ─────────────

def test_follow_up_names_are_not_checked_for_receipt():
    from src.assets import missing_named, request_text
    from src.slack_run import build_material
    kv = {"content": "Here is the material.\n\n[Follow-up answer from Dushyant]: GIFs for releases:\n"
                     "cockpit-3.0_zone-overlays-in-video.gif\ncockpit-3.0_live-annotations.gif",
          "text_files": [{"name": "guide.md", "content": "Media: zone-overlays.gif, zone-proximity-warnings.gif"}],
          "assets": [{"url": "u1", "name": "zone-overlays.gif"}]}
    m = build_material(kv)
    assert "cockpit-3.0_live-annotations.gif" in m["content"]  # still part of the material for drafting
    assert missing_named(m["naming_text"], ["zone-overlays.gif"]) == ["zone-proximity-warnings.gif"]
    assert request_text("plain request, no answers") == "plain request, no answers"
    assert request_text("[Follow-up answer from A]: x.png") == ""


# ── Corpus: list_ids against the real SDK shapes, and one alert per failure ──

class _ListItem:
    def __init__(self, id):
        self.id = id


class _ListResponse:
    """pinecone 10.x: index.list() yields one of these per page."""
    def __init__(self, ids):
        self.vectors = [_ListItem(i) for i in ids]
        self.pagination = None

    def __getitem__(self, key):  # DictLikeStruct: unknown keys raise KeyError
        raise KeyError(key)

    def __iter__(self):
        return iter(self.vectors)


class _Index:
    def __init__(self, ids, shape):
        self.ids, self.shape, self.deleted = list(ids), shape, []

    def list(self, prefix="", namespace=""):
        ids = [i for i in self.ids if i.startswith(prefix or "")]
        pages = [ids[i:i + 2] for i in range(0, len(ids), 2)]
        if self.shape == "v10":
            return iter(_ListResponse(p) for p in pages)
        if self.shape == "v10-dict":
            return iter({"vectors": [{"id": i} for i in p], "pagination": None} for p in pages)
        return iter(pages)  # up to v7: lists of strings

    def fetch(self, ids, namespace=""):
        return {"vectors": {}}

    def delete(self, ids, namespace=""):
        self.deleted += ids
        self.ids = [i for i in self.ids if i not in ids]

    def upsert(self, vectors, namespace=""):
        pass

    def describe_index_stats(self):
        return {"total_vector_count": len(self.ids)}


@pytest.mark.parametrize("shape", ["v10", "v10-dict", "v7"])
def test_list_ids_reads_every_sdk_shape(shape):
    from src.corpus_sync import PineconeStore
    ids = ["gh:docs:a.md#0", "gh:docs:a.md#1", "legacy-1", "legacy-2", "gh:rel:b.md#0"]
    store = PineconeStore(_Index(ids, shape), sleep=lambda s: None)
    assert store.list_ids() == ids
    assert store.list_ids("gh:docs:") == ["gh:docs:a.md#0", "gh:docs:a.md#1"]


def test_unknown_list_item_fails_loudly_instead_of_deleting_objects():
    from src.corpus_sync import _ids_of
    with pytest.raises(TypeError):
        _ids_of(object())
    assert _ids_of(_ListItem(None)) == []


@pytest.mark.parametrize("shape", ["v10", "v7"])
def test_remove_legacy_with_sdk_shaped_index_deletes_only_crawler_vectors(monkeypatch, shape):
    import src.corpus_sync as cs
    ids = ["gh:docs:a.md#0", "legacy-1", "legacy-2", "https://docs.flytbase.com/x#3"]
    index = _Index(ids, shape)
    store = cs.PineconeStore(index, sleep=lambda s: None)
    monkeypatch.setattr(cs, "discover", lambda gh, src: [object()])
    monkeypatch.setattr(cs, "_sync_source", lambda *a, **k: None)
    rep = cs.sync(gh=None, store=store, sources=cs.SOURCES, embed=None, clean_legacy=True)
    assert rep.errors == []
    assert sorted(index.deleted) == ["https://docs.flytbase.com/x#3", "legacy-1", "legacy-2"]
    assert index.ids == ["gh:docs:a.md#0"] and rep.legacy_deleted == 3


def test_remove_legacy_never_deletes_after_an_incomplete_read(monkeypatch):
    import src.corpus_sync as cs
    index = _Index(["gh:docs:a.md#0", "legacy-1"], "v10")
    store = cs.PineconeStore(index, sleep=lambda s: None)

    def discover(gh, src):
        if src is cs.SOURCES[-1]:
            raise cs.SourceReadError("GitHub 502")
        return [object()]
    monkeypatch.setattr(cs, "discover", discover)
    monkeypatch.setattr(cs, "_sync_source", lambda *a, **k: None)
    rep = cs.sync(gh=None, store=store, sources=cs.SOURCES, embed=None, clean_legacy=True)
    assert index.deleted == [] and rep.errors


def test_reported_marker_is_written_only_when_the_script_reports(monkeypatch, tmp_path):
    import src.corpus_sync as cs
    marker = tmp_path / "corpus_reported"
    monkeypatch.setenv("CORPUS_REPORTED_MARKER", str(marker))
    monkeypatch.setattr(cs, "post_alert", lambda *a, **k: True)
    monkeypatch.delenv("CORPUS_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cs.main([]) == 1 and marker.exists()  # reported (and alerted) itself
    marker.unlink()

    monkeypatch.setenv("CORPUS_GITHUB_TOKEN", "t")

    def crash(*a, **k):
        raise MemoryError("runner killed")
    monkeypatch.setattr(cs, "_main", crash)
    with pytest.raises(MemoryError):
        cs.main([])
    assert not marker.exists()  # a crash leaves no marker: the workflow's fallback alert fires
    assert cs.main(["--alert", "x"]) == 0 and not marker.exists()


def test_corpus_workflow_fallback_alert_is_gated_on_the_marker():
    wf = yaml.safe_load((WORKFLOWS / "corpus-refresh.yml").read_text())
    steps = wf["jobs"]["refresh-corpus"]["steps"]
    sync = next(s for s in steps if s.get("name") == "Sync the corpus from GitHub")
    fallback = next(s for s in steps if s.get("name") == "Alert if the job crashed before it could report")
    assert sync["env"]["CORPUS_REPORTED_MARKER"] == fallback["env"]["CORPUS_REPORTED_MARKER"]
    run = fallback["run"]
    assert run.index('[ -f "$CORPUS_REPORTED_MARKER" ]') < run.index("--alert")


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("reported,alerts", [(True, 0), (False, 1)])
def test_corpus_fallback_step_alerts_once(tmp_path, reported, alerts):
    wf = yaml.safe_load((WORKFLOWS / "corpus-refresh.yml").read_text())
    fallback = next(s for s in wf["jobs"]["refresh-corpus"]["steps"]
                    if s.get("name") == "Alert if the job crashed before it could report")
    marker = tmp_path / "corpus_reported"
    if reported:
        marker.write_text("reported\n")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "python").write_text(f"#!/bin/sh\necho alert >> {tmp_path}/alerts\n")
    (fake / "python").chmod(0o755)
    proc = subprocess.run(["bash", "-e", "-c", fallback["run"]], capture_output=True, text=True,
                          env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}", "CORPUS_REPORTED_MARKER": str(marker),
                               "RUN_URL": "u"})
    assert proc.returncode == 0, proc.stderr
    sent = (tmp_path / "alerts").read_text().count("alert") if (tmp_path / "alerts").exists() else 0
    assert sent == alerts
