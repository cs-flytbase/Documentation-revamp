"""Corpus sync: keep the Pinecone index in step with the docs and releases repos.

Replaces the website crawler (src/ingest.py). From July to October 2026 the
weekly job reported green while adding nothing: the sites answer bots with
429, the crawler fell back to a hardcoded list of 185 URLs that were already
indexed, and it never re-read a page that changed. The index sat at 971
vectors and missed every page written since.

This reads the source of truth instead: the Markdown in
FlytBaseAILabs/flytbase-docs and FlytBaseAILabs/flytbase-releases, through the
GitHub API. A page is indexed when its SUMMARY.md lists it.

Incremental and safe to interrupt:
- Each vector's id is stable: gh:<repo>:<page key>#<chunk>. The page key is a
  hash of the repo path, so ids are short and ASCII whatever the path holds.
- Every vector stores the page's blob SHA. A page is re-embedded only when its
  SHA changes.
- Writes are ordered so a crash at any point leaves a state the next run
  repairs. A page's chunks are upserted before its old surplus chunks are
  deleted, and chunk #0 (which carries the SHA the next run compares) is
  written last. Pages gone from the source are deleted only after every
  upsert succeeded, and only when every source was read completely.
- A partial source read never deletes anything.
- The old website crawler's vectors (non "gh:" ids) are removed only on
  request (--remove-legacy), and only after a clean sync of both repos.

Never silent: a summary goes to stdout and to the GitHub job summary, and a
source read failure, an empty source or a write error posts a Slack alert and
exits non-zero. "No changes" is a normal, green outcome.

Usage:
    python -m src.corpus_sync                  # sync both repos
    python -m src.corpus_sync --source docs    # one repo only
    python -m src.corpus_sync --dry-run        # report what would change, write nothing
    python -m src.corpus_sync --remove-legacy  # one-time: drop the old crawler's vectors after a clean sync
    python -m src.corpus_sync --alert "text"   # post an alert (used by the workflow on a crash)
"""
from __future__ import annotations

from src.alerts import shorten
import argparse
import base64
import hashlib
import json
import os
import posixpath
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from src.release_template import docs_url
from src.summary_placement import parse as parse_summary

ROOT = Path(__file__).resolve().parents[1]
ID_PREFIX = "gh:"
CONTENT_LIMIT = 8000          # Pinecone metadata limit used since the first ingestion
CHUNK_MAX_CHARS = 2000        # about 512 tokens, the configured chunk_max_tokens
MIN_CHUNK_CHARS = 20          # same floor as the old crawler
DEFAULT_ALERT_CHANNEL = "C0BGB8F56JX"

# Customer-specific documentation never goes into the shared corpus: it would
# surface in drafts for other customers.
EXCLUDED_PREFIXES = ("moi-on-premise/",)


@dataclass(frozen=True)
class Source:
    key: str            # --source value
    repo: str
    source_type: str    # metadata "source_type", as the research agent filters on it
    base_url: str
    summaries: tuple

    @property
    def short(self) -> str:
        return self.repo.split("/")[-1]

    def url(self, path: str) -> str:
        if self.source_type == "doc":
            return docs_url(path)
        page = re.sub(r"(^|/)README\.md$", "", path)
        return f"{self.base_url}/{re.sub(r'[.]md$', '', page)}".rstrip("/")


SOURCES = (
    Source("docs", "FlytBaseAILabs/flytbase-docs", "doc", "https://docs.flytbase.com",
           ("SUMMARY.md", "on-premise/SUMMARY.md")),
    Source("releases", "FlytBaseAILabs/flytbase-releases", "release", "https://releases.flytbase.com",
           ("SUMMARY.md", "on-premise/SUMMARY.md")),
)


class SourceReadError(Exception):
    """A source could not be read completely. Nothing may be deleted."""


# ── GitHub ─────────────────────────────────────────────────────────────

