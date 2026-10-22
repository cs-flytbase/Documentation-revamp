"""The release note template, enforced by code.

A release note is a note, not documentation: it says what problem existed,
what was released, its key benefits, its intended users and a summary. It has no steps, settings tables or troubleshooting;
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
    ## Key Benefits                           benefits of the approach (value proposition)
    ## Intended Users
    ## Summary                                what the reader walks away with
    For setup and full details, see [<doc page>](<link>).   only if a doc page is published
    ## Availability                           ONLY for features behind a feature flag

Headings and tone are enterprise wording (2026-10-22): "Why It Matters",
"Who It's For", "Key Takeaways" and "Access" read as informal. Release notes
are read by executives, marketing, operators and developers at once, so the
tone rules below write for all four.

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
You are writing a RELEASE NOTE: a short, professional announcement of what was released.

It is NOT documentation. It never contains steps, settings, tables, edge cases or
troubleshooting; the doc page has all of that and the release note links to it.
It answers: what problem existed, what was released, its key benefits, its
intended users, and a summary of what the reader should know.

TONE AND STYLE
FlytBase is an enterprise company working where aerospace and SaaS meet. The
release note is read by four audiences at once: executives, marketing,
operators and flight teams, and developers and integrators. Write for all four:
- Write in layers. The first sentence of each section and capability states
  the outcome in plain language, so an executive can stop there. The sentences
  after it say how it works in operations. Technical specifics (limits,
  distances, units, requirements, API details) go in the capability descriptions.
- Refer to people by role, in the third person: "Operators can...",
  "Remote operations teams can...". Never address the reader as "you".
- Declarative sentences only. No questions, exclamation marks, emojis or
  rhetorical openers.
- Present tense. One idea per sentence; aim for under 25 words.
- Spell out acronyms on first use: "No-Fly Zone (NFZ)".
- Quantify with the exact numbers and units from the source ("80 m at 10 m/s"),
  never a bare "earlier" or "faster". Never invent a number.
- Precise verbs (displays, warns, sends, shares, highlights). No hype or filler:
  no "game-changer", "seamless", "revolutionary", "cutting-edge", "empowers",
  "unlocks", "elevates", "imagine...".
- Use product names and on-screen labels exactly as in the source.

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
  "key_benefits": [{"benefit": "Short noun phrase, e.g. Faster response to observed events", "detail": "One sentence explaining it, concretely."}],
  "intended_users": [{"audience": "Role or team, e.g. Remote operations centres", "value": "What changes for them, in one sentence."}],
  "summary": ["What the reader should know, one line each."],
  "hero_asset": "exact-asset-filename to show at the top, or empty",
  "availability": "Only if the FEATURE FLAG block below names a flag: one or two sentences on how to get it enabled. Otherwise empty."
}

Field rules:
- capabilities: one entry per distinct capability in the source, in the source's order.
  Never merge two capabilities or drop one. A single-capability release has one entry.
- key_benefits: 3-5 benefits of this approach. Concrete outcomes (fewer steps,
  faster response, one screen instead of two), never vague praise.
- intended_users: 2-4 roles or teams actually named or clearly implied by the source.
- summary: 3-5 lines.
- Assets: use only filenames from RELEASE ASSETS. Every asset listed as REQUIRED
  must be used exactly once (as hero_asset or a capability asset). OPTIONAL assets
  may be used if they show a capability well. Never use one asset twice.
- No headings, tables, horizontal rules, hint blocks, step lists or video embeds
  inside any field. The template adds the video automatically.

Return ONLY the JSON object. No markdown wrapping.
"""


# Field names before 2026-10-22, still accepted.
_OLD_KEYS = {"key_benefits": "why_it_matters", "intended_users": "who_its_for", "summary": "key_takeaways",
             "availability": "access"}


def _get(data: dict, key: str):
    v = data.get(key)
    return v if v else data.get(_OLD_KEYS.get(key, ""), v)


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
    for key, low in (("key_benefits", 2), ("intended_users", 1), ("summary", 2)):
        items = _get(data, key) or []
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
    if not feature_flag and _clean(_get(data, "availability")):
        problems.append('release note "availability" must be empty: this feature is not behind a feature flag')
    return problems


def render(data: dict, required_assets: list[str], youtube_link: str = "", doc_title: str = "",
           asset_descriptions: dict | None = None, feature_flag: str = "",
           optional_assets: list[str] = ()) -> dict:
    """Build the release note. Returns {"filename", "frontmatter", "content"}.

    Every REQUIRED asset is embedded exactly once even if the model forgot it.
    The Availability section exists only when a feature flag is known.
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
        for item in _get(data, key) or []:
            if isinstance(item, dict):
                head, tail = _clean(item.get(a)), _clean(item.get(b))
                if head:
                    rows.append(f"- **{head.rstrip(':.')}:** {tail}" if tail else f"- **{head}**")
            elif _clean(item):
                rows.append(f"- {_clean(item)}")
        return rows

    out += ["## Key Benefits", ""] + bullets("key_benefits", "benefit", "detail") + [""]
    out += ["## Intended Users", ""] + bullets("intended_users", "audience", "value") + [""]
    out += ["## Summary", ""] + bullets("summary", "", "") + [""]

    if doc_title:
        out += [f"{DOC_LINK_PREFIX}[{doc_title}]({DOC_LINK_PLACEHOLDER}).", ""]

    if feature_flag:
        # The flag answer is sometimes a sentence ("contact FlytBase team for
        # enablement"), not a flag name; only a name is shown as one.
        is_name = " " not in feature_flag.strip()
        access = _clean(_get(data, "availability")) or (
            f"{feature} is not enabled by default. Contact the FlytBase team to enable it for your organization.")
        if is_name and feature_flag not in access:
            access += f" Feature flag: `{feature_flag}`."
        out += ["## Availability", "", access, ""]

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
