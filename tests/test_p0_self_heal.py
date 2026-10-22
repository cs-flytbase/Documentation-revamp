"""P0 self-healing (2026-10-22): a run repairs what it can and flags the rest.

Regression for the Cockpit 3.0 run: the doc page was guessed into Live Video
Feed from a stale IA file, a legacy fallback invented "## Live Video Feed",
every image got "Image asset: <file>" as alt text, a duplicate GIF shipped
twice, unsupported claims shipped with no flag, and the release note linked
to a URL that 404'd. Each path below must end in a published PR with a
precise "Needs attention" item: never a crash, a silent skip or a failed run.
"""
import json
import sys
import types
from pathlib import Path

import pytest

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
sys.modules.setdefault("requests", types.SimpleNamespace(Session=lambda: types.SimpleNamespace(headers={}), HTTPError=Exception))

from src.assets import dedupe, missing_named  # noqa: E402
from src.link_check import repair_links  # noqa: E402
from src.summary_placement import (  # noqa: E402
    chain_label, insert_entry, nearest_ancestor, parent_chains, parse, resolve_exact, resolve_section, validate_insert,
)

FIX = Path(__file__).parent / "fixtures"
CLOUD = (FIX / "cloud_SUMMARY.md").read_text()
HTMFO = "in-flight-modules/how-to-manage-your-flight-operations"
COCKPIT_LINE = f"  * [Cockpit 3.0]({HTMFO}/cockpit-3-0.md)"
LIVE_GUESS = f"{HTMFO}/multi-view-dashboard/live-video-feed"


# ── A fake GitHub that behaves like the real API, errors included ─────────

class FakeGitHub:
    def __init__(self, docs_summary=CLOUD, releases_summary="# Releases\n\n## OCTOBER 2026\n\n* [Old](october-2026/old.md)\n"):
        self.files = {("FlytBaseAILabs/flytbase-docs", "SUMMARY.md"): docs_summary,
                      ("FlytBaseAILabs/flytbase-releases", "SUMMARY.md"): releases_summary}
        self.main = dict(self.files)
        self.branches, self.prs, self.labels, self.calls = set(), {}, [], []
        self.fail = {}  # (method, path prefix) -> exception or status to raise

    def http_error(self, status):
        import src.tools.github_publisher as gp
        err = gp.requests.HTTPError(f"HTTP {status}")
        err.response = types.SimpleNamespace(status_code=status)
        return err

    def api(self, method, repo, path, **kw):
        self.calls.append((method, repo, path))
        for (m, prefix), what in self.fail.items():
            if m == method and path.startswith(prefix):
                raise what if isinstance(what, BaseException) else self.http_error(what)
        if path == "git/ref/heads/main":
            return {"object": {"sha": "base"}}
        if path == "git/refs":
            ref = (repo, kw["json"]["ref"])
            if ref in self.branches:
                raise self.http_error(422)
            self.branches.add(ref)
            return {}
        if path.startswith("git/trees/"):
            return {"tree": [{"path": p, "type": "blob"} for (r, p) in self.main if r == repo]}
        if path == "pulls" and method == "POST":
            head = (repo, kw["json"]["head"])
            if head in self.prs:
                raise self.http_error(422)
            n = len(self.prs) + 1
            self.prs[head] = {"number": n, "html_url": f"https://github.com/{repo}/pull/{n}", **kw["json"]}
            return self.prs[head]
        if path == "pulls" and method == "GET":
            branch = kw["params"]["head"].split(":", 1)[1]
            return [self.prs[(repo, branch)]] if (repo, branch) in self.prs else []
        if path.startswith("pulls/") and method == "PATCH":
            pr = next(p for p in self.prs.values() if p["number"] == int(path.split("/")[1]))
            pr.update(kw["json"])
            return pr
        if path == "labels" or path.endswith("/labels"):
            self.labels.append((repo, path, kw.get("json")))
            return {}
        raise AssertionError(f"unexpected API call {method} {path}")


