"""The documentation template, enforced by code.

A doc page is the technical reference: problem, use cases, prerequisites
and dependencies, navigation, the main controls, how to do each task, the
key rules, limitations and troubleshooting, what happens afterwards. It is
read by people and very often by LLMs, so every section must stand on its
own. (Senior review, 2026-10-19.)

Concise, not exhaustive (review of the first Cockpit 3.0 page, 2026-10-19):
the page listed 60 interface rows and 80 rules, most of them repeating the
steps. So the reference sections have hard caps, enforced here: the model
is told to pick what matters, check_fields sends it back if it overshoots,
and render() cuts anything past the cap.

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
    ## Interface Reference                   table: Control | What It Does (main controls only, max 10)
    ## How To  (### <Task>, numbered steps, images right after their step)
    ## Behaviour and Rules                   one list, max 10
    ## Limitations, Edge Cases and Troubleshooting  (### Limitations, max 5 / ### Troubleshooting table, max 6)
    ## After You Finish
    ## Related Pages

Master page (a section's parent overview): Overview, Problem Statement,
What's in This Section, Navigation, Common Prerequisites, Related Pages.
"""
import re
from pathlib import Path

GAP = "Not covered in the source material."

# Hard caps per page (see the module docstring).
MAX_CONTROLS = 10
MAX_RULES = 10
MAX_LIMITATIONS = 5
MAX_TROUBLESHOOTING = 6
MAX_WORDS = 30

