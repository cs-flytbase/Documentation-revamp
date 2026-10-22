"""Weekly end-to-end check: the real pipeline, the real model, a sandbox.

Unit tests and the CI replay (tests/test_replay_cockpit.py) use recorded
model answers, so they cannot notice a model, prompt or API change that
breaks a real run. Once a week this runs the whole pipeline for real on the
committed Cockpit 3.0 material, in sandbox mode: everything is committed to
disposable branches under sandbox/<tag>/ in the docs and releases repos, no
PR is opened, no label is added and no existing page is edited. The branches
are read back through the GitHub API, checked against the same invariants as
the replay (src/invariants.py), and deleted, always, even when the run
crashes.

Results: a table in the job summary, one best-effort journal record, and a
Slack message ONLY when an invariant fails. The job fails when an invariant
fails, so the Actions history shows it too.

    python -m src.e2e_check            # the weekly run
    python -m src.e2e_check --keep     # leave the sandbox branches for a look
"""
from __future__ import annotations

from src.alerts import shorten
import base64
import json as _json
import os
import posixpath
import random
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from src.assets import missing_named
from src.invariants import Published, RepoView, Result, check_all, table

DOCS = "FlytBaseAILabs/flytbase-docs"
RELEASES = "FlytBaseAILabs/flytbase-releases"
FEATURE_NAME = "cockpit-3-0-video-overlays-update"
FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "cockpit_3_0"
TARGET_SECTION = "Cockpit 3.0"
DEFAULT_ALERT_CHANNEL = "C0BGB8F56JX"
TIMEOUT = 30


# ── GitHub ───────────────────────────────────────────────────────────────

