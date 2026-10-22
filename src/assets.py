"""Asset hygiene before drafting (P0, 2026-10-22).

Cockpit 3.0 shipped go-to-location-from-video.gif twice: Slack delivered the
same file in two messages, the second was saved as "-2", and both landed in
the release note and the doc page. Nine screenshots the material named were
never received, and nothing said so.

dedupe() drops byte-identical files before the model sees them, and merges
their routing. missing_named() lists media files the material names that did
not arrive, so the PR and Slack say so.
"""
import hashlib
import re
from pathlib import Path

MEDIA = r"png|jpe?g|gif|webp|mp4|mov|webm|m4v"
_NAMED = re.compile(rf"(?<![\w/.-])([\w][\w./()-]*?\.(?:{MEDIA}))\b", re.I)


def _hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def dedupe(paths: list[str], audience: dict | None = None) -> tuple[list[str], dict, dict]:
    """(kept paths, {dropped name: kept name}, merged audience).

    The first file of each identical group is kept. If the copies were routed
    differently (one "release", one "doc"), the kept file goes to "both".
    """
    audience = dict(audience or {})
    seen: dict[str, str] = {}
    kept, dropped = [], {}
    for p in paths:
        try:
            digest = _hash(p)
        except OSError:
            kept.append(p)
            continue
        name = Path(p).name
        if digest in seen:
            first = seen[digest]
            dropped[name] = first
            a, b = audience.get(first), audience.pop(name, None)
            if b and a and a != b:
                audience[first] = "both"
            elif b and not a:
                audience[first] = b
            continue
        seen[digest] = name
        kept.append(p)
    return kept, dropped, audience


def _key(name: str) -> str:
    """Compare names the way Slack and safe_filename mangle them."""
    stem = Path(name.strip().lower()).stem
    return re.sub(r"[^a-z0-9]+", "-", stem).strip("-")


def named_media(text: str) -> list[str]:
    """Every media file name the material mentions, once each, in order."""
    out = []
    for m in _NAMED.finditer(text or ""):
        name = m.group(1).strip().strip("'\"`").split("/")[-1]
        if name not in out:
            out.append(name)
    return out


def missing_named(text: str, received: list[str]) -> list[str]:
    """Media file names mentioned in the material that were never received."""
    have = {_key(n) for n in received}
    out = []
    for m in _NAMED.finditer(text or ""):
        name = m.group(1).strip().strip("'\"`")
        name = name.split("/")[-1]
        if _key(name) not in have and name not in out:
            out.append(name)
    return out