class GitHub:
    """Minimal GitHub REST client with retries. urllib only, so the tests and
    CI need no extra packages."""

    API = "https://api.github.com"

    def __init__(self, token: str, opener=None, sleep=time.sleep, attempts: int = 5, max_wait: float = 60.0):
        self.token = token
        self.open = opener or (lambda req, timeout: urllib.request.urlopen(req, timeout=timeout))
        self.sleep = sleep
        self.attempts = attempts
        self.max_wait = max_wait

    def _wait_for(self, attempt: int, headers) -> float:
        headers = headers or {}
        if headers.get("Retry-After"):
            try:
                return min(float(headers["Retry-After"]), self.max_wait)
            except ValueError:
                pass
        if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset"):
            try:
                return min(max(float(headers["X-RateLimit-Reset"]) - time.time(), 1.0), self.max_wait)
            except ValueError:
                pass
        return min(2.0 ** attempt, self.max_wait)

    def get(self, path: str, raw: bool = False):
        url = path if path.startswith("http") else f"{self.API}/{path}"
        accept = "application/vnd.github.raw" if raw else "application/vnd.github+json"
        last = ""
        for attempt in range(self.attempts):
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.token}", "Accept": accept,
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "FlytBase-Corpus-Sync",
            })
            try:
                with self.open(req, 30) as resp:
                    body = resp.read()
                    return body.decode("utf-8", "replace") if raw else json.loads(body)
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code} on {path}"
                retryable = e.code in (429, 500, 502, 503, 504) or (
                    e.code == 403 and (e.headers or {}).get("X-RateLimit-Remaining") == "0")
                if not retryable:
                    raise SourceReadError(last) from e
                wait = self._wait_for(attempt, e.headers)
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
                last = f"{type(e).__name__} on {path}: {e}"
                wait = self._wait_for(attempt, None)
            if attempt < self.attempts - 1:
                self.sleep(wait)
        raise SourceReadError(f"{last} (after {self.attempts} attempts)")

    def tree(self, repo: str, ref: str = "main") -> dict[str, str]:
        """{path: blob sha} for every file on the branch. A truncated tree is a
        partial read and is refused."""
        data = self.get(f"repos/{repo}/git/trees/{ref}?recursive=1")
        if data.get("truncated"):
            raise SourceReadError(f"the git tree of {repo} is truncated; refusing a partial read")
        return {e["path"]: e["sha"] for e in data.get("tree", []) if e.get("type") == "blob"}

    def blob(self, repo: str, sha: str) -> str:
        data = self.get(f"repos/{repo}/git/blobs/{sha}")
        if data.get("encoding") == "base64":
            return base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
        return data.get("content", "")


# ── Pages and chunks ───────────────────────────────────────────────────

@dataclass
class Page:
    source: Source
    path: str
    sha: str
    breadcrumb: str

    @property
    def key(self) -> str:
        return hashlib.sha1(f"{self.source.repo}:{self.path}".encode()).hexdigest()[:24]

    @property
    def id_prefix(self) -> str:
        return f"{ID_PREFIX}{self.source.short}:{self.key}#"

    def chunk_id(self, i: int) -> str:
        return f"{self.id_prefix}{i}"


def pages_from_summary(summary_text: str, summary_path: str) -> list[tuple[str, str]]:
    """(repo path, breadcrumb) for every page SUMMARY.md lists, in order."""
    _, items, _ = parse_summary(summary_text)
    base = posixpath.dirname(summary_path)
    out = []
    for item in items:
        link = item.path.split("#")[0].strip()
        if not link.endswith(".md") or re.match(r"^[a-z]+://", link):
            continue
        path = posixpath.normpath(posixpath.join(base, link)) if base else posixpath.normpath(link)
        chain, node = [], item
        while node:
            chain.append(node.title)
            node = node.parent
        crumbs = ([item.heading] if item.heading else []) + list(reversed(chain))
        out.append((path, " > ".join(crumbs)))
    return out


