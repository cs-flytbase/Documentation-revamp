"""The release note template, enforced by code.

The model never writes release note Markdown. It returns the template's
fields as JSON, and render() builds the page. Every release note therefore
has the same structure, in the same order, and cannot grow a constraints
table, a troubleshooting section or a 3000-character walkthrough.

Brevity comes from the size of each field (a few sentences, a few steps),
not from a limit on the whole note: a release with eight features is long,
and nothing is cut to make it fit.

    ---
    description: <one sentence, max 150 characters>
    ---
    # <Title>
    {% embed url="<video>" %}              only if a video was given

    <Summary: 2-3 sentences>

    ## What's New
    ### <Feature>                          omitted when there is one feature
    <1-3 sentences: what it does and why it matters>
    **How to use it:** <one line>  |  numbered steps (up to 5)
    ![alt](assets/<file>)

    ## Good to Know                        only if there are points
    - <key requirement or limit>           up to 5

    ## Learn More                          only when a doc page is published
    [<Doc page title>](<link>)
"""
import re
from pathlib import Path

# The publisher replaces this with the doc page's live URL once it knows where
# the page is placed, or removes the Learn More section if there is no page.
DOC_LINK_PLACEHOLDER = "{{DOC_PAGE_URL}}"
LEARN_MORE_HEADING = "## Learn More"

MAX_FEATURE_SENTENCES = 3
MAX_STEPS = 5
MAX_GOOD_TO_KNOW = 5

RELEASE_NOTE_PROMPT = r"""You are the Drafting Agent for the FlytBase documentation pipeline.
You are writing a RELEASE NOTE: a brief overview of what was released and how to use it.

The release note is NOT the documentation. The doc page carries every technical
detail (full steps, constraints, troubleshooting); the release note links to it.
Keep each field brief, but do not drop any feature or anything a user needs in
order to start using it.

You do not write Markdown or choose the structure. Fill in the fields below as
JSON; the page is built from them by code in a fixed template.

{
  "filename": "feature-slug.md",
  "title": "Plain title naming what was released, e.g. 'Cockpit 3.0: Zone Overlays, Proximity Warnings and Live Annotations'",
  "description": "One sentence, at most 150 characters, saying what is new.",
  "summary": "2-3 sentences: what is new and who it is for.",
  "features": [
    {
      "name": "Feature name as it appears in the product",
      "what": "1-3 sentences: what it does and why it matters to the operator.",
      "how_to_use": ["Short step", "Short step"],
      "assets": [{"file": "exact-asset-filename.gif", "alt": "What the image or GIF shows"}]
    }
  ],
  "good_to_know": ["Only key requirements or limits a user must know before using it."]
}

Field rules:
- features: one entry per feature or change in the source, in the source's order.
  Never merge two features into one entry or drop one.
- what: at most 3 sentences. Facts from the source only.
- how_to_use: the shortest way to start using it, as at most 5 short steps
  (or a single step). Exact on-screen labels in **bold**. Leave out options,
  edge cases and settings detail; those belong on the doc page.
- assets: attach every asset from the ASSET MAP to the feature it shows, using
  the exact filename. Each asset is used exactly once.
- good_to_know: 0-5 short bullets, only requirements or limits that would stop
  someone from using the feature. Omit it ([]) when there are none.
- No tables, headings, horizontal rules, hint blocks or video embeds inside any
  field. The template adds the video automatically.

Return ONLY the JSON object. No markdown wrapping.
"""


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def _clean_field(text: str) -> str:
    """Fields are prose: strip structure the model may have slipped in."""
    text = (text or "").strip()
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)              # headings
    text = re.sub(r"^\s*(---|\*\*\*)\s*$", "", text, flags=re.M)     # rules
    text = re.sub(r"\{%.*?%\}", "", text, flags=re.S)                # directives
    return re.sub(r"\n{2,}", "\n", text).strip()


def check_fields(data: dict, asset_filenames: list[str]) -> list[str]:
    """Problems with the model's fields, phrased for a targeted retry."""
    problems = []
    for key in ("title", "description", "summary"):
        if not str(data.get(key, "")).strip():
            problems.append(f'release note field "{key}" is empty')
    if len(str(data.get("description", ""))) > 150:
        problems.append(f'release note description is {len(data["description"])} characters (max 150)')
    features = data.get("features") or []
    if not features:
        problems.append("release note has no features")
    used = []
    for f in features:
        name = f.get("name", "?")
        if len(_sentences(f.get("what", ""))) > MAX_FEATURE_SENTENCES:
            problems.append(f'release note feature "{name}": "what" has more than {MAX_FEATURE_SENTENCES} sentences')
        steps = f.get("how_to_use") or []
        if isinstance(steps, str):
            steps = [steps]
        if len(steps) > MAX_STEPS:
            problems.append(f'release note feature "{name}": {len(steps)} steps (max {MAX_STEPS}); keep only the essentials')
        for a in f.get("assets") or []:
            used.append(a.get("file") if isinstance(a, dict) else a)
    if len(data.get("good_to_know") or []) > MAX_GOOD_TO_KNOW:
        problems.append(f"release note good_to_know has more than {MAX_GOOD_TO_KNOW} points")
    for missing in [a for a in asset_filenames if a not in used]:
        problems.append(f"asset '{missing}' missing from release note (attach it to the feature it shows)")
    for unknown in [u for u in used if u not in asset_filenames]:
        problems.append(f"release note uses unknown asset '{unknown}' (use only ASSET MAP filenames)")
    return problems


