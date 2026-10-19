"""Pages go where the user asked, nested correctly, with links that resolve.

Regression for 2026-10-15: "Smart Flight Logs", requested under Flex Factory,
was published under Post-Flight Modules because single-page publishing ignored
target_section. The fixtures are the real cloud and on-premise SUMMARY.md files.
"""
import sys
import types
from pathlib import Path

import pytest

from src.summary_placement import (
    insert_entries, insert_entry, relative_to_summary, repo_path_for, resolve_section,
)

FIX = Path(__file__).parent / "fixtures"
CLOUD = (FIX / "cloud_SUMMARY.md").read_text()
ONPREM = (FIX / "onprem_SUMMARY.md").read_text()


@pytest.mark.parametrize("asked, label, folder, indent", [
    ("Flex Factory", "Flex Factory", "flinks-and-flows/flex-factory", 2),
    ("flex factory", "Flex Factory", "flinks-and-flows/flex-factory", 2),
    ("3D Mission Planning", "Pre-Flight Modules > 3D Mission Planning", "pre-flight-modules/3d-mission-planning", 2),
    ("Pre-Flight Modules > Planning", "Pre-Flight Modules > Planning", "pre-flight-modules/planning", 2),
    ("Post Flight Modules", None, "post-flight-modules", 0),
    ("Post-Flight Modules", None, "post-flight-modules", 0),
])
def test_resolves_real_sections(asked, label, folder, indent):
    p = resolve_section(CLOUD, asked)
    assert p is not None
    if label:
        assert p.label == label
    assert (p.folder, p.indent) == (folder, indent)


def test_unknown_section_is_none_not_a_guess():
    assert resolve_section(CLOUD, "Nonexistent Section") is None
    assert resolve_section(CLOUD, "") is None


def test_smart_flight_logs_lands_under_flex_factory():
    p = resolve_section(CLOUD, "Flex Factory")
    file_path = repo_path_for(p.folder, "smart-flight-logs.md", "SUMMARY.md")
    assert file_path == "flinks-and-flows/flex-factory/smart-flight-logs.md"
    new = insert_entry(CLOUD, p, "Smart Flight Logs", relative_to_summary(file_path, "SUMMARY.md"))
    lines = new.splitlines()
    i = lines.index("  * [Smart Flight Logs](flinks-and-flows/flex-factory/smart-flight-logs.md)")
    # Nested with the other Flex Factory pages, before the next section.
    assert lines[i - 1].startswith("  * [")
    assert "Discover More" in "\n".join(lines[i + 1:i + 4])


def test_heading_section_appends_after_last_page_not_after_blank_line():
    p = resolve_section(CLOUD, "Post-Flight Modules")
    new = insert_entry(CLOUD, p, "X Page", "post-flight-modules/x-page.md").splitlines()
    i = new.index("* [X Page](post-flight-modules/x-page.md)")
    assert new[i - 1].startswith("* [")  # directly after the last page, no gap


def test_onprem_links_are_relative_to_its_summary():
    p = resolve_section(ONPREM, "Installation")
    file_path = repo_path_for(p.folder, "air-gapped.md", "on-premise/SUMMARY.md")
    assert file_path == "on-premise/installation/air-gapped.md"
    assert relative_to_summary(file_path, "on-premise/SUMMARY.md") == "installation/air-gapped.md"


def test_already_listed_is_a_no_op():
    p = resolve_section(CLOUD, "Flex Factory")
    once = insert_entry(CLOUD, p, "Smart Flight Logs", "flinks-and-flows/flex-factory/smart-flight-logs.md")
    assert insert_entry(once, p, "Smart Flight Logs", "flinks-and-flows/flex-factory/smart-flight-logs.md") == once


def test_parent_with_subsections_nests_children():
    p = resolve_section(CLOUD, "Flex Factory")
    new = insert_entries(CLOUD, p, [
        ("Parent", "flinks-and-flows/flex-factory/parent/README.md", 0),
        ("Child", "flinks-and-flows/flex-factory/parent/child.md", 1),
    ])
    assert "  * [Parent](flinks-and-flows/flex-factory/parent/README.md)\n    * [Child](flinks-and-flows/flex-factory/parent/child.md)" in new


# ── End to end through the publisher ────────────────────────────────────

def _publisher(monkeypatch, summary_text):
    """A GitHubPublisher with GitHub replaced by an in-memory branch."""
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
    sys.modules.setdefault("requests", types.SimpleNamespace(Session=lambda: types.SimpleNamespace(headers={}), HTTPError=Exception))
    import importlib
    import src.config
    monkeypatch.setattr(src.config, "GITHUB_TOKEN", "test", raising=False)
    gp = importlib.import_module("src.tools.github_publisher")
    monkeypatch.setattr(gp, "GITHUB_TOKEN", "test", raising=False)
    pub = gp.GitHubPublisher()
    files = {"SUMMARY.md": summary_text}
    monkeypatch.setattr(pub, "_get_branch_sha", lambda *a: "base")
    monkeypatch.setattr(pub, "_create_branch", lambda *a: None)
    monkeypatch.setattr(pub, "_get_file", lambda repo, path, branch: (files.get(path), "sha") if path in files else (None, None))
    monkeypatch.setattr(pub, "_write_file", lambda repo, path, content, branch, msg, existing_sha=None: files.__setitem__(path, content))
    monkeypatch.setattr(pub, "_protect_readme", lambda *a: None)
    monkeypatch.setattr(pub, "_create_pr", lambda repo, branch, title, body: files.__setitem__("__pr_body__", body) or "https://github.com/x/pull/1")
    return pub, files


