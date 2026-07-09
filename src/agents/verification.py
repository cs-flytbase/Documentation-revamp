"""Verification agent — fact-checks generated content against source material.

Runs after the drafting agent, before publishing. Checks every factual claim
in the release note and doc page against the PM doc and corpus chunks.
Blocks PR creation if critical issues (hallucinations) are found.
"""

import json

from src.agents.base import BaseAgent

SYSTEM_PROMPT = """You are the Verification Agent for the FlytBase documentation pipeline.

Your job: fact-check AI-generated documentation against the source material.
You receive the generated release note and doc page, plus the original PM document
and relevant corpus chunks. You must verify every factual claim.

For each piece of generated content, check:
1. Is every stated fact present in the PM doc, transcript, or corpus?
2. Are any numbers, measurements, or constraints invented (not in the source)?
3. Are any capabilities or features mentioned that the PM doc does NOT describe?
4. Are any limitations or edge cases fabricated?
5. Are product names and terminology correct?

Categorize each issue:
- "critical": False information — a claim that contradicts the source or was invented.
  Examples: made-up numbers, invented constraints, features that don't exist,
  incorrect product names, fabricated technical details.
- "warning": Minor assumption — a reasonable inference but not explicitly stated.
  Examples: slightly reworded constraint, implied workflow that isn't explicit,
  generic phrasing that could be more specific.

Return a JSON object:
{
  "verified": true/false,
  "faithfulness_score": 0-10,
  "issues": [
    {
      "severity": "critical | warning",
      "location": "release_note | doc_page",
      "claim": "the exact text that is problematic",
      "reason": "why this is flagged — what's wrong with it",
      "source_check": "what the PM doc actually says, or 'not mentioned in PM doc'"
    }
  ],
  "summary": "1-2 sentence overall assessment"
}

Rules:
- "verified" must be false if there are ANY critical issues.
- faithfulness_score: 10 = perfectly faithful, 0 = mostly hallucinated.
  8-10: minor wording differences only. 5-7: some assumptions made. Below 5: significant fabrication.
- Be thorough but fair. Do not flag paraphrasing as hallucination.
  The drafting agent is allowed to rephrase — only flag when meaning changes or facts are invented.
- If numbers appear in the output (distances, percentages, counts), they MUST appear
  in the PM doc. Any number not in the source material is a critical issue.
- Return ONLY valid JSON. No markdown wrapping.
"""


class VerificationAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="verification",
            system_prompt=SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=4096,
        )

    def _format_corpus_chunks(self, corpus_chunks: list[dict]) -> str:
        """Format corpus chunks for the verification prompt."""
        lines = []
        for chunk in corpus_chunks[:10]:
            if isinstance(chunk, dict):
                heading = chunk.get("heading", chunk.get("source_url", ""))
                content = chunk.get("content_preview", chunk.get("content", ""))[:500]
                lines.append(f"- {heading}: {content}")
        return "\n".join(lines) if lines else "No corpus chunks available."

    def verify(
        self,
        release_note_content: str,
        doc_page_content: str,
        pm_doc: str,
        corpus_chunks: list[dict],
        transcript: str = "",
    ) -> dict:
        """Verify generated content against source material."""
        corpus_text = self._format_corpus_chunks(corpus_chunks)

        transcript_section = ""
        if transcript:
            transcript_section = f"""
--- TRANSCRIPT (also a valid source) ---
{transcript[:4000]}
--- END TRANSCRIPT ---
"""

        prompt = f"""Verify the following generated documentation against the source material.

--- GENERATED RELEASE NOTE ---
{release_note_content}
--- END RELEASE NOTE ---

--- GENERATED DOC PAGE ---
{doc_page_content}
--- END DOC PAGE ---

--- ORIGINAL PM DOCUMENT (primary source of truth) ---
{pm_doc}
--- END PM DOCUMENT ---
{transcript_section}
--- EXISTING CORPUS CONTEXT ---
{corpus_text}
--- END CORPUS ---

Check EVERY factual claim in both outputs. Flag anything not supported by the sources.
Pay special attention to:
- Numbers and measurements (must be in the PM doc)
- Feature capabilities (must be described in the PM doc)
- Constraints and limitations (must match the PM doc exactly)
- Technical details about how the feature works (must be in the PM doc)

Return the verification result as JSON."""

        response = self.run(prompt)

        try:
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(text)

            # Enforce: verified is determined by critical issues only
            critical_issues = [i for i in result.get("issues", []) if i.get("severity") == "critical"]
            result["verified"] = len(critical_issues) == 0

            return result
        except json.JSONDecodeError:
            return {
                "verified": False,
                "faithfulness_score": 0,
                "issues": [{"severity": "critical", "location": "both", "claim": "N/A",
                           "reason": "Verification agent failed to return valid JSON",
                           "source_check": "N/A"}],
                "summary": "Verification failed — could not parse response.",
            }
