"""Drafting agent — produces the actual documentation markdown.

Persistent agent (supports multi-turn for reviewer iteration).
"""

import json
import re
from pathlib import Path

from src.agents.base import BaseAgent
from src.config import PROJECT_ROOT

SYSTEM_PROMPT = r"""You are the Drafting Agent for the FlytBase documentation pipeline.

You produce TWO very different outputs from a single PM document. Understand the difference:

═══════════════════════════════════════════════════════════
RELEASE NOTE — customer-facing value story
═══════════════════════════════════════════════════════════

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

═══════════════════════════════════════════════════════════
DOC PAGE — operator reference
═══════════════════════════════════════════════════════════

Purpose: Tell the operator exactly how to use it. This is the reference document they
come back to after reading the release note. It must be COMPREHENSIVE — every detail,
every edge case, every constraint from the PM doc must be here.

The doc page must be LONG. Minimum 1500 words. If the PM doc has rich detail, the doc
page must reflect that. Do not summarize — expand. A person reading only the doc page
should be able to fully understand and use the feature without reading anything else.

Structure:
1. Frontmatter with description
2. H1 title
3. Overview — NOT just 2-3 sentences. Write a full paragraph (4-6 sentences) explaining:
   - What the feature does
   - Why it exists (what problem it solves)
   - How it fits into the broader product workflow
   - Who should use it and when
4. How It Works — a dedicated section explaining the underlying mechanism in plain English.
   Not steps — conceptual understanding. What happens under the hood when the operator
   uses this feature? What does the system do with the input?
5. Prerequisites — what needs to be set up before using this feature
6. Step-by-step configuration — numbered steps with bold action verbs.
   Each step must have:
   - The action (what to click/select/fill)
   - What happens as a result
   - Any sub-steps or options available
   - Screenshot placed immediately after the step it illustrates
   Do NOT compress multiple actions into one step. Keep steps atomic.
7. Constraints & Limitations — full table AND a paragraph explaining each constraint
   in context. Don't just list them — explain WHY each constraint exists if the PM doc
   mentions it.
8. Hardware compatibility table
9. Edge Cases & Troubleshooting — cover every edge case the PM doc mentions.
   Format as: problem → cause → solution.
10. Related pages

Voice rules:
- Professional, instructional, direct. Imperative for steps.
- "Navigate to", "Click", "Select" — not "you should navigate to".
- No narrative storytelling in steps. But the Overview and How It Works sections
  should read as proper explanatory prose, not bullets.
- Every detail from the PM doc must appear somewhere in the doc page. Nothing is
  too minor to include if the PM doc mentioned it.

═══════════════════════════════════════════════════════════
IMAGE PLACEMENT RULES
═══════════════════════════════════════════════════════════

You receive a list of assets with filenames and descriptions.
You also receive an ASSET MAP that tells you exactly where each image goes.

- Use this syntax: ![descriptive alt text](assets/{filename})
- Place each image IMMEDIATELY AFTER the paragraph that describes what it shows.
- The hero image/GIF goes right after the YouTube embed at the very top.
- EVERY provided asset MUST appear in the output. If you have 4 assets, there
  must be 4 image references in the release note and 4 in the doc page.

═══════════════════════════════════════════════════════════
FORMATTING
═══════════════════════════════════════════════════════════

- Horizontal rules (---) between every major section.
- Tables with all rows/columns from PM doc. Use | for tables.
- Bold UI elements: **Add Pointer**, **Save feedback**.
- Navigation paths with >: **Verkos AI > Site Pointers**.
- GitBook hints: {% hint style="info" %} ... {% endhint %}
- Frontmatter: ---\ndescription: >-\n  Summary here.\n---

═══════════════════════════════════════════════════════════
CRITICAL REQUIREMENTS
═══════════════════════════════════════════════════════════

1. EVERY section from the PM document must appear in the release note.
   Count the PM doc's H2 headings. Your release note must cover ALL of them.
2. EVERY asset must be embedded with ![alt](assets/filename) in BOTH outputs.
3. YouTube link MUST be the FIRST thing after the H1 title in the release note.
4. Release note must be 3000+ characters. Doc page must be 4000+ characters.
5. Do NOT genericize. If the PM doc says "a water-filled well gives off the
   same thermal signature as a shallow pond" — use those exact words.

YOUR OUTPUT MUST BE A SINGLE JSON OBJECT:
{
  "release_note": {
    "filename": "feature-slug.md",
    "frontmatter": "---\ndescription: >-\n  ...\n---",
    "content": "full markdown content"
  },
  "doc_page": {
    "filename": "feature-slug.md",
    "target_ia_node": "IA node ID",
    "target_path": "folder path",
    "frontmatter": "---\ndescription: >-\n  ...\n---",
    "content": "full markdown content"
  },
  "impacted_page_edits": [
    {
      "source_url": "URL of the existing page",
      "ia_node": "node ID",
      "edit_description": "what changed and why",
      "section_heading": "exact heading text to locate in the file, e.g. '## Verkos AI: Detect Anything Agents'",
      "patch_mode": "append | replace",
      "patch_content": "the markdown content to append after the section or replace the section with"
    }
  ]
}

impacted_page_edits rules:
- section_heading must be the EXACT heading as it appears in the existing page (copy from corpus results).
- patch_mode "append": adds patch_content after the section's existing content, before the next heading.
- patch_mode "replace": replaces everything between section_heading and the next heading.
- Use "append" for adding cross-references. Use "replace" only if the section content is wrong.
- patch_content must be valid markdown, ready to insert as-is.

Return ONLY valid JSON. No markdown wrapping.
"""


class DraftingAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="drafting",
            system_prompt=SYSTEM_PROMPT,
            temperature=0.3,
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
        """Parse [SCREENSHOT] and [GIF] markers from PM doc, map to provided assets."""
        # Find all markers in PM doc order
        markers = []
        for match in re.finditer(r'\*?\*?\[(?:SCREENSHOT|HERO GIF|GIF)[^\]]*\]\*?\*?', pm_doc):
            # Get the surrounding context
            start = max(0, match.start() - 200)
            context = pm_doc[start:match.start()].strip().split('\n')[-2:]
            markers.append({
                "marker": match.group(),
                "context": " ".join(context),
            })

        # Also find *Capture: ...* descriptions
        capture_descs = re.findall(r'\*Capture:\s*(.+?)\*', pm_doc, re.DOTALL)

        lines = ["ASSET MAP — where each image should be placed:\n"]

        for i, filename in enumerate(asset_filenames):
            vision_desc = ""
            if i < len(vision_output):
                vision_desc = vision_output[i].get("description", "")

            marker_context = ""
            if i < len(markers):
                marker_context = f"PM doc marker: {markers[i]['marker']}\n   Context before marker: {markers[i]['context']}"

            capture_desc = ""
            if i < len(capture_descs):
                capture_desc = f"PM doc description: {capture_descs[i][:150]}"

            lines.append(f"Asset {i+1}: {filename}")
            if vision_desc:
                lines.append(f"   Vision description: {vision_desc}")
            if marker_context:
                lines.append(f"   {marker_context}")
            if capture_desc:
                lines.append(f"   {capture_desc}")
            lines.append("")

        return "\n".join(lines)

    def draft(
        self,
        pm_doc: str,
        research_output: dict,
        vision_output: list[dict],
        asset_filenames: list[str],
        youtube_link: str = "",
        release_month: str = "",
        transcript: str = "",
    ) -> dict:
        claude_md = self._load_claude_md()
        memory_context = self._load_relevant_memory()
        asset_map = self._build_asset_map(pm_doc, asset_filenames, vision_output)

        transcript_section = ""
        if transcript:
            transcript_section = f"""
--- CLUESO VIDEO TRANSCRIPT ---
This is the transcript from the feature demo video. It contains spoken explanations,
step-by-step walkthroughs, and real operator context. Use it to:
- Fill in procedural details that the PM doc may have skipped
- Extract exact UI element names and navigation paths as spoken by the presenter
- Pick up on edge cases or tips mentioned verbally
- Add depth to the How It Works and step-by-step sections

{transcript}
--- END TRANSCRIPT ---
"""

        user_message = f"""Produce the release note and doc page from this PM document.

--- PM DOCUMENT (use ALL of it, do NOT skip sections) ---
{pm_doc}
--- END PM DOCUMENT ---
{transcript_section}
--- RESEARCH OUTPUT ---
{json.dumps(research_output, indent=2)}
--- END RESEARCH OUTPUT ---

--- {asset_map} ---

--- YOUTUBE LINK ---
{youtube_link if youtube_link else "None provided."}
PLACEMENT: Immediately after the H1 title in the release note. At the end of How It Works in the doc page.
--- END YOUTUBE ---

--- MEMORY FILES ---
{memory_context}
--- END MEMORY ---

--- CLAUDE.MD CONVENTIONS ---
{claude_md}
--- END CLAUDE.MD ---

Release month: {release_month or "current month"}

CHECKLIST before you respond:
[ ] Release note covers ALL H2 sections from PM doc
[ ] YouTube embed is the FIRST thing after H1 in release note
[ ] Every asset filename appears in BOTH release note and doc page
[ ] Release note is 3000+ chars, doc page is 4000+ chars
[ ] Real-world examples from PM doc are preserved word-for-word
[ ] Hardware compatibility table is included
[ ] Constraints table is included
[ ] Steps have bold headers with full detail paragraphs
[ ] Transcript details (if provided) are incorporated into steps and How It Works"""

        self.conversation_history = [{"role": "user", "content": user_message}]
        response = self.run(user_message)
        self.conversation_history.append({"role": "assistant", "content": response})

        try:
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(text)
        except json.JSONDecodeError:
            return {"error": "Failed to parse drafting output", "raw_response": response}

        warnings = self._validate_output(result, asset_filenames, youtube_link, pm_doc)
        if warnings:
            result["_validation_warnings"] = warnings

        return result

    def _validate_output(self, result, asset_filenames, youtube_link, pm_doc):
        warnings = []
        release_content = result.get("release_note", {}).get("content", "")
        doc_content = result.get("doc_page", {}).get("content", "")
        all_content = release_content + doc_content

        for filename in asset_filenames:
            if filename not in release_content:
                warnings.append(f"Asset '{filename}' missing from release note")
            if filename not in doc_content:
                warnings.append(f"Asset '{filename}' missing from doc page")

        if youtube_link and youtube_link not in release_content:
            warnings.append("YouTube link not in release note")

        # Check YouTube is near the top (within first 500 chars)
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
