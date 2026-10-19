"""Drafting agent — produces the actual documentation markdown.

Split into two separate LLM calls:
1. Release note (brief overview of what was released and how to use it,
   rendered from fixed template fields)
2. Doc page (operator reference) + impacted page edits

This ensures each output gets the full context window for prose quality.
"""

import json
import re
from pathlib import Path

from src.agents.base import BaseAgent
from src.config import PROJECT_ROOT

# The release note is built from fields by code (src/release_template.py), so
# its structure cannot drift; the prompt only asks for the fields.
from src.release_template import RELEASE_NOTE_PROMPT, check_fields, render as render_release  # noqa: E402
from src.style_check import autofix, errors as style_errors, lint  # noqa: E402
from src.style_guide import feedback_block, style_block  # noqa: E402

DOC_PAGE_PROMPT = r"""You are the Drafting Agent for the FlytBase documentation pipeline.
You are producing a DOC PAGE — a comprehensive operator reference document.

Purpose: Tell the operator exactly how to use it. This is the reference document they
come back to after reading the release note. It must be COMPREHENSIVE — every detail,
every edge case, every constraint from the PM doc must be here.

The doc page must be LONG and DETAILED. Minimum 1500 words / 6000+ characters.
A person reading only the doc page should be able to fully understand and use the
feature without reading anything else. Do NOT summarize — expand and explain.

Structure (follow this order exactly):
1. Frontmatter with description
2. H1 title
3. Overview — Write a FULL paragraph (6-8 sentences) explaining:
   - What the feature does in specific terms
   - What problem it solves (with concrete examples from the PM doc)
   - How it fits into the broader product workflow
   - Who should use it and when
   - What the key benefit is for daily operations
   VIDEO: if a YOUTUBE LINK is provided below, embed it on its own line at the
   END of the Overview section, using exactly:
   {% embed url="<the exact YOUTUBE LINK>" %}
   Never as a plain Markdown link, never above the Overview. If the PM document
   or the request says where the video must go, put it there instead. If the
   YOUTUBE LINK says "None provided", there is no video.
4. How It Works — a dedicated section (2-3 paragraphs minimum) explaining the
   underlying mechanism in plain English. Not steps — conceptual understanding.
   What happens under the hood when the operator uses this feature? What does
   the system do with the input? Include any technical details from the PM doc
   about GPS radius, AI models, processing, etc.
5. Prerequisites — what needs to be set up before using this feature
6. Step-by-step configuration — numbered steps with bold action verbs.
   Each step MUST have:
   - The action (what to click/select/fill) in bold
   - A full sentence describing what happens as a result
   - Any sub-steps or options available as nested bullets
   - Screenshot placed immediately after the step it illustrates
   Do NOT compress multiple actions into one step. Keep steps atomic.
   Write 1-2 sentences per step, not just a phrase.
7. Constraints & Limitations — full table AND a paragraph (3-4 sentences)
   explaining each constraint in context. Don't just list them — explain
   WHY each constraint exists if the PM doc mentions the reason.
9. Edge Cases & Troubleshooting — cover EVERY edge case the PM doc mentions.
   Format as: problem → cause → solution. Write at least 3-4 entries.
   If the PM doc mentions what happens in corner cases, include ALL of them.
10. Related pages

Voice rules:
- Professional, instructional, direct. Imperative for steps.
- "Navigate to", "Click", "Select" — not "you should navigate to".
- The Overview and How It Works sections should read as proper explanatory
  prose — multiple full paragraphs, not bullet points.
- Every single detail from the PM doc must appear somewhere in the doc page.
  Nothing is too minor to include if the PM doc mentioned it.

IMAGE PLACEMENT (CRITICAL — read carefully):
- You receive an ASSET MAP with descriptions, keywords, and context for each image/GIF.
- Syntax: ![descriptive alt text](assets/{filename})
- For EACH asset in the ASSET MAP:
  1. Read its KEYWORDS and CONTEXT
  2. Find the paragraph in YOUR draft that discusses the same feature/workflow
  3. Place the image IMMEDIATELY AFTER that paragraph
- EVERY provided asset MUST appear in the doc page. Count them. If the ASSET MAP has 5 assets, your output must have 5 ![...](assets/...) references.
- NEVER place an image under an unrelated section.

FORMATTING:
- Horizontal rules (---) between every major section.
- Tables with all rows/columns from PM doc.
- Bold UI elements: **Add Pointer**, **Save feedback**.
- Navigation paths with >: **Verkos AI > Site Pointers**.
- GitBook hints: {% hint style="info" %} ... {% endhint %}
- Frontmatter: ---\ndescription: >-\n  Summary here.\n---
- The frontmatter description MUST be under 150 characters. One short sentence only.

CRITICAL REQUIREMENTS:
1. Doc page must be 6000+ characters / 1500+ words. This is NON-NEGOTIABLE.
2. EVERY asset must be embedded in the doc page.
3. How It Works section must be 2-3 paragraphs minimum.
4. Overview must be 6-8 sentences minimum.
5. Do NOT invent, assume, or hallucinate ANY details not in the PM doc.
   If the PM doc doesn't mention something, do NOT make it up.
   Only include facts directly stated in the PM doc, transcript, or corpus.

YOUR OUTPUT MUST BE A JSON OBJECT:
{
  "filename": "feature-slug.md",
  "target_ia_node": "IA node ID from research output",
  "target_path": "folder path from research output",
  "frontmatter": "---\ndescription: >-\n  ...\n---",
  "content": "full markdown content",
  "impacted_page_edits": [
    {
      "source_url": "URL of the existing page to patch",
      "ia_node": "node ID",
      "edit_description": "what changed and why",
      "section_heading": "## exact heading text as it appears in the existing page",
      "patch_mode": "append",
      "patch_content": "the markdown to insert after that section"
    }
  ]
}

EXAMPLE impacted_page_edits entry:
{
  "source_url": "https://releases.flytbase.com/february-2026/verkos-ai-detect-anything-agents",
  "ia_node": "uncategorized",
  "edit_description": "Add cross-reference to Site Pointers feature",
  "section_heading": "## Verkos AI: Detect Anything Agents",
  "patch_mode": "append",
  "patch_content": "\n### Related Features\n\n- [Site Pointers](../may-2026/site-pointers.md): Teach the AI what it gets wrong at your site through operator feedback.\n"
}

impacted_page_edits rules:
- section_heading must start with ## or ### and be the EXACT heading from the existing page.
- patch_mode "append": inserts patch_content after the section, before the next heading.
- patch_mode "replace": replaces everything between section_heading and the next heading.
- Use "append" for cross-references. Use "replace" only if content is wrong.

Return ONLY valid JSON. No markdown wrapping.
"""


