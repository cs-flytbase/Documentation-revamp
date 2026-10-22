"""The documentation master template, enforced by code.

A doc page is the absolute truth about how a feature works and how to use it.
It is read by pilots, operators, admins and solution integrators, and often by
LLMs answering a single question. Nothing in it is there to persuade.
(Senior review 2026-10-19; master template 2026-10-22.)

The model fills in fields; render() builds the page in a fixed order. The
master template has 20 sections. Material rarely covers all of them, so:

- Only Overview and at least one Procedure are required. Without them there
  is nothing to publish (check_fields sends the draft back).
- Every other section is rendered only when it has content. An empty section
  is left out entirely: no heading, no placeholder. It is listed as omitted
  for the reviewer, and Son of Anton asks about it before the run.

Detail is not limited. Redundancy is: every fact has exactly one home section
(see SECTION_HOMES), and src/doc_checks.py sends the draft back when the
same sentence or the same number shows up in two sections.

Page (sections in this order, each only if it has content):
    # <Title>
    {% embed %}                     video first, if given
    ## Overview                     required
    ## Use Cases                    ### When to Use / ### When Not to Use
    ## Availability
    ## Compatibility
    ## Prerequisites
    ## Dependencies
    ## Roles and Permissions
    ## Navigation
    ## Controls and Indicators      table: Control | Description
    ## Procedures                   required; ### <Task>, steps, images after their step
    ## Expected Behavior
    ## Operating Constraints
    ## Edge Cases                   table: Condition | What Happens
    ## System Messages              table: Message | Meaning | Action
    ## Frequently Asked Questions   bold question, then the answer
    ## After Completion
    ## Glossary                     table: Term | Definition
    ## Related Pages

Master page (a section's parent overview): Overview, Problem Statement,
What's in This Section, Navigation, Common Prerequisites, Related Pages.
"""
import re
from pathlib import Path

# Where each kind of fact lives. A fact is written once, in its home; other
# sections refer to it ("see Expected Behavior") instead of restating it.
SECTION_HOMES = {
    "How to do something": "Procedures",
    "What a button, toggle, icon or on-screen indicator is": "Controls and Indicators",
    "Defaults, thresholds, timings and rules": "Expected Behavior",
    "What the feature cannot do or does not support": "Operating Constraints",
    "Unusual conditions and what the system does in them": "Edge Cases",
    "On-screen messages, what they mean and what to do": "System Messages",
    "Questions a user would actually ask": "Frequently Asked Questions",
}

# (field, heading) in render order. Required sections are marked.
SECTIONS = [
    ("overview", "Overview"),
    ("use_cases", "Use Cases"),
    ("availability", "Availability"),
    ("compatibility", "Compatibility"),
    ("prerequisites", "Prerequisites"),
    ("dependencies", "Dependencies"),
    ("roles_and_permissions", "Roles and Permissions"),
    ("navigation", "Navigation"),
    ("controls_and_indicators", "Controls and Indicators"),
    ("procedures", "Procedures"),
    ("expected_behavior", "Expected Behavior"),
    ("operating_constraints", "Operating Constraints"),
    ("edge_cases", "Edge Cases"),
    ("system_messages", "System Messages"),
    ("faqs", "Frequently Asked Questions"),
    ("after_completion", "After Completion"),
    ("glossary", "Glossary"),
    ("related_pages", "Related Pages"),
]
REQUIRED = ("overview", "procedures")
MAX_FAQ_SENTENCES = 2

_HOMES = "\n".join(f"  - {fact}: {home}" for fact, home in SECTION_HOMES.items())