def discover(gh: GitHub, source: Source) -> list[Page]:
    """Every page of a source, read completely or not at all."""
    tree = gh.tree(source.repo)
    pages, seen = [], set()
    for summary_path in source.summaries:
        if summary_path not in tree:
            continue
        text = gh.blob(source.repo, tree[summary_path])
        for path, crumb in pages_from_summary(text, summary_path):
            if path in seen or path.startswith(EXCLUDED_PREFIXES) or path not in tree:
                continue
            seen.add(path)
            pages.append(Page(source, path, tree[path], crumb))
    return pages


_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_DIRECTIVE = re.compile(r"\{%.*?%\}", re.S)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_HTML = re.compile(r"<[^>]+>")
_HEADING = re.compile(r"^(#{1,4})\s+(.*?)\s*#*\s*$")


def _clean(text: str) -> str:
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _DIRECTIVE.sub("", text)
    text = _HTML.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _split_long(text: str) -> list[str]:
    """Split a long section on paragraph boundaries into pieces of at most
    CHUNK_MAX_CHARS (a single oversized paragraph is cut on a sentence)."""
    if len(text) <= CHUNK_MAX_CHARS:
        return [text]
    pieces, current = [], ""
    for para in re.split(r"\n\s*\n", text):
        while len(para) > CHUNK_MAX_CHARS:
            cut = para.rfind(". ", 0, CHUNK_MAX_CHARS)
            cut = cut + 1 if cut > CHUNK_MAX_CHARS // 2 else CHUNK_MAX_CHARS
            pieces.append((current + "\n\n" + para[:cut]).strip() if current else para[:cut].strip())
            current, para = "", para[cut:].strip()
        if current and len(current) + len(para) + 2 > CHUNK_MAX_CHARS:
            pieces.append(current.strip())
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        pieces.append(current.strip())
    return pieces


def chunk_markdown(markdown: str) -> list[tuple[str, str]]:
    """[(heading, content)] split on headings, like the crawler split the HTML."""
    text = _FRONTMATTER.sub("", markdown.replace("\r\n", "\n"))
    sections, heading, buf = [], "", []
    in_code = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            sections.append((heading, "\n".join(buf)))
            heading, buf = _clean(m.group(2)), []
        else:
            buf.append(line)
    sections.append((heading, "\n".join(buf)))
    out = []
    for head, body in sections:
        body = _clean(body)
        if len(body) < MIN_CHUNK_CHARS:
            continue
        head = head or "Introduction"
        for piece in _split_long(body):
            out.append((head, f"{head}\n\n{piece}"))
    return out


_KEYWORDS = ("mission", "dock", "drone", "flight", "payload", "gimbal", "annotation", "overlay", "map", "zone",
             "failsafe", "flink", "flow", "alarm", "rtsp", "streaming", "gallery", "report", "scheduler", "terrain",
             "altitude", "airsense", "cas", "verkos", "detection", "ai", "spot check", "video wall", "fleet",
             "speaker", "spotlight", "parachute", "sso", "3d", "grid", "path", "kml", "wpml", "breakpoint",
             "dronedeploy", "pix4d", "strayos", "hextronics", "cockpit", "thermal", "isotherm")


def feature_tags(text: str) -> list[str]:
    low = text.lower()
    return [k for k in _KEYWORDS if k in low]


def source_date(url: str) -> str:
    months = {m: f"{i:02d}" for i, m in enumerate(
        ("january", "february", "march", "april", "may", "june", "july", "august",
         "september", "october", "november", "december"), 1)}
    m = re.search(r"/([a-z]+)-(\d{4})/", url.lower())
    return f"{m.group(2)}-{months[m.group(1)]}" if m and m.group(1) in months else ""


def _ia_lookup() -> list[tuple[str, str, str]]:
    """(url path, node id, label) from config/ia_structure.yaml, longest path first.
    Kept only so the "ia_node" metadata the research agent returns stays filled."""
    try:
        data = yaml.safe_load((ROOT / "config/ia_structure.yaml").read_text())
    except (OSError, yaml.YAMLError):
        return []
    out = []

    def walk(nodes):
        for n in nodes or []:
            out.append((str(n.get("path", "")).rstrip("/"), str(n.get("id", "")), str(n.get("label", ""))))
            walk(n.get("children"))
    walk(data.get("ia_nodes"))
    return sorted(out, key=lambda x: -len(x[0]))


