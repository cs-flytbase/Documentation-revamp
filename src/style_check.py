"""Deterministic style checks for release notes and doc pages.

Reviewer feedback used to be appended to memory/*.md and pasted into the
prompt, hoping the model would follow every rule. That does not scale: past a
few dozen rules some are ignored and some contradict each other. Rules that a
machine can check live in config/style_rules.yaml and are enforced here, on
every draft, however many there are.

autofix() repairs what is safe to repair mechanically (typography, product
name spellings). lint() reports the rest as issues: "error" issues are sent
back to the model as an exact list to fix, "warn" issues are shown to the
reviewer in the PR.
"""
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

RULES_PATH = Path(__file__).resolve().parents[1] / "config" / "style_rules.yaml"


@dataclass
class Issue:
    rule: str
    severity: str        # "error" or "warn"
    line: int            # 1-based line in the checked text
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}"


@lru_cache(maxsize=1)
def load_rules(path: str = str(RULES_PATH)) -> dict:
    return yaml.safe_load(Path(path).read_text()) or {}


# ── Masking ────────────────────────────────────────────────────────────
# Code, URLs, link targets and directives are never style-checked or fixed:
# "docs.flytbase.com" must not become "docs.FlytBase.com".

_MASKS = [
    re.compile(r"```.*?```", re.S),                 # fenced code
    re.compile(r"`[^`\n]+`"),                       # inline code
    re.compile(r"\{%.*?%\}"),                       # GitBook directives
    re.compile(r"\]\([^)]*\)"),                     # link / image targets
    re.compile(r"https?://\S+"),                    # bare URLs
    re.compile(r"^---\n.*?\n---\n", re.S),          # frontmatter (checked separately)
]


def _mask(text: str):
    """Replace protected spans with same-length placeholders. Returns (masked, spans)."""
    spans = []
    chars = list(text)
    for pattern in _MASKS:
        for m in pattern.finditer(text):
            if any(s <= m.start() < e for s, e in spans):
                continue
            spans.append((m.start(), m.end()))
            for i in range(m.start(), m.end()):
                if chars[i] != "\n":
                    chars[i] = "\x00"
    return "".join(chars), spans


def _unmask(fixed_masked: str, original: str) -> str:
    """Put protected characters back. Only valid when lengths did not change."""
    return "".join(o if f == "\x00" else f for f, o in zip(fixed_masked, original))


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# ── Autofix ────────────────────────────────────────────────────────────

_TYPOGRAPHY = [
    (re.compile(r"(?<=\d)\s*[–—]\s*(?=\d)"), "-"),     # ranges: 1–5 -> 1-5
    (re.compile(r"\s*[—–]\s*"), ", "),                  # em/en dash -> comma
    (re.compile(r"[“”]"), '"'),
    (re.compile(r"[‘’]"), "'"),
    (re.compile(r"…"), "..."),
    (re.compile(r" | "), " "),
]


def autofix(text: str, rules: dict | None = None) -> tuple[str, list[str]]:
    """Apply safe mechanical fixes outside code, URLs and link targets.

    Returns (fixed text, list of human-readable fixes applied).
    """
    rules = rules if rules is not None else load_rules()
    fixes = []
    out = []
    # Work segment by segment so replacements never touch a masked span.
    masked, _ = _mask(text)
    segments = re.split(r"(\x00+)", masked)
    pos = 0
    for seg in segments:
        original = text[pos:pos + len(seg)]
        pos += len(seg)
        if not seg or seg[0] == "\x00":
            out.append(original)
            continue
        fixed = seg
        for pattern, repl in _TYPOGRAPHY:
            fixed, n = pattern.subn(repl, fixed)
            if n:
                fixes.append(f"typography: {pattern.pattern!r} x{n}")
        for term in rules.get("terminology", []):
            if not term.get("right"):
                continue
            fixed, n = re.subn(term["wrong"], term["right"], fixed)
            if n:
                fixes.append(f"terminology: -> {term['right']} x{n}")
        out.append(fixed)
    return "".join(out), fixes


# ── Lint ───────────────────────────────────────────────────────────────

_WE = re.compile(r"\b(we|We|our|Our|ours|us)\b")
_TAG = None


def _frontmatter_description(text: str) -> str | None:
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return None
    fm = m.group(1)
    d = re.search(r"^description:\s*(>-?|\|)?\s*\n?((?:\s{2,}.*\n?)*|.*)$", fm, re.M)
    if not d:
        return ""
    return " ".join(part.strip() for part in d.group(2).splitlines() if part.strip())


