"""Weekly reliability report: per-capability success rates from the run journal.

Every Son of Anton capability journals each outcome (src/run_journal.py on
the pipeline side, src/journal.js in the worker). Once a week this reads the
last 14 days from the worker's /journal endpoint and posts ONE Slack message:
each capability's runs and ok / repaired / flagged / failed rates, the
end-of-run invariants, the most common reasons things were flagged or
failed, and the change against the week before.

It always posts, even in a quiet week, so silence never means "broken". If
the journal cannot be read, that is itself reported, and the job fails.

Run as: python3 -m src.reliability_report
"""
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from src.health_check import DEFAULT_CHANNEL, DEFAULT_WORKER, http, post_slack

STATUSES = ("ok", "repaired", "flagged", "failed")
WEEK = timedelta(days=7)

# Display order and names for the capabilities; anything else is listed after.
CAPABILITIES = [
    ("intake_review", "Intake review"), ("file_manifest", "File manifest"), ("placement_proposal", "Placement proposal"),
    ("confirmation", "Run summary"), ("go", "Go"), ("dispatch", "Dispatch"),
    ("material", "Material"), ("assets", "Assets"), ("source", "Source document"), ("analysis", "Analysis"),
    ("vision", "Image descriptions"), ("drafting", "Drafting"), ("checks", "Fact check"),
    ("placement", "Placement"), ("links", "Links"), ("publish", "Publish"), ("notify", "Slack outcome"),
]
INVARIANTS = [
    ("invariant:files", "Files named = received = published"),
    ("invariant:placement", "Placed where confirmed"),
    ("invariant:prs", "PRs expected = created"),
    ("invariant:links", "No broken links left"),
    ("invariant:needs_attention", "Nothing left for a person"),
]
_LABELS = dict(CAPABILITIES + INVARIANTS)


def _ts(record) -> datetime | None:
    try:
        return datetime.fromisoformat(str(record["ts"]).replace("Z", "+00:00"))
    except (KeyError, ValueError, TypeError):
        return None


def valid(record) -> bool:
    return (isinstance(record, dict) and record.get("status") in STATUSES and bool(record.get("stage"))
            and _ts(record) is not None)


def split_weeks(records: list, now: datetime) -> tuple[list, list, int]:
    """(this week, last week, malformed) from up to 14 days of records."""
    this, last, bad = [], [], 0
    for r in records:
        if not valid(r):
            bad += 1
            continue
        t = _ts(r)
        if t >= now - WEEK:
            this.append(r)
        elif t >= now - 2 * WEEK:
            last.append(r)
    return this, last, bad


def rates(records: list) -> dict:
    """stage -> {"n": count, "ok": %, "repaired": %, "flagged": %, "failed": %, "good": %}.
    good = ok + repaired: the stage delivered without a person."""
    counts: dict = defaultdict(Counter)
    for r in records:
        counts[r["stage"]][r["status"]] += 1
    out = {}
    for stage, c in counts.items():
        n = sum(c.values())
        row = {"n": n, **{s: 100.0 * c[s] / n for s in STATUSES}}
        row["good"] = row["ok"] + row["repaired"]
        out[stage] = row
    return out


_NOISE = re.compile(r"`[^`]*`|\"[^\"]*\"|\*\*[^*]*\*\*|\b[\w./-]+\.(?:md|png|jpe?g|gif|webp|mp4|mov)\b|\d+(?:\.\d+)?")


def reason_key(detail: str) -> str:
    """Group details that differ only in names and numbers. HTTP status codes
    (400-599) are kept: "GitHub 502" and "GitHub 403" are different problems."""
    def blank(m):
        return m.group(0) if re.fullmatch(r"[45]\d\d", m.group(0)) else "…"
    text = _NOISE.sub(blank, detail or "")
    text = re.sub(r"(…[\s,;:]*)+", "… ", text)
    return " ".join(text.split())[:120] or "(no detail)"


def top_reasons(records: list, n: int = 5) -> list[tuple[str, int, str]]:
    """(reason, count, stage) for flagged and failed records, most common first."""
    c: Counter = Counter()
    where: dict = {}
    for r in records:
        if r["status"] in ("flagged", "failed") and not r["stage"].startswith("invariant:"):
            k = reason_key(r.get("detail", ""))
            c[k] += 1
            where.setdefault(k, r["stage"])
    return [(k, v, where[k]) for k, v in c.most_common(n)]