def ia_node(url: str, lookup) -> str:
    path = urllib.parse.urlparse(url).path.rstrip("/")
    for p, nid, _ in lookup:
        if p and (path == p or path.startswith(p + "/")):
            return nid
    return "uncategorized"


def build_vectors(page: Page, markdown: str, embed, lookup) -> list[dict]:
    chunks = chunk_markdown(markdown)
    if not chunks:
        return []
    url = page.source.url(page.path)
    values = embed([c for _, c in chunks])
    if len(values) != len(chunks):
        raise RuntimeError(f"embedding returned {len(values)} vectors for {len(chunks)} chunks of {page.path}")
    node = ia_node(url, lookup)
    vectors = []
    for i, ((heading, content), vec) in enumerate(zip(chunks, values)):
        vectors.append({"id": page.chunk_id(i), "values": list(vec), "metadata": {
            "content": content[:CONTENT_LIMIT], "heading": heading,
            "ia_node": node, "ia_label": page.breadcrumb,
            "source_type": page.source.source_type, "source_url": url, "source_date": source_date(url),
            "feature_tags": feature_tags(content),
            "repo": page.source.repo, "path": page.path, "blob_sha": page.sha,
            "chunk_index": i, "chunk_count": len(chunks),
        }})
    return vectors


# ── The index ──────────────────────────────────────────────────────────

class PineconeStore:
    """The operations the sync needs, over a Pinecone Index object."""

    def __init__(self, index, namespace: str = "", sleep=time.sleep, attempts: int = 4):
        self.index = index
        self.namespace = namespace
        self.sleep = sleep
        self.attempts = attempts

    def _retry(self, what: str, fn):
        for attempt in range(self.attempts):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 - the SDK raises many types
                if attempt == self.attempts - 1:
                    raise RuntimeError(f"Pinecone {what} failed after {self.attempts} attempts: {e}") from e
                self.sleep(min(2.0 ** attempt, 30.0))

    def list_ids(self, prefix: str = "") -> list[str]:
        """Every vector ID (with `prefix`). The SDK's page shape changed across
        versions: up to v7 `index.list()` yields lists of ID strings, v10 yields
        ListResponse pages whose `.vectors` are ListItem(id=...). Treating a v10
        page as an ID broke the first --remove-legacy run (2026-10-22, run
        39744249307: "slice(17, None, None)"). _ids_of accepts every shape and
        rejects anything that is not an ID, so a future change fails loudly
        instead of feeding objects into deletes."""
        def run():
            ids = []
            pages = self.index.list(prefix=prefix, namespace=self.namespace) if prefix else \
                self.index.list(namespace=self.namespace)
            for page in pages:
                ids.extend(_ids_of(page))
            return ids
        return self._retry("list", run)

    def fetch_metadata(self, ids: list[str]) -> dict[str, dict]:
        out = {}
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            res = self._retry("fetch", lambda: self.index.fetch(ids=batch, namespace=self.namespace))
            vectors = getattr(res, "vectors", None) or (res.get("vectors", {}) if isinstance(res, dict) else {})
            for vid, v in vectors.items():
                meta = getattr(v, "metadata", None)
                out[vid] = dict(meta if meta is not None else (v.get("metadata") or {}))
        return out

    def upsert(self, vectors: list[dict]) -> None:
        for i in range(0, len(vectors), 100):
            batch = vectors[i:i + 100]
            self._retry("upsert", lambda: self.index.upsert(vectors=batch, namespace=self.namespace))

    def delete(self, ids: list[str]) -> None:
        for i in range(0, len(ids), 1000):
            batch = ids[i:i + 1000]
            self._retry("delete", lambda: self.index.delete(ids=batch, namespace=self.namespace))

    def count(self) -> int:
        stats = self._retry("stats", lambda: self.index.describe_index_stats())
        return int(getattr(stats, "total_vector_count", None) or (stats.get("total_vector_count", 0) if isinstance(stats, dict) else 0))


