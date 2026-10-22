"""Every automated Slack alert goes through shorten() before it is posted.

A live alert on 2026-10-22 listed 50 file names in one Slack message, in the
team channel. Alerts now say what broke in a few short lines; the run link
has the rest. (They also go to SLACK_ALERT_CHANNEL, set to a DM, not the
team channel.)
"""
MAX_LINES = 8
MAX_LINE = 180


def shorten(text: str, max_lines: int = MAX_LINES, max_line: int = MAX_LINE) -> str:
    """At most max_lines non-empty lines, each at most max_line characters.
    The last line (the run link) is always kept."""
    lines = [l for l in (text or "").splitlines()]
    link = next((l for l in reversed(lines) if l.strip().startswith(("Run:", "Full report:", "Run "))), "")
    body = [l for l in lines if l != link]
    out, kept = [], 0
    for l in body:
        if not l.strip():
            if out and out[-1]:
                out.append("")
            continue
        if kept == max_lines:
            hidden = sum(1 for x in body[body.index(l):] if x.strip())
            out.append(f"...and {hidden} more line(s) in the run.")
            break
        out.append(l if len(l) <= max_line else l[:max_line - 3].rstrip() + "...")
        kept += 1
    while out and not out[-1]:
        out.pop()
    if link:
        out += ["", link if len(link) <= 300 else link[:297] + "..."]
    return "\n".join(out)