DOC_PAGE_PROMPT = r"""You are the Drafting Agent for the FlytBase documentation pipeline.
You are writing a DOC PAGE: the absolute truth about how a feature works and how to use it.

It is read by pilots, operators, admins and solution integrators, and often by
LLMs answering a single question. It is NOT marketing and never persuades.
Include every detail the source material gives: every procedure, rule,
threshold, constraint, edge case, on-screen message and question. Detail is
welcome. Repetition is not: state every fact exactly ONCE, in its home section.

You do not write Markdown or choose the structure. Fill in the fields below as
JSON; the page is built from them by code in a fixed template. A field the
source material does not cover stays empty ("" or []), and its section is left
off the page. NEVER invent content to fill a field, and never guess.

{
  "filename": "feature-slug.md",
  "target_path": "IA path for the page, e.g. in-flight-modules",
  "title": "Feature name",
  "description": "One sentence, at most 150 characters: what this page documents.",
  "overview": "REQUIRED. One paragraph: what the feature is and what problem it solves.",
  "when_to_use": ["Situation in which to use it"],
  "when_not_to_use": ["Situation in which not to use it, and what to use instead"],
  "availability": "Feature flag, plan, cloud or on-premises availability, and how to get access.",
  "compatibility": ["Supported dock, drone, payload or camera"],
  "prerequisites": ["What must be true before starting: account, setting, hardware state"],
  "dependencies": ["What the feature relies on, and what happens without it"],
  "roles_and_permissions": ["Who can use or configure it"],
  "navigation": ["**Exact > Path > To > It**, plus every other entry point"],
  "controls_and_indicators": [{"name": "**Exact on-screen label**, or a consistent name for an icon", "description": "What it is, where it is, what it does"}],
  "procedures": [
    {"task": "REQUIRED, at least one. Task name, e.g. Show Zones on the Video",
     "steps": [{"text": "**Action verb** the **Element**. What happens as a result.", "asset": "exact-asset-filename or empty"}]}
  ],
  "expected_behavior": ["One default, threshold, timing or rule: If <condition>, <system> <does what>."],
  "operating_constraints": ["One thing the feature cannot do or does not support"],
  "edge_cases": [{"condition": "An unusual condition", "behavior": "What the system does"}],
  "system_messages": [{"message": "Exact on-screen text", "meaning": "What it means", "action": "What to do"}],
  "faqs": [{"question": "A question a user would actually ask?", "answer": "At most two sentences; refer to the home section instead of restating it."}],
  "after_completion": ["What stays saved, what resets, follow-up actions"],
  "glossary": [{"term": "Term specific to this feature", "definition": "One sentence"}],
  "related_pages": [{"title": "Page title", "url": "relative path or docs URL"}],
  "section_assets": {"overview": ["exact-asset-filename"], "navigation": [], "controls_and_indicators": []},
  "impacted_page_edits": []
}

WHERE EACH FACT LIVES (write it there and nowhere else):
__HOMES__
Other sections refer to the home ("see Expected Behavior") instead of restating a
fact. A number with its unit (40 m, 5 km, 0.5 seconds) appears in ONE section only.

TONE AND STYLE
- Neutral and factual. Describe what the system does, not why it is good. No
  benefit language, no marketing words, no "simply", "just" or "easily".
- The system is the subject: "Cockpit displays...", "The drone returns...".
  Address the reader directly only in procedures, where steps are commands.
- Condition first: "If the dock is offline, Cockpit hides **Go to location**."
- Every statement can be checked: exact numbers with units, exact on-screen
  labels in bold, exact system messages in quotes. Hedge only where the source
  does ("about 4 seconds" stays "about"); never add "may" on your own.
- One fact per sentence, under 25 words. In procedures, one action per step,
  followed by its result.
- One name per thing, throughout the page. Once it is "No-Fly Zone (NFZ)", it
  never becomes "restricted area".
- Present tense, active voice. Spell out acronyms on first use.
- Every section makes sense on its own: name the feature instead of "this".
- US English spelling: color, behavior, center, meter, organization.

Field rules:
- procedures: one task per thing a user does. Steps are atomic.
- Assets: use only filenames from DOC ASSETS. Every DOC ASSET must be used
  exactly once: on the step it illustrates, or in section_assets for the
  section it illustrates.
- No headings, tables, horizontal rules or video embeds inside any field.

impacted_page_edits (cross-references in existing pages):
[{"source_url": "https://docs.flytbase.com/...", "edit_description": "...",
  "section_heading": "## Exact Existing Heading", "patch_mode": "append",
  "patch_content": "\n### Related Features\n\n- [Title](relative/path.md): one line.\n"}]
section_heading must be the EXACT heading from the existing page. Use "append" for cross-references.

Return ONLY the JSON object. No markdown wrapping.
""".replace("__HOMES__", _HOMES)

GAP = "Not covered in the source material."  # kept for callers; no longer printed on pages


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
        if isinstance(v, dict):
            v = ": ".join(str(x) for x in v.values() if x)
        v = re.sub(r"^\s*(\d+[.)]|[-*])\s+", "", _clean(v))
        if v:
            out.append(v)
    return out


def _rows(value, *keys) -> list[tuple]:
    """Table rows from a list of dicts; a row needs its first key."""
    rows = []
    for r in value or []:
        if isinstance(r, dict) and _cell(r.get(keys[0])):
            rows.append(tuple(_cell(r.get(k)) for k in keys))
    return rows


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