def _ids_of(page) -> list[str]:
    """Vector IDs from one page of index.list(), whatever the SDK version."""
    if page is None:
        return []
    if isinstance(page, str):
        return [page]
    if isinstance(page, (list, tuple)):
        return [i for item in page for i in _ids_of(item)]
    if isinstance(page, dict):
        if "vectors" in page:
            return _ids_of(page["vectors"])
        if "id" in page:
            return [page["id"]] if page["id"] else []
    vectors = getattr(page, "vectors", None)
    if vectors is not None and not isinstance(vectors, str):
        return _ids_of(list(vectors))
    vid = getattr(page, "id", None)
    if isinstance(vid, str):
        return [vid] if vid else []
    if vid is None and hasattr(page, "id"):
        return []  # a ListItem that carried no ID
    raise TypeError(f"unrecognised index.list() item: {type(page).__name__}")


@dataclass
class Existing:
    """What the index holds for one page."""
    ids: set = field(default_factory=set)
    sha: str = ""
    chunk_count: int = -1


def index_state(store: PineconeStore, source: Source) -> dict[str, Existing]:
    """{page key: Existing} for one source's GitHub-sourced vectors.
    The SHA comes from chunk #0, which every write updates last."""
    prefix = f"{ID_PREFIX}{source.short}:"
    state: dict[str, Existing] = {}
    for vid in store.list_ids(prefix):
        key = vid[len(prefix):].rsplit("#", 1)[0]
        state.setdefault(key, Existing()).ids.add(vid)
    heads = store.fetch_metadata([f"{prefix}{k}#0" for k in state])
    for k, ex in state.items():
        meta = heads.get(f"{prefix}{k}#0") or {}
        ex.sha = str(meta.get("blob_sha", ""))
        try:
            ex.chunk_count = int(meta.get("chunk_count", -1))
        except (TypeError, ValueError):
            ex.chunk_count = -1
    return state


def _index_of(vid: str) -> int:
    try:
        return int(vid.rsplit("#", 1)[1])
    except (IndexError, ValueError):
        return -1


# ── The sync ───────────────────────────────────────────────────────────

@dataclass
class Report:
    scanned: int = 0
    added: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    deleted: list = field(default_factory=list)
    repaired: list = field(default_factory=list)
    unchanged: int = 0
    empty: list = field(default_factory=list)
    legacy_deleted: int = 0
    vectors_before: int = 0
    vectors_after: int = 0
    seconds: float = 0.0
    errors: list = field(default_factory=list)
    skipped_deletes: str = ""
    dry_run: bool = False

    def markdown(self) -> str:
        mode = " (dry run: nothing written)" if self.dry_run else ""
        lines = [f"## Corpus sync{mode}", "",
                 "| Pages scanned | Added | Updated | Deleted | Repaired | Unchanged | Vectors before | Vectors after | Duration |",
                 "|---|---|---|---|---|---|---|---|---|",
                 f"| {self.scanned} | {len(self.added)} | {len(self.updated)} | {len(self.deleted)} | {len(self.repaired)} "
                 f"| {self.unchanged} | {self.vectors_before} | {self.vectors_after} | {self.seconds:.0f}s |", ""]
        if self.legacy_deleted:
            lines += [f"Removed {self.legacy_deleted} legacy vectors from the old website crawler.", ""]
        for label, items in (("Added", self.added), ("Updated", self.updated), ("Deleted", self.deleted),
                             ("Repaired", self.repaired), ("No indexable text", self.empty)):
            if items:
                lines += [f"**{label}:** " + ", ".join(f"`{p}`" for p in items[:50])
                          + (f" and {len(items) - 50} more" if len(items) > 50 else ""), ""]
        if self.skipped_deletes:
            lines += [f"**Deletions skipped:** {self.skipped_deletes}", ""]
        if self.errors:
            lines += ["**Errors:**", ""] + [f"- {e}" for e in self.errors] + [""]
        return "\n".join(lines)


