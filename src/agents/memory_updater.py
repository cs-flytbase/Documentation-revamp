"""Memory updater — parses reviewer feedback and appends rules to memory files.

Runs after a pipeline run when you provide feedback.
Reads your feedback, determines which memory file each lesson belongs in, appends the rule.

Usage:
    python -m src.agents.memory_updater --feedback "YouTube was placed at bottom, not top" --run output/run_xxx
"""

import argparse
import json
from datetime import date
from pathlib import Path

from src.agents.base import BaseAgent
from src.config import PROJECT_ROOT

ALLOWED_FILES = {"voice_corrections.md", "placement_corrections.md", "formatting_corrections.md", "terminology.md"}

SYSTEM_PROMPT = """You are the Memory Updater for the FlytBase documentation pipeline.

Your job: given reviewer feedback on a documentation draft, determine what lessons should be
saved to the memory system so the drafting agent does not repeat the same mistakes.

You receive:
- The reviewer's feedback
- The original draft that was reviewed (optional)

You must return a JSON array of memory updates:
[
  {
    "file": "voice_corrections.md | placement_corrections.md | formatting_corrections.md | terminology.md",
    "rule": "the specific rule to append, written as a clear instruction the drafting agent can follow",
    "category": "voice | placement | formatting | terminology"
  }
]

File assignment rules:
- voice_corrections.md: tone, language, word choice, narrative structure, what to lead with
- placement_corrections.md: which IA section a page belongs in, where in the docs hierarchy a feature lives
- formatting_corrections.md: heading levels, table structure, hint block usage, step numbering, image placement
- terminology.md: product name spellings, capitalization, acronyms, hyphenation rules

Only save lessons that are GENERALIZABLE across future runs.
If a reviewer fixes a one-off typo, do not save it.
Write rules as direct instructions: "Always use X" or "Never do Y when Z".
Return an empty array [] if there are no generalizable lessons.
Return ONLY valid JSON. No markdown wrapping.
"""


class MemoryUpdater(BaseAgent):
    def __init__(self):
        super().__init__(
            name="memory_updater",
            system_prompt=SYSTEM_PROMPT,
            model="claude-sonnet-4-6",
            temperature=0.1,
            max_tokens=2048,
        )

    def analyze_corrections(self, feedback: str, original_draft: str = "") -> list[dict]:
        prompt = f"""Here is the reviewer's feedback on a documentation draft:

--- FEEDBACK ---
{feedback}
--- END FEEDBACK ---

{f'--- ORIGINAL DRAFT ---{chr(10)}{original_draft[:3000]}{chr(10)}--- END DRAFT ---' if original_draft else ''}

Analyze this feedback and return the memory updates as specified."""

        response = self.run(prompt)

        try:
            text = response.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            updates = json.loads(text)
            # Only write to allowed files — never create new feature files
            return [u for u in updates if u.get("file") in ALLOWED_FILES]
        except json.JSONDecodeError:
            return []

    def apply_updates(self, updates: list[dict]) -> list[str]:
        memory_dir = PROJECT_ROOT / "memory"
        today = date.today().isoformat()
        applied = []

        for update in updates:
            filename = update["file"]
            rule = update["rule"]
            category = update.get("category", "general")

            filepath = memory_dir / filename
            if not filepath.exists():
                continue  # Never create files outside the allowed set

            with open(filepath, "a") as f:
                f.write(f"\n- [{today}] [{category}] {rule}\n")

            applied.append(f"{filename}: {rule}")

        return applied

    def update_memory(self, feedback: str, original_draft: str = "") -> list[str]:
        updates = self.analyze_corrections(feedback, original_draft)
        if not updates:
            return ["No generalizable lessons found in this feedback."]
        return self.apply_updates(updates)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update memory files from reviewer feedback.")
    parser.add_argument("--feedback", required=True, help="Reviewer feedback text")
    parser.add_argument("--run", default="", help="Path to pipeline run output directory (optional)")
    args = parser.parse_args()

    original_draft = ""
    if args.run:
        run_path = Path(args.run)
        release_files = list(run_path.glob("release_note/*.md"))
        if release_files:
            original_draft = release_files[0].read_text()

    updater = MemoryUpdater()
    results = updater.update_memory(args.feedback, original_draft)

    print("\nMemory updates applied:")
    for r in results:
        print(f"  - {r}")