# Alt text is one short sentence of what the image shows. The full vision
# description (with values read off the screen) stays drafting context; on
# the page it read as claims and the fact-checker flagged every one of them
# (2026-10-22, Terrain Aware Grid PRs).
ALT_MAX_WORDS = 15


def _alt(text, file):
    alt = " ".join(str(text or Path(file).stem.replace("-", " ").replace("_", " ")).split())
    alt = re.split(r"(?<=[.!?])\s+", alt, maxsplit=1)[0]
    # Only the lead clause: lists of values read off the screen follow a comma.
    alt = re.split(r"\s*[,;:(]\s*|\s+-\s+", alt, maxsplit=1)[0]
    words = alt.split()
    if len(words) > ALT_MAX_WORDS:
        alt = " ".join(words[:ALT_MAX_WORDS]).rstrip(",;:")
    return alt.rstrip(".").replace("[", "(").replace("]", ")")


def _procedures(data: dict) -> list[dict]:
    return [t for t in data.get("procedures") or data.get("how_to") or []
            if isinstance(t, dict) and _clean(t.get("task"))]


def section_text(data: dict) -> dict[str, list[str]]:
    """The prose of each section, as the redundancy checks see it."""
    out = {
        "Overview": [_clean(data.get("overview"))],
        "Use Cases": _items(data.get("when_to_use")) + _items(data.get("when_not_to_use")),
        "Availability": [_clean(data.get("availability"))],
        "Compatibility": _items(data.get("compatibility")),
        "Prerequisites": _items(data.get("prerequisites")),
        "Dependencies": _items(data.get("dependencies")),
        "Roles and Permissions": _items(data.get("roles_and_permissions")),
        "Navigation": _items(data.get("navigation")),
        "Controls and Indicators": [f"{n} {d}" for n, d in _rows(data.get("controls_and_indicators"), "name", "description")],
        "Procedures": [_clean(s.get("text") if isinstance(s, dict) else s)
                       for t in _procedures(data) for s in t.get("steps") or []],
        "Expected Behavior": _items(data.get("expected_behavior")),
        "Operating Constraints": _items(data.get("operating_constraints")),
        "Edge Cases": [f"{c}. {b}" for c, b in _rows(data.get("edge_cases"), "condition", "behavior")],
        "System Messages": [f"{m} {x} {a}" for m, x, a in _rows(data.get("system_messages"), "message", "meaning", "action")],
        "Frequently Asked Questions": [a for _, a in _rows(data.get("faqs"), "question", "answer")],
        "After Completion": _items(data.get("after_completion")),
        "Glossary": [f"{t} {d}" for t, d in _rows(data.get("glossary"), "term", "definition")],
    }
    return {k: [t for t in v if t] for k, v in out.items()}


def check_fields(data: dict, doc_assets: list[str], require_all: bool = True) -> list[str]:
    """Problems with the model's fields, phrased for a targeted retry.

    require_all is False for one page of a subsections run: that page may use
    any of the run's assets but only needs the ones that show its feature.
    """
    from src.doc_checks import redundancy_problems

    problems = []
    for key in ("title", "description", "overview"):
        if not _clean(data.get(key)):
            problems.append(f'doc page field "{key}" is empty (required)')
    if not _procedures(data):
        problems.append('doc page has no procedures (at least one is required)')
    if len(str(data.get("description", ""))) > 150:
        problems.append(f'doc page description is {len(str(data["description"]))} characters (max 150)')
    for q, a in _rows(data.get("faqs"), "question", "answer"):
        n = len([s for s in re.split(r"(?<=[.!?])\s+", a) if s])
        if n > MAX_FAQ_SENTENCES:
            problems.append(f'FAQ "{q[:50]}" has a {n}-sentence answer (max {MAX_FAQ_SENTENCES}): '
                            "refer to the home section instead of restating it")
    problems += redundancy_problems(data)
    used = []
    for task in _procedures(data):
        for step in task.get("steps") or []:
            if isinstance(step, dict) and step.get("asset"):
                used.append(step["asset"])
    for files in (data.get("section_assets") or {}).values():
        used += [f for f in files or [] if f]
    for a in used:
        if a not in doc_assets:
            problems.append(f"doc page uses asset '{a}' that is not in DOC ASSETS")
    for a in (doc_assets if require_all else []):
        if a not in used:
            problems.append(f"asset '{a}' missing from doc page (put it on the step or section it shows)")
    for a in {x for x in used if used.count(x) > 1}:
        problems.append(f"doc page uses asset '{a}' more than once")
    return problems