# Phrases a model uses when it declines rather than answers. A refusal is not
# malformed JSON - reporting it as a parse error hid the real cause for two
# whole pipeline runs, which produced "no PRs created" and nothing else.
_REFUSAL_MARKERS = (
    "i'm sorry, but i can't",
    "i'm sorry, but i cannot",
    "i am sorry, but i can't",
    "i cannot assist",
    "i can't assist",
    "i'm unable to assist",
    "i am unable to assist",
    "i can't help with that",
    "i cannot help with that",
    "as an ai language model",
)


def _looks_like_refusal(text: str) -> bool:
    """True when the model declined instead of producing output."""
    if not text:
        return False
    head = text.strip()[:400].lower()
    return any(marker in head for marker in _REFUSAL_MARKERS)


def _diagnose_bad_output(text: str) -> str:
    """Explain WHY output could not be used, in terms a human can act on."""
    if not text or not text.strip():
        return "the model returned an empty response"
    if _looks_like_refusal(text):
        snippet = text.strip()[:200].replace("\n", " ")
        return (
            "the model REFUSED to generate this content. It replied: "
            f'"{snippet}". This usually means the prompt contains contradictory '
            "or impossible instructions - check that the required structure in the "
            "agent prompt does not conflict with rules in memory/"
        )
    if "{" not in text:
        snippet = text.strip()[:200].replace("\n", " ")
        return f'the model returned prose instead of JSON: "{snippet}"'
    return "the model returned JSON that could not be parsed (likely truncated or malformed)"