def sync(gh: GitHub, store: PineconeStore | None, sources, embed=None, dry_run: bool = False,
         clean_legacy: bool = False, clock=time.time) -> Report:
    """One sync pass. Raises nothing for expected failures: they land in
    report.errors, and the caller alerts and exits non-zero."""
    start = clock()
    rep = Report(dry_run=dry_run)
    lookup = _ia_lookup()

    # 1. Read every source completely before touching the index.
    discovered: list[tuple[Source, list[Page]]] = []
    complete = True
    for src in sources:
        try:
            pages = discover(gh, src)
        except SourceReadError as e:
            rep.errors.append(f"{src.repo}: {e}")
            complete = False
            continue
        if not pages:
            rep.errors.append(f"{src.repo}: found 0 pages in its SUMMARY.md files; refusing to treat that as empty")
            complete = False
            continue
        discovered.append((src, pages))
        rep.scanned += len(pages)

    if store is None:  # dry run without index access: report the source side only
        rep.seconds = clock() - start
        return rep

    try:
        rep.vectors_before = rep.vectors_after = store.count()
        for src, pages in discovered:
            _sync_source(gh, store, src, pages, embed, lookup, rep, dry_run, complete)
        if not complete:
            rep.skipped_deletes = "a source was not read completely, so no page was deleted"
        elif clean_legacy and len(discovered) == len(SOURCES) and not rep.errors:
            legacy = [v for v in store.list_ids() if not v.startswith(ID_PREFIX)]
            if legacy and not dry_run:
                store.delete(legacy)
            rep.legacy_deleted = len(legacy)
        if not dry_run:
            rep.vectors_after = store.count()
    except Exception as e:  # noqa: BLE001 - reported and alerted, never swallowed
        rep.errors.append(f"index write failed: {e}")
    rep.seconds = clock() - start
    return rep


def _sync_source(gh, store, src, pages, embed, lookup, rep, dry_run, complete):
    state = index_state(store, src)
    current = {p.key: p for p in pages}

    for page in pages:
        ex = state.get(page.key)
        if ex and ex.sha == page.sha:
            surplus = sorted(v for v in ex.ids if ex.chunk_count >= 0 and _index_of(v) >= ex.chunk_count)
            if surplus:  # a previous run stopped between upsert and cleanup
                rep.repaired.append(page.path)
                if not dry_run:
                    store.delete(surplus)
            else:
                rep.unchanged += 1
            continue
        if dry_run:
            (rep.updated if ex else rep.added).append(page.path)
            continue
        try:
            markdown = gh.blob(src.repo, page.sha)
        except SourceReadError as e:
            rep.errors.append(f"{src.repo}/{page.path}: {e}")
            continue
        vectors = build_vectors(page, markdown, embed, lookup)
        if not vectors:
            # Nothing to index (e.g. a heading-only section page). It has no
            # vectors to remember its SHA by, so it is re-read on every run.
            rep.empty.append(page.path)
            if ex:  # the page used to have text: its old chunks are stale
                store.delete(sorted(ex.ids))
            continue
        (rep.updated if ex else rep.added).append(page.path)
        # Chunk #0 carries the SHA the next run compares, so it goes last: a
        # crash before it leaves the old SHA and the page is redone.
        store.upsert(vectors[1:])
        store.upsert(vectors[:1])
        if ex:
            keep = {v["id"] for v in vectors}
            stale = sorted(v for v in ex.ids if v not in keep)
            if stale:
                store.delete(stale)

    # Pages gone from the source: only with a complete read, only after every
    # upsert above succeeded (an exception skips this), never on errors.
    gone = [k for k in state if k not in current]
    if gone and complete and not rep.errors:
        for k in gone:
            rep.deleted.append(f"{src.short}:{k}")
        if not dry_run:
            store.delete(sorted(v for k in gone for v in state[k].ids))


# ── Alerts and entry point ─────────────────────────────────────────────

