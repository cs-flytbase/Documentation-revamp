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
        path = posixpath.normpath(posixpath.join(base, _strip_anchor(target)))
        if path.startswith(".."):
            problems.append(f'{kind} "{text}" points outside the repo: {target}')
            continue
        candidates = [path]
        if not posixpath.splitext(path)[1]:
            candidates += [f"{path}.md", f"{path}/README.md"]
        if not any(exists(c) for c in candidates):
            problems.append(f'{kind} "{text or posixpath.basename(path)}" points to {target}, which does not exist ({path})')
    return problems
