"""Corpus sync from the GitHub repos (2026-10-22).

The website crawler reported green for three months while adding nothing.
These tests pin the replacement: the right pages, a true incremental diff,
writes that survive a crash, no deletion after a partial read, and an alert
whenever something is wrong.
"""
import base64
import io
import json
import types
import urllib.error

import pytest

from src import corpus_sync as cs

DOCS = cs.Source("docs", "Org/docs", "doc", "https://docs.flytbase.com", ("SUMMARY.md", "on-premise/SUMMARY.md"))
RELS = cs.Source("releases", "Org/releases", "release", "https://releases.flytbase.com", ("SUMMARY.md",))

DOCS_SUMMARY = """# Table of contents

* [Welcome](README.md)

## In-Flight Modules

* [How to Manage](in-flight-modules/how-to-manage/README.md)
  * [Cockpit 3.0](in-flight-modules/how-to-manage/cockpit-3-0.md)
    * [Zone Overlays](in-flight-modules/how-to-manage/cockpit-3-0/zone-overlays.md)
* [External](https://example.com/x.md)
* [Missing](in-flight-modules/not-in-tree.md)
"""
ONPREM_SUMMARY = "* [Install](install.md)\n* [Cockpit again](../in-flight-modules/how-to-manage/cockpit-3-0.md)\n"
PAGE = "---\ndescription: x\n---\n\n# Zone Overlays\n\nZone Overlays draws No-Fly Zones on the live video in Cockpit.\n\n## Procedures\n\n1. Click **Video overlays** to open the panel and turn on Zone overlays.\n\n![img](assets/a.png)\n"


# ── fakes ──────────────────────────────────────────────────────────────