def make_publisher(monkeypatch, gh: FakeGitHub, scope=None):
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    import importlib
    gp = importlib.import_module("src.tools.github_publisher")
    monkeypatch.setattr(gp, "GITHUB_TOKEN", "test", raising=False)
    pub = gp.GitHubPublisher()
    pub._sleep = lambda s: None
    monkeypatch.setattr(pub, "_api", gh.api)
    monkeypatch.setattr(pub, "_get_file", lambda repo, path, branch: (
        (gh.main if branch == "main" else gh.files).get((repo, path)), "sha") if (repo, path) in (gh.main if branch == "main" else gh.files) else (None, None))

    def write(repo, path, content, branch, msg, existing_sha=None):
        gh.files[(repo, path)] = content
        pub._written.setdefault(repo, set()).add(path)
    monkeypatch.setattr(pub, "_write_file", write)
    monkeypatch.setattr(pub, "_upload_asset", lambda repo, asset, folder, *a, **k: gh.files.__setitem__((repo, f"{folder}/{Path(asset).name}"), "bin"))
    return gp, pub


def doc_draft(target_path=LIVE_GUESS, content="# Zone Overlays\n\nBody.", guess=""):
    d = {"doc_page": {"filename": "zone-overlays.md", "frontmatter": "---\ndescription: x\n---",
                      "content": content, "target_path": target_path},
         "release_note": {}, "impacted_page_edits": []}
    if guess:
        d["_placement_guess"] = guess
    return d


def publish(pub, draft, **kw):
    kw.setdefault("mode", "doc_only")
    return pub.publish(draft, output_dir="/tmp", feature_slug="zone-overlays", bundle_asset_paths=kw.pop("assets", []),
                       release_month="october-2026", slack_thread_ts="1792409125.805209", **kw)


def docs_summary(gh):
    return gh.files[("FlytBaseAILabs/flytbase-docs", "SUMMARY.md")]


def docs_pr(gh):
    return next(p for (r, _), p in gh.prs.items() if r.endswith("flytbase-docs"))


# ── Placement: the incident ───────────────────────────────────────────────

def test_live_video_feed_guess_goes_under_a_real_parent_never_a_new_group(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, doc_draft())
    summary = docs_summary(gh)
    assert summary.count("\n## ") == CLOUD.count("\n## ")          # no new group
    assert "## Live Video Feed" not in summary
    assert r["docs_section"].endswith("Multi View Dashboard")
    assert r["docs_file"] == f"{HTMFO}/multi-view-dashboard/zone-overlays.md"
    assert f"    * [Zone Overlays]({HTMFO}/multi-view-dashboard/zone-overlays.md)" in summary
    assert any("Placement unconfirmed" in a and "move_page" in a for a in r["needs_attention"])
    body = docs_pr(gh)["body"]
    assert body.startswith("## ⚠️ Needs attention") and "- [ ] Placement unconfirmed" in body
    assert ("FlytBaseAILabs/flytbase-docs", "issues/1/labels", {"labels": ["needs-attention"]}) in gh.labels


def test_confirmed_cockpit_3_0_nests_under_the_page(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, doc_draft(), target_section="Cockpit 3.0")
    lines = docs_summary(gh).splitlines()
    i = lines.index(COCKPIT_LINE)
    assert lines[i + 1] == f"    * [Zone Overlays]({HTMFO}/cockpit-3-0/zone-overlays.md)"
    assert r["docs_file"] == f"{HTMFO}/cockpit-3-0/zone-overlays.md"
    assert not any("unconfirmed" in a for a in r["needs_attention"])
    assert gh.labels == []  # nothing to flag, no label


