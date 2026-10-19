"""The release note template, enforced by code.

A release note is a note, not documentation: it says what problem existed,
what was released, why the approach is worth it, who it is for and what the
reader walks away with. It has no steps, settings tables or troubleshooting;
those live on the doc page it links to. (Senior review, 2026-10-19.)

The model never writes release note Markdown. It returns the template's
fields as JSON and render() builds the page, so every release note has the
same structure in the same order:

    ---
    description: <one sentence>
    ---
    # <Title>
    {% embed url="<video>" %}                 only if a video was given
    ![hero](assets/<hero>)                    visual introduction, if an image is given
    ## The Problem
    ## Introducing <Feature>
    ### <Capability>  + image                   one per capability when there are several
    ## Why It Matters                         benefits of the approach (value proposition)
    ## Who It's For
    ## Key Takeaways                          what the reader walks away with
    For setup and full details, see [<doc page>](<link>).   only if a doc page is published
    ## Access                                 ONLY for features behind a feature flag

There is no length limit: each field is short, and a release with many
capabilities is allowed to be long.
"""
import re
from pathlib import Path

DOC_LINK_PLACEHOLDER = "{{DOC_PAGE_URL}}"
DOC_LINK_PREFIX = "For setup steps and full details, see "

MAX_PROBLEM_SENTENCES = 4
MAX_INTRO_SENTENCES = 4
MAX_CAPABILITY_SENTENCES = 3
MAX_LIST = 6

RELEASE_NOTE_PROMPT = r"""You are the Drafting Agent for the FlytBase documentation pipeline.
You are writing a RELEASE NOTE: a short, marketable note announcing what was released.

It is NOT documentation. It never contains steps, settings, tables, edge cases or
troubleshooting; the doc page has all of that and the release note links to it.
It answers: what problem existed, what was released, why this approach is worth
it, who it is for, and what the reader walks away with.

You do not write Markdown or choose the structure. Fill in the fields below as
JSON; the page is built from them by code in a fixed template.

{
  "filename": "feature-slug.md",
  "title": "Plain title naming what was released",
  "feature_name": "The feature or release name as used in the product",
  "description": "One sentence, at most 150 characters: what is new and the main benefit.",
  "problem": "2-4 sentences: what was hard, slow or impossible before, in the operator's terms.",
  "introduction": "2-4 sentences: what the feature is and what it does.",
  "capabilities": [
    {"name": "Capability name", "description": "1-3 sentences: what it does for the operator.", "asset": "exact-asset-filename.gif or empty"}
  ],
  "why_it_matters": [{"benefit": "Short benefit", "detail": "One sentence explaining it, concretely."}],
  "who_its_for": [{"audience": "Role or team", "value": "What changes for them, in one sentence."}],
  "key_takeaways": ["What the reader walks away with, one line each."],
  "hero_asset": "exact-asset-filename to show at the top, or empty",
  "access": "Only if the FEATURE FLAG block below names a flag: one or two sentences on how to get it enabled. Otherwise empty."
}

Field rules:
- capabilities: one entry per distinct capability in the source, in the source's order.
  Never merge two capabilities or drop one. A single-capability release has one entry.
- why_it_matters: 3-5 benefits of this approach. Concrete outcomes (fewer steps,
  faster response, one screen instead of two), never vague praise.
- who_its_for: 2-4 audiences actually named or clearly implied by the source.
- key_takeaways: 3-5 lines.
- Assets: use only filenames from RELEASE ASSETS. Every asset listed as REQUIRED
  must be used exactly once (as hero_asset or a capability asset). OPTIONAL assets
  may be used if they show a capability well. Never use one asset twice.
- Persuasive through concrete value, never through hype: no "game-changer",
  "seamless", "revolutionary", "imagine...", or invented numbers.
- No headings, tables, horizontal rules, hint blocks, step lists or video embeds
  inside any field. The template adds the video automatically.

Return ONLY the JSON object. No markdown wrapping.
"""


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s]


def _clean(text) -> str:
    """Fields are prose: strip structure the model may have slipped in."""
    text = str(text or "").strip()
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*(---|\*\*\*)\s*$", "", text, flags=re.M)
    text = re.sub(r"\{%.*?%\}", "", text, flags=re.S)
    text = re.sub(r"^\s*(\d+[.)]|[-*])\s+", "", text, flags=re.M)
    return re.sub(r"\s*\n\s*", " ", text).strip()


def _alt(text: str, file: str) -> str:
    alt = " ".join(str(text or Path(file).stem.replace("-", " ").replace("_", " ")).split())
    return alt.replace("[", "(").replace("]", ")")