class GitHubAPI:
    """Minimal GitHub REST client: timeouts, retries on 429/5xx/network,
    other 4xx raised at once. Same call shape as the publisher's _api."""

    def __init__(self, token: str, attempts: int = 4, sleep=time.sleep, opener=None):
        self.token, self.attempts, self.sleep = token, attempts, sleep
        self.opener = opener or urllib.request.urlopen

    def __call__(self, method: str, repo: str, path: str, params: dict | None = None, json: dict | None = None, **_):
        url = f"https://api.github.com/repos/{repo}/{path}"
        if params:
            url += ("&" if "?" in url else "?") + "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items())
        data = None if json is None else _json.dumps(json).encode()
        for attempt in range(self.attempts):
            req = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": f"token {self.token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "FlytBase-E2E/1.0",
                **({"Content-Type": "application/json"} if data else {})})
            try:
                with self.opener(req, timeout=TIMEOUT) as resp:
                    body = resp.read()
                    return _json.loads(body) if body else {}
            except urllib.error.HTTPError as e:
                if (e.code == 429 or e.code >= 500) and attempt + 1 < self.attempts:
                    self.sleep(2 ** attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt + 1 == self.attempts:
                    raise
                self.sleep(2 ** attempt)
        return {}


def _text(api, repo: str, path: str, ref: str) -> str | None:
    try:
        data = api("GET", repo, f"contents/{path}", params={"ref": ref})
    except Exception as e:  # noqa: BLE001
        if getattr(e, "code", None) == 404 or getattr(getattr(e, "response", None), "status_code", None) == 404:
            return None
        raise
    raw = base64.b64decode(data["content"])
    return raw.decode("utf-8", "replace")


def _paths(api, repo: str, ref: str) -> set:
    """Every file on a branch. Goes through the commit's tree sha, which works
    for branch names with slashes."""
    sha = api("GET", repo, f"git/ref/heads/{ref}")["object"]["sha"]
    tree = api("GET", repo, f"git/commits/{sha}")["tree"]["sha"]
    return {t["path"] for t in api("GET", repo, f"git/trees/{tree}?recursive=1").get("tree", []) if t.get("type") == "blob"}


def view_of(api, repo: str, branch: str | None, summary_path: str) -> RepoView:
    """What the run left in one repo: SUMMARY on main and on the branch, every
    path on the branch, and the text of each .md page the branch added."""
    main_paths = _paths(api, repo, "main")
    summary_main = _text(api, repo, summary_path, "main") or ""
    if not branch:
        return RepoView(summary_main, summary_main, main_paths, {})
    paths = _paths(api, repo, branch)
    pages = {p: _text(api, repo, p, branch) or "" for p in sorted(paths - main_paths)
             if p.endswith(".md") and posixpath.basename(p) != "SUMMARY.md"}
    return RepoView(summary_main, _text(api, repo, summary_path, branch) or "", paths, pages)


def sandbox_refs(api, repo: str, tag: str) -> list[str]:
    from src.tools.github_publisher import sandbox_prefix
    found = api("GET", repo, f"git/matching-refs/heads/{sandbox_prefix(tag)}/") or []
    return [r["ref"][len("refs/heads/"):] for r in found if r.get("ref", "").startswith("refs/heads/")]


def cleanup(api, tag: str, repos=(DOCS, RELEASES)) -> Result:
    """Delete every sandbox branch of this run. Never raises."""
    deleted, failed = [], []
    for repo in repos:
        try:
            branches = sandbox_refs(api, repo, tag)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{repo}: could not list sandbox branches ({e})")
            continue
        for b in branches:
            try:
                api("DELETE", repo, f"git/refs/heads/{b}")
                deleted.append(f"{repo}@{b}")
            except Exception as e:  # noqa: BLE001
                failed.append(f"{repo}@{b}: {e}")
    return Result("sandbox branches deleted", not failed,
                  (f"deleted {len(deleted)}" + (f"; NOT deleted: {'; '.join(failed)}" if failed else "")))


# ── The run ──────────────────────────────────────────────────────────────

def fixture_material(fixture: Path = FIXTURE) -> tuple[str, list[str]]:
    md = next(p for p in fixture.glob("*.md")).read_text()
    return md, sorted(p.name for p in (fixture / "assets").iterdir())


def audience_for(files: list[str]) -> dict:
    """The split agreed for this release: GIFs on both outputs, screenshots on the doc page."""
    return {f: ("both" if f.lower().endswith(".gif") else "doc") for f in files}


def missing_attention(md: str, received: list[str]) -> list[str]:
    missing = missing_named(md, received)
    if not missing:
        return []
    return [f"The material names {len(missing)} file(s) that were never received: " + ", ".join(missing)
            + ". Send them in the thread and ask for a re-run, or remove them from the material."]


def run_check(api, run_pipeline, tag: str, fixture: Path = FIXTURE, keep: bool = False,
              target_section: str = TARGET_SECTION, summary_path: str = "SUMMARY.md") -> tuple[list[Result], dict]:
    """Run the pipeline in sandbox `tag`, check what landed, clean up.
    Returns (results, pipeline result). Never raises."""
    md, files = fixture_material(fixture)
    results: list[Result] = []
    outcome: dict = {}
    workdir = Path(tempfile.mkdtemp(prefix="e2e-"))
    previous = os.environ.get("PIPELINE_SANDBOX")
    os.environ["PIPELINE_SANDBOX"] = tag
    try:
        # Named like a real run's bundle (input_bundles/<feature name>): the
        # branch slug comes from it, so the sandbox branch is predictable.
        bundle = workdir / FEATURE_NAME
        shutil.copytree(fixture, bundle)
        attention = missing_attention(md, files)
        outcome = run_pipeline(str(bundle), mode="both", target_section=target_section,
                               asset_audience=audience_for(files), attention=attention, doc_scope="cloud") or {}
        pr = outcome.get("pr_results") or {}
        branches = {repo: b for repo, b in pr.get("sandbox_branches") or []}
        errors = list(pr.get("errors") or []) + list(outcome.get("errors") or [])
        both = bool(branches.get(DOCS)) and bool(branches.get(RELEASES))
        results.append(Result("run published both outputs", outcome.get("status") == "success" and both and not errors,
                              f"status {outcome.get('status')}; docs {'yes' if branches.get(DOCS) else 'NO'}, "
                              f"release {'yes' if branches.get(RELEASES) else 'NO'}"
                              + (f"; errors: {'; '.join(map(str, errors))[:300]}" if errors else "")))
        published = Published(
            docs=view_of(api, DOCS, branches.get(DOCS), summary_path),
            releases=view_of(api, RELEASES, branches.get(RELEASES), summary_path),
            named_media=missing_named(md, []),
            needs_attention=list(pr.get("needs_attention") or outcome.get("attention") or []),
            target_section=target_section, summary_path=summary_path)
        results += check_all(published)
    except Exception as e:  # noqa: BLE001 - reported, then the branches are still deleted
        results.append(Result("pipeline run", False, f"crashed: {e!r}"[:400]))
    finally:
        if previous is None:
            os.environ.pop("PIPELINE_SANDBOX", None)
        else:
            os.environ["PIPELINE_SANDBOX"] = previous
        if keep:
            results.append(Result("sandbox branches deleted", True, f"kept on request (--keep): sandbox/{tag}/"))
        else:
            results.append(cleanup(api, tag))
        shutil.rmtree(workdir, ignore_errors=True)
    return results, outcome


# ── Reporting ────────────────────────────────────────────────────────────

def slack_text(results: list[Result], run_url: str = "") -> str:
    failed = [r for r in results if not r.ok]
    lines = [f"The weekly end-to-end run failed {len(failed)} invariant(s). The unit tests are green, "
             "which tells you exactly how much unit tests are worth.", ""]
    lines += [f"- *{r.name}*: {r.detail}" for r in failed]
    if run_url:
        lines += ["", f"Run: {run_url}"]
    return "\n".join(lines)


def post_slack(text: str, token: str, channel: str, opener=None) -> str:
    """Post; returns "" on success or the reason it failed. Never raises."""
    if not token:
        return "SLACK_BOT_TOKEN is not set"
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request("https://slack.com/api/chat.postMessage",
                                 data=_json.dumps({"channel": channel, "text": shorten(text)}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
    try:
        with opener(req, timeout=TIMEOUT) as resp:
            body = _json.loads(resp.read() or b"{}")
        return "" if body.get("ok") else f"Slack said: {body.get('error', 'unknown error')}"
    except Exception as e:  # noqa: BLE001
        return f"Slack post failed: {e}"


def post_journal(worker_url: str, token: str, record: dict, opener=None) -> str:
    """Best effort. "" when stored or when the endpoint does not exist yet (404)."""
    if not worker_url or not token:
        return "journal not configured"
    opener = opener or urllib.request.urlopen
    req = urllib.request.Request(worker_url.rstrip("/") + "/journal", data=_json.dumps(record).encode(),
                                 headers={"X-Content-Token": token, "Content-Type": "application/json"}, method="POST")
    try:
        with opener(req, timeout=TIMEOUT):
            return ""
    except urllib.error.HTTPError as e:
        return "" if e.code == 404 else f"journal answered HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return f"journal unreachable: {e}"


def report(results: list[Result], env=os.environ, slack_opener=None, journal_opener=None, started: float = 0.0) -> int:
    ok = all(r.ok for r in results)
    run_url = ""
    if env.get("GITHUB_RUN_ID"):
        run_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env.get('GITHUB_REPOSITORY', '')}/actions/runs/{env['GITHUB_RUN_ID']}"
    notes = []
    if not ok:
        err = post_slack(slack_text(results, run_url), env.get("SLACK_BOT_TOKEN", ""),
                         env.get("SLACK_ALERT_CHANNEL") or DEFAULT_ALERT_CHANNEL, slack_opener)
        if err:
            notes.append(f"Slack alert NOT sent: {err}")
    failed = [r.name for r in results if not r.ok]
    err = post_journal(env.get("WORKER_URL") or "https://docagent-worker.se-team-charlie.workers.dev",
                       env.get("CONTENT_TOKEN", ""), {
                           "run_id": f"e2e-{env.get('GITHUB_RUN_ID') or int(time.time())}", "channel": "", "thread_ts": "",
                           "stage": "e2e", "status": "ok" if ok else "failed",
                           "detail": "all invariants hold" if ok else "failed: " + ", ".join(failed),
                           "duration_ms": int((time.time() - started) * 1000) if started else 0,
                           "ts": datetime.now(timezone.utc).isoformat()}, journal_opener)
    if err:
        notes.append(f"Journal record not stored: {err}")
    summary = "## Weekly end-to-end check\n\n" + table(results) + "\n"
    if notes:
        summary += "\n" + "\n".join(f"- {n}" for n in notes) + "\n"
    print(summary)
    path = env.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a") as f:
                f.write(summary)
        except OSError as e:
            print(f"Could not write the job summary: {e}")
    return 0 if ok else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    started = time.time()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    tag = f"e2e-{stamp}-{os.environ.get('GITHUB_RUN_ID') or random.randint(1000, 9999)}"
    if not token:
        return report([Result("configuration", False, "GITHUB_TOKEN is not set")], started=started)
    try:
        from src.orchestrator import run_pipeline
    except Exception as e:  # noqa: BLE001
        return report([Result("pipeline import", False, f"{e!r}")], started=started)
    results, _ = run_check(GitHubAPI(token), run_pipeline, tag, keep="--keep" in argv)
    return report(results, started=started)


if __name__ == "__main__":
    sys.exit(main())