def test_research_pick_from_the_live_sidebar_is_resolved_exactly(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    label = next(c for c in parent_chains(CLOUD) if c.endswith("Cockpit 3.0"))
    r = publish(pub, doc_draft(guess=label))
    assert r["docs_file"] == f"{HTMFO}/cockpit-3-0/zone-overlays.md"
    assert any("Placement unconfirmed" in a and "Cockpit 3.0" in a for a in r["needs_attention"])


def test_a_garbage_model_pick_falls_back_to_the_guessed_path(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, doc_draft(guess="Totally > Invented > Section"))
    assert r["docs_section"].endswith("Multi View Dashboard")


def test_nothing_fits_page_is_published_but_kept_out_of_the_sidebar(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, doc_draft(target_path="zzz/qqq"))
    assert docs_summary(gh) == CLOUD
    assert r["docs_file"] == "zone-overlays.md" and r["docs_pr"]
    assert any("NOT in the sidebar" in a for a in r["needs_attention"])


@pytest.mark.parametrize("summary", ["", "# Table of contents\n", "## Only\n\n\t\n## Headings\n"])
def test_empty_or_odd_summary_never_crashes(monkeypatch, summary):
    gh = FakeGitHub(docs_summary=summary)
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, doc_draft())
    assert r["docs_pr"] and not r["errors"]
    assert docs_summary(gh) == summary or "## " not in docs_summary(gh).replace(summary, "")


def test_missing_summary_is_flagged(monkeypatch):
    gh = FakeGitHub()
    del gh.files[("FlytBaseAILabs/flytbase-docs", "SUMMARY.md")]
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, doc_draft())
    assert r["docs_pr"] and any("Could not read SUMMARY.md" in a for a in r["needs_attention"])


def test_summary_read_error_is_flagged_not_fatal(monkeypatch):
    gh = FakeGitHub()
    gp, pub = make_publisher(monkeypatch, gh)

    def boom(repo, path, branch):
        if path == "SUMMARY.md":
            raise TimeoutError("GitHub timed out")
        return None, None
    monkeypatch.setattr(pub, "_get_file", boom)
    r = publish(pub, doc_draft())
    assert r["docs_pr"] and not r["errors"]
    assert any("Resolving placement failed" in a for a in r["needs_attention"])


def test_duplicate_titles_are_ambiguous_not_guessed():
    summary = "## A\n\n* [Settings](a/settings/README.md)\n  * [X](a/settings/x.md)\n\n## B\n\n* [Settings](b/settings/README.md)\n  * [Y](b/settings/y.md)\n"
    assert resolve_section(summary, "Settings") is None
    assert resolve_section(summary, "B > Settings").folder == "b/settings"
    dup = "## A\n\n* [Same](a/same.md)\n* [Same](a/same2.md)\n"
    assert resolve_exact(dup, "A > Same") is None


def test_requested_section_that_is_ambiguous_is_flagged(monkeypatch):
    summary = "## A\n\n* [Settings](a/settings/README.md)\n  * [X](a/settings/x.md)\n\n## B\n\n* [Settings](b/settings/README.md)\n  * [Y](b/settings/y.md)\n"
    gh = FakeGitHub(docs_summary=summary)
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, doc_draft(target_path="a/settings"), target_section="Settings")
    assert any('"Settings", is not in the sidebar (or matches more than one place)' in a for a in r["needs_attention"])
    assert r["docs_file"] == "a/settings/zone-overlays.md"


def test_leaf_parent_folder_convention():
    p = resolve_section(CLOUD, "Cockpit 3.0")
    assert (p.folder, p.indent, p.matched) == (f"{HTMFO}/cockpit-3-0", 4, "leaf item")
    readme_leaf = "## A\n\n* [Guide](a/guide/README.md)\n"
    assert resolve_section(readme_leaf, "Guide").folder == "a/guide"


def test_nearest_ancestor_and_chains():
    guess = "In-Flight Modules > How to Manage Your Flight Operations > Multi View Dashboard > Live Video Feed"
    assert nearest_ancestor(CLOUD, guess).label.endswith("Multi View Dashboard")
    assert nearest_ancestor(CLOUD, "Nowhere > At All") is None
    chains = parent_chains(CLOUD)
    assert "In-Flight Modules > How to Manage Your Flight Operations > Cockpit 3.0" in chains
    assert len(chains) == len(set(chains))


# ── Sidebar validation after the write ───────────────────────────────────

