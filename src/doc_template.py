"""The documentation template, enforced by code.

A doc page is the exhaustive technical reference: problem, use cases,
prerequisites and dependencies, navigation, every control on the screen,
how to do each task, the rules and expected behaviour, limitations, edge
cases, what happens afterwards. It is read by people and very often by
LLMs, so every section must stand on its own. (Senior review, 2026-10-19.)

The model fills in fields; render() builds the page in a fixed order. A
section the source material does not cover is never dropped silently: it
renders as an explicit "Not covered in the source material" note and is
listed as a gap for the reviewer.

Feature (nested) page:
    # <Title>
    {% embed %}                              video first, if given
    ## Overview
    ## Problem Statement
    ## Use Cases  (### When to Use / ### When Not to Use)
    ## Before You Start  (### Prerequisites / ### Dependencies)
    ## Navigation
    ## Interface Reference                   table: Element | Location | What It Does | Effect
    ## How To  (### <Task>, numbered steps, images right after their step)
    ## Behaviour and Rules  (### Rules / ### Expected Behaviour)
    ## Limitations, Edge Cases and Troubleshooting  (### Limitations table / ### Edge Cases and Troubleshooting table)
    ## After You Finish
    ## Related Pages

Master page (a section's parent overview): Overview, Problem Statement,
What's in This Section, Navigation, Common Prerequisites, Related Pages.
"""
import re
from pathlib import Path

GAP = "Not covered in the source material."

DOC_PAGE_PROMPT = r"""You are the Drafting Agent for the FlytBase documentation pipeline.
You are writing a DOC PAGE: the exhaustive technical reference for a feature.

It is NOT marketing. It is read by operators and very often by LLMs answering
questions, so it must be complete, precise and unambiguous, and every section
must make sense on its own. Include every detail the source gives: every
button, every rule, every condition, every limit, every message.

You do not write Markdown or choose the structure. Fill in the fields below as
JSON; the page is built from them by code in a fixed template. If the source
material does not cover a field, leave it empty ("" or []): the template
marks it as a gap for the reviewer. NEVER invent content to fill a field.

{
  "filename": "feature-slug.md",
  "target_path": "IA path for the page, e.g. in-flight-modules",
  "title": "Feature name",
  "description": "One sentence, at most 150 characters: what this page documents.",
  "overview": "One paragraph: what the feature is and what it does.",
  "problem_statement": "What is being solved, why this feature exists, and the precise definition of the problem.",
  "when_to_use": ["Situation in which to use it"],
  "when_not_to_use": ["Situation in which not to use it, and what to use instead"],
  "prerequisites": ["Account, role, hardware, setting or feature flag needed first"],
  "dependencies": ["Feature or system this relies on, and what happens without it"],
  "navigation": ["**Exact > Path > To > It**, plus every other entry point"],
  "interface_reference": [
    {"element": "**Exact on-screen label**, or a consistent descriptive name for an icon (e.g. the layers icon)",
     "location": "Where it is on the screen", "what_it_does": "Its purpose", "effect": "What changes when it is used"}
  ],
  "how_to": [
    {"task": "Task name (e.g. Show or Hide No-Fly Zones)",
     "steps": [{"text": "**Action verb** the **Element**. What happens as a result.", "asset": "exact-asset-filename or empty"}]}
  ],
  "rules": ["If <condition>, then <behaviour>. Every hard rule, default, limit, ordering and permission."],
  "expected_behaviour": ["What the system does in normal operation: states, indicators, messages, timing."],
  "limitations": [{"component": "Component", "details": "Limit or constraint"}],
  "edge_cases": [{"situation": "...", "cause": "...", "what_happens": "...", "what_to_do": "..."}],
  "after_you_finish": ["What persists, what resets, follow-up actions, cleanup."],
  "related_pages": [{"title": "Page title", "url": "relative path or docs URL"}],
  "section_assets": {"overview": ["exact-asset-filename"], "navigation": [], "interface_reference": []},
  "impacted_page_edits": []
}

Field rules:
- interface_reference must list EVERY button, toggle, icon, field, menu and
  panel the source shows or mentions, with the exact on-screen label in bold.
  Every element named in how_to steps must also appear here.
- how_to: one task per thing an operator does. Steps are atomic: one action per
  step, starting with the action verb in bold, followed by what happens.
- Assets: use only filenames from DOC ASSETS. Every DOC ASSET must be used
  exactly once: on the step it illustrates, or in section_assets for the
  section it illustrates.
- Plain technical English. Exact labels. Consistent names for icons and sections.
- No headings, tables, horizontal rules or video embeds inside any field.

impacted_page_edits (cross-references in existing pages):
[{"source_url": "https://docs.flytbase.com/...", "edit_description": "...",
  "section_heading": "## Exact Existing Heading", "patch_mode": "append",
  "patch_content": "\n### Related Features\n\n- [Title](relative/path.md): one line.\n"}]
section_heading must be the EXACT heading from the existing page. Use "append" for cross-references.

Return ONLY the JSON object. No markdown wrapping.
"""