def check_fields(data: dict, required_assets: list[str], optional_assets: list[str] = (),
                 feature_flag: str = "") -> list[str]:
    """Problems with the model's fields, phrased for a targeted retry."""
    problems = []
    for key in ("title", "description", "problem", "introduction"):
        if not _clean(data.get(key)):
            problems.append(f'release note field "{key}" is empty')
    if len(str(data.get("description", ""))) > 150:
        problems.append(f'release note description is {len(str(data["description"]))} characters (max 150)')
    if len(_sentences(data.get("problem"))) > MAX_PROBLEM_SENTENCES:
        problems.append(f'release note "problem" has more than {MAX_PROBLEM_SENTENCES} sentences')
    if len(_sentences(data.get("introduction"))) > MAX_INTRO_SENTENCES:
        problems.append(f'release note "introduction" has more than {MAX_INTRO_SENTENCES} sentences')
    caps = [c for c in (data.get("capabilities") or []) if isinstance(c, dict)]
    if not caps:
        problems.append("release note has no capabilities")
    for c in caps:
        if len(_sentences(c.get("description"))) > MAX_CAPABILITY_SENTENCES:
            problems.append(f'release note capability "{c.get("name", "?")}": more than {MAX_CAPABILITY_SENTENCES} sentences')
    for key, low in (("why_it_matters", 2), ("who_its_for", 1), ("key_takeaways", 2)):
        items = data.get(key) or []
        if len(items) < low:
            problems.append(f'release note "{key}" needs at least {low} entries')
        if len(items) > MAX_LIST:
            problems.append(f'release note "{key}" has more than {MAX_LIST} entries')
    allowed = set(required_assets) | set(optional_assets)
    used = [a for a in [data.get("hero_asset")] + [c.get("asset") for c in caps] if a]
    for a in used:
        if a not in allowed:
            problems.append(f"release note uses asset '{a}' that is not in RELEASE ASSETS")
    for a in required_assets:
        if a not in used:
            problems.append(f"asset '{a}' missing from release note (use it as hero_asset or a capability asset)")
    dupes = {a for a in used if used.count(a) > 1}
    for a in dupes:
        problems.append(f"release note uses asset '{a}' more than once")
    if not feature_flag and _clean(data.get("access")):
        problems.append('release note "access" must be empty: this feature is not behind a feature flag')
    return problems


def render(data: dict, required_assets: list[str], youtube_link: str = "", doc_title: str = "",
           asset_descriptions: dict | None = None, feature_flag: str = "",
           optional_assets: list[str] = ()) -> dict:
    """Build the release note. Returns {"filename", "frontmatter", "content"}.

    Every REQUIRED asset is embedded exactly once even if the model forgot it.
    The Access section exists only when a feature flag is known.
    """
    descs = asset_descriptions or {}
    allowed = set(required_assets) | set(optional_assets)
    title = _clean(data.get("title")) or "Release Note"
    feature = _clean(data.get("feature_name")) or title
    caps = [c for c in (data.get("capabilities") or []) if isinstance(c, dict)]

    used = set()

    def take(file):
        if file and file in allowed and file not in used:
            used.add(file)
            return file
        return None

    hero = take(data.get("hero_asset"))
    cap_assets = [take(c.get("asset")) for c in caps]
    missing = [a for a in required_assets if a not in used]
    for a in missing:  # attach forgotten required assets: hero first, then capabilities
        if not hero:
            hero = take(a)
            continue
        slot = next((i for i, x in enumerate(cap_assets) if not x), None)
        if slot is None:
            caps.append({"name": "", "description": ""})
            cap_assets.append(None)
            slot = len(caps) - 1
        cap_assets[slot] = take(a)

    out = [f"# {title}", ""]
    if youtube_link:
        out += [f'{{% embed url="{youtube_link}" %}}', ""]
    if hero:
        out += [f"![{_alt(descs.get(hero), hero)}](assets/{hero})", ""]

    out += ["## The Problem", "", _clean(data.get("problem")), ""]
    out += [f"## Introducing {feature}", "", _clean(data.get("introduction")), ""]
    multi = len([c for c in caps if _clean(c.get("name"))]) > 1
    for c, asset in zip(caps, cap_assets):
        name, desc = _clean(c.get("name")), _clean(c.get("description"))
        if multi and name:
            out += [f"### {name}", ""]
        if desc and (multi or desc != _clean(data.get("introduction"))):
            out += [desc, ""]
        if asset:
            out += [f"![{_alt(descs.get(asset) or name, asset)}](assets/{asset})", ""]

    def bullets(key, a, b):
        rows = []
        for item in data.get(key) or []:
            if isinstance(item, dict):
                head, tail = _clean(item.get(a)), _clean(item.get(b))
                if head:
                    rows.append(f"- **{head.rstrip(':.')}:** {tail}" if tail else f"- **{head}**")
            elif _clean(item):
                rows.append(f"- {_clean(item)}")
        return rows

    out += ["## Why It Matters", ""] + bullets("why_it_matters", "benefit", "detail") + [""]
    out += ["## Who It's For", ""] + bullets("who_its_for", "audience", "value") + [""]
    out += ["## Key Takeaways", ""] + bullets("key_takeaways", "", "") + [""]

    if doc_title:
        out += [f"{DOC_LINK_PREFIX}[{doc_title}]({DOC_LINK_PLACEHOLDER}).", ""]

    if feature_flag:
        access = _clean(data.get("access")) or (
            f"{feature} is behind the `{feature_flag}` feature flag and is not enabled by default. "
            "Contact your FlytBase account team to enable it for your organization.")
        if f"`{feature_flag}`" not in access and feature_flag not in access:
            access += f" Feature flag: `{feature_flag}`."
        out += ["## Access", "", access, ""]

    filename = Path(str(data.get("filename") or "release-note.md")).name
    if not filename.endswith(".md"):
        filename += ".md"
    description = " ".join(_clean(data.get("description")).split())
    return {
        "filename": filename,
        "frontmatter": f"---\ndescription: >-\n  {description}\n---",
        "content": "\n".join(out).rstrip() + "\n",
    }


def finalize_doc_link(content: str, url: str | None) -> str:
    """Put the doc page's URL into the link line, or drop the line if there is no page."""
    if DOC_LINK_PLACEHOLDER not in content:
        return content
    if url:
        return content.replace(DOC_LINK_PLACEHOLDER, url)
    lines = [l for l in content.split("\n") if DOC_LINK_PLACEHOLDER not in l]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).rstrip() + "\n"


def docs_url(repo_path: str) -> str:
    """Live URL of a docs repo page: path without .md, README is its folder."""
    path = re.sub(r"(^|/)README\.md$", "", repo_path)
    path = re.sub(r"\.md$", "", path)
    return f"https://docs.flytbase.com/{path}".rstrip("/")
