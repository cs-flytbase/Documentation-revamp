"""Atomic publishing (P1, 2026-10-22): each repo gets ONE commit holding every
file, the docs repo goes first, the release note never links a doc page that
has no PR, and a branch that ends without a PR is deleted."""
from tests.test_p0_self_heal import HTMFO, FakeGitHub, _release, doc_draft, make_publisher, publish

DOCS = "FlytBaseAILabs/flytbase-docs"
RELS = "FlytBaseAILabs/flytbase-releases"
PAGE = f"{HTMFO}/cockpit-3-0/zone-overlays.md"
NOTE = "october-2026/zone-overlays.md"
EXISTING = "in-flight-modules/annotations.md"


def commits(gh, repo):
    return [c for c in gh.commit_log if c[0] == repo]


def full_draft(with_impacted=True):
    d = doc_draft(content="# Zone Overlays\n\nBody.\n\n![zones](assets/z.png)\n")
    d["release_note"] = _release()
    if with_impacted:
        d["impacted_page_edits"] = [{"source_url": "https://docs.flytbase.com/in-flight-modules/annotations",
                                     "section_heading": "## Related", "patch_mode": "append",
                                     "patch_content": "\n- [Zone Overlays](x.md)\n"}]
    return d


def gh_with_page():
    gh = FakeGitHub()
    gh.files[(DOCS, EXISTING)] = "# Annotations\n\n## Related\n\n- [Maps](maps.md)\n"
    return gh


def png(tmp_path):
    f = tmp_path / "z.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
    return str(f)


# ── One commit per repo ────────────────────────────────────────────────────

def test_every_file_for_a_repo_lands_in_one_commit(monkeypatch, tmp_path):
    gh = gh_with_page()
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, full_draft(), mode="both", target_section="Cockpit 3.0", assets=[png(tmp_path)])
    assert r["docs_pr"] and r["releases_pr"] and not r["errors"]
    (docs_commit,) = commits(gh, DOCS)
    assert set(docs_commit[2]) == {PAGE, "SUMMARY.md", f"{HTMFO}/cockpit-3-0/assets/z.png", EXISTING}
    assert isinstance(gh.tree_of(DOCS, "docs/zone-overlays-1792409125805209")[f"{HTMFO}/cockpit-3-0/assets/z.png"], bytes)
    (rel_commit,) = commits(gh, RELS)
    assert NOTE in rel_commit[2] and "SUMMARY.md" in rel_commit[2]
    # No one-file-at-a-time writes any more.
    assert not any(m == "PUT" and p.startswith("contents/") for m, _, p in gh.calls)


def test_docs_are_published_first_and_the_release_links_the_real_page(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, full_draft(with_impacted=False), mode="both", target_section="Cockpit 3.0")
    assert [c[0] for c in gh.commit_log] == [DOCS, RELS]
    note = gh.files[(RELS, NOTE)]
    assert f"https://docs.flytbase.com/{HTMFO}/cockpit-3-0/zone-overlays" in note
    assert r["docs_file"] == PAGE


# ── A file that cannot be uploaded ─────────────────────────────────────────

def test_a_failed_blob_drops_that_file_flags_it_and_still_commits_the_rest(monkeypatch, tmp_path):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    # Docs blobs go page, SUMMARY.md, asset: the asset's blob fails.
    gh.fail[("POST", "git/blobs")] = [None, None, 500]
    r = publish(pub, full_draft(with_impacted=False), target_section="Cockpit 3.0", assets=[png(tmp_path)])
    assert r["docs_pr"] and not r["errors"]
    (c,) = commits(gh, DOCS)
    assert PAGE in c[2] and f"{HTMFO}/cockpit-3-0/assets/z.png" not in c[2]
    assert any("assets/z.png` could not be uploaded and is not in this PR" in a for a in r["needs_attention"])
    assert "assets/z.png` could not be uploaded" in next(p for (rp, _), p in gh.prs.items() if rp == DOCS)["body"]