_FIELDS_LIST = ["when_to_use", "when_not_to_use", "prerequisites", "dependencies", "navigation",
                "rules", "expected_behaviour", "after_you_finish"]


def _clean(text) -> str:
    text = str(text or "").strip()
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*(---|\*\*\*)\s*$", "", text, flags=re.M)
    text = re.sub(r"\{%\s*embed.*?%\}", "", text, flags=re.S)
    return text.strip()


def _cell(text) -> str:
    return " ".join(_clean(text).replace("|", "/").split())


def _items(value) -> list[str]:
    if isinstance(value, str):
        value = [value] if value.strip() else []
    out = []
    for v in value or []:
        v = re.sub(r"^\s*(\d+[.)]|[-*])\s+", "", _clean(v))
        if v:
            out.append(v)
    return out


_SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into", "of", "off", "on", "onto",
          "or", "over", "the", "to", "up", "via", "vs", "with"}


def title_case(text: str) -> str:
    """Title Case for headings, leaving words that already have capitals (labels,
    acronyms, product names) exactly as written."""
    words = _clean(text).split()
    out = []
    for i, w in enumerate(words):
        if any(c.isupper() for c in w) or not w[:1].isalpha():
            out.append(w)
        elif i > 0 and w.lower() in _SMALL:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _alt(text, file):
    alt = " ".join(str(text or Path(file).stem.replace("-", " ").replace("_", " ")).split())
    return alt.replace("[", "(").replace("]", ")")


