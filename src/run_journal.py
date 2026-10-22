"""Run journal: one record per stage of every pipeline run, plus end-of-run
invariants, sent to the worker's /journal endpoint.

This is how every capability gets a measured success rate (the weekly
reliability report reads it). Each record is ok, repaired, flagged or
failed:

    ok        the stage did its job with nothing to fix
    repaired  something was wrong and the pipeline fixed it (a duplicate
              dropped, a broken link re-pointed, a model fallback used)
    flagged   something a person must check (listed under Needs attention)
    failed    the run stopped at this stage

Statuses come from the same needs-attention and fixes lists the PR and Slack
show, so the journal can never disagree with what the requester saw.

The journal must never affect a run: every network call has a timeout and
one retry, and every failure is logged and swallowed.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

RUN_STAGES = ["material", "assets", "source", "analysis", "vision", "drafting", "checks",
              "placement", "links", "publish"]
TIMEOUT_S = 10

# Which capability a Needs-attention or fixes line belongs to. First match wins;
# anything unrecognised is charged to publish, so nothing goes uncounted.
_CLASSES = [
    ("vision", re.compile(r"image description failed", re.I)),
    ("checks", re.compile(r"^fact check", re.I)),
    ("assets", re.compile(r"never received|could not download|could not be uploaded|^dropped .+ same file", re.I)),
    ("placement", re.compile(r"sidebar|placement|summary\.md|the section you asked for", re.I)),
    ("links", re.compile(r"\blink\b|cross-link|points to|points outside", re.I)),
]


def classify(item: str) -> str:
    for stage, pattern in _CLASSES:
        if pattern.search(item or ""):
            return stage
    return "publish"


def run_id_from_env(env=None) -> str:
    env = os.environ if env is None else env
    rid = env.get("GITHUB_RUN_ID", "")
    if rid:
        return f"gh-{rid}-{env.get('GITHUB_RUN_ATTEMPT', '1') or '1'}"
    return f"local-{int(time.time() * 1000)}"


class Timeline:
    """When each stage started, in order, so durations can be reported."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.order: list[str] = []
        self._start: dict[str, float] = {}
        self._end: float | None = None

    def mark(self, stage: str) -> None:
        if stage not in self._start:
            self.order.append(stage)
            self._start[stage] = self._clock()

    def close(self) -> None:
        self._end = self._clock()

    def duration_ms(self, stage: str) -> int | None:
        if stage not in self._start:
            return None
        i = self.order.index(stage)
        end = self._start[self.order[i + 1]] if i + 1 < len(self.order) else (self._end or self._clock())
        return int(max(0.0, end - self._start[stage]) * 1000)


def _status(stage: str, failed_stage: str, attention: list, fixes: list):
    if stage == failed_stage:
        return "failed"
    flags = [a for a in attention if classify(a) == stage]
    if flags:
        return "flagged"
    if [f for f in fixes if classify(f) == stage]:
        return "repaired"
    return "ok"


def stage_records(timeline: Timeline, failed_stage: str = "", failure_reason: str = "",
                  attention=(), fixes=(), mode: str = "both", images: int = 0) -> list[dict]:
    """One record per stage the run reached, plus the capabilities that live
    inside a stage (vision inside analysis; placement and links inside publish)."""
    attention, fixes = list(attention or []), list(fixes or [])
    reached = [s for s in timeline.order if s in RUN_STAGES]
    out = []

    def add(stage, duration):
        status = _status(stage, failed_stage, attention, fixes)
        if status == "failed":
            detail = failure_reason
        else:
            mine = [a for a in attention if classify(a) == stage] or [f for f in fixes if classify(f) == stage]
            detail = "; ".join(mine[:3]) + (f" (+{len(mine) - 3} more)" if len(mine) > 3 else "")
        out.append({"stage": stage, "status": status, "detail": detail, "duration_ms": duration})

    for s in reached:
        add(s, timeline.duration_ms(s))
        if s == "analysis" and images and failed_stage != "analysis":
            add("vision", None)
    if "publish" in reached and failed_stage != "publish":
        if mode != "release_only":
            add("placement", None)
        add("links", None)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _last(label: str) -> str:
    return _norm(re.split(r"\s*>\s*", label or "")[-1])