def _extract_json(text: str) -> dict:
    """Robustly extract a JSON object from LLM output.

    Handles: markdown fences, leading/trailing text, nested braces.
    """
    text = text.strip()

    # Strategy 1: Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Strip markdown code fences (```json ... ``` or ``` ... ```)
    fence_match = re.search(r'```(?:json)?\s*\n(.*?)```', text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find the outermost { ... } in the text
    first_brace = text.find('{')
    if first_brace != -1:
        depth = 0
        last_brace = -1
        in_string = False
        escape_next = False
        for i in range(first_brace, len(text)):
            c = text[i]
            if escape_next:
                escape_next = False
                continue
            if c == '\\' and in_string:
                escape_next = True
                continue
            if c == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    last_brace = i
                    break
        if last_brace != -1:
            try:
                return json.loads(text[first_brace:last_brace + 1])
            except json.JSONDecodeError:
                pass

    raise json.JSONDecodeError("No valid JSON found in response", text, 0)


class DraftingAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="drafting",
            system_prompt="",  # Set per-call
            temperature=0.4,
            max_tokens=16384,
        )
        self.conversation_history: list[dict] = []

    def _load_claude_md(self) -> str:
        path = PROJECT_ROOT / "CLAUDE.md"
        return path.read_text() if path.exists() else ""

    def _docs_conventions(self) -> str:
        """CLAUDE.md conventions for doc pages.

        Its release-notes section ("storytelling", question subheadings)
        contradicts the release template and the director's style review, so
        it is cut. Release notes follow src/release_template.py instead.
        """
        text = self._load_claude_md()
        text = re.sub(r"### Release Notes.*?(?=\n### |\n## )", "", text, flags=re.S)
        text = re.sub(r"### Frontmatter Template \(release notes\).*?(?=\n### |\n## |\n---|\Z)", "", text, flags=re.S)
        return text[:4000]

    def _rules_for(self, kind: str) -> str:
        """Style guide plus capped recent feedback. kind is "release" or "doc"."""
        parts = [style_block(kind)]
        feedback = feedback_block(PROJECT_ROOT / "memory")
        if feedback:
            parts.append(feedback)
        if kind == "doc":
            parts.append(f"--- CONVENTIONS (CLAUDE.md, doc pages) ---\n{self._docs_conventions()}\n--- END CONVENTIONS ---")
        return "\n\n".join(parts)

    @staticmethod
    def _h1(content: str) -> str:
        m = re.search(r"^#\s+(.+)$", content or "", re.M)
        return m.group(1).strip() if m else ""

    def _style_pass(self, kind: str, result: dict) -> tuple[dict, list]:
        """Apply safe automatic fixes, then lint. Returns (result, issues)."""
        if not result or not result.get("content"):
            return result, []
        content, _ = autofix(result["content"])
        frontmatter = result.get("frontmatter", "")
        if frontmatter:
            body = re.sub(r"^---\n|\n---\s*$", "", frontmatter.strip())
            fixed_body, _ = autofix(body)
            frontmatter = f"---\n{fixed_body}\n---"
        result = {**result, "content": content, "frontmatter": frontmatter}
        return result, lint(f"{frontmatter}\n\n{content}" if frontmatter else content, kind)

    def _build_asset_map(self, pm_doc: str, asset_filenames: list[str], vision_output: list[dict]) -> str:
        """Build content-aware asset map for the drafting agent.

        Instead of telling the drafter WHERE to place each image,
        we tell it WHAT the image shows and let it match to the
        correct section based on content relevance.
        """
        lines = [
            "ASSET MAP — you MUST place every asset below in the document.",
            "Read each asset's description and keywords, then place it in the section that discusses the SAME feature/workflow.",
            "Use syntax: ![alt text](assets/filename)",
            "DO NOT skip any asset. Every single one must appear in the output.\n",
        ]

        vision_lookup = {}
        for v in vision_output:
            vision_lookup[v.get("file_name", "")] = v

        for i, filename in enumerate(asset_filenames):
            vision = vision_lookup.get(filename, {})
            desc = vision.get("description", "No description available")
            keywords = vision.get("content_keywords", [])
            context = vision.get("feature_context", "")
            is_hero = vision.get("is_hero", False)
            is_gif = filename.lower().endswith(".gif")

            asset_type = "animated GIF" if is_gif else "screenshot"
            lines.append(f"Asset {i+1}: {filename} ({asset_type})")
            if is_hero:
                lines.append("   ROLE: HERO image — place at the very top, right after the H1 title or YouTube embed")
            lines.append(f"   SHOWS: {desc}")
            if keywords:
                lines.append(f"   KEYWORDS: {', '.join(keywords)}")
            if context:
                lines.append(f"   CONTEXT: {context}")
            lines.append(f"   RULE: Find the section in YOUR draft that discusses {', '.join(keywords[:3]) if keywords else 'this feature'}. Place this asset there.")
            lines.append("")

        lines.append("FINAL CHECK: Count your ![...](assets/...) references. You must have EXACTLY " + str(len(asset_filenames)) + " image references.")

        return "\n".join(lines)

    def _build_context(self, pm_doc: str, research_output: dict, vision_output: list[dict],
                       asset_filenames: list[str], youtube_link: str, release_month: str,
                       transcript: str, exemplar: str = "") -> str:
        """Build the shared context block used by both calls."""
        asset_map = self._build_asset_map(pm_doc, asset_filenames, vision_output)

        # Trim research output to essentials
        research_trimmed = {
            "target_ia_node": research_output.get("target_ia_node", {}),
            "impacted_pages": research_output.get("impacted_pages", []),
            "feature_summary": research_output.get("feature_summary", ""),
        }

        transcript_section = ""
        if transcript:
            transcript_section = f"""
--- CLUESO VIDEO TRANSCRIPT ---
Use this for: procedural details, exact UI names, edge cases, tips.
{transcript[:8000]}
--- END TRANSCRIPT ---
"""

        exemplar_section = ""
        if exemplar:
            exemplar_section = f"""
--- EXEMPLAR PAGE (match this level of detail and structure) ---
{exemplar[:6000]}
--- END EXEMPLAR ---
"""

        return f"""--- PM DOCUMENT (use ALL of it, do NOT skip sections) ---
{pm_doc}
--- END PM DOCUMENT ---
{transcript_section}{exemplar_section}
--- RESEARCH OUTPUT ---
{json.dumps(research_trimmed, indent=2)}
--- END RESEARCH OUTPUT ---

--- {asset_map} ---

--- YOUTUBE LINK ---
{youtube_link if youtube_link else "None provided."}
--- END YOUTUBE ---

Release month: {release_month or "current month"}"""

    def draft(
        self,
        pm_doc: str,
        research_output: dict,
        vision_output: list[dict],
        asset_filenames: list[str],
        youtube_link: str = "",
        release_month: str = "",
        transcript: str = "",
        exemplar: str = "",
        mode: str = "both",
        subsection_focus: dict = None,
    ) -> dict:
        context = self._build_context(
            pm_doc, research_output, vision_output, asset_filenames,
            youtube_link, release_month, transcript, exemplar,
        )

        # When drafting a subsection, add focus instructions
        if subsection_focus:
            context += f"""

--- SUBSECTION FOCUS ---
You are drafting ONE SUBSECTION of a larger grouped release. Focus ONLY on the content described below.
Title: {subsection_focus['title']}
Scope: {subsection_focus['summary']}
Slug: {subsection_focus['slug']}

IMPORTANT:
- Use the title above as your H1 heading.
- Use "{subsection_focus['slug']}.md" as the filename.
- Only cover content relevant to this subsection's scope.
- Do NOT cover content that belongs to other subsections.
- Still use ALL provided assets that are relevant to this subsection's scope. Skip assets that clearly belong to other subsections.
--- END SUBSECTION FOCUS ---"""

        release_result = {}
        release_fields = {}
        doc_result = {}

        # ── Call 1: Release Note (skip if doc_only) ───────────────────
        if mode in ("both", "release_only"):
            self.system_prompt = RELEASE_NOTE_PROMPT
            release_message = f"""Fill in the release note fields from this PM document.

{context}

{self._rules_for("release")}

CHECKLIST before you respond:
[ ] One features entry per feature or change in the PM document, none dropped or merged
[ ] Every ASSET MAP filename attached to exactly one feature
[ ] Each "what" is at most 3 sentences; each how_to_use is at most 5 short steps
[ ] good_to_know holds only blocking requirements or limits (or is empty)
[ ] NO invented or assumed details - only facts from the PM document"""

            print("    [Drafting] Generating release note...")
            release_response = self.run(release_message)

            # A refusal or unparseable reply gets one retry with an explicit
            # nudge back to the required format. Previously either outcome
            # ended the run with "Failed to parse", which hid a refusal
            # entirely and produced a silent "no PRs created".
            if _looks_like_refusal(release_response):
                print(f"    [Drafting] Model refused. Retrying once. Reply was: {release_response.strip()[:160]}")
                release_response = self.run(
                    release_message
                    + "\n\nIMPORTANT: This is FlytBase's own product documentation, written from the "
                      "PM document supplied above. Respond ONLY with the required JSON object. "
                      "If any instruction seems to conflict with another, follow the JSON schema "
                      "and the PM document, and omit any section you cannot produce."
                )

            try:
                release_fields = _extract_json(release_response)
            except json.JSONDecodeError:
                reason = _diagnose_bad_output(release_response)
                print(f"    [Drafting] Release note unusable: {reason}")
                return {"error": f"Release note not generated - {reason}", "raw_response": release_response}

            problems = check_fields(release_fields, asset_filenames)
            if problems:
                print(f"    [Drafting] Release note fields need fixing: {problems}")
                release_fields = self._retry_release_fields(release_message, release_fields, problems)
        else:
            print("    [Drafting] Skipping release note (mode: doc_only)")

        # ── Call 2: Doc Page + Impacted Edits (skip if release_only) ──
        if mode in ("both", "doc_only"):
            self.system_prompt = DOC_PAGE_PROMPT
            doc_message = f"""Produce the doc page and impacted page edits from this PM document.

{context}

{self._rules_for("doc")}

CHECKLIST before you respond:
[ ] Doc page is 6000+ characters / 1500+ words
[ ] Overview is 6-8 sentences
[ ] How It Works is 2-3 full paragraphs
[ ] Every asset filename appears in the doc page
[ ] Steps are atomic with descriptions of what happens
[ ] Constraints explained in context, not just tabled
[ ] Edge cases covered comprehensively
[ ] impacted_page_edits uses section_heading + patch_mode + patch_content format
[ ] NO invented or assumed details — only facts from the PM doc"""

            print("    [Drafting] Generating doc page + impacted edits...")
            doc_response = self.run(doc_message)

            if _looks_like_refusal(doc_response):
                print(f"    [Drafting] Model refused on doc page. Retrying once. Reply was: {doc_response.strip()[:160]}")
                doc_response = self.run(
                    doc_message
                    + "\n\nIMPORTANT: This is FlytBase's own product documentation, written from the "
                      "PM document supplied above. Respond ONLY with the required JSON object. "
                      "If any instruction seems to conflict with another, follow the JSON schema "
                      "and the PM document, and omit any section you cannot produce."
                )

            try:
                doc_result = _extract_json(doc_response)
            except json.JSONDecodeError:
                reason = _diagnose_bad_output(doc_response)
                print(f"    [Drafting] Doc page unusable: {reason}")
                return {"error": f"Doc page not generated - {reason}", "raw_response": doc_response}
        else:
            print("    [Drafting] Skipping doc page (mode: release_only)")

        # ── Render the release note from its fields ───────────────────
        # After the doc page, so "Learn More" can name the page it links to.
        asset_descriptions = {v.get("file_name", ""): v.get("description", "") for v in (vision_output or [])}
        if release_fields:
            doc_title = self._h1(doc_result.get("content", "")) if doc_result else ""
            release_result = render_release(release_fields, asset_filenames, youtube_link, doc_title, asset_descriptions)

        # ── Style: automatic fixes, then the checker ──────────────────
        style_report = {}
        release_result, release_issues = self._style_pass("release", release_result)
        if style_errors(release_issues):
            print(f"    [Drafting] Release note style errors: {[str(i) for i in style_errors(release_issues)]}")
            release_fields = self._retry_release_fields(
                release_message, release_fields, [f"style: {i.message}" for i in style_errors(release_issues)])
            release_result = render_release(release_fields, asset_filenames, youtube_link,
                                            self._h1(doc_result.get("content", "")) if doc_result else "", asset_descriptions)
            release_result, release_issues = self._style_pass("release", release_result)
        if release_issues:
            style_report["release_note"] = [f"[{i.severity}] {i}" for i in release_issues][:15]

        if doc_result:
            fixed_doc, doc_issues = self._style_pass("doc", {k: v for k, v in doc_result.items() if k != "impacted_page_edits"})
            if style_errors(doc_issues):
                print(f"    [Drafting] Doc page style errors: {[str(i) for i in style_errors(doc_issues)]}")
                fixed_doc = self._retry_doc_style(fixed_doc, style_errors(doc_issues))
                fixed_doc, doc_issues = self._style_pass("doc", fixed_doc)
            doc_result = {**fixed_doc, "impacted_page_edits": doc_result.get("impacted_page_edits", [])}
            if doc_issues:
                style_report["doc_page"] = [f"[{i.severity}] {i}" for i in doc_issues][:15]

        # ── Combine results ────────────────────────────────────────────
        combined = {
            "release_note": release_result,
            "doc_page": {k: v for k, v in doc_result.items() if k != "impacted_page_edits"} if doc_result else {},
            "impacted_page_edits": doc_result.get("impacted_page_edits", []),
        }

        # ── Validate and auto-retry ───────────────────────────────────
        warnings = self._validate_output(combined, asset_filenames, youtube_link, pm_doc, mode)
        if warnings:
            critical_warnings = [w for w in warnings if "missing from" in w or "too short" in w]
            if critical_warnings:
                print(f"    [Drafting] Auto-retrying due to: {critical_warnings}")
                combined = self._auto_retry(combined, critical_warnings, context, asset_filenames, youtube_link, pm_doc)
                # A retried doc page has not been through the style pass yet.
                if combined.get("doc_page"):
                    combined["doc_page"], doc_issues = self._style_pass("doc", combined["doc_page"])
                    if doc_issues:
                        style_report["doc_page"] = [f"[{i.severity}] {i}" for i in doc_issues][:15]
                    else:
                        style_report.pop("doc_page", None)
                # Re-validate after retry
                warnings = self._validate_output(combined, asset_filenames, youtube_link, pm_doc, mode)

            if warnings:
                combined["_validation_warnings"] = warnings

        if style_report:
            combined["_style_report"] = style_report
        return combined

    def _retry_release_fields(self, release_message: str, fields: dict, problems: list[str]) -> dict:
        """One targeted retry of the release note fields. Keeps the old fields if it fails."""
        self.system_prompt = RELEASE_NOTE_PROMPT
        response = self.run(
            release_message
            + "\n\nYour previous answer was:\n" + json.dumps(fields, ensure_ascii=False)
            + "\n\nFix ONLY these problems and return the complete JSON object again:\n- "
            + "\n- ".join(problems)
        )
        try:
            return _extract_json(response)
        except json.JSONDecodeError:
            return fields

    def _retry_doc_style(self, doc: dict, issues: list) -> dict:
        """One targeted retry fixing style errors in the doc page. Keeps the old page if it fails."""
        self.system_prompt = DOC_PAGE_PROMPT
        response = self.run(
            "Here is a doc page as JSON. Fix ONLY the problems listed below. Keep every other "
            "word, heading, image and link exactly as it is. Return the same JSON shape "
            '({"filename", "frontmatter", "content", "target_path"} plus any other keys it had).\n\n'
            "PROBLEMS:\n- " + "\n- ".join(str(i) for i in issues)
            + "\n\nDOC PAGE JSON:\n" + json.dumps(doc, ensure_ascii=False)
        )
        try:
            fixed = _extract_json(response)
            return {**doc, **{k: v for k, v in fixed.items() if k in ("content", "frontmatter")}} if fixed.get("content") else doc
        except json.JSONDecodeError:
            return doc

    def _auto_retry(self, current_result: dict, issues: list[str], context: str,
                    asset_filenames: list[str], youtube_link: str, pm_doc: str) -> dict:
        """Retry the specific output that has issues."""
        doc_issues = [w for w in issues if "doc page" in w.lower()]

        # Release notes are retried field by field in draft(); the template makes
        # the old whole-note retry unnecessary, so only the doc page is retried here.
        if doc_issues:
            self.system_prompt = DOC_PAGE_PROMPT
            retry_msg = f"""Your previous doc page had these problems:
{chr(10).join('- ' + w for w in doc_issues)}

Fix ALL of these issues. Here is the context again:
{context}

Return the corrected doc page as JSON (include impacted_page_edits)."""
            response = self.run(retry_msg)
            try:
                result = _extract_json(response)
                current_result["doc_page"] = {k: v for k, v in result.items() if k != "impacted_page_edits"}
                if "impacted_page_edits" in result:
                    current_result["impacted_page_edits"] = result["impacted_page_edits"]
            except json.JSONDecodeError:
                pass

        return current_result

    def _validate_output(self, result, asset_filenames, youtube_link, pm_doc, mode="both"):
        warnings = []
        release_content = result.get("release_note", {}).get("content", "")
        doc_content = result.get("doc_page", {}).get("content", "")

        # A placeholder or empty embed renders as a broken block on the live
        # site. Two published release notes already carry one of these, written
        # when no youtube_link reached the drafting agent.
        # The release note is rendered from fields by code, so its embed, assets
        # and structure are guaranteed there; these checks cover the doc page.
        for label, content in (("doc page", doc_content),):
            if not content:
                continue
            if 'url="YOUTUBE_URL"' in content or "url='YOUTUBE_URL'" in content:
                warnings.append(
                    f"CRITICAL: {label} contains the literal placeholder "
                    f'{{% embed url="YOUTUBE_URL" %}} - replace it with the real URL or remove the embed'
                )
            if 'embed url=""' in content.replace(" ", "").replace('embedurl=""', 'embed url=""'):
                warnings.append(
                    f"CRITICAL: {label} contains an empty embed url - "
                    "remove the embed rather than publishing a broken one"
                )
            if youtube_link and youtube_link not in content and "{% embed" in content:
                warnings.append(
                    f"{label} has an embed that does not use the supplied YouTube link ({youtube_link})"
                )

        for filename in asset_filenames:
            # Check for proper image markdown syntax, not just raw filename
            img_ref = f"](assets/{filename})"
            if doc_content and img_ref not in doc_content:
                warnings.append(f"Asset '{filename}' missing from doc page (must use ![alt](assets/{filename}) format)")

        wants_doc = mode in ("both", "doc_only")

        # The doc page prompt had no video rule, so a requested video came out
        # as a plain link above the Overview. Require the real embed.
        if wants_doc and youtube_link and doc_content and f'{{% embed url="{youtube_link}" %}}' not in doc_content:
            warnings.append(
                f'YouTube embed missing from doc page (must be {{% embed url="{youtube_link}" %}} '
                "on its own line at the end of the Overview, not a plain link)"
            )

        # The doc page has a minimum because it is the full reference. The
        # release note has no length limit either way: it is brief by
        # template, and a release with many features is allowed to be long.
        if mode in ("both", "doc_only") and len(doc_content) < 4000:
            warnings.append(f"Doc page too short ({len(doc_content)} chars, need 4000+)")

        pm_headings = re.findall(r'^##\s+\*?\*?(.+?)\*?\*?\s*$', pm_doc, re.MULTILINE)
        for heading in pm_headings:
            clean = heading.strip("* ").lower()
            if "media asset" in clean:
                continue
            all_content = release_content + doc_content
            if clean not in all_content.lower():
                warnings.append(f"PM section '{clean}' may not be covered")

        return warnings

    def revise(self, reviewer_feedback: str) -> dict:
        self.conversation_history.append({
            "role": "user",
            "content": f"Reviewer feedback:\n{reviewer_feedback}\n\nRevise and return complete JSON.",
        })
        response = self.run_with_history(self.conversation_history)
        self.conversation_history.append({"role": "assistant", "content": response})
        try:
            return _extract_json(response)
        except json.JSONDecodeError:
            return {"error": "Failed to parse revised output", "raw_response": response}