def _draft(target_path):
    return {
        "doc_page": {
            "filename": "smart-flight-logs.md",
            "frontmatter": "---\ndescription: x\n---",
            "content": "# Smart Flight Logs\n\nBody.",
            "target_path": target_path,
        },
        "release_note": {},
        "impacted_page_edits": [],
    }


def _publish(pub, target_section):
    return pub.publish(
        _draft("post-flight-modules"), output_dir="/tmp", feature_slug="smart-flight-logs",
        bundle_asset_paths=[], mode="doc_only", target_section=target_section,
    )


def test_publisher_honours_requested_section_over_the_model_guess(monkeypatch):
    pub, files = _publisher(monkeypatch, CLOUD)
    results = _publish(pub, "Flex Factory")
    assert "flinks-and-flows/flex-factory/smart-flight-logs.md" in files
    assert "post-flight-modules/smart-flight-logs.md" not in files
    assert "  * [Smart Flight Logs](flinks-and-flows/flex-factory/smart-flight-logs.md)" in files["SUMMARY.md"]
    assert results["docs_section"] == "Flex Factory"
    assert results["docs_file"] == "flinks-and-flows/flex-factory/smart-flight-logs.md"
    assert "**Placed under:** Flex Factory" in files["__pr_body__"]
    assert not results.get("warnings")


def test_publisher_warns_loudly_when_section_is_unknown(monkeypatch):
    pub, files = _publisher(monkeypatch, CLOUD)
    results = _publish(pub, "Flux Factori Of Doom")
    assert any("Flux Factori Of Doom" in w for w in results["warnings"])
    assert "⚠️" in files["__pr_body__"]


# ── Release note links to its doc page (release template, 2026-10-19) ──────

def _two_repo_publisher(monkeypatch, docs_summary, releases_summary, scope=None):
    monkeypatch.setenv("GITHUB_TOKEN", "test")
    sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
    sys.modules.setdefault("requests", types.SimpleNamespace(Session=lambda: types.SimpleNamespace(headers={}), HTTPError=Exception))
    import importlib
    gp = importlib.import_module("src.tools.github_publisher")
    monkeypatch.setattr(gp, "GITHUB_TOKEN", "test", raising=False)
    from src.doc_scope import get_scope
    pub = gp.GitHubPublisher(get_scope(scope) if scope else gp.CLOUD)
    files = {(gp.DOCS_REPO, pub.scope.summary_path): docs_summary, (gp.RELEASES_REPO, pub.scope.summary_path): releases_summary}
    monkeypatch.setattr(pub, "_get_branch_sha", lambda *a: "base")
    monkeypatch.setattr(pub, "_create_branch", lambda *a: None)
    monkeypatch.setattr(pub, "_get_file", lambda repo, path, branch: (files.get((repo, path)), "sha") if (repo, path) in files else (None, None))
    monkeypatch.setattr(pub, "_write_file", lambda repo, path, content, branch, msg, existing_sha=None: files.__setitem__((repo, path), content))
    monkeypatch.setattr(pub, "_protect_readme", lambda *a: None)
    monkeypatch.setattr(pub, "_create_pr", lambda repo, branch, title, body: files.__setitem__((repo, "__pr_body__"), body) or "https://github.com/x/pull/1")
    return gp, pub, files


def _both_draft(target_path):
    from src.release_template import render
    rn = render({"filename": "smart-flight-logs.md", "title": "Smart Flight Logs", "description": "Review flights.",
                 "summary": "Review completed flights in one place.", "features": [{"name": "Smart Flight Logs", "what": "Replay flights."}]},
                [], doc_title="Smart Flight Logs")
    d = _draft(target_path)
    d["release_note"] = rn
    d["_style_report"] = {"release_note": ["[warn] line 9: sentence has 31 words (aim for 30 or fewer)"]}
    return d


def test_release_note_links_to_where_the_doc_page_will_land(monkeypatch):
    gp, pub, files = _two_repo_publisher(monkeypatch, CLOUD, "# Releases\n\n## OCTOBER 2026\n\n")
    pub.publish(_both_draft("post-flight-modules"), output_dir="/tmp", feature_slug="smart-flight-logs",
                bundle_asset_paths=[], release_month="october-2026", mode="both", target_section="Flex Factory")
    note = files[(gp.RELEASES_REPO, "october-2026/smart-flight-logs.md")]
    assert "[Smart Flight Logs](https://docs.flytbase.com/flinks-and-flows/flex-factory/smart-flight-logs)" in note
    assert (gp.DOCS_REPO, "flinks-and-flows/flex-factory/smart-flight-logs.md") in files  # and it does land there
    assert "### Style check" in files[(gp.RELEASES_REPO, "__pr_body__")]


def test_release_only_drops_learn_more(monkeypatch):
    gp, pub, files = _two_repo_publisher(monkeypatch, CLOUD, "# Releases\n\n## OCTOBER 2026\n\n")
    pub.publish(_both_draft("post-flight-modules"), output_dir="/tmp", feature_slug="smart-flight-logs",
                bundle_asset_paths=[], release_month="october-2026", mode="release_only")
    note = files[(gp.RELEASES_REPO, "october-2026/smart-flight-logs.md")]
    assert "Learn More" not in note and "{{DOC_PAGE_URL}}" not in note


def test_onprem_release_sidebar_links_are_relative(monkeypatch):
    gp, pub, files = _two_repo_publisher(monkeypatch, ONPREM, "# Releases\n\n## OCTOBER 2026\n\n", scope="onprem")
    pub.publish(_both_draft("installation"), output_dir="/tmp", feature_slug="smart-flight-logs",
                bundle_asset_paths=[], release_month="october-2026", mode="release_only")
    summary = files[(gp.RELEASES_REPO, "on-premise/SUMMARY.md")]
    assert "(october-2026/smart-flight-logs.md)" in summary
    assert "(on-premise/october-2026" not in summary
