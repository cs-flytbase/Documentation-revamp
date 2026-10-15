"""Decide where a new page goes, from SUMMARY.md itself.

The sidebar is the source of truth for where a section lives. Placement used to
be a model guess (target_path) plus a "## heading" substring match, which:
  - ignored the section the user asked for on single pages, so "put it under
    Flex Factory" produced a page under Post-Flight Modules;
  - never matched sidebar items, only "## " headings;
  - inserted pages at the top level even when the section's pages are nested;
  - wrote repo paths ("on-premise/x.md") into on-premise/SUMMARY.md, whose
    links are relative to on-premise/.

resolve_section() finds the section a request names and returns where the page
should live and where its sidebar line goes. insert_entry() writes that line.
Both are pure functions over the SUMMARY text, so they are easy to test.
"""
import posixpath
import re
from dataclasses import dataclass, field

_ITEM = re.compile(r"^(\s*)[*-] \[(.+?)\]\((.+?)\)\s*$")
_HEADING = re.compile(r"^#{2,3}\s+(.+?)\s*$")


def norm(text: str) -> str:
    """'Post-Flight Modules' and 'post flight modules' compare equal."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


@dataclass
class Item:
    idx: int          # line index in SUMMARY.md
    indent: int
    title: str
    path: str         # as written in SUMMARY.md (relative to its directory)
    heading: str      # the "## " section it sits under
    children: list = field(default_factory=list)
    parent: "Item | None" = None


@dataclass
class Placement:
    label: str        # human-readable, e.g. "Flex Factory > Flex Factory"
    folder: str       # directory for the new file, relative to SUMMARY.md's directory
    insert_at: int    # line index the new entry is inserted at
    indent: int
    matched: str      # what the request matched: "item", "heading", "leaf item"


def parse(summary: str):
    lines = summary.splitlines()
    items, headings = [], []
    heading = ""
    stack: list[Item] = []
    for i, line in enumerate(lines):
        h = _HEADING.match(line)
        if h:
            heading = h.group(1)
            headings.append((i, heading))
            stack = []
            continue
        m = _ITEM.match(line)
        if not m:
            continue
        item = Item(i, len(m.group(1).expandtabs(2)), m.group(2).strip(), m.group(3).strip(), heading)
        while stack and stack[-1].indent >= item.indent:
            stack.pop()
        if stack:
            item.parent = stack[-1]
            stack[-1].children.append(item)
        stack.append(item)
        items.append(item)
    return lines, items, headings


def _last_line(item: Item) -> int:
    last = item.idx
    for child in item.children:
        last = max(last, _last_line(child))
    return last


def _common_dir(paths: list[str]) -> str:
    dirs = [posixpath.dirname(p) for p in paths if p]
    if not dirs:
        return ""
    try:
        return posixpath.commonpath(dirs)
    except ValueError:
        return ""


def _chain(item: Item) -> list[str]:
    out = []
    while item:
        out.append(item.title)
        item = item.parent
    return list(reversed(out))


def _item_placement(item: Item, matched: str) -> Placement:
    if item.children:
        folder = _common_dir([c.path for c in item.children]) or posixpath.dirname(item.path)
        indent = item.children[0].indent
    else:
        folder = posixpath.dirname(item.path)
        indent = item.indent + 2
    label = " > ".join(([item.heading] if item.heading and norm(item.heading) != norm(item.title) else []) + _chain(item))
    return Placement(label, folder, _last_line(item) + 1, indent, matched)


def _heading_placement(lines, items, idx: int, title: str) -> Placement | None:
    block = [it for it in items if it.heading == title and it.idx > idx]
    if not block:
        return Placement(title, "", idx + 2 if idx + 1 < len(lines) and not lines[idx + 1].strip() else idx + 1, 0, "heading")
    top = min(it.indent for it in block)
    last = max(_last_line(it) for it in block)
    return Placement(title, _common_dir([it.path for it in block]), last + 1, top, "heading")


def resolve_section(summary: str, wanted: str, allow_leaf: bool = True) -> Placement | None:
    """Where a page asked to go under `wanted` belongs, or None if unclear.

    `wanted` may be a breadcrumb ("Pre-Flight Modules > Planning"); the last
    part is the section, earlier parts disambiguate. A sidebar item with
    child pages beats a "## " heading of the same name (Flex Factory is both),
    which beats a leaf page. Ambiguity returns None rather than a guess.
    """
    parts = [p for p in re.split(r"\s*(?:>|/)\s*", wanted or "") if norm(p)]
    if not parts:
        return None
    target, context = norm(parts[-1]), {norm(p) for p in parts[:-1]}
    lines, items, headings = parse(summary)

    def in_context(item: Item) -> bool:
        if not context:
            return True
        names = {norm(t) for t in _chain(item)[:-1]} | {norm(item.heading)}
        return bool(context & names)

    for exact in (True, False):
        def hit(name: str) -> bool:
            n = norm(name)
            if exact:
                return n == target
            return len(target) >= 4 and (target in n or n in target) and len(n) >= 4

        parents = [it for it in items if it.children and hit(it.title) and in_context(it)]
        heads = [(i, h) for i, h in headings if hit(h)] if not context else []
        leaves = [it for it in items if not it.children and hit(it.title) and in_context(it)] if allow_leaf else []

        if len(parents) == 1:
            return _item_placement(parents[0], "item")
        if len(parents) > 1:
            return None
        if len(heads) == 1:
            return _heading_placement(lines, items, *heads[0])
        if len(heads) > 1:
            return None
        if len(leaves) == 1:
            return _item_placement(leaves[0], "leaf item")
        if len(leaves) > 1:
            return None
    return None


def insert_entry(summary: str, placement: Placement, title: str, rel_path: str) -> str:
    """Insert '* [title](rel_path)' at the placement. No-op if already listed."""
    if f"]({rel_path})" in summary:
        return summary
    lines = summary.splitlines()
    lines.insert(placement.insert_at, " " * placement.indent + f"* [{title}]({rel_path})")
    return "\n".join(lines) + "\n"


def relative_to_summary(repo_path: str, summary_path: str) -> str:
    """A repo path as SUMMARY.md must link it (on-premise/x.md -> x.md)."""
    base = posixpath.dirname(summary_path)
    if base and repo_path.startswith(base + "/"):
        return repo_path[len(base) + 1:]
    return repo_path


def repo_path_for(folder: str, filename: str, summary_path: str) -> str:
    """Where a file lives in the repo, given a SUMMARY-relative folder."""
    base = posixpath.dirname(summary_path)
    rel = posixpath.join(folder, filename) if folder else filename
    return posixpath.join(base, rel) if base else rel


def insert_entries(summary: str, placement: Placement, entries: list[tuple[str, str, int]]) -> str:
    """Insert several lines at once: (title, rel_path, depth below the placement).

    Used for a parent page with its subsections. No-op if the first entry is
    already listed.
    """
    if not entries or f"]({entries[0][1]})" in summary:
        return summary
    lines = summary.splitlines()
    new = [" " * (placement.indent + 2 * depth) + f"* [{title}]({rel})" for title, rel, depth in entries]
    lines[placement.insert_at:placement.insert_at] = new
    return "\n".join(lines) + "\n"
