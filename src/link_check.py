"""Check that a page's links and images point at files that exist.

The model invents plausible paths: a README that is not there, a sibling page
with the wrong slug, an image it never uploaded. None of that was checked
before a PR was opened. check_page() resolves every relative link, every
image and every docs.flytbase.com URL against the files that exist (on main,
or written by this run) and reports the ones that do not.
"""
import posixpath
import re

_LINK = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_DOCS_HOST = "https://docs.flytbase.com/"


def _strip_anchor(target: str) -> str:
    return target.split("#", 1)[0].split("?", 1)[0]


def check_page(content: str, page_path: str, exists, docs_exists=None) -> list[str]:
    """Problems with links and images in `content`.

    page_path is the page's path in its repo; exists(path) says whether a path
    exists in that repo. docs.flytbase.com URLs are checked with docs_exists
    (the docs repo), defaulting to exists. Returns one problem per broken target.
    """
    problems, seen = [], set()
    base = posixpath.dirname(page_path)
    body = re.sub(r"```.*?```", "", content, flags=re.S)
    for bang, text, target in _LINK.findall(body):
        if target in seen or target.startswith(("mailto:", "#", "{{")):
            continue
        seen.add(target)
        kind = "image" if bang else "link"
        if target.startswith(_DOCS_HOST):
            path = _strip_anchor(target[len(_DOCS_HOST):]).strip("/")
            candidates = [f"{path}.md", f"{path}/README.md"] if path else ["README.md"]
            if not any((docs_exists or exists)(c) for c in candidates):
                problems.append(f'{kind} "{text}" points to {target}, but no page exists at {candidates[0]}')
            continue
        if re.match(r"^[a-z]+://", target):
            continue  # other sites are not checked
        raw = _strip_anchor(target)
        path = posixpath.normpath(raw.lstrip("/") if raw.startswith("/") else posixpath.join(base, raw))
        if path.startswith(".."):
            problems.append(f'{kind} "{text}" points outside the repo: {target}')
            continue
        candidates = [path]
        if not posixpath.splitext(path)[1]:
            candidates += [f"{path}.md", f"{path}/README.md"]
        if not any(exists(c) for c in candidates):
            problems.append(f'{kind} "{text or posixpath.basename(path)}" points to {target}, which does not exist ({path})')
    return problems


def _target_exists(target: str, page_path: str, exists, docs_exists=None) -> bool | None:
    """True/False for a checkable link target, None for external or special ones."""
    if target.startswith(("mailto:", "#", "{{")):
        return None
    if target.startswith(_DOCS_HOST):
        path = _strip_anchor(target[len(_DOCS_HOST):]).strip("/")
        candidates = [f"{path}.md", f"{path}/README.md"] if path else ["README.md"]
        return any((docs_exists or exists)(c) for c in candidates)
    if re.match(r"^[a-z]+://", target):
        return None
    raw = _strip_anchor(target)
    # "/in-flight-modules/x" is repo-rooted (GitBook style); anything else is page-relative.
    path = posixpath.normpath(raw.lstrip("/") if raw.startswith("/") else posixpath.join(posixpath.dirname(page_path), raw))
    if path.startswith(".."):
        return False
    candidates = [path]
    if not posixpath.splitext(path)[1]:
        candidates += [f"{path}.md", f"{path}/README.md"]
    return any(exists(c) for c in candidates)


def repair_links(content: str, page_path: str, exists, summary: str = "", summary_path: str = "SUMMARY.md",
                 docs_exists=None) -> tuple[str, list[str], list[str]]:
    """Fix broken page links instead of shipping them (P0, 2026-10-22).

    A broken link is re-pointed at the page it names, found in the live
    sidebar by its link text or by its file name. A link that cannot be
    resolved becomes plain text, so a reader never hits a 404. Images are not
    touched here (a missing image is reported by check_page).

    Returns (content, fixed descriptions, unlinked descriptions).
    """
    from src.summary_placement import norm, parse

    items = parse(summary)[1] if summary else []
    base = posixpath.dirname(summary_path)
    repo = lambda p: posixpath.join(base, p) if base else p
    fixed, unlinked = [], []

    def find(text: str, target: str):
        by_title = [it for it in items if norm(it.title) == norm(text)]
        if len(by_title) == 1:
            return repo(by_title[0].path)
        stem = posixpath.splitext(posixpath.basename(_strip_anchor(target).rstrip("/")))[0]
        if stem and stem.lower() != "readme":
            by_file = [it for it in items if posixpath.splitext(posixpath.basename(it.path))[0] == stem]
            if len(by_file) == 1:
                return repo(by_file[0].path)
        return None

    def fix(m):
        bang, text, target = m.group(1), m.group(2), m.group(3)
        if bang:
            return m.group(0)
        ok = _target_exists(target, page_path, exists, docs_exists)
        if ok is None or ok:
            return m.group(0)
        found = find(text, target)
        if found:
            if target.startswith(_DOCS_HOST):
                new = _DOCS_HOST + re.sub(r"(/README)?\.md$", "", found)
            else:
                new = posixpath.relpath(found, posixpath.dirname(page_path) or ".")
            fixed.append(f'link "{text}": {target} -> {new}')
            return f"[{text}]({new})"
        unlinked.append(f'link "{text}" pointed to {target}, which does not exist; it is now plain text')
        return text

    parts = re.split(r"(```.*?```)", content, flags=re.S)
    out = "".join(p if p.startswith("```") else _LINK.sub(fix, p) for p in parts)
    return out, fixed, unlinked