def test_validate_insert_accepts_a_clean_entry_and_catches_everything_else():
    p = resolve_section(CLOUD, "Cockpit 3.0")
    good = insert_entry(CLOUD, p, "Zone Overlays", f"{HTMFO}/cockpit-3-0/zone-overlays.md")
    assert validate_insert(CLOUD, good, p, exists=lambda path: True) == []
    assert any("does not exist" in x for x in validate_insert(CLOUD, good, p, exists=lambda path: False))
    group = good.replace(COCKPIT_LINE, COCKPIT_LINE + "\n\n## Live Video Feed\n")
    assert any("new sidebar group" in x for x in validate_insert(CLOUD, group, p))
    removed = good.replace("* [Discover More]", "* [Gone]") if "* [Discover More]" in good else good.replace(COCKPIT_LINE + "\n", "", 1)
    assert any("changed or removed" in x for x in validate_insert(CLOUD, removed, p))
    wrong = insert_entry(CLOUD, resolve_section(CLOUD, "Flex Factory"), "Zone Overlays", "flinks-and-flows/flex-factory/z.md")
    assert any("not \"" in x for x in validate_insert(CLOUD, wrong, p))
    twice = good.replace(f"    * [Zone Overlays]({HTMFO}/cockpit-3-0/zone-overlays.md)",
                         f"    * [Zone Overlays]({HTMFO}/cockpit-3-0/zone-overlays.md)\n    * [Zone Overlays 2]({HTMFO}/cockpit-3-0/zone-overlays.md)")
    assert any("more than once" in x for x in validate_insert(CLOUD, twice, p))


def test_a_bad_sidebar_edit_is_rebuilt_from_main(monkeypatch):
    gh = FakeGitHub()
    gp, pub = make_publisher(monkeypatch, gh)
    p = resolve_section(CLOUD, "Cockpit 3.0")
    broken = CLOUD.replace("## Discover More", "## Live Video Feed\n\n## Discover More")
    good_entry = insert_entry(broken, p, "Zone Overlays", f"{HTMFO}/cockpit-3-0/zone-overlays.md")
    attention = []
    pub._written["FlytBaseAILabs/flytbase-docs"] = {f"{HTMFO}/cockpit-3-0/zone-overlays.md"}
    pub._write_summary_entry("b", p, CLOUD, "sha", good_entry, "msg", attention)
    assert attention == []
    out = docs_summary(gh)
    assert "## Live Video Feed" not in out and f"    * [Zone Overlays]({HTMFO}/cockpit-3-0/zone-overlays.md)" in out


def test_an_unfixable_sidebar_edit_is_not_written_and_is_flagged(monkeypatch):
    gh = FakeGitHub()
    gp, pub = make_publisher(monkeypatch, gh)
    p = resolve_section(CLOUD, "Cockpit 3.0")
    attention = []
    pub._write_summary_entry("b", p, CLOUD, "sha", CLOUD + "\n## Junk\n", "msg", attention)
    assert docs_summary(gh) == CLOUD
    assert attention and "was NOT updated" in attention[0]


def test_docs_never_create_a_group_even_through_the_old_writers(monkeypatch):
    gh = FakeGitHub()
    gp, pub = make_publisher(monkeypatch, gh)
    with pytest.raises(ValueError, match="only created for release months"):
        pub._update_summary("FlytBaseAILabs/flytbase-docs", "b", "Live Video Feed", "x.md", "X")
    with pytest.raises(ValueError):
        pub._update_summary_nested("FlytBaseAILabs/flytbase-docs", "b", "Live Video Feed", "p/README.md", "P", [])


# ── Idempotent re-runs ────────────────────────────────────────────────────

def test_a_rerun_reuses_the_branch_and_pr_and_adds_no_duplicate_entry(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    first = publish(pub, doc_draft(), target_section="Cockpit 3.0")
    _, pub2 = make_publisher(monkeypatch, gh)
    second = publish(pub2, doc_draft(content="# Zone Overlays\n\nBody, revised."), target_section="Cockpit 3.0")
    assert first["docs_pr"] == second["docs_pr"]
    assert len(gh.prs) == 1 and len(gh.branches) == 1
    assert docs_summary(gh).count("](in-flight-modules/how-to-manage-your-flight-operations/cockpit-3-0/zone-overlays.md)") == 1
    assert "revised" in gh.files[("FlytBaseAILabs/flytbase-docs", f"{HTMFO}/cockpit-3-0/zone-overlays.md")]


# ── GitHub failures degrade, never crash ──────────────────────────────────

def test_label_creation_failing_still_publishes(monkeypatch):
    gh = FakeGitHub()
    gh.fail[("POST", "labels")] = 500
    gh.fail[("POST", "issues/")] = 403
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, doc_draft())
    assert r["docs_pr"] and not r["errors"]