def _label(stage: str) -> str:
    if stage.startswith("tool:"):
        return f"Tool {stage[5:]}"
    return _LABELS.get(stage, stage)


def _order(stages) -> list:
    known = [s for s, _ in CAPABILITIES + INVARIANTS if s in stages]
    rest = sorted(s for s in stages if s not in known)
    return known + rest


def _pct(x: float) -> str:
    return f"{x:.0f}%"


def _delta(now: dict, before: dict, stage: str) -> str:
    if stage not in before or stage not in now:
        return "new" if stage in now and stage not in before else ""
    d = now[stage]["good"] - before[stage]["good"]
    return "=" if abs(d) < 0.5 else f"{d:+.0f}"


def build(records: list, now: datetime, malformed_api: int = 0) -> dict:
    this, last, bad = split_weeks(records, now)
    r_now, r_before = rates(this), rates(last)
    caps = [s for s in _order(r_now) if not s.startswith(("invariant:", "tool:"))]
    invs = [s for s in _order(r_now) if s.startswith("invariant:")]
    tools = Counter(r["stage"] for r in this if r["stage"].startswith("tool:"))
    total = len([r for r in this if not r["stage"].startswith(("invariant:", "tool:"))])
    good = len([r for r in this if not r["stage"].startswith(("invariant:", "tool:")) and r["status"] in ("ok", "repaired")])
    total_b = len([r for r in last if not r["stage"].startswith(("invariant:", "tool:"))])
    good_b = len([r for r in last if not r["stage"].startswith(("invariant:", "tool:")) and r["status"] in ("ok", "repaired")])
    return {
        "since": (now - WEEK).date().isoformat(), "until": now.date().isoformat(),
        "runs": len({r["run_id"] for r in this if str(r.get("run_id", "")).startswith("gh-")}),
        "threads": len({r["run_id"] for r in this if str(r.get("run_id", "")).startswith("thread:")}),
        "records": len(this), "malformed": bad + malformed_api,
        "overall": 100.0 * good / total if total else None,
        "overall_before": 100.0 * good_b / total_b if total_b else None,
        "caps": [(s, r_now[s], _delta(r_now, r_before, s)) for s in caps],
        "invariants": [(s, r_now[s], _delta(r_now, r_before, s)) for s in invs],
        "tools": tools.most_common(),
        "reasons": top_reasons(this),
    }


def table_rows(rows) -> list[str]:
    head = f"{'Capability':<34}{'n':>5}{'ok':>7}{'fixed':>7}{'flag':>7}{'fail':>7}{'Δ wk':>7}"
    out = [head, "-" * len(head)]
    for stage, r, d in rows:
        out.append(f"{_label(stage)[:33]:<34}{r['n']:>5}{_pct(r['ok']):>7}{_pct(r['repaired']):>7}"
                   f"{_pct(r['flagged']):>7}{_pct(r['failed']):>7}{d:>7}")
    return out


def slack_message(rep: dict, run_url: str = "") -> str:
    period = f"{rep['since']} to {rep['until']}"
    if not rep["records"]:
        lines = [f":zzz: *Reliability report, {period}.* Quiet week: no runs, nothing to measure. "
                 "Not broken, just unused. The daily health check still watched the plumbing."]
        if rep["malformed"]:
            lines.append(f"{rep['malformed']} malformed journal record(s) were skipped, which is its own small embarrassment.")
        return "\n".join(lines)
    overall = rep["overall"]
    trend = ""
    if overall is not None and rep["overall_before"] is not None:
        d = overall - rep["overall_before"]
        trend = " Same as last week." if abs(d) < 0.5 else f" {d:+.0f} points against last week."
    verdict = ("Everything delivered without a human. Suspicious, but I'll allow it." if overall == 100 else
               "Mostly fine. The rest is listed below, so nobody gets to be surprised." if overall and overall >= 90 else
               "Not great. Details below, ranked by how often they embarrassed us.")
    lines = [f":bar_chart: *Reliability report, {period}.* {rep['runs']} pipeline run(s), {rep['threads']} Slack thread(s). "
             f"Stages delivered without a person: *{_pct(overall)}*.{trend} {verdict}", "",
             "*Per capability* (ok, fixed automatically, flagged for a person, failed; Δ wk is the change in ok+fixed):",
             "```", *table_rows(rep["caps"]), "```"]
    if rep["invariants"]:
        lines += ["*End-of-run invariants:*", "```", *table_rows(rep["invariants"]), "```"]
    if rep["tools"]:
        lines.append("*Tool errors:* " + ", ".join(f"{_label(s)} x{n}" for s, n in rep["tools"]))
    if rep["reasons"]:
        lines.append("*Most common reasons something needed a person:*")
        lines += [f"  {i}. {k} ({_label(s)}, x{n})" for i, (k, n, s) in enumerate(rep["reasons"], 1)]
    if rep["malformed"]:
        lines.append(f"_{rep['malformed']} malformed journal record(s) skipped._")
    if run_url:
        lines += ["", f"Full table: {run_url}"]
    return "\n".join(lines)