def render(data: dict, doc_assets: list[str], youtube_link: str = "",
           asset_descriptions: dict | None = None, place_leftovers: bool = True) -> tuple[dict, list[str]]:
    """Build the doc page. Returns ({"filename","frontmatter","content","target_path"}, omitted).

    omitted lists the master template sections left out because the material
    did not cover them; the PR shows it to the reviewer.
    """
    descs = asset_descriptions or {}
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

    def bullets(items):
        return [f"- {i}" for i in items] + [""]

    def table(header, rows):
        cols = len(header)
        return (["| " + " | ".join(header) + " |", "|" + "---|" * cols]
                + ["| " + " | ".join(r) + " |" for r in rows] + [""])

    body: dict[str, list[str]] = {}
    overview = _clean(data.get("overview"))
    if overview:
        body["overview"] = [overview, ""]
    use, avoid = _items(data.get("when_to_use")), _items(data.get("when_not_to_use"))
    if use or avoid:
        body["use_cases"] = (["### When to Use", ""] + bullets(use) if use else []) + \
                            (["### When Not to Use", ""] + bullets(avoid) if avoid else [])
    availability = _clean(data.get("availability"))
    if availability:
        body["availability"] = [availability, ""]
    for key in ("compatibility", "prerequisites", "dependencies", "roles_and_permissions", "navigation",
                "expected_behavior", "operating_constraints", "after_completion"):
        items = _items(data.get(key))
        if items:
            body[key] = bullets(items)
    controls = _rows(data.get("controls_and_indicators"), "name", "description")
    if controls:
        body["controls_and_indicators"] = table(["Control", "Description"], controls)
    procs = []
    for t in _procedures(data):
        procs += [f"### {title_case(t.get('task'))}", ""]
        n = 0
        for step in t.get("steps") or []:
            text = _clean(step.get("text") if isinstance(step, dict) else step)
            text = re.sub(r"^\s*\d+[.)]\s+", "", text)
            if not text:
                continue
            n += 1
            procs += [f"{n}. {text}", ""]
            if isinstance(step, dict):
                procs += img(step.get("asset"), text)
    if procs:
        body["procedures"] = procs
    edges = _rows(data.get("edge_cases"), "condition", "behavior")
    if edges:
        body["edge_cases"] = table(["Condition", "What Happens"], edges)
    messages = _rows(data.get("system_messages"), "message", "meaning", "action")
    if messages:
        body["system_messages"] = table(["Message", "Meaning", "Action"],
                                        [(f'"{m.strip(chr(34))}"', x, a) for m, x, a in messages])
    faqs = _rows(data.get("faqs"), "question", "answer")
    if faqs:
        body["faqs"] = [line for q, a in faqs for line in (f"**{q.rstrip('?')}?**", "", a, "")]
    glossary = _rows(data.get("glossary"), "term", "definition")
    if glossary:
        body["glossary"] = table(["Term", "Definition"], glossary)
    related = [r for r in data.get("related_pages") or [] if isinstance(r, dict) and _clean(r.get("title")) and _clean(r.get("url"))]
    if related:
        body["related_pages"] = [f"- [{_clean(r['title'])}]({_clean(r['url'])})" for r in related] + [""]

    sections, omitted = [], []
    for key, heading in SECTIONS:
        content = body.get(key, []) + sec_imgs(key)
        if not body.get(key) and key != "related_pages":
            omitted.append(heading)
        if content:
            sections.append([key, heading, content])

    # A doc asset is never dropped: anything not placed yet (forgotten, or put
    # on a section name that does not exist) goes at the end of Procedures.
    # Not on one page of a subsections run: there, an unused asset belongs to
    # another feature's page.
    if place_leftovers:
        extra = []
        for a in doc_assets:
            extra += img(a)
        if extra:
            proc = next((s for s in sections if s[0] == "procedures"), None)
            if proc:
                proc[2] += extra
            else:
                sections.append(["procedures", "Procedures", extra])

    title = _clean(data.get("title")) or "Untitled"
    out = [f"# {title}", ""]
    if youtube_link:
        out += [f'{{% embed url="{youtube_link}" %}}', ""]
    for _, heading, content in sections:
        out += [f"## {heading}", ""] + content

    filename = Path(str(data.get("filename") or "doc.md")).name
    if not filename.endswith(".md"):
        filename += ".md"
    description = " ".join(_clean(data.get("description")).split())
    return ({
        "filename": filename,
        "frontmatter": f"---\ndescription: >-\n  {description}\n---",
        "content": "\n".join(out).rstrip() + "\n",
        "target_path": str(data.get("target_path") or ""),
    }, omitted)


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