def render(data: dict, asset_filenames: list[str], youtube_link: str = "",
           doc_title: str = "", asset_descriptions: dict | None = None) -> dict:
    """Build the release note. Returns {"filename", "frontmatter", "content"}.

    Every asset is embedded exactly once: assets the model did not attach go
    under the last feature with their vision description as alt text, so a
    GIF can never silently disappear.
    """
    asset_descriptions = asset_descriptions or {}
    title = _clean_field(data.get("title", "")) or "Release Note"
    description = " ".join(_clean_field(data.get("description", "")).split())
    summary = _clean_field(data.get("summary", ""))
    features = [f for f in (data.get("features") or []) if isinstance(f, dict)]

    placed = set()
    for f in features:
        kept = []
        for a in f.get("assets") or []:
            file = a.get("file") if isinstance(a, dict) else a
            if file in asset_filenames and file not in placed:
                kept.append({"file": file, "alt": (a.get("alt") if isinstance(a, dict) else "") or asset_descriptions.get(file, "")})
                placed.add(file)
        f["_assets"] = kept
    leftover = [a for a in asset_filenames if a not in placed]
    if leftover:
        if not features:
            features = [{"name": title, "what": "", "how_to_use": []}]
        features[-1].setdefault("_assets", [])
        features[-1]["_assets"] += [{"file": a, "alt": asset_descriptions.get(a, "")} for a in leftover]

    out = [f"# {title}", ""]
    if youtube_link:
        out += [f'{{% embed url="{youtube_link}" %}}', ""]
    if summary:
        out += [summary, ""]

    out += ["## What's New", ""]
    single = len(features) == 1
    for f in features:
        if not single:
            out += [f"### {_clean_field(f.get('name', '')) or 'Update'}", ""]
        what = _clean_field(f.get("what", ""))
        if what:
            out += [what, ""]
        steps = f.get("how_to_use") or []
        if isinstance(steps, str):
            steps = [steps]
        steps = [_clean_field(re.sub(r"^\s*(\d+[.)]|[-*])\s+", "", s)) for s in steps if str(s).strip()]
        if len(steps) == 1:
            out += [f"**How to use it:** {steps[0]}", ""]
        elif steps:
            out += ["**How to use it:**", ""] + [f"{i}. {s}" for i, s in enumerate(steps, 1)] + [""]
        for a in f.get("_assets", []):
            alt = " ".join((a["alt"] or Path(a["file"]).stem.replace("-", " ")).split()).replace("]", ")").replace("[", "(")
            out += [f"![{alt}](assets/{a['file']})", ""]

    good = [_clean_field(g) for g in (data.get("good_to_know") or []) if str(g).strip()]
    if good:
        out += ["## Good to Know", ""] + [f"- {g}" for g in good] + [""]

    if doc_title:
        out += [LEARN_MORE_HEADING, "", f"For the full details, see [{doc_title}]({DOC_LINK_PLACEHOLDER}).", ""]

    filename = Path(str(data.get("filename") or "release-note.md")).name
    if not filename.endswith(".md"):
        filename += ".md"
    return {
        "filename": filename,
        "frontmatter": f"---\ndescription: >-\n  {description}\n---",
        "content": "\n".join(out).rstrip() + "\n",
    }


def finalize_doc_link(content: str, url: str | None) -> str:
    """Put the doc page's URL into Learn More, or drop the section if there is none."""
    if DOC_LINK_PLACEHOLDER not in content:
        return content
    if url:
        return content.replace(DOC_LINK_PLACEHOLDER, url)
    return re.sub(rf"\n?{re.escape(LEARN_MORE_HEADING)}\n.*?(?=\n## |\Z)", "\n", content, flags=re.S).rstrip() + "\n"


def docs_url(repo_path: str) -> str:
    """Live URL of a docs repo page: path without .md, README is its folder."""
    path = re.sub(r"(^|/)README\.md$", "", repo_path)
    path = re.sub(r"\.md$", "", path)
    return f"https://docs.flytbase.com/{path}".rstrip("/")
