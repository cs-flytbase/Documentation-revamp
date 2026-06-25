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
2. YouTube embed IMMEDIATELY after title: {% embed url="YOUTUBE_URL" %}
3. Opening narrative — describe the problem in concrete, real-world terms.
   Use the PM doc's exact examples (thermal wells, maintenance sheds, etc.)
   Keep the punchy, conversational tone from the PM doc.
4. Feature introduction — what it is and how it solves the problem.
   Screenshot right after this section.
5. How it generalizes / why it's foundational — the bigger picture of why
   this matters. Don't skip the "why" sections from the PM doc.
6. Step-by-step walkthrough — bold step headers ("**Step 1: ...**") with
   full paragraph under each. Place screenshots AFTER the step they illustrate.
7. Constraints & requirements table
8. Hardware compatibility table
9. Access section

Voice rules:
- Conversational, direct. Like you're explaining to a smart operator over coffee.
- Start sections with the PROBLEM, then the solution. Never lead with the feature name.
- Keep the PM doc's real-world examples and analogies word-for-word.
- Use "you" and "your" — speak directly to the operator.
- No marketing language. No "revolutionary", "seamless", "cutting-edge".
- State limitations honestly.

IMAGE PLACEMENT:
- You receive an ASSET MAP showing each image, its description, and which section it belongs to.
- Use this syntax: ![descriptive alt text](assets/{filename})
- Place each image IMMEDIATELY AFTER the paragraph about the thing it shows.
- The hero image/GIF goes right after the YouTube embed.
- EVERY provided asset MUST appear in the release note.

FORMATTING:
- Horizontal rules (---) between every major section.
- Tables with all rows/columns from PM doc.
- Bold UI elements: **Add Pointer**, **Save feedback**.
- Navigation paths with >: **Verkos AI > Site Pointers**.
- GitBook hints: {% hint style="info" %} ... {% endhint %}
- Frontmatter: ---\ndescription: >-\n  Summary here.\n---

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
8. Hardware compatibility table
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

IMAGE PLACEMENT:
- You receive an ASSET MAP showing each image, its description, and which section it belongs to.
- Use this syntax: ![descriptive alt text](assets/{filename})
- EVERY provided asset MUST appear in the doc page.
- Place each image after the step or section it illustrates.

FORMATTING:
- Horizontal rules (---) between every major section.
- Tables with all rows/columns from PM doc.
- Bold UI elements: **Add Pointer**, **Save feedback**.
- Navigation paths with >: **Verkos AI > Site Pointers**.
- GitBook hints: {% hint style="info" %} ... {% endhint %}
- Frontmatter: ---\ndescription: >-\n  Summary here.\n---

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
        """Build asset map using vision agent's section-aware placement."""
        lines = ["ASSET MAP — where each image should be placed:\n"]

        # Build a lookup from filename to vision output
        vision_lookup = {}
        for v in vision_output:
            vision_lookup[v.get("file_name", "")] = v

        # Sort assets alphabetically for deterministic ordering
        sorted_assets = sorted(asset_filenames)

        for i, filename in enumerate(sorted_assets):
            vision = vision_lookup.get(filename, {})
            desc = vision.get("description", "")
            section = vision.get("section_heading", "")
            step_num = vision.get("step_number")
            is_hero = vision.get("is_hero", False)

            lines.append(f"Asset {i+1}: {filename}")
            if is_hero:
                lines.append("   PLACEMENT: This is the HERO image — place at the very top after YouTube embed")
            elif section:
                lines.append(f"   PLACEMENT: Under section '{section}'")
            if step_num:
                lines.append(f"   STEP: After step {step_num}")
            if desc:
                lines.append(f"   DESCRIPTION: {desc}")
            lines.append("")

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
    ) -> dict:
        context = self._build_context(
            pm_doc, research_output, vision_output, asset_filenames,
            youtube_link, release_month, transcript, exemplar,
        )

        # ── Call 1: Release Note ──────────────────────────────────────
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

        try:
            text = release_response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            release_result = json.loads(text)
        except json.JSONDecodeError:
            return {"error": "Failed to parse release note output", "raw_response": release_response}

        # ── Call 2: Doc Page + Impacted Edits ──────────────────────────
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

        try:
            text = doc_response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            doc_result = json.loads(text)
        except json.JSONDecodeError:
            return {"error": "Failed to parse doc page output", "raw_response": doc_response}

        # ── Combine results ────────────────────────────────────────────
        combined = {
            "release_note": release_result,
            "doc_page": {k: v for k, v in doc_result.items() if k != "impacted_page_edits"},
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
                text = response.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0]
                current_result["release_note"] = json.loads(text)
            except json.JSONDecodeError:
                pass  # Keep original if retry fails

        if doc_issues:
            self.system_prompt = DOC_PAGE_PROMPT
            retry_msg = f"""Your previous doc page had these problems:
{chr(10).join('- ' + w for w in doc_issues)}

Fix ALL of these issues. Here is the context again:
{context}

Return the corrected doc page as JSON (include impacted_page_edits)."""
            response = self.run(retry_msg)
            try:
                text = response.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0]
                result = json.loads(text)
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

        for filename in asset_filenames:
            if filename not in release_content:
                warnings.append(f"Asset '{filename}' missing from release note")
            if filename not in doc_content:
                warnings.append(f"Asset '{filename}' missing from doc page")

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
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
        except json.JSONDecodeError:
            return {"error": "Failed to parse revised output", "raw_response": response}