class FakeGitHub:
    """Serves trees and blobs. files: {repo: {path: (sha, text)}}."""

    def __init__(self, files, fail=None, truncated=()):
        self.files = files
        self.fail = fail or {}          # url substring -> list of HTTP codes to raise first
        self.truncated = set(truncated)
        self.calls = []

    def opener(self, req, timeout):
        url = req.full_url
        self.calls.append(url)
        for key, codes in self.fail.items():
            if key in url and codes:
                code = codes.pop(0)
                hdrs = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "0"} if code == 403 else {}
                raise urllib.error.HTTPError(url, code, "err", hdrs, io.BytesIO(b""))
        repo = "/".join(url.split("/repos/")[1].split("/")[:2])
        if "/git/trees/" in url:
            tree = [{"path": p, "sha": sha, "type": "blob"} for p, (sha, _) in self.files[repo].items()]
            body = {"tree": tree, "truncated": repo in self.truncated}
        else:
            sha = url.rsplit("/", 1)[1]
            text = next(t for s, t in self.files[repo].values() if s == sha)
            body = {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
        return io.BytesIO(json.dumps(body).encode())


class FakeIndex:
    def __init__(self, fail_upsert_after=None):
        self.vectors = {}
        self.fail_upsert_after = fail_upsert_after
        self.upserts = 0
        self.deleted = []

    def list(self, prefix=None, namespace=""):
        ids = sorted(v for v in self.vectors if not prefix or v.startswith(prefix))
        for i in range(0, len(ids), 3):   # paginated, as the SDK yields pages
            yield ids[i:i + 3]

    def fetch(self, ids, namespace=""):
        return types.SimpleNamespace(vectors={
            i: types.SimpleNamespace(metadata=self.vectors[i]["metadata"]) for i in ids if i in self.vectors})

    def upsert(self, vectors, namespace=""):
        if self.fail_upsert_after is not None and self.upserts >= self.fail_upsert_after:
            raise RuntimeError("pinecone down")
        self.upserts += 1
        for v in vectors:
            self.vectors[v["id"]] = v

    def delete(self, ids, namespace=""):
        self.deleted.extend(ids)
        for i in ids:
            self.vectors.pop(i, None)

    def describe_index_stats(self):
        return types.SimpleNamespace(total_vector_count=len(self.vectors))


def embed(texts):
    return [[float(len(t)), 1.0] for t in texts]


REAL_GITHUB = cs.GitHub  # tests of main() replace cs.GitHub


def gh(fake):
    return REAL_GITHUB("t", opener=fake.opener, sleep=lambda s: None)


def store(index):
    return cs.PineconeStore(index, sleep=lambda s: None)


def repo_files(page_sha="p1", page=PAGE):
    return {
        "Org/docs": {
            "SUMMARY.md": ("s1", DOCS_SUMMARY),
            "on-premise/SUMMARY.md": ("s2", ONPREM_SUMMARY),
            "README.md": ("r1", "# Welcome\n\nWelcome to the FlytBase documentation for operators."),
            "in-flight-modules/how-to-manage/README.md": ("h1", "# How to Manage\n\n"),
            "in-flight-modules/how-to-manage/cockpit-3-0.md": ("c1", "# Cockpit 3.0\n\nCockpit 3.0 is the single drone command center."),
            "in-flight-modules/how-to-manage/cockpit-3-0/zone-overlays.md": (page_sha, page),
            "on-premise/install.md": ("i1", "# Install\n\nInstall the on-premise stack on the server first."),
            "moi-on-premise/secret.md": ("m1", "# Customer\n\nCustomer specific."),
            "in-flight-modules/how-to-manage/cockpit-3-0/assets/a.png": ("a1", "binary"),
        },
        "Org/releases": {
            "SUMMARY.md": ("rs", "## October 2026\n\n* [Video Overlays](october-2026/video-overlays.md)\n"),
            "october-2026/video-overlays.md": ("v1", "# Video Overlays\n\nCockpit 3.0 adds four video overlay capabilities."),
        },
    }


def run(fake, index, **kw):
    kw.setdefault("clean_legacy", False)
    return cs.sync(gh(fake), store(index), (DOCS, RELS), embed=embed, **kw)


# ── what gets indexed ──────────────────────────────────────────────────

def test_pages_come_from_summary_only_with_breadcrumbs():
    pages = cs.discover(gh(FakeGitHub(repo_files())), DOCS)
    paths = [p.path for p in pages]
    assert paths == ["README.md", "in-flight-modules/how-to-manage/README.md",
                     "in-flight-modules/how-to-manage/cockpit-3-0.md",
                     "in-flight-modules/how-to-manage/cockpit-3-0/zone-overlays.md", "on-premise/install.md"]
    # external links, files missing from the tree, assets, customer-specific and
    # pages listed twice (the on-prem SUMMARY repeats Cockpit 3.0) are excluded
    assert not any(p.startswith("moi-on-premise/") for p in paths)
    zone = pages[3]
    assert zone.breadcrumb == "In-Flight Modules > How to Manage > Cockpit 3.0 > Zone Overlays"


def test_public_urls_and_dates():
    assert DOCS.url("in-flight-modules/how-to-manage/README.md") == "https://docs.flytbase.com/in-flight-modules/how-to-manage"
    assert DOCS.url("README.md") == "https://docs.flytbase.com"
    assert RELS.url("october-2026/video-overlays.md") == "https://releases.flytbase.com/october-2026/video-overlays"
    assert cs.source_date("https://releases.flytbase.com/october-2026/video-overlays") == "2026-10"
    assert cs.source_date("https://docs.flytbase.com/x") == ""


def test_markdown_chunking_strips_noise_and_splits_long_sections():
    chunks = cs.chunk_markdown(PAGE)
    assert [h for h, _ in chunks] == ["Zone Overlays", "Procedures"]
    body = chunks[1][1]
    assert "![img]" not in body and "description:" not in chunks[0][1]
    long = "# T\n\n" + "\n\n".join("Sentence number %d is here." % i * 20 for i in range(30))
    assert all(len(c) <= cs.CHUNK_MAX_CHARS + 10 for _, c in cs.chunk_markdown(long))
    code = cs.chunk_markdown("```\n# a shell comment, not a heading\n```\n")
    assert [h for h, _ in code] == ["Introduction"]  # '#' inside a code block does not start a section


def test_metadata_is_compatible_with_retrieval():
    index = FakeIndex()
    run(FakeGitHub(repo_files()), index)
    meta = next(v["metadata"] for v in index.vectors.values() if v["metadata"]["path"].endswith("zone-overlays.md"))
    for key in ("content", "heading", "ia_node", "ia_label", "source_type", "source_url", "source_date", "feature_tags"):
        assert key in meta
    assert meta["source_type"] == "doc" and meta["blob_sha"] == "p1" and meta["chunk_count"] == 2
    assert all(v.startswith("gh:") and v.isascii() for v in index.vectors)


# ── the incremental diff ───────────────────────────────────────────────

def test_first_run_adds_everything_second_run_changes_nothing():
    index = FakeIndex()
    rep = run(FakeGitHub(repo_files()), index)
    assert len(rep.added) == 5 and rep.errors == []
    assert rep.empty == ["in-flight-modules/how-to-manage/README.md"]  # heading only: nothing to index
    before = dict(index.vectors)
    rep2 = run(FakeGitHub(repo_files()), index)
    assert rep2.added == rep2.updated == rep2.deleted == [] and rep2.errors == []
    assert index.vectors == before


def test_changed_page_is_reembedded_and_shrunk_page_loses_old_chunks():
    index = FakeIndex()
    run(FakeGitHub(repo_files()), index)
    rep = run(FakeGitHub(repo_files(page_sha="p2", page="# Zone Overlays\n\nOnly one section is left on this page now.")), index)
    assert rep.updated == ["in-flight-modules/how-to-manage/cockpit-3-0/zone-overlays.md"]
    zone = [v for v in index.vectors.values() if v["metadata"]["path"].endswith("zone-overlays.md")]
    assert len(zone) == 1 and zone[0]["metadata"]["blob_sha"] == "p2"


def test_removed_page_is_deleted_only_after_a_complete_read():
    index = FakeIndex()
    run(FakeGitHub(repo_files()), index)
    files = repo_files()
    files["Org/releases"]["SUMMARY.md"] = ("rs2", "## October 2026\n\n* [Other](october-2026/other.md)\n")
    files["Org/releases"]["october-2026/other.md"] = ("o1", "# Other\n\nA different release note entirely here.")
    rep = run(FakeGitHub(files), index)
    assert len(rep.deleted) == 1 and rep.added == ["october-2026/other.md"]
    assert not any(v["metadata"]["path"] == "october-2026/video-overlays.md" for v in index.vectors.values())


# ── never delete on a partial read ─────────────────────────────────────

def test_failed_source_read_deletes_nothing_and_reports():
    index = FakeIndex()
    run(FakeGitHub(repo_files()), index)
    count = len(index.vectors)
    fake = FakeGitHub(repo_files(), fail={"Org/releases/git/trees": [404]})
    rep = run(fake, index)
    assert index.vectors and len(index.vectors) == count and index.deleted == []
    assert rep.errors and "Org/releases" in rep.errors[0]
    assert "no page was deleted" in rep.skipped_deletes


def test_truncated_tree_is_a_partial_read():
    index = FakeIndex()
    run(FakeGitHub(repo_files()), index)
    rep = run(FakeGitHub(repo_files(), truncated={"Org/docs"}), index)
    assert "truncated" in rep.errors[0] and index.deleted == []


def test_empty_summary_is_an_error_not_an_empty_corpus():
    index = FakeIndex()
    run(FakeGitHub(repo_files()), index)
    files = repo_files()
    files["Org/releases"]["SUMMARY.md"] = ("rs3", "# nothing here\n")
    rep = run(FakeGitHub(files), index)
    assert "0 pages" in rep.errors[0] and index.deleted == []


def test_failed_page_read_skips_every_deletion():
    index = FakeIndex()
    run(FakeGitHub(repo_files()), index)
    files = repo_files(page_sha="p2", page=PAGE + "\nMore.")
    # the release page is removed from the SUMMARY...
    files["Org/releases"]["SUMMARY.md"] = ("rs2", "* [X](october-2026/x.md)\n")
    files["Org/releases"]["october-2026/x.md"] = ("x1", "# X\n\nA brand new release page body.")
    fake = FakeGitHub(files, fail={"/git/blobs/p2": [500] * 10})  # ...but a docs page cannot be read
    rep = run(fake, index)
    assert any("zone-overlays" in e for e in rep.errors)
    assert rep.deleted == [] and not any("video-overlays" in i for i in index.deleted)


# ── interrupted runs are repaired ──────────────────────────────────────

def test_crash_mid_page_leaves_old_sha_so_the_page_is_redone():
    index = FakeIndex()
    run(FakeGitHub(repo_files()), index)
    files = repo_files(page_sha="p2", page=PAGE + "\n## Extra\n\nA third section that adds another chunk.")
    index.fail_upsert_after = index.upserts + 1   # chunks 1..n land, chunk #0 never does
    rep = run(FakeGitHub(files), index)
    assert rep.errors and "index write failed" in rep.errors[0]
    head = [v for k, v in index.vectors.items() if k.endswith("#0") and v["metadata"]["path"].endswith("zone-overlays.md")][0]
    assert head["metadata"]["blob_sha"] == "p1"
    index.fail_upsert_after = None
    rep = run(FakeGitHub(files), index)
    assert rep.updated == ["in-flight-modules/how-to-manage/cockpit-3-0/zone-overlays.md"] and rep.errors == []
    zone = [v for v in index.vectors.values() if v["metadata"]["path"].endswith("zone-overlays.md")]
    assert {v["metadata"]["blob_sha"] for v in zone} == {"p2"} and len(zone) == 3


def test_surplus_chunks_left_by_a_crash_are_cleaned_up():
    index = FakeIndex()
    run(FakeGitHub(repo_files()), index)
    zone0 = next(k for k, v in index.vectors.items() if k.endswith("#0") and v["metadata"]["path"].endswith("zone-overlays.md"))
    orphan = zone0[:-1] + "7"
    index.vectors[orphan] = {"id": orphan, "values": [0, 0], "metadata": {}}
    rep = run(FakeGitHub(repo_files()), index)
    assert rep.repaired == ["in-flight-modules/how-to-manage/cockpit-3-0/zone-overlays.md"] and orphan not in index.vectors


def test_rerun_is_idempotent():
    index = FakeIndex()
    for _ in range(3):
        rep = run(FakeGitHub(repo_files()), index)
    assert rep.unchanged == 5 and rep.empty == ["in-flight-modules/how-to-manage/README.md"]
    assert rep.errors == [] and rep.added == rep.updated == rep.deleted == []


# ── retries ────────────────────────────────────────────────────────────

def test_rate_limits_and_server_errors_are_retried():
    sleeps = []
    fake = FakeGitHub(repo_files(), fail={"Org/docs/git/trees": [403, 502, 429]})
    client = cs.GitHub("t", opener=fake.opener, sleep=sleeps.append)
    assert "SUMMARY.md" in client.tree("Org/docs")
    assert len(sleeps) == 3


def test_retries_give_up_with_a_clear_error():
    fake = FakeGitHub(repo_files(), fail={"Org/docs/git/trees": [503] * 10})
    with pytest.raises(cs.SourceReadError, match="after 5 attempts"):
        cs.GitHub("t", opener=fake.opener, sleep=lambda s: None).tree("Org/docs")


def test_auth_errors_are_not_retried():
    fake = FakeGitHub(repo_files(), fail={"Org/docs/git/trees": [401]})
    with pytest.raises(cs.SourceReadError, match="HTTP 401"):
        cs.GitHub("t", opener=fake.opener, sleep=lambda s: pytest.fail("must not retry")).tree("Org/docs")


def test_pinecone_errors_are_retried():
    index = FakeIndex()
    calls = {"n": 0}
    real = index.describe_index_stats

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("blip")
        return real()
    index.describe_index_stats = flaky
    assert store(index).count() == 0 and calls["n"] == 3


# ── dry run, legacy vectors ────────────────────────────────────────────

def test_dry_run_writes_nothing():
    index = FakeIndex()
    rep = run(FakeGitHub(repo_files()), index, dry_run=True)
    assert len(rep.added) == 6 and index.vectors == {} and index.upserts == 0
    assert "dry run" in rep.markdown()


def test_legacy_crawler_vectors_removed_only_on_request_after_a_clean_full_sync():
    index = FakeIndex()
    index.vectors["keepme"] = {"id": "keepme", "values": [0, 0], "metadata": {}}
    cs.sync(gh(FakeGitHub(repo_files())), store(index), (DOCS, RELS), embed=embed)
    assert "keepme" in index.vectors  # never removed by default
    del index.vectors["keepme"]
    index.vectors["5f2b9c0e"] = {"id": "5f2b9c0e", "values": [0, 0], "metadata": {"source_url": "https://docs.flytbase.com"}}
    bad = FakeGitHub(repo_files(), fail={"Org/releases/git/trees": [404]})
    cs.sync(gh(bad), store(index), (DOCS, RELS), embed=embed, clean_legacy=True)
    assert "5f2b9c0e" in index.vectors
    rep = cs.sync(gh(FakeGitHub(repo_files())), store(index), (DOCS, RELS), embed=embed, clean_legacy=True)
    assert rep.legacy_deleted == 1 and "5f2b9c0e" not in index.vectors
    one = FakeIndex()
    one.vectors["abc"] = {"id": "abc", "values": [0, 0], "metadata": {}}
    cs.sync(gh(FakeGitHub(repo_files())), store(one), (DOCS,), embed=embed, clean_legacy=True)
    assert "abc" in one.vectors  # a single-source run never removes legacy vectors


# ── never silent ───────────────────────────────────────────────────────

def test_alert_on_errors_and_summary_written(monkeypatch, tmp_path):
    posted = []
    monkeypatch.setattr(cs, "post_alert", lambda text, **k: posted.append(text) or True)
    monkeypatch.setattr(cs, "_store", lambda: store(FakeIndex()))
    monkeypatch.setattr(cs, "_embedder", lambda: embed)
    monkeypatch.setattr(cs, "SOURCES", (DOCS, RELS))
    fake = FakeGitHub(repo_files(), fail={"Org/releases/git/trees": [404]})
    monkeypatch.setattr(cs, "GitHub", lambda token: gh(fake))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("CORPUS_GITHUB_TOKEN", "t")
    monkeypatch.setenv("PINECONE_API_KEY", "k")
    assert cs.main([]) == 1
    assert posted and "Corpus sync needs attention" in posted[0] and "Org/releases" in posted[0]
    assert "| Pages scanned |" in summary.read_text()


def test_no_changes_is_green_and_silent(monkeypatch):
    posted = []
    index = FakeIndex()
    run(FakeGitHub(repo_files()), index)
    monkeypatch.setattr(cs, "post_alert", lambda text, **k: posted.append(text) or True)
    monkeypatch.setattr(cs, "_store", lambda: store(index))
    monkeypatch.setattr(cs, "_embedder", lambda: embed)
    monkeypatch.setattr(cs, "SOURCES", (DOCS, RELS))
    monkeypatch.setattr(cs, "GitHub", lambda token: gh(FakeGitHub(repo_files())))
    monkeypatch.setenv("CORPUS_GITHUB_TOKEN", "t")
    monkeypatch.setenv("PINECONE_API_KEY", "k")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert cs.main([]) == 0 and posted == []


def test_missing_token_alerts(monkeypatch):
    posted = []
    monkeypatch.setattr(cs, "post_alert", lambda text, **k: posted.append(text) or True)
    monkeypatch.delenv("CORPUS_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cs.main([]) == 1 and "no GitHub token" in posted[0]


def test_post_alert_never_raises():
    def boom(req, timeout):
        raise OSError("network down")
    assert cs.post_alert("x", token="t", channel="C1", opener=boom) is False
    assert cs.post_alert("x", token="") is False
    sent = {}

    def ok(req, timeout):
        sent["body"] = json.loads(req.data)
        return io.BytesIO(b'{"ok": true}')
    assert cs.post_alert("hello", token="t", opener=ok) is True
    assert sent["body"]["channel"] == "C0BGB8F56JX"


def test_workflow_is_daily_manual_and_alerts_on_crash():
    import yaml
    from pathlib import Path
    wf = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/corpus-refresh.yml").read_text())
    on = wf.get("on", wf.get(True))
    assert on["schedule"][0]["cron"] == "0 3 * * *" and "workflow_dispatch" in on
    steps = wf["jobs"]["refresh-corpus"]["steps"]
    run_step = next(s for s in steps if "src.corpus_sync" in s.get("run", "") and "--alert" not in s.get("run", ""))
    assert run_step["env"]["CORPUS_GITHUB_TOKEN"] == "${{ secrets.FLYTBASE_GITHUB_TOKEN }}"
    crash = next(s for s in steps if s.get("if") == "failure()")
    assert "--alert" in crash["run"]
    assert not (Path(__file__).resolve().parents[1] / "src/ingest.py").exists()
