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
        # A page becoming a parent (Cockpit 3.0 > Zone Overlays): its children
        # live in a folder named after it, next to it, like GitBook does.
        base, name = posixpath.split(item.path)
        stem = posixpath.splitext(name)[0]
        folder = base if stem.lower() == "readme" else posixpath.join(base, stem)
        indent = item.indent + 2
    label = " > ".join(([item.heading] if item.heading and norm(item.heading) != norm(item.title) else []) + _chain(item))
    return Placement(label, folder, _last_line(item) + 1, indent, matched)


def _heading_placement(lines, items, idx: int, title: str) -> Placement | None:
    block = [it for it in items if it.heading == title and it.idx > idx]
    if not block:
        return Placement(title, "", idx + 2 if idx + 1 < len(lines) and not lines[idx + 1].strip() else idx + 1, 0, "heading")
    top = min(it.indent for it in block)
    last = max(_last_line(it) for it in block)
    return Placement(title, _section_dir([it.path for it in block]), last + 1, top, "heading")


def _section_dir(paths: list[str]) -> str:
    """The folder a section's pages live in. One stray page elsewhere (AVSS at
    the repo root under In-Flight Modules) must not send new pages to the root:
    fall back to the top folder most of the pages share."""
    common = _common_dir(paths)
    if common:
        return common
    from collections import Counter
    tops = Counter(p.split("/", 1)[0] for p in paths if "/" in p)
    if tops:
        top, n = tops.most_common(1)[0]
        if n * 2 > len(paths):
            return top
    return ""


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


# ── Placement without guessing (P0, 2026-10-22) ──────────────────────────
#
# The Cockpit 3.0 doc was placed from a stale IA file, the guess failed to
# resolve, and a legacy fallback invented a new "## Live Video Feed" group.
# These helpers give the model the LIVE sidebar to choose from, resolve its
# choice exactly, fall back to the nearest real ancestor, and check the
# sidebar after writing.


def chain_label(item: Item) -> str:
    """'In-Flight Modules > How to Manage Your Flight Operations > Cockpit 3.0'."""
    head = [item.heading] if item.heading and norm(item.heading) != norm(_chain(item)[0]) else []
    return " > ".join(head + _chain(item))


def parent_chains(summary: str) -> list[str]:
    """Every place a page can go, one exact label per line, for the model to pick from."""
    _, items, headings = parse(summary)
    out = [h for _, h in headings]
    out += [chain_label(it) for it in items]
    seen, unique = set(), []
    for label in out:
        if label not in seen:
            seen.add(label)
            unique.append(label)
    return unique


def resolve_exact(summary: str, chain: str) -> Placement | None:
    """Resolve a label copied from parent_chains(). Exact match only; a page
    (leaf) is allowed because the label names it exactly."""
    want = norm(chain)
    if not want:
        return None
    lines, items, headings = parse(summary)
    hits = [it for it in items if norm(chain_label(it)) == want]
    if len(hits) == 1:
        return _item_placement(hits[0], "item" if hits[0].children else "leaf item")
    if hits:
        return None
    heads = [(i, h) for i, h in headings if norm(h) == want]
    if len(heads) == 1:
        return _heading_placement(lines, items, *heads[0])
    return None


def nearest_ancestor(summary: str, guess: str) -> Placement | None:
    """The deepest existing section along a guessed breadcrumb.

    "In-Flight Modules > ... > Multi View Dashboard > Live Video Feed", where
    Live Video Feed is a page, resolves to Multi View Dashboard. Never a leaf,
    never a new section.
    """
    parts = [p for p in re.split(r"\s*(?:>|/)\s*", guess or "") if norm(p)]
    for i in range(len(parts), 0, -1):
        placement = resolve_section(summary, " > ".join(parts[:i]), allow_leaf=False)
        if placement:
            return placement
    return None


def validate_insert(before: str, after: str, placement: Placement, exists=None, base: str = "") -> list[str]:
    """Problems with a sidebar edit: anything other than new '* [..](..)' lines
    at the placement. exists(repo_path) checks linked files; base is the
    SUMMARY.md directory."""
    import difflib
    problems = []
    old, new = before.splitlines(), after.splitlines()
    added = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            problems.append(f"{i2 - i1} existing sidebar line(s) were changed or removed")
        if tag in ("replace", "insert"):
            added += list(range(j1, j2))
    added_set = set(added)
    _, items, _ = parse(after)
    by_idx = {it.idx: it for it in items}
    want_parent = norm(placement.label.split(" > ")[-1])
    links = [it.path for it in items]
    for j in added:
        line = new[j]
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            problems.append(f'a new sidebar group was added: "{line.strip()}"')
            continue
        item = by_idx.get(j)
        if not item:
            problems.append(f'a line that is not a page entry was added: "{line.strip()}"')
            continue
        parent = norm(item.parent.title) if item.parent else norm(item.heading)
        if parent != want_parent and not (item.parent and item.parent.idx in added_set):
            problems.append(f'"{item.title}" is under "{item.parent.title if item.parent else item.heading}", '
                            f'not "{placement.label}"')
        if links.count(item.path) > 1:
            problems.append(f'"{item.path}" is listed more than once')
        if exists is not None and not exists(posixpath.join(base, item.path) if base else item.path):
            problems.append(f'"{item.title}" links to {item.path}, which does not exist')
    return problems