_BOLD = re.compile(r"\*\*(.+?)\*\*")
# Action verbs that start a step in bold are not interface elements.
_VERBS = {"click", "select", "open", "turn", "toggle", "enable", "disable", "choose", "enter", "type", "drag",
          "right-click", "double-click", "navigate", "go", "set", "pick", "press", "tap", "hover", "close",
          "launch", "save", "cancel", "move", "check", "uncheck", "expand", "collapse", "switch", "use", "draw",
          "zoom", "tilt", "find", "watch", "review", "wait", "upload", "download", "add", "remove", "delete",
          "edit", "confirm", "clear", "scroll", "follow"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def interface_gaps(data: dict) -> list[str]:
    """Bold UI labels used in how_to steps that the Interface Reference does not define."""
    defined = " | ".join(_norm(r.get("element", "")) for r in data.get("interface_reference") or [] if isinstance(r, dict))
    missing = []
    for task in data.get("how_to") or []:
        for step in (task.get("steps") or []) if isinstance(task, dict) else []:
            text = (step.get("text", "") if isinstance(step, dict) else str(step)).strip()
            for m in _BOLD.finditer(text):
                label = m.group(1)
                # The bold at the very start of a step is its action verb
                # ("**Turn on** ..."), per the docs convention, not a control.
                if m.start() == 0:
                    continue
                n = _norm(label)
                if not n or n in _VERBS:
                    continue
                parts = [p.strip() for p in re.split(r">", label)]
                for part in parts:
                    pn = _norm(part)
                    if pn and pn not in _VERBS and pn not in defined and pn not in missing:
                        missing.append(pn)
    return missing


def check_fields(data: dict, doc_assets: list[str]) -> list[str]:
    """Problems with the model's fields, phrased for a targeted retry."""
    problems = []
    for key in ("title", "description", "overview"):
        if not _clean(data.get(key)):
            problems.append(f'doc page field "{key}" is empty')
    if len(str(data.get("description", ""))) > 150:
        problems.append(f'doc page description is {len(str(data["description"]))} characters (max 150)')
    for label in interface_gaps(data):
        problems.append(f'doc page step uses "{label}" but interface_reference does not define it')
    used = []
    for task in data.get("how_to") or []:
        for step in task.get("steps") or []:
            if isinstance(step, dict) and step.get("asset"):
                used.append(step["asset"])
    for files in (data.get("section_assets") or {}).values():
        used += [f for f in files or [] if f]
    for a in used:
        if a not in doc_assets:
            problems.append(f"doc page uses asset '{a}' that is not in DOC ASSETS")
    for a in doc_assets:
        if a not in used:
            problems.append(f"asset '{a}' missing from doc page (put it on the step or section it shows)")
    for a in {x for x in used if used.count(x) > 1}:
        problems.append(f"doc page uses asset '{a}' more than once")
    return problems


def render(data: dict, doc_assets: list[str], youtube_link: str = "",
           asset_descriptions: dict | None = None) -> tuple[dict, list[str]]:
    """Build the doc page. Returns ({"filename","frontmatter","content","target_path"}, gaps)."""
    descs = asset_descriptions or {}
    gaps: list[str] = []
    used = set()
    section_assets = {k: [a for a in (v or []) if a in doc_assets] for k, v in (data.get("section_assets") or {}).items()}

    def img(file, fallback=""):
        if not file or file not in doc_assets or file in used:
            return []
        used.add(file)
        return [f"![{_alt(descs.get(file) or fallback, file)}](assets/{file})", ""]

    def sec_imgs(key):
        out = []
        for f in section_assets.get(key, []):
            out += img(f)
        return out

    def para(key, heading, out, level="##"):
        text = _clean(data.get(key))
        out += [f"{level} {heading}", ""]
        if text:
            out += [text, ""]
        else:
            out += [GAP, ""]
            gaps.append(heading)
        out += sec_imgs(key)

    def bullet_list(key, heading, out, level="###"):
        items = _items(data.get(key))
        out += [f"{level} {heading}", ""]
        if items:
            out += [f"- {i}" for i in items] + [""]
        else:
            out += [GAP, ""]
            gaps.append(heading)
        out += sec_imgs(key)

    title = _clean(data.get("title")) or "Untitled"
    out = [f"# {title}", ""]
    if youtube_link:
        out += [f'{{% embed url="{youtube_link}" %}}', ""]

    para("overview", "Overview", out)
    para("problem_statement", "Problem Statement", out)

    out += ["## Use Cases", ""]
    bullet_list("when_to_use", "When to Use", out)
    bullet_list("when_not_to_use", "When Not to Use", out)

    out += ["## Before You Start", ""]
    bullet_list("prerequisites", "Prerequisites", out)
    bullet_list("dependencies", "Dependencies", out)

    nav = _items(data.get("navigation"))
    out += ["## Navigation", ""]
    out += ([f"- {n}" for n in nav] + [""]) if nav else [GAP, ""]
    if not nav:
        gaps.append("Navigation")
    out += sec_imgs("navigation")

    rows = [r for r in data.get("interface_reference") or [] if isinstance(r, dict) and _cell(r.get("element"))]
    out += ["## Interface Reference", ""]
    if rows:
        out += ["| Element | Location | What It Does | Effect |", "|---|---|---|---|"]
        out += [f"| {_cell(r.get('element'))} | {_cell(r.get('location'))} | {_cell(r.get('what_it_does'))} | {_cell(r.get('effect'))} |" for r in rows]
        out += [""]
    else:
        out += [GAP, ""]
        gaps.append("Interface Reference")
    out += sec_imgs("interface_reference")

    tasks = [t for t in data.get("how_to") or [] if isinstance(t, dict) and _clean(t.get("task"))]
    out += ["## How To", ""]
    if not tasks:
        out += [GAP, ""]
        gaps.append("How To")
    for t in tasks:
        out += [f"### {title_case(t.get('task'))}", ""]
        n = 0
        for step in t.get("steps") or []:
            text = _clean(step.get("text") if isinstance(step, dict) else step)
            text = re.sub(r"^\s*\d+[.)]\s+", "", text)
            if not text:
                continue
            n += 1
            out += [f"{n}. {text}", ""]
            if isinstance(step, dict):
                out += img(step.get("asset"), text)
    out += sec_imgs("how_to")

    out += ["## Behaviour and Rules", ""]
    bullet_list("rules", "Rules", out)
    bullet_list("expected_behaviour", "Expected Behaviour", out)

    out += ["## Limitations, Edge Cases and Troubleshooting", ""]
    lim = [r for r in data.get("limitations") or [] if isinstance(r, dict) and _cell(r.get("component"))]
    out += ["### Limitations", ""]
    if lim:
        out += ["| Component | Details |", "|---|---|"] + [f"| {_cell(r.get('component'))} | {_cell(r.get('details'))} |" for r in lim] + [""]
    else:
        out += [GAP, ""]
        gaps.append("Limitations")
    edges = [r for r in data.get("edge_cases") or [] if isinstance(r, dict) and _cell(r.get("situation"))]
    out += ["### Edge Cases and Troubleshooting", ""]
    if edges:
        out += ["| Situation | Cause | What Happens | What to Do |", "|---|---|---|---|"]
        out += [f"| {_cell(r.get('situation'))} | {_cell(r.get('cause'))} | {_cell(r.get('what_happens'))} | {_cell(r.get('what_to_do'))} |" for r in edges] + [""]
    else:
        out += [GAP, ""]
        gaps.append("Edge Cases and Troubleshooting")
    out += sec_imgs("limitations") + sec_imgs("edge_cases")

    after = _items(data.get("after_you_finish"))
    out += ["## After You Finish", ""]
    out += ([f"- {a}" for a in after] + [""]) if after else [GAP, ""]
    if not after:
        gaps.append("After You Finish")

    related = [r for r in data.get("related_pages") or [] if isinstance(r, dict) and _clean(r.get("title")) and _clean(r.get("url"))]
    if related:
        out += ["## Related Pages", ""] + [f"- [{_clean(r['title'])}]({_clean(r['url'])})" for r in related] + [""]

    # A doc asset is never dropped: anything left goes at the end of How To.
    leftover = [a for a in doc_assets if a not in used]
    if leftover:
        at = out.index("## Behaviour and Rules")
        extra = []
        for a in leftover:
            extra += img(a)
        out[at:at] = extra

    filename = Path(str(data.get("filename") or "doc.md")).name
    if not filename.endswith(".md"):
        filename += ".md"
    description = " ".join(_clean(data.get("description")).split())
    return ({
        "filename": filename,
        "frontmatter": f"---\ndescription: >-\n  {description}\n---",
        "content": "\n".join(out).rstrip() + "\n",
        "target_path": str(data.get("target_path") or ""),
    }, gaps)


def render_master(title: str, summary: str, children: list[dict], navigation: str = "",
                  prerequisites: list[str] = (), problem: str = "", related: list[dict] = ()) -> str:
    """A section's parent overview page. children: [{"title", "path", "summary"}]."""
    out = [f"# {title}", "", "## Overview", "", _clean(summary) or GAP, ""]
    if problem:
        out += ["## Problem Statement", "", _clean(problem), ""]
    out += ["## What's in This Section", ""]
    for c in children:
        line = f"- [{c['title']}]({c['path']})"
        if c.get("summary"):
            line += f": {_clean(c['summary'])}"
        out.append(line)
    out.append("")
    if navigation:
        out += ["## Navigation", "", _clean(navigation), ""]
    if prerequisites:
        out += ["## Common Prerequisites", ""] + [f"- {_clean(p)}" for p in prerequisites] + [""]
    if related:
        out += ["## Related Pages", ""] + [f"- [{r['title']}]({r['url']})" for r in related] + [""]
    return "\n".join(out).rstrip() + "\n"