def test_if_no_file_can_be_uploaded_there_is_no_branch_and_a_clear_reason(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    gh.fail[("POST", "git/blobs")] = 500
    r = publish(pub, doc_draft(), target_section="Cockpit 3.0")
    assert not r["docs_pr"] and not gh.branches
    assert any("no file could be uploaded" in e for e in r["errors"])


# ── The commit itself failing ──────────────────────────────────────────────

def test_a_failed_commit_is_retried_once(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    gh.fail[("POST", "git/commits")] = [502]
    r = publish(pub, doc_draft(), target_section="Cockpit 3.0")
    assert r["docs_pr"] and not r["errors"] and len(commits(gh, DOCS)) == 1


def test_a_commit_that_fails_twice_leaves_no_branch_and_says_why(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    gh.fail[("POST", "git/trees")] = [500, 500]
    r = publish(pub, doc_draft(), target_section="Cockpit 3.0")
    assert not r["docs_pr"] and (DOCS, "docs/zone-overlays-1792409125805209") not in gh.refs
    assert any("could not commit to FlytBaseAILabs/flytbase-docs after a retry" in e for e in r["errors"])


def test_branch_ref_creation_failing_is_retried(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    gh.fail[("POST", "git/refs")] = [503]
    r = publish(pub, doc_draft(), target_section="Cockpit 3.0")
    assert r["docs_pr"] and len(gh.branches) == 1


# ── Docs failing never leaves the release note pointing at a 404 ───────────

def test_no_doc_pr_means_the_release_note_says_coming_soon_and_is_flagged(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    # Docs: both commit attempts fail. Releases: commits normally.
    gh.fail[("POST", "git/trees")] = [500, 500]
    r = publish(pub, full_draft(with_impacted=False), mode="both", target_section="Cockpit 3.0")
    assert not r["docs_pr"] and r["releases_pr"]
    note = gh.files[(RELS, NOTE)]
    assert "Documentation: coming soon." in note and "docs.flytbase.com" not in note and "{{DOC_PAGE_URL}}" not in note
    assert any("coming soon" in a for a in r["needs_attention"])
    assert "docs_file" not in r


def test_release_only_drops_the_doc_line_without_a_coming_soon(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    r = publish(pub, full_draft(with_impacted=False), mode="release_only")
    note = gh.files[(RELS, NOTE)]
    assert "coming soon" not in note and "{{DOC_PAGE_URL}}" not in note and "docs.flytbase.com" not in note
    assert r["releases_pr"] and not commits(gh, DOCS)


# ── Cleanup ────────────────────────────────────────────────────────────────

def test_a_branch_this_run_created_is_deleted_when_its_pr_cannot_be_opened(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    gh.fail[("POST", "pulls")] = 500
    r = publish(pub, doc_draft(), target_section="Cockpit 3.0")
    assert not r["docs_pr"] and r["errors"]
    assert len(commits(gh, DOCS)) == 1                      # it was committed...
    assert (DOCS, "docs/zone-overlays-1792409125805209") not in gh.refs  # ...then removed
    assert ("DELETE", DOCS, "git/refs/heads/docs/zone-overlays-1792409125805209") in gh.calls


def test_a_failed_rerun_never_deletes_the_branch_of_the_earlier_run(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    first = publish(pub, doc_draft(), target_section="Cockpit 3.0")
    _, pub2 = make_publisher(monkeypatch, gh)
    gh.fail[("POST", "git/trees")] = [500, 500]
    second = publish(pub2, doc_draft(content="# Zone Overlays\n\nRevised."), target_section="Cockpit 3.0")
    assert first["docs_pr"] and not second["docs_pr"]
    assert (DOCS, "docs/zone-overlays-1792409125805209") in gh.refs
    assert not any(m == "DELETE" for m, _, _ in gh.calls)


# ── Re-runs ────────────────────────────────────────────────────────────────

def test_a_rerun_adds_one_fast_forward_commit_to_the_same_branch_and_pr(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    first = publish(pub, doc_draft(), target_section="Cockpit 3.0")
    _, pub2 = make_publisher(monkeypatch, gh)
    second = publish(pub2, doc_draft(content="# Zone Overlays\n\nRevised."), target_section="Cockpit 3.0")
    assert first["docs_pr"] == second["docs_pr"] and len(gh.prs) == 1 and len(gh.branches) == 1
    c1, c2 = commits(gh, DOCS)
    assert c2[2] == [PAGE]  # only the page changed; the sidebar entry was already there
    assert ("PATCH", DOCS, "git/refs/heads/docs/zone-overlays-1792409125805209") in gh.calls


# ── Subsections ────────────────────────────────────────────────────────────

def test_subsection_release_notes_link_each_childs_real_doc_page(monkeypatch):
    gh = FakeGitHub()
    _, pub = make_publisher(monkeypatch, gh)
    child = lambda slug, title: {"slug": slug, "draft": {
        "release_note": _release(title),
        "doc_page": {"filename": f"{slug}.md", "frontmatter": "---\ndescription: x\n---", "content": f"# {title}\n\nBody.",
                     "target_path": "x"}}}
    subs = {"parent_slug": "video-overlays", "parent_title": "Video Overlays", "parent_content": "# Video Overlays\n\nFour features.",
            "children": [child("zone-overlays", "Zone Overlays"), child("live-annotation", "Live Annotation")]}
    r = pub.publish({"doc_page": {}, "release_note": {}, "impacted_page_edits": []}, output_dir="/tmp",
                    feature_slug="video-overlays", bundle_asset_paths=[], release_month="october-2026",
                    slack_thread_ts="1.2", mode="both", subsections=subs, target_section="Cockpit 3.0")
    assert r["docs_pr"] and r["releases_pr"] and not r["errors"]
    assert len(commits(gh, DOCS)) == 1 and len(commits(gh, RELS)) == 1
    folder = f"{HTMFO}/cockpit-3-0/video-overlays"
    for slug in ("zone-overlays", "live-annotation"):
        note = gh.files[(RELS, f"october-2026/video-overlays/{slug}.md")]
        assert f"https://docs.flytbase.com/{folder}/{slug}" in note
