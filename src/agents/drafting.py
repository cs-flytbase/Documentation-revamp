"""Drafting agent — produces the actual documentation markdown.

Split into two separate LLM calls:
1. Release note (customer-facing value story)
2. Doc page (operator reference) + impacted page edits

This ensures each output gets the full context window for prose quality.
"""

import json
import re
from pathlib import Path

from src.agents.base import BaseAgent
from src.config import PROJECT_ROOT

RELEASE_NOTE_PROMPT = r"""You are the Drafting Agent for the FlytBase documentation pipeline.
You are producing a RELEASE NOTE — a customer-facing value story.

Purpose: Show the customer why this matters. How does it reduce their overhead?
What pain goes away? What becomes possible that wasn't before?

Structure (follow this order exactly):
1. H1 title
2. YouTube embed IMMEDIATELY after the H1, on its own line, followed by a blank line:
   {% embed url="https://youtu.be/VIDEO_ID" %}
   Use the EXACT url given under YOUTUBE LINK below. This self-closing form is
   what renders a video preview on the published page - do not add a caption
   line or an {% endembed %} tag.
   If YOUTUBE LINK says "None provided", OMIT the embed line entirely. Never
   write the literal text YOUTUBE_URL, never write url="", and never invent a
   video URL. A placeholder or empty embed renders as a broken block on the
   live site.
3. Opening narrative — describe the problem in concrete, real-world terms.
   Use the PM doc's exact examples (thermal wells, maintenance sheds, etc.)
   Keep the punchy, conversational tone from the PM doc.
4. Feature introduction — what it is and how it solves the problem.
   Screenshot right after this section.
5. Why This Matters — the bigger picture of why this matters. Use exactly
   this heading. Don't skip the "why" sections from the PM doc.
6. Step-by-step walkthrough — bold step headers ("**Step 1: ...**") with
   full paragraph under each. Place screenshots AFTER the step they illustrate.
7. Constraints & requirements table
8. Access section

Voice rules:
- Conversational, direct. Like you're explaining to a smart operator over coffee.
- Start sections with the PROBLEM, then the solution. Never lead with the feature name.
- Keep the PM doc's real-world examples and analogies word-for-word.
- Use "you" and "your" — speak directly to the operator.
- No marketing language. No "revolutionary", "seamless", "cutting-edge".
- State limitations honestly.

IMAGE PLACEMENT (CRITICAL — read carefully):
- You receive an ASSET MAP with descriptions, keywords, and context for each image/GIF.
- Syntax: ![descriptive alt text](assets/{filename})
- For EACH asset in the ASSET MAP:
  1. Read its KEYWORDS and CONTEXT
  2. Find the paragraph in YOUR draft that discusses the same feature/workflow
  3. Place the image IMMEDIATELY AFTER that paragraph
- The HERO image goes right after the YouTube embed (or after the H1 title if no YouTube).
- EVERY provided asset MUST appear in the release note. Count them. If the ASSET MAP has 5 assets, your output must have 5 ![...](assets/...) references.
- NEVER place an image under an unrelated section. If the image shows "API key configuration", it goes under a setup/configuration section, NOT under "Flyt-Sense Alerts".

FORMATTING:
- Horizontal rules (---) between every major section.
- Tables with all rows/columns from PM doc.
- Bold UI elements: **Add Pointer**, **Save feedback**.
- Navigation paths with >: **Verkos AI > Site Pointers**.
- GitBook hints: {% hint style="info" %} ... {% endhint %}
- Frontmatter: ---\ndescription: >-\n  Summary here.\n---
- The frontmatter description MUST be under 150 characters. One short sentence only.

CRITICAL REQUIREMENTS:
1. EVERY section from the PM document must appear in the release note.
2. EVERY asset must be embedded.
3. YouTube link MUST be the FIRST thing after the H1 title.
4. Release note must be 3000+ characters.
5. Do NOT genericize. Use the PM doc's exact examples word-for-word.
6. Do NOT invent, assume, or hallucinate ANY details not in the PM doc.
   If the PM doc doesn't mention a number, constraint, or capability, do NOT make one up.

YOUR OUTPUT MUST BE A JSON OBJECT:
{
  "filename": "feature-slug.md",
  "frontmatter": "---\ndescription: >-\n  ...\n---",
  "content": "full markdown content"
}

Return ONLY valid JSON. No markdown wrapping.
"""

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

    def _load_relevant_memory(self) -> str:
        memory_dir = PROJECT_ROOT / "memory"
        contents = []

        for correction_file in ["voice_corrections.md", "placement_corrections.md", "formatting_corrections.md", "terminology.md"]:
            path = memory_dir / correction_file
            if path.exists():
                text = path.read_text().strip()
                if "No corrections yet" not in text:
                    contents.append(f"--- {correction_file} ---\n{text}")

        return "\n\n".join(contents) if contents else "No relevant memory files."

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
        memory_context = self._load_relevant_memory()
        claude_md = self._load_claude_md()
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