def _prose_lines(masked: str):
    """(line number, text) for lines that are prose: not headings, tables or list markers only."""
    for i, line in enumerate(masked.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith(("#", "|", "{%", "\x00", "!")):
            continue
        yield i, s


def lint(text: str, kind: str = "doc", rules: dict | None = None) -> list[Issue]:
    """Report style problems. kind is "release" or "doc"."""
    rules = rules if rules is not None else load_rules()
    issues: list[Issue] = []
    masked, _ = _mask(text)
    lower = masked.lower()

    for item in rules.get("banned_phrases", []):
        phrase = item["phrase"].lower()
        for m in re.finditer(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lower):
            issues.append(Issue("banned_phrase", "error", _line_of(masked, m.start()),
                                f'remove marketing/storytelling phrase "{text[m.start():m.end()]}"'))

    if kind == "doc":
        for item in rules.get("doc_banned_phrases", []):
            phrase = item["phrase"].lower()
            for m in re.finditer(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lower):
                issues.append(Issue("doc_banned_phrase", "error", _line_of(masked, m.start()),
                                    f'doc pages state facts: remove "{text[m.start():m.end()]}"'))
        for item in rules.get("uk_spellings", []):
            for m in re.finditer(rf"(?<![A-Za-z]){re.escape(item['wrong'])}(?![A-Za-z])", masked, re.I):
                if "**" in masked[max(0, m.start() - 40):m.start()] and "**" in masked[m.end():m.end() + 40]:
                    continue  # inside a bold on-screen label: keep the label as it is
                issues.append(Issue("us_spelling", "error", _line_of(masked, m.start()),
                                    f'US English: "{m.group(0)}" -> "{item["right"]}"'))

    for claim in rules.get("forbidden_claims", []):
        for m in re.finditer(claim["pattern"], masked):
            issues.append(Issue("forbidden_claim", "error", _line_of(masked, m.start()),
                                f'"{m.group(0)}": {claim["reason"]}'))

    for term in rules.get("terminology", []):
        if term.get("right"):
            continue
        for m in re.finditer(term["wrong"], masked):
            issues.append(Issue("terminology", "error", _line_of(masked, m.start()),
                                f'"{m.group(0)}": write the numeral form'))

    for m in _WE.finditer(masked):
        issues.append(Issue("we", "error", _line_of(masked, m.start()),
                            f'"{m.group(0)}": refer to the company as "FlytBase", never "we/our/us"'))

    tags = "|".join(rules.get("raw_html_tags", []))
    if tags:
        for m in re.finditer(rf"</?({tags})\b[^>]*>", masked, re.I):
            issues.append(Issue("raw_html", "error", _line_of(masked, m.start()),
                                f"raw HTML {m.group(0)!r}: FlytDocs shows it as literal text; use Markdown"))

    for m in re.finditer(r"[—–“”‘’…]", masked):
        issues.append(Issue("typography", "error", _line_of(masked, m.start()),
                            f"non-ASCII typography {m.group(0)!r}"))

    for i, line in enumerate(masked.splitlines(), 1):
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        if h and "/" in h.group(2):
            issues.append(Issue("slash_heading", "error", i, f'heading "{h.group(2)}" uses a slash; use one label'))
        if h and h.group(2).rstrip().endswith("?"):
            issues.append(Issue("question_heading", "warn", i, f'heading "{h.group(2)}" is a question; use a label'))

    if text.lstrip().startswith("---"):
        desc = _frontmatter_description(text.lstrip())
        limit = rules.get("description_max_chars", 150)
        if not desc:
            issues.append(Issue("description", "error", 1, "frontmatter description is empty"))
        elif len(desc) > limit:
            issues.append(Issue("description", "error", 1, f"frontmatter description is {len(desc)} characters (max {limit})"))

    max_words = rules.get("max_sentence_words", 30)
    for i, line in _prose_lines(masked):
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            words = len(re.findall(r"[A-Za-z0-9']+", sentence))
            if words > max_words:
                issues.append(Issue("long_sentence", "warn", i, f"sentence has {words} words (aim for {max_words} or fewer)"))

    return sorted(issues, key=lambda x: (x.severity != "error", x.line))


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == "error"]