DOC_PAGE_PROMPT = r"""You are the Drafting Agent for the FlytBase documentation pipeline.
You are writing a DOC PAGE: the technical reference for a feature.

It is NOT marketing. It is read by operators and often by LLMs answering
questions, so it must be precise and unambiguous, and every section must make
sense on its own. It must also be SHORT ENOUGH TO READ. Do not try to capture
every detail the source gives: pick what an operator needs to use the feature
correctly and safely, and leave out the rest. Say each fact once, in the
section where it belongs; never repeat in one section what another already says.

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
     "what_it_does": "One short sentence: what it does, and where it is if that is not obvious"}
  ],
  "how_to": [
    {"task": "Task name (e.g. Show or Hide No-Fly Zones)",
     "steps": [{"text": "**Action verb** the **Element**. What happens as a result.", "asset": "exact-asset-filename or empty"}]}
  ],
  "rules": ["One key rule or behaviour: a default, threshold, limit or condition that changes what the operator does"],
  "limitations": ["One thing the feature does not do, or a hard constraint"],
  "troubleshooting": [{"problem": "What the operator sees", "fix": "What to do about it"}],
  "after_you_finish": ["What persists, what resets, follow-up actions, cleanup."],
  "related_pages": [{"title": "Page title", "url": "relative path or docs URL"}],
  "section_assets": {"overview": ["exact-asset-filename"], "navigation": [], "interface_reference": []},
  "impacted_page_edits": []
}

Field rules (the caps are per page and are enforced; order each list by importance):
- interface_reference: only the MAIN controls, at most {max_controls}: the toggles,
  buttons and menus an operator actually uses. Not every option inside a menu
  (list the menu once and name its options in the sentence), not colours,
  symbols, status labels or indicators, not fields that only display a value.
- rules: at most {max_rules}, one sentence each. Defaults, thresholds, limits and
  conditions only. Do not restate a step from how_to or a row from interface_reference.
- limitations: at most {max_limitations}, one sentence each.
- troubleshooting: at most {max_troubleshooting} rows, the problems an operator is most likely to hit.
- Every bullet, row and cell: at most {max_words} words.
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
DOC_PAGE_PROMPT = (DOC_PAGE_PROMPT.replace("{max_controls}", str(MAX_CONTROLS)).replace("{max_rules}", str(MAX_RULES))
                   .replace("{max_limitations}", str(MAX_LIMITATIONS)).replace("{max_troubleshooting}", str(MAX_TROUBLESHOOTING))
                   .replace("{max_words}", str(MAX_WORDS)))



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


def _limitations(data: dict) -> list[str]:
    """Limitations as sentences. Older drafts used {component, details} rows."""
    out = []
    for v in data.get("limitations") or []:
        if isinstance(v, dict):
            v = ": ".join(x for x in (_cell(v.get("component")), _cell(v.get("details"))) if x)
        v = _cell(v)
        if v:
            out.append(v)
    return out


def _troubleshooting(data: dict) -> list[tuple[str, str]]:
    rows = []
    for r in data.get("troubleshooting") or []:
        if isinstance(r, dict) and _cell(r.get("problem")):
            rows.append((_cell(r.get("problem")), _cell(r.get("fix"))))
    return rows


def _controls(data: dict) -> list[tuple[str, str]]:
    return [(_cell(r.get("element")), _cell(r.get("what_it_does")))
            for r in data.get("interface_reference") or [] if isinstance(r, dict) and _cell(r.get("element"))]


def _words(text: str) -> int:
    return len(re.sub(r"\*\*", "", text).split())


def check_fields(data: dict, doc_assets: list[str], require_all: bool = True) -> list[str]:
    """Problems with the model's fields, phrased for a targeted retry.

    require_all is False for one page of a subsections run: that page may use
    any of the run's assets but only needs the ones that show its feature.
    """
    problems = []
    for key in ("title", "description", "overview"):
        if not _clean(data.get(key)):
            problems.append(f'doc page field "{key}" is empty')
    if len(str(data.get("description", ""))) > 150:
        problems.append(f'doc page description is {len(str(data["description"]))} characters (max 150)')
    sizes = {
        "interface_reference": (len(_controls(data)), MAX_CONTROLS, "main controls only"),
        "rules": (len(_items(data.get("rules"))), MAX_RULES, "keep defaults, thresholds, limits and conditions; drop anything the steps already say"),
        "limitations": (len(_limitations(data)), MAX_LIMITATIONS, "keep the most important"),
        "troubleshooting": (len(_troubleshooting(data)), MAX_TROUBLESHOOTING, "keep the most likely problems"),
    }
    for key, (n, cap, hint) in sizes.items():
        if n > cap:
            problems.append(f"doc page {key} has {n} entries (max {cap}): {hint}")
    long = [t for t in _items(data.get("rules")) + _limitations(data)
            + [c for row in _controls(data) + _troubleshooting(data) for c in row] if _words(t) > MAX_WORDS]
    for t in long[:5]:
        problems.append(f'doc page entry has {_words(t)} words (max {MAX_WORDS}), shorten it: "{t[:60]}..."')
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
    for a in (doc_assets if require_all else []):
        if a not in used:
            problems.append(f"asset '{a}' missing from doc page (put it on the step or section it shows)")
    for a in {x for x in used if used.count(x) > 1}:
        problems.append(f"doc page uses asset '{a}' more than once")
    return problems


def render(data: dict, doc_assets: list[str], youtube_link: str = "",
           asset_descriptions: dict | None = None, place_leftovers: bool = True) -> tuple[dict, list[str]]:
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

    rows = _controls(data)[:MAX_CONTROLS]
    out += ["## Interface Reference", ""]
    if rows:
        out += ["| Control | What It Does |", "|---|---|"] + [f"| {e} | {w} |" for e, w in rows] + [""]
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

    rules = _items(data.get("rules"))[:MAX_RULES]
    out += ["## Behaviour and Rules", ""]
    out += ([f"- {r}" for r in rules] + [""]) if rules else [GAP, ""]
    if not rules:
        gaps.append("Behaviour and Rules")
    out += sec_imgs("rules")

    out += ["## Limitations, Edge Cases and Troubleshooting", ""]
    lim = _limitations(data)[:MAX_LIMITATIONS]
    out += ["### Limitations", ""]
    out += ([f"- {l}" for l in lim] + [""]) if lim else [GAP, ""]
    if not lim:
        gaps.append("Limitations")
    trouble = _troubleshooting(data)[:MAX_TROUBLESHOOTING]
    out += ["### Troubleshooting", ""]
    if trouble:
        out += ["| Problem | What to Do |", "|---|---|"] + [f"| {p} | {f} |" for p, f in trouble] + [""]
    else:
        out += [GAP, ""]
        gaps.append("Troubleshooting")
    out += sec_imgs("limitations") + sec_imgs("troubleshooting")

    after = _items(data.get("after_you_finish"))
    out += ["## After You Finish", ""]
    out += ([f"- {a}" for a in after] + [""]) if after else [GAP, ""]
    if not after:
        gaps.append("After You Finish")

    related = [r for r in data.get("related_pages") or [] if isinstance(r, dict) and _clean(r.get("title")) and _clean(r.get("url"))]
    if related:
        out += ["## Related Pages", ""] + [f"- [{_clean(r['title'])}]({_clean(r['url'])})" for r in related] + [""]

    # A doc asset is never dropped: anything left goes at the end of How To.
    # Not on one page of a subsections run: there, an unused asset belongs to
    # another feature's page, not at the end of this one.
    leftover = [a for a in doc_assets if a not in used] if place_leftovers else []
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