def test_asset_upload_failure_is_flagged(monkeypatch, tmp_path):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    monkeypatch.setattr(pub, "_upload_asset", lambda *a, **k: "Asset upload failed (z.gif): 502")
    img = tmp_path / "z.gif"
    img.write_bytes(b"GIF")
    r = publish(pub, doc_draft(content="# Z\n\n![z](assets/z.gif)\n"), assets=[str(img)], target_section="Cockpit 3.0")
    assert r["docs_pr"] and any("z.gif could not be uploaded" in a for a in r["needs_attention"])


def test_cross_link_to_a_missing_page_is_flagged(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    d = doc_draft()
    d["impacted_page_edits"] = [{"source_url": "https://docs.flytbase.com/nope/page", "section_heading": "## A",
                                 "patch_mode": "append", "patch_content": "x"}]
    r = publish(pub, d, target_section="Cockpit 3.0")
    assert r["docs_pr"] and any("nope/page.md does not exist" in a for a in r["needs_attention"])


def test_api_retries_rate_limits_server_errors_and_network_errors_but_not_404(monkeypatch):
    import importlib
    gp = importlib.import_module("src.tools.github_publisher")
    monkeypatch.setattr(gp, "GITHUB_TOKEN", "test", raising=False)
    pub = gp.GitHubPublisher()
    pub._sleep = lambda s: None

    class Resp:
        def __init__(self, status, body=b"{}"):
            self.status_code, self.content = status, body

        def raise_for_status(self):
            if self.status_code >= 400:
                err = gp.requests.HTTPError(str(self.status_code))
                err.response = self
                raise err

        def json(self):
            return json.loads(self.content)

    script = [Resp(502), OSError("reset"), Resp(429), Resp(200, b'{"ok": 1}')]

    def request(method, url, **kw):
        assert kw["timeout"] == 60
        r = script.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r
    pub.session = types.SimpleNamespace(request=request)
    pub.API_ATTEMPTS = 4
    assert pub._api("GET", "o/r", "x") == {"ok": 1}
    script[:] = [Resp(404)]
    with pytest.raises(Exception):
        pub._api("GET", "o/r", "x")
    assert script == []  # a 404 is an answer, not retried


# ── Links ─────────────────────────────────────────────────────────────────

def test_repair_links_repoints_unlinks_and_leaves_images_and_code_alone():
    summary = "## In-Flight Modules\n\n* [Annotations](in-flight-modules/annotations/README.md)\n* [Go to Location (GTL)](in-flight-modules/flight-execution/go-to-location-gtl.md)\n"
    have = {"in-flight-modules/annotations/README.md", "in-flight-modules/flight-execution/go-to-location-gtl.md"}
    page = f"{HTMFO}/cockpit-3-0/zone-overlays.md"
    content = ("[Annotations](/in-flight-modules/annotation) [GTL](https://docs.flytbase.com/in-flight-modules/go-to-location-gtl) "
               "[Ghost](../ghost.md) ![img](assets/missing.png) [ok](https://example.com)\n```\n[Ghost](../ghost.md)\n```\n")
    out, fixed, unlinked = repair_links(content, page, lambda p: p in have, summary)
    assert "[Annotations](../../annotations/README.md)" in out
    assert "[GTL](https://docs.flytbase.com/in-flight-modules/flight-execution/go-to-location-gtl)" in out
    assert " Ghost ![img](assets/missing.png) [ok](https://example.com)" in out
    assert "```\n[Ghost](../ghost.md)\n```" in out
    assert len(fixed) == 2 and len(unlinked) == 1


def test_release_note_link_follows_where_the_doc_actually_landed(monkeypatch):
    gh = FakeGitHub()
    gp, pub = make_publisher(monkeypatch, gh)
    from src.release_template import render
    rn = render({"filename": "zone-overlays.md", "title": "Zone Overlays", "description": "x", "problem": "p.",
                 "introduction": "i.", "capabilities": [{"name": "Z", "description": "d."}]}, [], doc_title="Zone Overlays")
    d = doc_draft()
    d["release_note"] = rn
    monkeypatch.setattr(pub, "_planned_doc_path", lambda *a, **k: "somewhere/else.md")
    r = publish(pub, d, mode="both", target_section="Cockpit 3.0")
    note = gh.files[("FlytBaseAILabs/flytbase-releases", "october-2026/zone-overlays.md")]
    assert f"https://docs.flytbase.com/{HTMFO}/cockpit-3-0/zone-overlays" in note
    assert "somewhere/else" not in note
    assert any("doc link changed" in f for f in r["fixes"])


# ── Assets ────────────────────────────────────────────────────────────────

def test_dedupe_drops_identical_files_and_merges_routing(tmp_path):
    a, b, c = tmp_path / "go.gif", tmp_path / "go-2.gif", tmp_path / "other.gif"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    c.write_bytes(b"different")
    kept, dropped, aud = dedupe([str(a), str(b), str(c), str(tmp_path / "unreadable.gif")],
                                {"go.gif": "release", "go-2.gif": "doc"})
    assert [Path(p).name for p in kept] == ["go.gif", "other.gif", "unreadable.gif"]
    assert dropped == {"go-2.gif": "go.gif"} and aud == {"go.gif": "both"}


def test_missing_named_files_are_found():
    material = ("Media: `zone-overlays.gif`, zone-overlays-01-video-overlays-panel.png, "
                "assets/live-annotation-05-symbol-menu.png and Cockpit-3.0_go-to-location.GIF")
    received = ["zone-overlays.gif", "Cockpit 3.0_go to location.gif"]
    assert missing_named(material, received) == ["zone-overlays-01-video-overlays-panel.png", "live-annotation-05-symbol-menu.png"]
    assert missing_named("", received) == []


def test_routing_applies_to_every_copy_of_a_duplicate_name():
    from src.slack_run import build_material
    m = build_material({"assets": [{"url": "u1", "name": "go.gif"}, {"url": "u2", "name": "go.gif"}],
                        "intake": {"asset_audience": {"go.gif": "both"}}})
    assert sorted(m["asset_audience"].values()) == ["both", "both"] and len(m["asset_audience"]) == 2


# ── Vision ────────────────────────────────────────────────────────────────

def _vision(monkeypatch, answers):
    from src.agents.vision import VisionAgent
    v = VisionAgent.__new__(VisionAgent)
    v.model, v._sleep, v.calls = "primary", (lambda s: None), []
    v._encode_image = lambda path: ("b64", "image/png")

    def call(prompt, images, model=None):
        v.calls.append(model)
        a = answers.pop(0) if answers else RuntimeError("model unavailable")
        if isinstance(a, BaseException):
            raise a
        return a
    v._call_vision_batch = call
    return v


def _ok(*names):
    return json.dumps([{"file_name": n, "description": f"Shows {n}."} for n in names])


def test_vision_retries_bad_json_then_succeeds(monkeypatch):
    v = _vision(monkeypatch, ["not json", '[{"file_name": "a.png"', _ok("a.png")])
    out = v.describe_all_assets(["/x/a.png"])
    assert out[0]["description"] == "Shows a.png." and not out[0].get("_fallback")
    assert len(v.calls) == 3


def test_vision_uses_the_fallback_model(monkeypatch):
    monkeypatch.setenv("VISION_FALLBACK_MODEL", "backup")
    v = _vision(monkeypatch, [RuntimeError("402 credits")] * 3 + [_ok("a.png")])
    out = v.describe_all_assets(["/x/a.png"])
    assert v.calls == ["primary"] * 3 + ["backup"] and not out[0].get("_fallback")


def test_vision_total_failure_gives_context_alt_text_never_a_placeholder(monkeypatch):
    monkeypatch.delenv("VISION_FALLBACK_MODEL", raising=False)
    v = _vision(monkeypatch, [])
    doc = "## Zone Overlays\n\n- zone-overlays-01-video-overlays-panel.png: the Video overlays panel with the Zone overlays toggle\n"
    out = v.describe_all_assets(["/x/zone-overlays-01-video-overlays-panel.png", "/x/IMG_0042.png"], doc)
    assert all(o["_fallback"] for o in out)
    assert "Image asset" not in json.dumps(out) and "Unknown" not in json.dumps(out)
    assert out[0]["alt_text"].startswith("Zone overlays video overlays panel: the Video overlays panel")
    assert out[1]["alt_text"]


def test_vision_partial_answer_keeps_what_it_got(monkeypatch):
    v = _vision(monkeypatch, [_ok("a.png")] * 3)
    out = v.describe_all_assets(["/x/a.png", "/x/b.png"])
    assert not out[0].get("_fallback") and out[1]["_fallback"]


def test_vision_batches_large_sets(monkeypatch):
    names = [f"f{i}.png" for i in range(13)]
    v = _vision(monkeypatch, [_ok(*names[0:6]), _ok(*names[6:12]), _ok(*names[12:])])
    out = v.describe_all_assets([f"/x/{n}" for n in names])
    assert len(v.calls) == 3 and not any(o.get("_fallback") for o in out)


def test_vision_unreadable_file_gets_a_fallback(monkeypatch):
    v = _vision(monkeypatch, [_ok("a.png")])
    real = v._encode_image
    v._encode_image = lambda p: (_ for _ in ()).throw(OSError("corrupt")) if p.endswith("b.png") else real(p)
    out = v.describe_all_assets(["/x/a.png", "/x/b.png"])
    assert not out[0].get("_fallback") and out[1]["_fallback"]


# ── Verification repair ───────────────────────────────────────────────────

CRIT = {"issues": [{"severity": "critical", "location": "doc", "claim": "Works at night", "reason": "not in source"}]}


def test_unsupported_claims_get_one_repair_pass():
    from src.repair import repair_after_verification
    attention = []
    d, v = repair_after_verification(lambda claims: {"fixed": claims}, lambda d: {"issues": []}, {"orig": 1}, CRIT, attention)
    assert d == {"fixed": ["[doc] Works at night (not in source)"]} and attention == []


@pytest.mark.parametrize("redraft", [
    lambda c: {"still": 1},
    lambda c: {"error": "bad JSON"},
    lambda c: (_ for _ in ()).throw(RuntimeError("model down")),
])
def test_unrepaired_claims_are_flagged_and_the_first_draft_kept_on_error(redraft):
    from src.repair import repair_after_verification
    attention = []
    d, v = repair_after_verification(redraft, lambda d: CRIT, {"orig": 1}, CRIT, attention)
    assert attention == ["Fact check: not supported by the source material: [doc] Works at night (not in source)"]


def test_clean_verification_makes_no_extra_calls():
    from src.repair import repair_after_verification
    called = []
    d, v = repair_after_verification(lambda c: called.append(1), lambda d: called.append(2), {"o": 1}, {"issues": []}, [])
    assert called == [] and d == {"o": 1}


# ── Slack ─────────────────────────────────────────────────────────────────

def test_slack_message_lists_needs_attention_and_fixes():
    from src.slack_notify import build_message
    msg = build_message({"docs_pr": "https://github.com/x/pull/1", "needs_attention": ["Placement unconfirmed: ..."],
                         "warnings": ["Placement unconfirmed: ...", "other"], "fixes": ["a", "b"]}, {"status": "success"}, "run")
    assert "Needs your eyes before merging (1)" in msg and "• Placement unconfirmed" in msg
    assert msg.count("Placement unconfirmed") == 1 and "• other" in msg
    assert "Fixed 2 thing(s)" in msg


def test_chain_label_round_trips_through_resolve_exact():
    for item in parse(CLOUD)[1]:
        label = chain_label(item)
        if [chain_label(i) for i in parse(CLOUD)[1]].count(label) == 1:
            assert resolve_exact(CLOUD, label) is not None, label
