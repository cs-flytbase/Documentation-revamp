"""The writing rules every draft follows, in one place, the same on every run.

These are the judgment rules a checker cannot fully verify. They are kept
short on purpose and capped (MAX_RULES): a new rule replaces or merges with an
old one through a reviewed PR, instead of being appended forever. Rules a
machine CAN check (banned phrases, product spellings, raw HTML, false claims)
live in config/style_rules.yaml and are enforced by src/style_check.py.

Migrated on 2026-10-19 from memory/voice_corrections.md,
memory/formatting_corrections.md and memory/placement_corrections.md, which
had grown to 46 rules pasted raw into every prompt, several of them
contradicting each other and the release note template.
"""
import re
from datetime import date
from pathlib import Path

MAX_RULES = 15

# Applies to release notes and doc pages alike.
STYLE_RULES = [
    "Write plain technical English (ASD-STE100 style): short sentences, active voice, concrete terms.",
    "Say what the feature does, then why it matters. No storytelling hooks, no build-up, no marketing language.",
    'Address the reader as "you". Refer to the company as "FlytBase", never "we", "our" or "us".',
    "Use only facts from the source material. Never invent a number, limit, capability, setting, link or date.",
    "State limitations plainly. Do not soften or hide them.",
    "Use the exact on-screen labels in bold (**Video overlays**). Show navigation with >: **Settings > Telemetry**.",
    "Plain Markdown only, plus GitBook directives ({% hint %}, {% embed %}, {% content-ref %}, {% tabs %}) and code fences. No raw HTML of any kind.",
    "Images: ![descriptive alt text](path), never <img> or <figure>. Links: [text](url). Describe inline icons in words.",
    "Lists: at most one level of nesting.",
    "Numbers: spell out one to nine, digits for 10 and above, and always digits with units (5 m, 3 drones).",
    "Spell out an acronym on first use with the short form in brackets, then use the short form.",
    "Headings are single Title Case labels: no slashes, no questions, no dual titles.",
    "Use ASCII punctuation only: no em dashes, curly quotes or ellipsis characters.",
]
assert len(STYLE_RULES) <= MAX_RULES, "The style guide is capped. Merge or replace a rule instead of adding one."

# Rules that only make sense for doc pages (the full technical reference).
DOC_PAGE_RULES = [
    'Constraints tables use the column headers "Components" and "Details".',
    "Whenever hardware compatibility is mentioned, add a sentence linking to the hardware compatibility page.",
    "Place the page in the section of the information architecture it belongs to; never duplicate a page that exists.",
    "ON-PREMISE PAGES ONLY (paths under on-premise/): every page in Installation, System and Organization "
    "Configuration, Device Management, and Maintenance and Operations opens, immediately after the H1, with "
    'this exact callout: {% hint style="danger" %} Follow this documentation exactly as written. Do not '
    "deviate based on individual discretion. {% endhint %} (not on cloud pages, not on release notes).",
    "ON-PREMISE PAGES ONLY: rollback is a section inside the System Updates page, never a page of its own.",
]


def style_block(kind: str) -> str:
    """The rules block for a prompt. kind is "release" or "doc"."""
    lines = ["WRITING RULES (follow all of them):"]
    lines += [f"{i}. {r}" for i, r in enumerate(STYLE_RULES, 1)]
    if kind == "doc":
        lines.append("")
        lines.append("DOC PAGE RULES:")
        lines += [f"- {r}" for r in DOC_PAGE_RULES]
    return "\n".join(lines)


# ── Feedback that arrived after the migration ─────────────────────────
#
# The memory updater and Son of Anton still record reviewer feedback in
# memory/*.md. Everything up to MIGRATED_ON is now in the rules above or in
# config/style_rules.yaml. Newer entries are shown to the model, capped, until
# someone folds them into the rules through a PR. A growing pile never reaches
# the prompt again.

MIGRATED_ON = date(2026, 10, 19)
MAX_PENDING_FEEDBACK = 12
_FEEDBACK_FILES = ["voice_corrections.md", "formatting_corrections.md", "placement_corrections.md", "terminology.md"]
_DATED = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})\]\s*(.*)$")


def pending_feedback(memory_dir: Path, limit: int = MAX_PENDING_FEEDBACK) -> list[str]:
    """Reviewer feedback newer than the migration, newest first, at most `limit`."""
    entries = []
    for name in _FEEDBACK_FILES:
        path = memory_dir / name
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            m = _DATED.match(line.strip())
            if not m:
                continue
            day = date.fromisoformat(m.group(1))
            if day >= MIGRATED_ON:
                entries.append((day, m.group(2).strip()))
    entries.sort(key=lambda e: e[0], reverse=True)
    return [text for _, text in entries[:limit]]


def feedback_block(memory_dir: Path) -> str:
    items = pending_feedback(memory_dir)
    if not items:
        return ""
    body = "\n".join(f"- {t}" for t in items)
    return ("RECENT REVIEWER FEEDBACK (newer than the rules above; follow it unless it conflicts "
            "with the template or the rules, in which case the template and rules win):\n" + body)
