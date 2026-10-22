"""Redundancy and naming checks for doc pages, run on the model's fields.

The first Cockpit 3.0 doc page was unreadable because the same facts were
written up to four times: as a step, an interface row, a rule and an
expected behavior. Detail was never the problem; repetition was. So every
fact has one home section (doc_template.SECTION_HOMES) and these checks
find a fact that shows up in a second section:

- the same sentence, or nearly, in two sections
- the same quantity with its unit (40 m, 5 km, 0.5 seconds) in two sections
- the same on-screen label written with different capitalization

Problems go back to the model once, with the exact sentence or number; what
survives the retry is reported in the PR for the reviewer.
"""
import re

from src.doc_template import section_text

MIN_TOKENS = 6          # shorter sentences ("Off by default.") repeat legitimately
SIMILARITY = 0.75       # Dice overlap of the content words of two sentences
MAX_PROBLEMS = 10       # keep the retry prompt focused

_STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "if", "in", "is", "it", "its", "of",
         "on", "or", "that", "the", "their", "then", "this", "to", "when", "with", "you", "your"}
_UNITS = (r"km/h|m/s|mph|km|ms|m|ft|seconds?|secs?|s|minutes?|mins?|hours?|h|%|°C|°F|MB|GB|KB|fps|Mbps|Hz|px")
_QTY = re.compile(rf"(?<![\w.])(\d+(?:\.\d+)?)\s?({_UNITS})(?![\w/])")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _tokens(sentence: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", re.sub(r"[*`\"]", "", sentence.lower()))
    return {w for w in words if w not in _STOP}


def _unit(u: str) -> str:
    u = u.lower()
    return {"second": "s", "seconds": "s", "sec": "s", "secs": "s", "minute": "min", "minutes": "min",
            "mins": "min", "hour": "h", "hours": "h"}.get(u, u)


def duplicate_sentences(sections: dict[str, list[str]]) -> list[tuple[str, str, str]]:
    """(section_a, section_b, sentence) for near-identical sentences in two sections."""
    seen: list[tuple[str, set, str]] = []
    found = []
    for name, texts in sections.items():
        for text in texts:
            for s in _sentences(text):
                toks = _tokens(s)
                if len(toks) < MIN_TOKENS:
                    continue
                for other, otoks, osent in seen:
                    if other == name:
                        continue
                    # Dice: symmetric, so a short step that names a feature is not
                    # "the same fact" as an Overview sentence listing every feature
                    # (the Cockpit 3.0 replay caught that false positive).
                    overlap = 2 * len(toks & otoks) / (len(toks) + len(otoks))
                    if overlap >= SIMILARITY:
                        found.append((other, name, s))
                        break
                seen.append((name, toks, s))
    return found


def repeated_quantities(sections: dict[str, list[str]]) -> dict[str, list[str]]:
    """Quantity ("40 m") -> sections it appears in, for those in more than one."""
    where: dict[str, list[str]] = {}
    for name, texts in sections.items():
        for text in texts:
            for num, unit in _QTY.findall(text):
                q = f"{float(num):g} {_unit(unit)}"
                if name not in where.setdefault(q, []):
                    where[q].append(name)
    return {q: names for q, names in where.items() if len(names) > 1}


def label_variants(sections: dict[str, list[str]]) -> list[list[str]]:
    """Bold labels written with different capitalization, e.g. **Video overlays** / **Video Overlays**."""
    forms: dict[str, set] = {}
    for texts in sections.values():
        for text in texts:
            for label in _BOLD.findall(text):
                forms.setdefault(" ".join(label.lower().split()), set()).add(label.strip())
    return [sorted(v) for v in forms.values() if len(v) > 1]


def redundancy_problems(data: dict) -> list[str]:
    """Problems phrased for the model's retry."""
    sections = section_text(data)
    problems = []
    for a, b, s in duplicate_sentences(sections):
        problems.append(f'the same fact is in "{a}" and "{b}": "{s[:90]}". Keep it only in its home section '
                        "and remove it from the other (refer to the home section if needed)")
    for q, names in repeated_quantities(sections).items():
        problems.append(f'"{q}" is stated in {", ".join(names)}. State it once, in its home section; '
                        "elsewhere refer to that section")
    for forms in label_variants(sections):
        problems.append(f"the same label is written differently: {' / '.join(f'**{f}**' for f in forms)}. "
                        "Use the exact on-screen label everywhere")
    return problems[:MAX_PROBLEMS]