def post_alert(text: str, token: str | None = None, channel: str | None = None, opener=None) -> bool:
    """Post to Slack. Never raises: an alert that fails is logged, not fatal."""
    token = token if token is not None else os.environ.get("SLACK_BOT_TOKEN", "")
    channel = channel or os.environ.get("SLACK_ALERT_CHANNEL") or DEFAULT_ALERT_CHANNEL
    if not token:
        print("[corpus-sync] SLACK_BOT_TOKEN is not set; alert not sent:", text)
        return False
    req = urllib.request.Request("https://slack.com/api/chat.postMessage",
                                 data=json.dumps({"channel": channel, "text": shorten(text)}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=15) as resp:
            ok = json.loads(resp.read()).get("ok", False)
    except Exception as e:  # noqa: BLE001
        print(f"[corpus-sync] alert failed: {e}")
        return False
    if not ok:
        print("[corpus-sync] Slack refused the alert")
    return bool(ok)


def alert_text(rep: Report) -> str:
    run = os.environ.get("GITHUB_SERVER_URL", "") and (
        f"{os.environ['GITHUB_SERVER_URL']}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    lines = [":rotating_light: *Corpus sync needs attention.* The knowledge index did not fully update, so "
             "drafts may cite stale docs. I'd rather tell you than pretend otherwise."]
    lines += [f"• {e}" for e in rep.errors[:10]]
    if rep.skipped_deletes:
        lines.append(f"• Deletions skipped: {rep.skipped_deletes}.")
    if run:
        lines.append(f"Run: {run}")
    return "\n".join(lines)


def _embedder():
    from sentence_transformers import SentenceTransformer
    settings = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
    model = SentenceTransformer(settings["pinecone"]["embedding_model"])
    return lambda texts: model.encode(texts, show_progress_bar=False, normalize_embeddings=True).tolist()


def _store() -> PineconeStore:
    from pinecone import Pinecone
    settings = yaml.safe_load((ROOT / "config/settings.yaml").read_text())
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])
    return PineconeStore(pc.Index(settings["pinecone"]["index_name"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sync the docs and releases repos into Pinecone")
    ap.add_argument("--source", choices=["all"] + [s.key for s in SOURCES], default="all")
    ap.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    ap.add_argument("--remove-legacy", action="store_true",
                    help="one-time migration: after a clean full sync, remove the old website crawler's vectors")
    ap.add_argument("--alert", help="post this text as an alert and exit")
    args = ap.parse_args(argv)

    if args.alert:
        post_alert(args.alert)
        return 0
    rc = _main(args)
    # Reached only when the script ran to its own report (and its own alert, if
    # any); a crash raises past this line. The workflow's crash alert fires only
    # without this marker, so one failure never produces two Slack alerts (it
    # did on 2026-10-22).
    _mark_reported()
    return rc


def _mark_reported() -> None:
    marker = os.environ.get("CORPUS_REPORTED_MARKER", "")
    if not marker:
        return
    try:
        Path(marker).write_text("reported\n")
    except OSError as e:
        print(f"[corpus-sync] could not write the report marker {marker}: {e}", file=sys.stderr)


def _main(args) -> int:
    token = os.environ.get("CORPUS_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        post_alert(":rotating_light: *Corpus sync could not start:* no GitHub token (CORPUS_GITHUB_TOKEN) is configured.")
        print("No GitHub token configured", file=sys.stderr)
        return 1
    sources = [s for s in SOURCES if args.source in ("all", s.key)]
    try:
        store = _store() if os.environ.get("PINECONE_API_KEY") else None
        if store is None and not args.dry_run:
            raise RuntimeError("PINECONE_API_KEY is not set")
        embed = None if args.dry_run else _embedder()
    except Exception as e:  # noqa: BLE001
        rep = Report(errors=[f"setup failed: {e}"])
    else:
        rep = sync(GitHub(token), store, sources, embed=embed, dry_run=args.dry_run,
                   clean_legacy=args.remove_legacy)

    summary = rep.markdown()
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(summary + "\n")
    if rep.errors:
        post_alert(alert_text(rep))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
