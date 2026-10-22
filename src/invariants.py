"""What a published run must satisfy, checked on what actually landed.

The incidents of the week of 2026-10-19 each broke one of these: nine named
screenshots never arrived and nobody said so, a GIF shipped twice, the doc
page went under Live Video Feed with a brand-new "## Live Video Feed" group,
the release note linked a doc URL that 404'd, every image got "Image asset:"
as alt text, and the doc page repeated the same numbers across sections.

The same checks run in two places, so they cannot drift apart:
- tests/test_replay_cockpit.py replays the Cockpit 3.0 material through the
  whole pipeline with recorded model answers, on every CI run;
- src/e2e_check.py runs the real pipeline weekly in sandbox mode and reads
  the sandbox branches back through the GitHub API.

Every check returns a result; none raises. A check that crashes is reported
as failed with the reason, so one bug cannot hide the others.
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field

from src.doc_checks import repeated_quantities
from src.link_check import check_page
from src.release_template import docs_url
from src.summary_placement import resolve_section, validate_insert

_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)
_EMBED = re.compile(r"!\[([^\]]*)\]\(assets/([^)\s]+)\)")
_PLACEHOLDER_ALT = re.compile(r"^\s*(image asset\b|unknown\b)", re.I)


@dataclass
class RepoView:
    """One repo as the run left it: main's SUMMARY, the branch's SUMMARY, every
    path on the branch, and the text of the pages the run added."""
    summary_main: str = ""
    summary: str = ""
    paths: set = field(default_factory=set)
    pages: dict = field(default_factory=dict)   # path -> text, new .md pages only


@dataclass
class Published:
    docs: RepoView
    releases: RepoView
    named_media: list            # media files the material names
    needs_attention: list        # every item the run flagged
    target_section: str = ""     # the confirmed section, if any
    summary_path: str = "SUMMARY.md"
    require_clean: bool = False  # a clean run must flag nothing
    allow_new_section: bool = False


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""


def _flagged(name: str, items: list) -> bool:
    return any(name in str(i) for i in items)


def _embeds(text: str) -> list[tuple[str, str]]:
    return _EMBED.findall(text or "")


def named_files_accounted(p: Published) -> Result:
    """Every file the material names is on a page (and uploaded next to it), or flagged."""
    lost = []
    for name in p.named_media:
        used = False
        for repo in (p.docs, p.releases):
            for path, text in repo.pages.items():
                if any(f == name for _, f in _embeds(text)):
                    asset = posixpath.join(posixpath.dirname(path), "assets", name)
                    if asset in repo.paths:
                        used = True
        if not used and not _flagged(name, p.needs_attention):
            lost.append(name)
    return Result("named files accounted for", not lost,
                  f"{len(p.named_media) - len(lost)}/{len(p.named_media)} used or flagged"
                  + (f"; silently lost: {', '.join(lost)}" if lost else ""))


def no_duplicate_assets(p: Published) -> Result:
    """No file shipped twice: no "x-2.gif" next to "x.gif", no image embedded twice on one page."""
    problems = []
    for label, repo in (("docs", p.docs), ("releases", p.releases)):
        names = {posixpath.basename(x) for x in repo.paths if "/assets/" in f"/{x}"}
        for n in names:
            m = re.match(r"^(.*)-(\d+)(\.[a-z0-9]+)$", n, re.I)
            if m and f"{m.group(1)}{m.group(3)}" in names:
                problems.append(f"{label}: {n} duplicates {m.group(1)}{m.group(3)}")
        for path, text in repo.pages.items():
            files = [f for _, f in _embeds(text)]
            for f in sorted({f for f in files if files.count(f) > 1}):
                problems.append(f"{path} embeds {f} {files.count(f)} times")
    return Result("no duplicate assets", not problems, "; ".join(problems) or "none")


def no_new_group(p: Published) -> Result:
    """The docs sidebar never gains a top-level "##" group the requester did not ask for."""
    before = len(_HEADING.findall(p.docs.summary_main))
    after = len(_HEADING.findall(p.docs.summary))
    added = sorted(set(_HEADING.findall(p.docs.summary)) - set(_HEADING.findall(p.docs.summary_main)))
    ok = after == before or (p.allow_new_section and after == before + 1)
    return Result("no new sidebar group", ok, f"{before} -> {after}" + (f"; added {added}" if added else ""))


def placement(p: Published) -> Result:
    """With a confirmed section, the doc page is nested exactly under it.
    Without one, the run must say the placement is unconfirmed."""
    docs_pages = sorted(p.docs.pages)
    if not docs_pages:
        return Result("placement", _flagged("doc page", p.needs_attention) or not p.docs.summary,
                      "no doc page was published")
    if not p.target_section:
        flagged = any("Placement unconfirmed" in str(i) or "NOT in the sidebar" in str(i) for i in p.needs_attention)
        return Result("placement", flagged, "no confirmed section; " + ("flagged as unconfirmed" if flagged
                                                                           else "NOT flagged as unconfirmed"))
    where = resolve_section(p.docs.summary_main, p.target_section, allow_leaf=True)
    if where is None:
        return Result("placement", False, f'"{p.target_section}" does not resolve in the sidebar on main')
    base = posixpath.dirname(p.summary_path)
    problems = validate_insert(p.docs.summary_main, p.docs.summary, where, lambda x: x in p.docs.paths, base)
    folder = posixpath.join(base, where.folder) if where.folder else base
    for path in docs_pages:
        if posixpath.dirname(path) != folder:
            problems.append(f"{path} is not in {folder or '(root)'}/")
    return Result("placement", not problems, "; ".join(problems) or f"under {where.label}")


def release_links_doc(p: Published) -> Result:
    """The release note links the doc page where it actually landed, or says it is coming."""
    problems = []
    doc_urls = [docs_url(x) for x in p.docs.pages]
    for path, text in p.releases.pages.items():
        if "{{DOC_PAGE_URL}}" in text:
            problems.append(f"{path} still has the {{{{DOC_PAGE_URL}}}} placeholder")
        linked = [u for u in re.findall(r"\((https://docs\.flytbase\.com/[^)\s]*)\)", text)]
        if doc_urls and not any(u in doc_urls for u in linked):
            problems.append(f"{path} does not link the published doc page ({doc_urls[0]})")
        if not doc_urls and linked and "coming soon" not in text.lower():
            stray = [u for u in linked if u not in doc_urls]
            if stray:
                problems.append(f"{path} links {stray[0]} but no doc page was published")
    return Result("release links the real doc page", not problems, "; ".join(problems) or "ok")


def no_placeholder_alt(p: Published) -> Result:
    bad = [f"{path}: ![{alt}]" for repo in (p.docs, p.releases) for path, text in repo.pages.items()
           for alt, _ in _embeds(text) if not alt.strip() or _PLACEHOLDER_ALT.match(alt)]
    return Result("no placeholder alt text", not bad, "; ".join(bad[:5]) or "ok")


def links_resolve(p: Published) -> Result:
    problems = []
    docs_exists = lambda x: x in p.docs.paths  # noqa: E731
    for repo in (p.docs, p.releases):
        exists = lambda x, r=repo: x in r.paths  # noqa: E731
        for path, text in repo.pages.items():
            problems += [f"{path}: {x}" for x in check_page(text, path, exists, docs_exists)]
    return Result("internal links resolve", not problems, "; ".join(problems[:5]) or "ok")


def sections_of(page: str) -> dict[str, list[str]]:
    """A rendered doc page split into its "##" sections, prose only (no images)."""
    out: dict[str, list[str]] = {}
    current = None
    for line in page.splitlines():
        m = re.match(r"^##\s+(.+)$", line)
        if m:
            current = m.group(1).strip()
            out[current] = []
        elif current and line.strip() and not line.lstrip().startswith("!["):
            out[current].append(line)
    return out


def no_repeated_quantities(p: Published) -> Result:
    found = []
    for path, text in p.docs.pages.items():
        for q, where in repeated_quantities(sections_of(text)).items():
            found.append(f"{posixpath.basename(path)}: {q} in {', '.join(where)}")
    return Result("no fact repeated across sections", not found, "; ".join(found[:5]) or "ok")


def nothing_flagged(p: Published) -> Result:
    if not p.require_clean:
        return Result("needs attention (informational)", True, f"{len(p.needs_attention)} item(s)")
    return Result("clean run flags nothing", not p.needs_attention,
                  "; ".join(str(i)[:120] for i in p.needs_attention[:5]) or "nothing flagged")


CHECKS = (named_files_accounted, no_duplicate_assets, no_new_group, placement, release_links_doc,
          no_placeholder_alt, links_resolve, no_repeated_quantities, nothing_flagged)


def check_all(p: Published) -> list[Result]:
    out = []
    for check in CHECKS:
        try:
            out.append(check(p))
        except Exception as e:  # noqa: BLE001 - one broken check must not hide the rest
            out.append(Result(check.__name__.replace("_", " "), False, f"the check itself crashed: {e!r}"))
    return out


def table(results: list[Result]) -> str:
    rows = ["| Invariant | Result | Detail |", "|---|---|---|"]
    for r in results:
        rows.append(f"| {r.name} | {'pass' if r.ok else 'FAIL'} | {r.detail.replace('|', '/')} |")
    return "\n".join(rows)