--- MEMORY FILES ---
{memory_context}
--- END MEMORY ---

--- CLAUDE.MD CONVENTIONS ---
{claude_md[:3000]}
--- END CLAUDE.MD ---

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
        doc_result = {}

        # ── Call 1: Release Note (skip if doc_only) ───────────────────
        if mode in ("both", "release_only"):
            self.system_prompt = RELEASE_NOTE_PROMPT
            release_message = f"""Produce the release note from this PM document.

{context}

CHECKLIST before you respond:
[ ] Release note covers ALL H2 sections from PM doc
[ ] YouTube embed is the FIRST thing after H1
[ ] Every asset filename appears in the release note
[ ] Release note is 3000+ characters
[ ] Real-world examples from PM doc are preserved word-for-word
[ ] NO invented or assumed details — only facts from the PM doc"""

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
                release_result = _extract_json(release_response)
            except json.JSONDecodeError:
                reason = _diagnose_bad_output(release_response)
                print(f"    [Drafting] Release note unusable: {reason}")
                return {"error": f"Release note not generated - {reason}", "raw_response": release_response}
        else:
            print("    [Drafting] Skipping release note (mode: doc_only)")

        # ── Call 2: Doc Page + Impacted Edits (skip if release_only) ──
        if mode in ("both", "doc_only"):
            self.system_prompt = DOC_PAGE_PROMPT
            doc_message = f"""Produce the doc page and impacted page edits from this PM document.

{context}

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

        # ── Combine results ────────────────────────────────────────────
        combined = {
            "release_note": release_result,
            "doc_page": {k: v for k, v in doc_result.items() if k != "impacted_page_edits"} if doc_result else {},
            "impacted_page_edits": doc_result.get("impacted_page_edits", []),
        }

        # ── Validate and auto-retry ───────────────────────────────────
        warnings = self._validate_output(combined, asset_filenames, youtube_link, pm_doc)
        if warnings:
            critical_warnings = [w for w in warnings if "missing from" in w or "too short" in w]
            if critical_warnings:
                print(f"    [Drafting] Auto-retrying due to: {critical_warnings}")
                combined = self._auto_retry(combined, critical_warnings, context, asset_filenames, youtube_link, pm_doc)
                # Re-validate after retry
                warnings = self._validate_output(combined, asset_filenames, youtube_link, pm_doc)

            if warnings:
                combined["_validation_warnings"] = warnings

        return combined

    def _auto_retry(self, current_result: dict, issues: list[str], context: str,
                    asset_filenames: list[str], youtube_link: str, pm_doc: str) -> dict:
        """Retry the specific output that has issues."""
        release_issues = [w for w in issues if "release note" in w.lower()]
        doc_issues = [w for w in issues if "doc page" in w.lower()]

        if release_issues:
            self.system_prompt = RELEASE_NOTE_PROMPT
            retry_msg = f"""Your previous release note had these problems:
{chr(10).join('- ' + w for w in release_issues)}

Fix ALL of these issues. Here is the context again:
{context}

Return the corrected release note as JSON."""
            response = self.run(retry_msg)
            try:
                current_result["release_note"] = _extract_json(response)
            except json.JSONDecodeError:
                pass

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

    def _validate_output(self, result, asset_filenames, youtube_link, pm_doc):
        warnings = []
        release_content = result.get("release_note", {}).get("content", "")
        doc_content = result.get("doc_page", {}).get("content", "")

        # A placeholder or empty embed renders as a broken block on the live
        # site. Two published release notes already carry one of these, written
        # when no youtube_link reached the drafting agent.
        for label, content in (("release note", release_content), ("doc page", doc_content)):
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
            if release_content and img_ref not in release_content:
                warnings.append(f"Asset '{filename}' missing from release note (must use ![alt](assets/{filename}) format)")
            if doc_content and img_ref not in doc_content:
                warnings.append(f"Asset '{filename}' missing from doc page (must use ![alt](assets/{filename}) format)")

        if youtube_link and youtube_link not in release_content:
            warnings.append("YouTube link not in release note")

        if youtube_link and youtube_link in release_content:
            yt_pos = release_content.index(youtube_link)
            if yt_pos > 500:
                warnings.append(f"YouTube link placed too far down in release note (position {yt_pos})")

        if len(release_content) < 3000:
            warnings.append(f"Release note too short ({len(release_content)} chars, need 3000+)")
        if len(doc_content) < 4000:
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