def invariants(*, named: list, missing_named: list, received_files: list, published_refs: set, dropped: list,
               mode: str, target_section: str, docs_section: str, missing_prs: list,
               link_problems: int, needs_attention: int, reached_publish: bool) -> list[dict]:
    """End-of-run checks, each a record of stage invariant:<name>."""
    out = []

    def add(name, ok, detail, bad="flagged"):
        out.append({"stage": f"invariant:{name}", "status": "ok" if ok else bad, "detail": detail, "duration_ms": None})

    images = [f for f in received_files if f not in set(dropped)]
    unpublished = [f for f in images if f not in published_refs] if reached_publish else []
    add("files", not missing_named and not unpublished,
        f"named {len(named)}, received {len(received_files)}, published {len(images) - len(unpublished)}"
        + (f"; never received: {', '.join(missing_named[:5])}" if missing_named else "")
        + (f"; not used on any page: {', '.join(unpublished[:5])}" if unpublished else ""))
    if mode != "release_only" and reached_publish:
        confirmed = bool(target_section.strip())
        placed = bool(docs_section) and not docs_section.startswith("(")
        same = confirmed and placed and _last(target_section) == _last(docs_section)
        add("placement", same,
            f"confirmed: {target_section or 'none'}; placed under: {docs_section or 'nothing'}")
    if reached_publish:
        add("prs", not missing_prs, "all expected PRs opened" if not missing_prs else f"missing: {', '.join(missing_prs)}", bad="failed")
    add("links", link_problems == 0, f"{link_problems} broken link(s) left")
    add("needs_attention", needs_attention == 0, f"{needs_attention} item(s) for a person")
    return out


def published_assets(draft: dict) -> set:
    """Asset file names referenced by any drafted page (release, doc, subsections)."""
    text = json.dumps(draft or {}, default=str)
    return {m for m in re.findall(r"\(assets/([^)\s\"\\]+)\)", text)}


class RunJournal:
    """Posts records to the worker. Never raises."""

    def __init__(self, worker_url: str, token: str, run_id: str, channel: str = "", thread_ts: str = "",
                 opener=None, sleep=None):
        self.url = (worker_url or "").rstrip("/") + "/journal" if worker_url else ""
        self.token = token or ""
        self.run_id = run_id
        self.channel, self.thread_ts = channel, thread_ts
        self._open = opener
        self._sleep = sleep
        self.failures: list[str] = []
        self._warned = False
        # Circuit breaker: once the worker is unreachable, stop trying for the
        # rest of the run. Without it, ~15 records x (2 attempts x 10s) would
        # add minutes to a run whose journal is down anyway.
        self._broken = ""

    @classmethod
    def from_env(cls, env=None, **kw):
        env = os.environ if env is None else env
        return cls(env.get("PL_WORKER_URL", ""), env.get("PL_CONTENT_TOKEN", ""), run_id_from_env(env),
                   env.get("PL_SLACK_CHANNEL", ""), env.get("PL_SLACK_THREAD_TS", ""), **kw)

    def post(self, stage: str, status: str, detail: str = "", duration_ms=None) -> bool:
        if not (self.url and self.token):
            if not self._warned:
                print("[journal] PL_WORKER_URL or PL_CONTENT_TOKEN not set; run journal disabled")
                self._warned = True
            return False
        if self._broken:
            self.failures.append(f"{stage}: skipped, journal unreachable ({self._broken})")
            return False
        body = json.dumps({
            "run_id": self.run_id, "channel": self.channel, "thread_ts": self.thread_ts,
            "stage": stage, "status": status, "detail": (detail or "")[:2000], "duration_ms": duration_ms,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "X-Content-Token": self.token, "User-Agent": "FlytBase-Pipeline/1.0"})
        last, unreachable = "", False
        open_ = self._open or urllib.request.urlopen
        for attempt in range(2):
            try:
                with open_(req, timeout=TIMEOUT_S) as r:
                    r.read()
                return True
            except urllib.error.HTTPError as e:
                last, unreachable = f"HTTP {e.code}", e.code >= 500
                if 400 <= e.code < 500:  # our record or our token is wrong: retrying will not help
                    break
            except Exception as e:  # noqa: BLE001 - the journal must never break a run
                last, unreachable = f"{type(e).__name__}: {e}", True
            if attempt == 0:
                (self._sleep or time.sleep)(1)
        if unreachable:
            self._broken = last
        self.failures.append(f"{stage}: {last}")
        print(f"[journal] could not record {stage} ({last}); the run is unaffected")
        return False

    def post_all(self, records: list[dict]) -> int:
        """Post every record; returns how many were stored. Never raises."""
        stored = 0
        for r in records:
            try:
                stored += bool(self.post(r["stage"], r["status"], r.get("detail", ""), r.get("duration_ms")))
            except Exception as e:  # noqa: BLE001
                print(f"[journal] skipped a record ({e})")
        return stored
