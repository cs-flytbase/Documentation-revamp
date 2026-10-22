"""Links and images are checked before a PR is opened."""
from src.link_check import check_page

FILES = {"pre-flight-modules/zones.md", "in-flight-modules/README.md", "in-flight-modules/assets/a.png"}
exists = FILES.__contains__


def test_good_links_pass():
    page = ("See [Zones](../pre-flight-modules/zones.md), [section](./), ![a](assets/a.png), "
            "[docs](https://docs.flytbase.com/pre-flight-modules/zones#x) and [web](https://example.com).")
    assert check_page(page, "in-flight-modules/video-overlays.md", exists) == []


def test_broken_targets_are_reported():
    page = ("[Ghost](../pre-flight-modules/ghost.md) ![missing](assets/never-uploaded.gif) "
            "[Old](https://docs.flytbase.com/no/such/page) [Up](../../../etc.md)")
    problems = check_page(page, "in-flight-modules/video-overlays.md", exists)
    assert len(problems) == 4
    assert any("ghost.md" in p for p in problems)
    assert any(p.startswith('image "missing"') for p in problems)
    assert any("no page exists at no/such/page.md" in p for p in problems)
    assert any("outside the repo" in p for p in problems)


def test_docs_urls_use_the_docs_repo_and_code_is_ignored():
    page = "[New](https://docs.flytbase.com/in-flight-modules/new-page)\n```\n[x](nowhere.md)\n```"
    assert check_page(page, "october-2026/x.md", lambda p: False, docs_exists={"in-flight-modules/new-page.md"}.__contains__) == []


def test_publisher_repairs_or_unlinks_broken_links_and_flags_them(monkeypatch):
    """P0 2026-10-22: a broken link is never shipped. It is re-pointed or unlinked, and flagged."""
    from tests.test_summary_placement import CLOUD, _publisher
    pub, files = _publisher(monkeypatch, CLOUD)
    monkeypatch.setattr(pub, "_api", lambda method, repo, path, **kw: {"tree": [{"path": "pre-flight-modules/zones.md", "type": "blob"}]})
    draft = {
        "doc_page": {"filename": "x.md", "frontmatter": "---\ndescription: x\n---",
                     "content": "# X\n\n[Zones](../pre-flight-modules/zones.md) [Ghost](../ghost.md)\n", "target_path": "in-flight-modules"},
        "release_note": {}, "impacted_page_edits": [],
    }
    results = pub.publish(draft, output_dir="/tmp", feature_slug="x", bundle_asset_paths=[], mode="doc_only")
    assert any("ghost.md" in w and "plain text" in w for w in results["needs_attention"])
    assert "ghost.md" in files["__pr_body__"] and "## ⚠️ Needs attention" in files["__pr_body__"]
    page = next(v for k, v in files.items() if k.endswith("/x.md"))
    assert "[Ghost]" not in page and "Ghost" in page and "[Zones](../pre-flight-modules/zones.md)" in page
