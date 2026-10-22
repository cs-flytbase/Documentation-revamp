"""Drafting agent: turns the PM document into a release note and a doc page.

Two separate model calls, one per output, because the two are different
documents with different readers (senior review, 2026-10-19):
1. Release note: a short marketable note (problem, what it is, why it
   matters, who it is for, takeaways). No steps. src/release_template.py
2. Doc page: the absolute truth on how to use the feature, from a 20-section
   master template; sections the material does not cover are left out, and
   every fact is stated once. src/doc_template.py, src/doc_checks.py

In both, the model only fills in fields. Code renders the page in a fixed
template, checks the fields, retries once with the exact problems, applies
the style checker, and reports whatever is left to the reviewer.
"""

import json
import re

from src.agents.base import BaseAgent
from src.config import PROJECT_ROOT
from src.doc_checks import redundancy_problems
from src.doc_template import DOC_PAGE_PROMPT, check_fields as check_doc_fields, render as render_doc
from src.release_template import RELEASE_NOTE_PROMPT, check_fields as check_release_fields, render as render_release
from src.style_check import autofix, errors as style_errors, lint
from src.style_guide import feedback_block, style_block

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



VALID_AUDIENCES = ("release", "doc", "both")


def route_assets(asset_filenames: list[str], audience: dict | None) -> tuple[list[str], list[str], list[str]]:
    """Split assets between the two outputs.

    Returns (doc_assets, release_required, release_optional). With no
    instructions every asset goes on the doc page and the release note picks
    the ones that introduce the release best. "release" keeps an asset off the
    doc page; "doc" keeps it off the release note; "both" puts it in both.
    """
    audience = {k: v for k, v in (audience or {}).items() if v in VALID_AUDIENCES}
    doc_assets = [a for a in asset_filenames if audience.get(a, "doc") in ("doc", "both")]
    release_required = [a for a in asset_filenames if audience.get(a) in ("release", "both")]
    release_optional = [a for a in asset_filenames if a not in audience]
    return doc_assets, release_required, release_optional


class DraftingAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="drafting",
            system_prompt="",  # Set per-call
            temperature=0.4,
            max_tokens=16384,
        )
        self.conversation_history: list[dict] = []

    # ── Context ────────────────────────────────────────────────────────

    def _load_claude_md(self) -> str:
        path = PROJECT_ROOT / "CLAUDE.md"
        return path.read_text() if path.exists() else ""

    def _docs_conventions(self) -> str:
        """CLAUDE.md conventions for doc pages, without its release-notes section
        ("storytelling"), which contradicts the release template."""
        text = self._load_claude_md()
        text = re.sub(r"### Release Notes.*?(?=\n### |\n## )", "", text, flags=re.S)
        text = re.sub(r"### Frontmatter Template \(release notes\).*?(?=\n### |\n## |\n---|\Z)", "", text, flags=re.S)
        return text[:4000]

    def _rules_for(self, kind: str) -> str:
        parts = [style_block(kind)]
        feedback = feedback_block(PROJECT_ROOT / "memory")
        if feedback:
            parts.append(feedback)
        if kind == "doc":
            parts.append(f"--- CONVENTIONS (CLAUDE.md, doc pages) ---\n{self._docs_conventions()}\n--- END CONVENTIONS ---")
        return "\n\n".join(parts)

    @staticmethod
    def _asset_block(title: str, files: list[str], descs: dict, note: str) -> str:
        if not files:
            return f"--- {title} ---\nNone.\n--- END {title} ---"
        lines = [f"--- {title} ---", note]
        for f in files:
            kind = "animated GIF" if f.lower().endswith(".gif") else "image"
            lines.append(f"- {f} ({kind}): {descs.get(f) or 'no description available'}")
        lines.append(f"--- END {title} ---")
        return "\n".join(lines)

    def _build_context(self, pm_doc: str, research_output: dict, transcript: str = "", exemplar: str = "") -> str:
        research = {
            "target_ia_node": research_output.get("target_ia_node", {}),
            "impacted_pages": research_output.get("impacted_pages", []),
            "feature_summary": research_output.get("feature_summary", ""),
        }
        parts = [f"--- PM DOCUMENT (the source material; use all of it, invent nothing) ---\n{pm_doc}\n--- END PM DOCUMENT ---"]
        if transcript:
            parts.append(f"--- VIDEO TRANSCRIPT ---\n{transcript[:8000]}\n--- END TRANSCRIPT ---")
        if exemplar:
            parts.append(f"--- EXISTING PAGE NEARBY (for terminology only, not structure) ---\n{exemplar[:4000]}\n--- END ---")
        parts.append(f"--- RESEARCH OUTPUT ---\n{json.dumps(research, indent=2)}\n--- END RESEARCH OUTPUT ---")
        return "\n\n".join(parts)

    # ── Model calls ────────────────────────────────────────────────────

    def _call_json(self, prompt: str, message: str, label: str) -> tuple[dict | None, str]:
        """One model call that must return JSON, with one retry on a refusal.
        Returns (data, error); data is None when the output is unusable."""
        self.system_prompt = prompt
        response = self.run(message)
        if _looks_like_refusal(response):
            print(f"    [Drafting] Model refused the {label}. Retrying once. Reply was: {response.strip()[:160]}")
            response = self.run(
                message
                + "\n\nIMPORTANT: This is FlytBase's own product documentation, written from the "
                  "PM document supplied above. Respond ONLY with the required JSON object. "
                  "If any instruction seems to conflict with another, follow the JSON schema "
                  "and the PM document, and leave out anything you cannot produce."
            )
        try:
            return _extract_json(response), ""
        except json.JSONDecodeError:
            return None, _diagnose_bad_output(response)

    def _retry_fields(self, prompt: str, message: str, fields: dict, problems: list[str], label: str) -> dict:
        """One targeted retry. Keeps the previous fields if the retry is unusable."""
        print(f"    [Drafting] Fixing the {label}: {problems[:6]}{' ...' if len(problems) > 6 else ''}")
        fixed, _ = self._call_json(
            prompt,
            message
            + "\n\nYour previous answer was:\n" + json.dumps(fields, ensure_ascii=False)
            + "\n\nFix ONLY these problems and return the complete JSON object again:\n- "
            + "\n- ".join(problems),
            label,
        )
        return fixed if isinstance(fixed, dict) else fields

    # ── Style ──────────────────────────────────────────────────────────

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

    @staticmethod
    def _h1(content: str) -> str:
        m = re.search(r"^#\s+(.+)$", content or "", re.M)
        return m.group(1).strip() if m else ""

    # ── Draft ──────────────────────────────────────────────────────────

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
        asset_audience: dict | None = None,
        feature_flag: str = "",
        fact_check: list[str] | None = None,
    ) -> dict:
        descs = {v.get("file_name", ""): v.get("description", "") for v in (vision_output or [])}
        # Alt text on the page: the short alt_text, not the full description,
        # which stays in the drafting context below.
        alts = {v.get("file_name", ""): (v.get("alt_text") or v.get("description", "")) for v in (vision_output or [])}
        doc_assets, rel_required, rel_optional = route_assets(asset_filenames, asset_audience)
        # One page of a subsections run sees every asset of the run but should
        # use only those that show its own feature. Nothing is required of it;
        # coverage across all the pages is checked by the orchestrator.
        whole_run = not subsection_focus
        if not whole_run:
            rel_optional, rel_required = rel_required + rel_optional, []
        context = self._build_context(pm_doc, research_output, transcript, exemplar)
        # Videos are read frame by frame but never embedded; what they show is
        # source material for both outputs.
        videos = {k: v for k, v in descs.items() if k and k not in asset_filenames and v}
        if videos:
            context += "\n\n--- WHAT THE SOURCE VIDEOS SHOW (source material, not embeddable) ---\n" + "\n".join(
                f"- {k}: {v}" for k, v in videos.items()) + "\n--- END ---"
        if fact_check:
            # Repair pass after verification (P0, 2026-10-22): the previous
            # draft made these claims and the source does not support them.
            context += ("\n\n--- FACT CHECK: FIX THESE ---\nThe previous draft made these claims, which the source "
                        "material does not support. Remove or correct each one, using only the source. Do not add "
                        "any new claim.\n" + "\n".join(f"- {c}" for c in fact_check) + "\n--- END FACT CHECK ---")
        if subsection_focus:
            context += f"""

--- SUBSECTION FOCUS ---
You are drafting ONE SUBSECTION of a larger grouped release. Cover ONLY this scope.
Title: {subsection_focus['title']}
Scope: {subsection_focus['summary']}
Filename: {subsection_focus['slug']}.md
Use only the assets relevant to this scope.
--- END SUBSECTION FOCUS ---"""

        wants_release = mode in ("both", "release_only")
        wants_doc = mode in ("both", "doc_only")
        release_fields, doc_fields = {}, {}
        release_msg = doc_msg = ""

        # ── Release note fields ──
        if wants_release:
            flag_block = (f"--- FEATURE FLAG ---\nThis feature is behind the `{feature_flag}` feature flag.\n--- END ---"
                          if feature_flag else "--- FEATURE FLAG ---\nNone: leave \"access\" empty.\n--- END ---")
            release_msg = "\n\n".join([
                "Fill in the release note fields from this source material.", context,
                self._asset_block("RELEASE ASSETS", rel_required + rel_optional, descs,
                                  f"REQUIRED (use each exactly once): {rel_required or 'none'}. "
                                  f"OPTIONAL (use the ones that introduce the release best): {rel_optional or 'none'}."),
                flag_block, self._rules_for("release"),
            ])
            print("    [Drafting] Generating release note...")
            release_fields, err = self._call_json(RELEASE_NOTE_PROMPT, release_msg, "release note")
            if release_fields is None:
                return {"error": f"Release note not generated - {err}"}
            problems = check_release_fields(release_fields, rel_required, rel_optional, feature_flag)
            if (rel_required or rel_optional) and not (release_fields.get("hero_asset") or any(
                    c.get("asset") for c in release_fields.get("capabilities") or [] if isinstance(c, dict))):
                problems.append("release note uses no image: set hero_asset to the asset that best introduces the release")
            if problems:
                release_fields = self._retry_fields(RELEASE_NOTE_PROMPT, release_msg, release_fields, problems, "release note")
        else:
            print("    [Drafting] Skipping release note (mode: doc_only)")

        # ── Doc page fields ──
        if wants_doc:
            doc_msg = "\n\n".join([
                "Fill in the doc page fields from this source material.", context,
                self._asset_block("DOC ASSETS", doc_assets, descs,
                                  "Use EVERY one exactly once, on the step or section it shows." if whole_run else
                                  "These cover the whole release. Use ONLY the ones that show this subsection, each at most once, on the step or section it shows."),
                self._rules_for("doc"),
            ])
            print("    [Drafting] Generating doc page + impacted edits...")
            doc_fields, err = self._call_json(DOC_PAGE_PROMPT, doc_msg, "doc page")
            if doc_fields is None:
                return {"error": f"Doc page not generated - {err}"}
            problems = check_doc_fields(doc_fields, doc_assets, require_all=whole_run)
            if problems:
                doc_fields = self._retry_fields(DOC_PAGE_PROMPT, doc_msg, doc_fields, problems, "doc page")
            # Repetition is the one problem a model often reintroduces while fixing
            # others (live e2e 2026-10-22: "40 m" in two sections after one retry).
            # One more pass on repetition alone, naming where each fact stays.
            repeats = redundancy_problems(doc_fields)
            if repeats:
                doc_fields = self._retry_fields(DOC_PAGE_PROMPT, doc_msg, doc_fields, repeats, "doc page repetition")
        else:
            print("    [Drafting] Skipping doc page (mode: release_only)")

        # ── Render, style-check, one style retry ──
        style_report, gaps = {}, []
        doc_result, release_result = {}, {}
        if doc_fields:
            doc_result, gaps = render_doc(doc_fields, doc_assets, youtube_link, alts, place_leftovers=whole_run)
            doc_result, issues = self._style_pass("doc", doc_result)
            if style_errors(issues):
                doc_fields = self._retry_fields(DOC_PAGE_PROMPT, doc_msg, doc_fields,
                                                [f"style: {i.message}" for i in style_errors(issues)], "doc page style")
                doc_result, gaps = render_doc(doc_fields, doc_assets, youtube_link, alts, place_leftovers=whole_run)
                doc_result, issues = self._style_pass("doc", doc_result)
            if issues:
                style_report["doc_page"] = [f"[{i.severity}] {i}" for i in issues][:15]
            # Repetition that survived the retry goes to the reviewer.
            repeats = redundancy_problems(doc_fields)
            if repeats:
                style_report.setdefault("doc_page", []).extend(f"[warn] {r}" for r in repeats)

        if release_fields:
            doc_title = self._h1(doc_result.get("content", "")) if doc_result else ""
            release_result = render_release(release_fields, rel_required, youtube_link, doc_title, alts,
                                            feature_flag, rel_optional)
            release_result, issues = self._style_pass("release", release_result)
            if style_errors(issues):
                release_fields = self._retry_fields(RELEASE_NOTE_PROMPT, release_msg, release_fields,
                                                    [f"style: {i.message}" for i in style_errors(issues)], "release note style")
                release_result = render_release(release_fields, rel_required, youtube_link, doc_title, alts,
                                                feature_flag, rel_optional)
                release_result, issues = self._style_pass("release", release_result)
            if issues:
                style_report["release_note"] = [f"[{i.severity}] {i}" for i in issues][:15]

        combined = {
            "release_note": release_result,
            "doc_page": doc_result,
            "impacted_page_edits": (doc_fields or {}).get("impacted_page_edits", []) if wants_doc else [],
        }
        if style_report:
            combined["_style_report"] = style_report
        if gaps:
            combined["_doc_gaps"] = gaps
        return combined

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
