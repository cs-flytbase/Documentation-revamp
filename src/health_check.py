"""Daily health check for Son of Anton and the docs pipeline.

Every failure this pipeline has had so far was found by a person, mid-run or
weeks later: OpenRouter credits ran out during a draft, the corpus refresh
reported green for months while adding nothing, a feedback workflow failed
on every run since August. This checks the things a run depends on, once a
day, before anyone needs them, and spends no model tokens doing it:

1. OpenRouter credit left
2. The pipeline's GitHub token can still write to every target repo
3. The Slack bot token is valid
4. The worker is up, and /content still refuses unauthenticated reads
5. Every workflow's last run passed, and scheduled ones are still running
6. The vector corpus is moving when the docs and releases move

Results go to the job summary every day. Slack hears about it only when
something is warn or fail. Warnings never fail the job; only a crash of
this script does.

Run as: python3 -m src.health_check
"""
from src.alerts import shorten
import base64
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import yaml

GITHUB = "https://api.github.com"
WRITE_REPOS = ["cs-flytbase/Documentation-revamp", "FlytBaseAILabs/flytbase-docs", "FlytBaseAILabs/flytbase-releases"]
WORKFLOW_REPOS = WRITE_REPOS[:1] + ["FlytBaseAILabs/docagent-worker"] + WRITE_REPOS[1:]
CONTENT_REPOS = ["FlytBaseAILabs/flytbase-docs", "FlytBaseAILabs/flytbase-releases"]
CORPUS_WORKFLOW = ".github/workflows/corpus-refresh.yml"
DEFAULT_WORKER = "https://docagent-worker.se-team-charlie.workers.dev"
DEFAULT_CHANNEL = "C0BGB8F56JX"
STALE_DAYS = 7

OK, WARN, FAIL, UNKNOWN = "ok", "warn", "fail", "unknown"
ICON = {OK: ":white_check_mark:", WARN: ":warning:", FAIL: ":x:", UNKNOWN: ":grey_question:"}


@dataclass
class Result:
    check: str
    status: str
    detail: str
    fix: str = ""


# ── HTTP ──────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


RETRIES = 3                 # attempts per request
BACKOFF = (1, 3)            # seconds between attempts
_sleep = time.sleep         # replaced in tests


def http(method: str, url: str, headers: dict | None = None, body: dict | None = None,
         raw: bool = False, timeout: int = 20) -> tuple[int, object]:
    """(status, data), retried on network errors, 429 and 5xx.

    data is parsed JSON, or text, or bytes when raw=True. status 0 means the
    request never got an answer (timeout, DNS, connection refused).
    """
    status, data = 0, ""
    for attempt in range(RETRIES):
        status, data = _http_once(method, url, headers, body, raw, timeout)
        if status != 0 and status != 429 and status < 500:
            return status, data
        if attempt < RETRIES - 1:
            _sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
    return status, data