def markdown(rep: dict) -> str:
    out = [f"## Reliability report, {rep['since']} to {rep['until']}", "",
           f"Pipeline runs: {rep['runs']}, Slack threads: {rep['threads']}, records: {rep['records']}, "
           f"malformed skipped: {rep['malformed']}", ""]
    for title, rows in (("Capabilities", rep["caps"]), ("Invariants", rep["invariants"])):
        if rows:
            out += [f"### {title}", "", "| Capability | n | ok | repaired | flagged | failed | Δ week |", "|---|---|---|---|---|---|---|"]
            out += [f"| {_label(s)} | {r['n']} | {_pct(r['ok'])} | {_pct(r['repaired'])} | {_pct(r['flagged'])} | "
                    f"{_pct(r['failed'])} | {d} |" for s, r, d in rows]
            out.append("")
    if rep["reasons"]:
        out += ["### Top reasons", ""] + [f"1. {k} ({_label(s)}, x{n})" for k, n, s in rep["reasons"]] + [""]
    if not rep["records"]:
        out += ["Quiet week: no runs.", ""]
    return "\n".join(out)


def fetch_records(env: dict, now: datetime, fetch=http) -> tuple[list, int, str]:
    """(records, malformed, error). error is "" on success."""
    url = (env.get("WORKER_URL") or DEFAULT_WORKER).rstrip("/")
    token = env.get("CONTENT_TOKEN", "")
    if not token:
        return [], 0, "CONTENT_TOKEN is not set"
    since = (now - 2 * WEEK).strftime("%Y-%m-%dT%H:%M:%SZ")
    status, data = fetch("GET", f"{url}/journal?since={since}", {"X-Content-Token": token}, None, False)
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("records"), list):
        why = data.get("error") if isinstance(data, dict) else str(data)[:200]
        return [], 0, f"GET /journal returned {status or 'no answer'}: {why}"
    return data["records"], int(data.get("malformed") or 0), ""


def _write_summary(env: dict, text: str) -> None:
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text + "\n")


def main(env=None, fetch=http, now: datetime | None = None) -> int:
    env = dict(os.environ if env is None else env)
    now = now or datetime.now(timezone.utc)
    channel = env.get("SLACK_ALERT_CHANNEL") or DEFAULT_CHANNEL
    token = env.get("SLACK_BOT_TOKEN", "")
    run_url = (f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env.get('GITHUB_REPOSITORY', '')}"
               f"/actions/runs/{env['GITHUB_RUN_ID']}") if env.get("GITHUB_RUN_ID") else ""

    records, malformed, error = fetch_records(env, now, fetch)
    if error:
        message = (f":x: *Reliability report: could not read the run journal* ({error}). "
                   "So this week's numbers are unknown, which is worse than bad numbers. Check the worker and CONTENT_TOKEN."
                   + (f"\n{run_url}" if run_url else ""))
        exit_code = 1
        rep_md = f"## Reliability report\n\n:x: Could not read the run journal: {error}\n"
    else:
        rep = build(records, now, malformed)
        message = slack_message(rep, run_url)
        rep_md = markdown(rep)
        exit_code = 0
    print(rep_md)
    _write_summary(env, rep_md)
    try:
        posted = bool(token) and post_slack(fetch, token, channel, message)
        why = "no SLACK_BOT_TOKEN" if not token else "chat.postMessage did not return ok"
    except Exception as e:  # noqa: BLE001
        posted, why = False, f"{type(e).__name__}: {e}"
    if posted:
        print(f"Posted to {channel}.")
    else:
        note = f"\n:x: **The report could not be posted to Slack ({why}).** This summary is the only copy.\n"
        print(note)
        _write_summary(env, note)
        exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