def _http_once(method, url, headers, body, raw, timeout) -> tuple[int, object]:
    """One request. Redirects are followed by hand and without the
    Authorization header: GitHub log downloads redirect to signed storage
    URLs that reject it."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"User-Agent": "FlytBase-HealthCheck/1.0", **(headers or {})}
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json; charset=utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            status, payload = r.status, r.read()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
            return _http_once("GET", e.headers["Location"], None, None, raw, timeout)
        status, payload = e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, str(e)
    if raw:
        return status, payload
    text = payload.decode("utf-8", "replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def _gh(fetch, token: str, path: str, raw: bool = False):
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return fetch("GET", path if path.startswith("http") else GITHUB + path, headers, None, raw)


# ── Checks ────────────────────────────────────────────────────────────

def _no_answer(status: int, data) -> str | None:
    """A request that never got an HTTP answer is a network problem, not a
    permissions one; say so instead of blaming the token."""
    return f"no response after {RETRIES} attempts ({str(data)[:120]})" if status == 0 else None


def check_openrouter(fetch, key: str, min_usd: float = 3.0, min_fraction: float = 0.2) -> Result:
    name = "OpenRouter credit"
    if not key:
        return Result(name, FAIL, "OPENROUTER_API_KEY is not set", "Add the OPENROUTER_API_KEY secret to this repo.")
    status, data = fetch("GET", "https://openrouter.ai/api/v1/key", {"Authorization": f"Bearer {key}"}, None, False)
    if status == 0:
        return Result(name, UNKNOWN, _no_answer(status, data), "Re-run the health check; OpenRouter may be down.")
    if status != 200 or not isinstance(data, dict):
        return Result(name, FAIL, f"key check returned HTTP {status}",
                      "The key may be revoked or rotated. Update the OPENROUTER_API_KEY secret.")
    d = data.get("data") or {}
    limit, usage = d.get("limit"), d.get("usage") or 0
    remaining = d.get("limit_remaining")
    if remaining is None and limit is not None:
        remaining = limit - usage
    if limit is None or remaining is None:
        return Result(name, OK, f"no spending limit on the key; ${usage:.2f} used")
    detail = f"${remaining:.2f} left of ${limit:.2f}"
    top_up = "Top up at https://openrouter.ai/settings/credits or raise the key's limit."
    if remaining <= 0:
        return Result(name, FAIL, detail + ": every run will fail at the first model call", top_up)
    if remaining < min_usd or (limit and remaining / limit < min_fraction):
        return Result(name, WARN, detail, top_up)
    return Result(name, OK, detail)


def check_github_repos(fetch, token: str, repos=WRITE_REPOS) -> list[Result]:
    out = []
    for repo in repos:
        name = f"GitHub write access: {repo}"
        if not token:
            out.append(Result(name, FAIL, "GH_TOKEN is not set", "Set FLYTBASE_GITHUB_TOKEN for the health check."))
            continue
        status, data = _gh(fetch, token, f"/repos/{repo}")
        if status == 0:
            out.append(Result(name, UNKNOWN, _no_answer(status, data), "Re-run the health check; GitHub may be down."))
        elif status != 200 or not isinstance(data, dict):
            out.append(Result(name, FAIL, f"HTTP {status}: the pipeline's token cannot see this repo",
                              "Regenerate FLYTBASE_GITHUB_TOKEN with access to it, or check it has not expired."))
        elif not (data.get("permissions") or {}).get("push"):
            out.append(Result(name, FAIL, "read-only: the pipeline cannot open PRs here",
                              "Give the token write (contents and pull requests) access to this repo."))
        else:
            out.append(Result(name, OK, "read and write"))
    return out


def check_slack(fetch, token: str) -> Result:
    name = "Slack bot token"
    if not token:
        return Result(name, FAIL, "SLACK_BOT_TOKEN is not set", "Add the SLACK_BOT_TOKEN secret.")
    status, data = fetch("POST", "https://slack.com/api/auth.test", {"Authorization": f"Bearer {token}"}, {}, False)
    if status == 0:
        return Result(name, UNKNOWN, _no_answer(status, data), "Re-run the health check; Slack may be down.")
    if status == 200 and isinstance(data, dict) and data.get("ok"):
        return Result(name, OK, f"valid ({data.get('user', 'bot')} in {data.get('team', 'workspace')})")
    err = data.get("error") if isinstance(data, dict) else f"HTTP {status}"
    return Result(name, FAIL, f"auth.test failed: {err}", "Reinstall the Slack app or update SLACK_BOT_TOKEN.")


def check_worker(fetch, url: str) -> list[Result]:
    url = (url or DEFAULT_WORKER).rstrip("/")
    status, data = fetch("GET", url + "/", None, None, False)
    up = Result("Worker reachable", OK, "Son of Anton is running") if status == 200 and "running" in str(data) else \
        Result("Worker reachable", FAIL, _no_answer(status, data) or f"GET / returned HTTP {status}",
               "Check the worker in the Cloudflare dashboard; redeploy with `npx wrangler deploy`.")
    status, _ = fetch("GET", url + "/content?key=health-check", None, None, False)
    if status == 401:
        locked = Result("Worker /content locked", OK, "refuses reads without the token")
    elif status == 0:
        locked = Result("Worker /content locked", UNKNOWN, "worker unreachable")
    else:
        locked = Result("Worker /content locked", FAIL,
                        f"returned HTTP {status} without a token: stored Slack material may be readable by anyone",
                        "Check that the worker requires X-Content-Token and that CONTENT_TOKEN is set (docagent-worker).")
    return [up, locked]


def cron_interval(expr: str) -> timedelta:
    """Rough period of a cron expression, enough to tell 'late' from 'stopped'."""
    parts = expr.split()
    if len(parts) != 5:
        return timedelta(days=1)
    minute, hour, dom, _mon, dow = parts
    if dow != "*":
        return timedelta(days=7)
    if dom != "*":
        return timedelta(days=31)
    m = re.fullmatch(r"\*/(\d+)", hour)
    if m:
        return timedelta(hours=int(m.group(1)))
    if hour == "*":
        return timedelta(hours=1)
    return timedelta(days=1)


def _schedules(fetch, token: str, repo: str, path: str) -> list[str]:
    status, data = _gh(fetch, token, f"/repos/{repo}/contents/{path}")
    if status != 200 or not isinstance(data, dict) or not data.get("content"):
        return []
    try:
        doc = yaml.safe_load(base64.b64decode(data["content"]).decode())
    except Exception:  # noqa: BLE001
        return []
    on = (doc or {}).get("on", (doc or {}).get(True)) or {}  # YAML 1.1 reads "on" as True
    if not isinstance(on, dict):
        return []
    return [s.get("cron", "") for s in on.get("schedule") or [] if isinstance(s, dict)]


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def check_workflows(fetch, token: str, now: datetime, repos=WORKFLOW_REPOS) -> list[Result]:
    out = []
    for repo in repos:
        status, data = _gh(fetch, token, f"/repos/{repo}/actions/workflows?per_page=100")
        if status != 200 or not isinstance(data, dict):
            out.append(Result(f"Workflows: {repo}", UNKNOWN, _no_answer(status, data) or f"cannot list workflows (HTTP {status})",
                              "Give the health check token Actions read access to this repo."))
            continue
        for wf in data.get("workflows") or []:
            path = wf.get("path", "")
            if not path.startswith(".github/workflows/"):
                continue  # GitHub's own dynamic workflows (Pages, Copilot)
            name = f"Workflow {repo.split('/')[1]}/{path.rsplit('/', 1)[-1]}"
            if wf.get("state") != "active":
                out.append(Result(name, WARN, f"state is {wf.get('state')}",
                                  "Re-enable it in the Actions tab, or delete the file if it is no longer used."))
                continue
            status, runs = _gh(fetch, token, f"/repos/{repo}/actions/workflows/{wf['id']}/runs?status=completed&per_page=1")
            last = ((runs or {}).get("workflow_runs") or [None])[0] if isinstance(runs, dict) else None
            problems = []
            if last and last.get("conclusion") in ("failure", "timed_out", "startup_failure"):
                problems.append((FAIL, f"last run {last['conclusion']} on {last.get('created_at', '')[:10]}",
                                 f"See {last.get('html_url', 'the Actions tab')}"))
            for cron in _schedules(fetch, token, repo, path):
                period = cron_interval(cron)
                status, sruns = _gh(fetch, token, f"/repos/{repo}/actions/workflows/{wf['id']}/runs?event=schedule&per_page=1")
                slast = ((sruns or {}).get("workflow_runs") or [None])[0] if isinstance(sruns, dict) else None
                if not slast:
                    problems.append((WARN, f"scheduled ({cron}) but has never run on schedule",
                                     "Scheduled workflows stop after 60 days without repo activity; re-enable it."))
                elif now - _ts(slast["created_at"]) > 2 * period:
                    problems.append((WARN, f"scheduled ({cron}) but last ran {slast['created_at'][:10]}",
                                     "Scheduled workflows stop after 60 days without repo activity; re-enable it."))
                break
            if problems:
                worst = FAIL if any(p[0] == FAIL for p in problems) else WARN
                out.append(Result(name, worst, "; ".join(p[1] for p in problems), " ".join(p[2] for p in problems)))
            else:
                out.append(Result(name, OK, f"last run {last.get('conclusion')}" if last else "no completed runs yet"))
    return out


_VECTORS = re.compile(r"Total vectors:\s*([\d,]+)")


def _run_log(fetch, token: str, repo: str, run_id: int) -> str:
    status, blob = _gh(fetch, token, f"/repos/{repo}/actions/runs/{run_id}/logs", raw=True)
    if status != 200 or not isinstance(blob, (bytes, bytearray)):
        return ""
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            return "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".txt"))
    except zipfile.BadZipFile:
        return blob.decode("utf-8", "replace") if isinstance(blob, (bytes, bytearray)) else ""


def _vectors(log: str) -> int | None:
    found = _VECTORS.findall(log)
    return int(found[-1].replace(",", "")) if found else None


def _pinecone_count(fetch, key: str, index: str) -> int | None:
    if not key:
        return None
    status, data = fetch("GET", f"https://api.pinecone.io/indexes/{index}", {"Api-Key": key}, None, False)
    host = data.get("host") if status == 200 and isinstance(data, dict) else None
    if not host:
        return None
    status, stats = fetch("POST", f"https://{host}/describe_index_stats", {"Api-Key": key}, {}, False)
    if status != 200 or not isinstance(stats, dict):
        return None
    return stats.get("totalVectorCount", stats.get("total_vector_count"))


def check_corpus(fetch, token: str, now: datetime, pinecone_key: str = "", index: str = "flytbase-docs",
                 repo: str = WRITE_REPOS[0]) -> Result:
    name = "Vector corpus freshness"
    since = now - timedelta(days=STALE_DAYS)
    status, data = _gh(fetch, token, f"/repos/{repo}/actions/workflows/corpus-refresh.yml/runs?status=success&per_page=20")
    runs = (data or {}).get("workflow_runs") or [] if isinstance(data, dict) else []
    recent = runs[0] if runs else None
    older = next((r for r in runs if _ts(r["created_at"]) <= since), None)

    current, recent_log = None, ""
    if recent:
        recent_log = _run_log(fetch, token, repo, recent["id"])
        current = _vectors(recent_log)
    pinecone_now = _pinecone_count(fetch, pinecone_key, index)
    if pinecone_now is not None:
        current = pinecone_now
    previous = _vectors(_run_log(fetch, token, repo, older["id"])) if older else None

    commits = 0
    for r in CONTENT_REPOS:
        status, data = _gh(fetch, token, f"/repos/{r}/commits?sha=main&since={since.strftime('%Y-%m-%dT%H:%M:%SZ')}&per_page=100")
        if status == 200 and isinstance(data, list):
            commits += len(data)

    blocked = bool(re.search(r"429|Too Many Requests", recent_log))
    fix = ("The refresh crawls the websites, which block it. It should read the docs and releases repos "
           "through the GitHub API (P1 corpus fix).")
    if current is None or previous is None:
        return Result(name, WARN if blocked else UNKNOWN,
                      f"cannot compare vector counts (now: {current}, {STALE_DAYS} days ago: {previous})"
                      + ("; the last refresh was rate-limited (429)" if blocked else ""), fix if blocked else
                      "Check the corpus-refresh logs for a 'Total vectors' line, or set PINECONE_API_KEY.")
    if current == previous and commits:
        return Result(name, WARN, f"{current} vectors, unchanged in {STALE_DAYS} days while the docs and "
                      f"releases repos had {commits} commit(s) to main"
                      + ("; the last refresh was rate-limited (429)" if blocked else ""), fix)
    if blocked:
        return Result(name, WARN, f"{current} vectors, but the last refresh was rate-limited (429)", fix)
    return Result(name, OK, f"{current} vectors ({current - previous:+d} in {STALE_DAYS} days, {commits} content commit(s))")


# ── Output ────────────────────────────────────────────────────────────

def summary_table(results: list[Result]) -> str:
    rows = ["| Check | Status | Detail | Fix |", "|---|---|---|---|"]
    for r in results:
        cell = lambda s: " ".join(str(s).replace("|", "/").split())  # noqa: E731
        rows.append(f"| {cell(r.check)} | {ICON[r.status]} {r.status} | {cell(r.detail)} | {cell(r.fix)} |")
    return "## Son of Anton health check\n\n" + "\n".join(rows) + "\n"


def build_message(results: list[Result], run_url: str = "") -> str | None:
    """The Slack alert, or None when there is nothing to say."""
    bad = [r for r in results if r.status in (WARN, FAIL)]
    if not bad:
        return None
    fails = sum(r.status == FAIL for r in bad)
    if fails:
        head = (f":rotating_light: *Daily health check:* {len(bad)} problem(s), {fails} of them will break the next run. "
                "Fix these before someone asks me for a release note.")
    else:
        head = f":warning: *Daily health check:* {len(bad)} thing(s) are drifting. Nothing is on fire yet. Yet."
    lines = [head, ""]
    for r in sorted(bad, key=lambda r: r.status != FAIL):
        lines.append(f"{ICON[r.status]} *{r.check}:* {r.detail}")
        if r.fix:
            lines.append(f"    Fix: {r.fix}")
    if run_url:
        lines += ["", f"Full report: {run_url}"]
    return "\n".join(lines)


def post_slack(fetch, token: str, channel: str, text: str) -> bool:
    status, data = fetch("POST", "https://slack.com/api/chat.postMessage", {"Authorization": f"Bearer {token}"},
                         {"channel": channel, "text": shorten(text), "unfurl_links": False}, False)
    return status == 200 and isinstance(data, dict) and bool(data.get("ok"))


def run_checks(env: dict, fetch=http, now: datetime | None = None) -> list[Result]:
    """Every check, each isolated: one crashing check is reported, not fatal."""
    now = now or datetime.now(timezone.utc)
    gh = env.get("GH_TOKEN", "")
    steps = [
        lambda: [check_openrouter(fetch, env.get("OPENROUTER_API_KEY", ""),
                                  float(env.get("OPENROUTER_MIN_USD") or 3), float(env.get("OPENROUTER_MIN_FRACTION") or 0.2))],
        lambda: check_github_repos(fetch, gh),
        lambda: [check_slack(fetch, env.get("SLACK_BOT_TOKEN", ""))],
        lambda: check_worker(fetch, env.get("WORKER_URL") or DEFAULT_WORKER),
        lambda: check_workflows(fetch, gh, now),
        lambda: [check_corpus(fetch, gh, now, env.get("PINECONE_API_KEY", ""), env.get("PINECONE_INDEX_NAME") or "flytbase-docs")],
    ]
    names = ["OpenRouter credit", "GitHub write access", "Slack bot token", "Worker", "Workflows", "Vector corpus freshness"]
    results: list[Result] = []
    for name, step in zip(names, steps):
        try:
            results += step()
        except Exception as e:  # noqa: BLE001
            results.append(Result(name, FAIL, f"the check itself crashed: {type(e).__name__}: {e}",
                                  "Look at the health-check job log."))
    return results


def main(env=None, fetch=http) -> int:
    env = dict(os.environ if env is None else env)
    results = run_checks(env, fetch)
    table = summary_table(results)
    print(table)
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(table)
    run_url = ""
    if env.get("GITHUB_RUN_ID"):
        run_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env.get('GITHUB_REPOSITORY', '')}/actions/runs/{env['GITHUB_RUN_ID']}"
    message = build_message(results, run_url)
    if message:
        channel = env.get("SLACK_ALERT_CHANNEL") or DEFAULT_CHANNEL
        try:
            posted = bool(env.get("SLACK_BOT_TOKEN")) and post_slack(fetch, env["SLACK_BOT_TOKEN"], channel, message)
        except Exception as e:  # noqa: BLE001
            posted, why = False, f"{type(e).__name__}: {e}"
        else:
            why = "no SLACK_BOT_TOKEN" if not env.get("SLACK_BOT_TOKEN") else "chat.postMessage did not return ok"
        note = (f"\nSlack alert posted to {channel}.\n" if posted else
                f"\n:x: **The Slack alert could not be posted ({why}).** Nobody was told; the problems above are the alert.\n")
        print(note)
        if env.get("GITHUB_STEP_SUMMARY"):
            with open(env["GITHUB_STEP_SUMMARY"], "a") as f:
                f.write(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
