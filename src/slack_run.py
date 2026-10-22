"""The Slack-triggered pipeline run, stage by stage.

This used to be ~150 lines of Python inside a YAML heredoc: untestable, and
twice broken by shell quoting. It now lives here, with tests.

Every stage is announced in ONE Slack message that is updated in place, so
the thread shows where the run is and, if it fails, exactly which stage
failed and why. The outcome is written to pipeline_status.json; the workflow
fails the job when the run failed, so "success" in Actions means success.

Run as: python3 -m src.slack_run   (configuration comes from PL_* env vars)
"""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

STATUS_FILE = "pipeline_status.json"
RESULTS_FILE = "pr_results.json"

STAGES = [
    ("material", "Read your material"),
    ("assets", "Fetch the images and videos"),
    ("source", "Write the source document"),
    ("analysis", "Look at every frame and work out where this goes"),
    ("drafting", "Draft the release note and the doc page"),
    ("checks", "Check them against the source"),
    ("publish", "Open the PRs"),
]
STAGE_NAMES = [s for s, _ in STAGES]

VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "loom.com", "wistia.com")
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
VIDEO_EXTS = {"mp4", "mov", "webm", "m4v"}


# ── Material ───────────────────────────────────────────────────────────

def _clean_link(u: str) -> str:
    u = (u or "").strip().lstrip("<").rstrip(">")
    return u.split("|", 1)[0].strip()


def safe_filename(name: str, taken: set, fallback: str) -> str:
    """A filesystem- and URL-safe version of the uploader's file name.

    Original names carry meaning ("03-configure-migration.png"); the pipeline
    used to throw them away and call everything image-N.
    """
    stem, dot, ext = (name or "").rpartition(".")
    if not dot:
        stem, ext = name or "", ""
    ext = re.sub(r"[^a-z0-9]", "", ext.lower())
    stem = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:60] or fallback
    candidate = f"{stem}.{ext}" if ext else stem
    n = 2
    while candidate in taken:
        candidate = f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}"
        n += 1
    taken.add(candidate)
    return candidate


def build_material(kv: dict) -> dict:
    """Everything the run needs from the stored Slack content, in one place."""
    links = [l for l in (_clean_link(x) for x in kv.get("links") or []) if l]
    video_links = [l for l in links if any(h in l.lower() for h in VIDEO_HOSTS)]
    page_links = [l for l in links if l not in video_links]
    raw = kv.get("content") or ""
    parts = page_links + [tf.get("content", "") for tf in kv.get("text_files") or []] + ([raw] if raw else [])

    # Assets: the new format keeps names, types and audiences; the old one was
    # a bare list of URLs.
    entries = kv.get("assets")
    if not entries:
        entries = [{"url": u, "name": ""} for u in kv.get("image_urls") or [] if u]
    taken, assets = set(), []
    for i, a in enumerate(entries, 1):
        url = a.get("url") or ""
        if not url:
            continue
        name = a.get("name") or url.split("?")[0].rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in IMAGE_EXTS | VIDEO_EXTS:
            name, ext = f"{name or f'image-{i}'}.png", "png"
        assets.append({"url": url, "original": a.get("name") or name,
                       "file": safe_filename(name, taken, f"asset-{i}"),
                       "kind": "video" if ext in VIDEO_EXTS else "image"})

    intake = kv.get("intake") or {}
    # The same name can arrive twice (Slack saves the second copy as "-2").
    # The agreed routing applies to every copy; identical copies are merged
    # before drafting (src/assets.py).
    by_original: dict[str, list] = {}
    for a in assets:
        by_original.setdefault(a["original"], []).append(a["file"])
    audience = {}
    for original, aud in (intake.get("asset_audience") or {}).items():
        if aud in ("release", "doc", "both"):
            for f in by_original.get(original, []):
                audience[f] = aud
    return {
        "content": "\n\n".join(p for p in parts if p),
        "comments": raw[:500],
        "reference_link": next((l for l in links if "releases.flytbase.com" in l), ""),
        "video_link": (intake.get("video_link") or (video_links[0] if video_links else "")),
        "assets": assets,
        "asset_audience": audience,
        "feature_flag": (intake.get("feature_flag") or "").strip(),
    }


def download(url: str, token: str, dest: Path, attempts: int = 3) -> str:
    """Download with retries. Returns "" on success or the last error."""
    error = ""
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) < 100 or data[:15].lower().startswith(b"<!doctype html"):
                raise ValueError("Slack returned a page instead of the file (bot may lack access)")
            dest.write_bytes(data)
            return ""
        except Exception as e:  # noqa: BLE001 - every failure is reported
            error = str(e)
            time.sleep(1.5 * (attempt + 1))
    return error


# ── Slack progress ─────────────────────────────────────────────────────

def _slack(method: str, token: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"https://slack.com/api/{method}", data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def progress_text(feature: str, done: list[str], current: str | None, failed: tuple | None,
                  notes: dict, run_url: str) -> str:
    """The progress message. Gilfoyle wrote it; the facts are still all there."""
    if failed:
        head = f":x: *{feature}* died at the stage below. Not my finest moment, and probably not my fault."
    elif current is None and done:
        head = f":white_check_mark: *{feature}*: every stage done. PR links are below."
    else:
        head = f":gear: Working on *{feature}*. Try to contain your excitement."
    lines = [head, ""]
    for key, label in STAGES:
        note = f" ({notes[key]})" if notes.get(key) else ""
        if failed and key == failed[0]:
            lines.append(f":x: {label}: *failed* - {failed[1]}")
        elif key in done:
            lines.append(f":white_check_mark: {label}{note}")
        elif key == current:
            lines.append(f":hourglass_flowing_sand: {label}...")
        else:
            lines.append(f":white_small_square: {label}")
    lines += ["", f"Run: {run_url}"]
    if failed:
        lines.append("Ask me why it failed and I'll read the logs so you don't have to.")
    return "\n".join(lines)


class Progress:
    """One Slack message, updated as stages move. Never raises."""

    def __init__(self, token: str, channel: str, thread_ts: str, feature: str, run_url: str):
        self.token, self.channel, self.thread_ts = token, channel, thread_ts
        self.feature, self.run_url = feature, run_url
        self.done, self.current, self.failed, self.notes, self.ts = [], None, None, {}, None

    def _render(self) -> str:
        return progress_text(self.feature, self.done, self.current, self.failed, self.notes, self.run_url)

    def _push(self) -> None:
        if not (self.token and self.channel and self.thread_ts):
            print(self._render())
            return
        try:
            if self.ts:
                _slack("chat.update", self.token, {"channel": self.channel, "ts": self.ts, "text": self._render()})
            else:
                r = _slack("chat.postMessage", self.token, {"channel": self.channel, "thread_ts": self.thread_ts,
                                                             "text": self._render(), "unfurl_links": False})
                self.ts = r.get("ts")
        except Exception as e:  # noqa: BLE001
            print(f"(progress message failed: {e})")

    def start(self, stage: str, note: str = "") -> None:
        if self.current and self.current not in self.done:
            self.done.append(self.current)
        for name in STAGE_NAMES[:STAGE_NAMES.index(stage)]:
            if name not in self.done:
                self.done.append(name)
        self.current = stage
        if note:
            self.notes[stage] = note
        self._push()

    def note(self, stage: str, text: str) -> None:
        self.notes[stage] = text
        self._push()

    def finish(self) -> None:
        if self.current and self.current not in self.done:
            self.done.append(self.current)
        self.current = None
        self._push()

    def fail(self, stage: str, reason: str) -> None:
        self.failed = (stage, reason[:300])
        self._push()


def write_status(status: str, stage: str = "", reason: str = "") -> None:
    Path(STATUS_FILE).write_text(json.dumps({"status": status, "stage": stage, "reason": reason}))


def expected_prs_missing(mode: str, pr_results: dict) -> list[str]:
    """PRs this mode should have opened but did not."""
    missing = []
    if mode in ("both", "release_only") and not pr_results.get("releases_pr"):
        missing.append("release note PR")
    if mode in ("both", "doc_only") and not pr_results.get("docs_pr"):
        missing.append("doc page PR")
    return missing


# ── The run ────────────────────────────────────────────────────────────

def main() -> int:
    sys.path.insert(0, ".")
    from src.diag import annotate, annotate_errors, install_excepthook
    install_excepthook()

    env = os.environ
    feature_name = env.get("PL_FEATURE_NAME", "") or "feature"
    mode = env.get("PL_MODE", "") or "both"
    token = env.get("SLACK_BOT_TOKEN", "")
    channel, thread_ts = env.get("PL_SLACK_CHANNEL", ""), env.get("PL_SLACK_THREAD_TS", "")
    run_url = "{}/{}/actions/runs/{}".format(env.get("GITHUB_SERVER_URL", "https://github.com"),
                                            env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_RUN_ID", ""))
    progress = Progress(token, channel, thread_ts, feature_name.replace("-", " ").title(), run_url)
    write_status("failed", "material", "the run stopped before finishing")  # overwritten on success
    stage = "material"

    def fail(reason: str) -> int:
        print(f"[Slack Pipeline] FAILED at {stage}: {reason}")
        annotate(reason, title=f"Pipeline failed at {stage}")
        progress.fail(stage, reason)
        write_status("failed", stage, reason)
        return 1

    try:
        # 1. Material
        progress.start("material")
        url = f"{env.get('PL_WORKER_URL', '')}/content?key={env.get('PL_CONTENT_KEY', '')}"
        try:
            # The worker serves stored material only with the shared token.
            headers = {"User-Agent": "FlytBase-Pipeline/1.0", "X-Content-Token": env.get("PL_CONTENT_TOKEN", "")}
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
                kv = json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            return fail(f"could not fetch the stored material ({e}). It expires after 24 hours; send it again.")
        m = build_material(kv)
        if not m["content"].strip() and not m["assets"]:
            return fail("the stored material is empty")
        print(f"[Slack Pipeline] Content: {len(m['content'])} chars, {len(m['assets'])} files, video link: {bool(m['video_link'])}")

        # 2. Assets
        stage = "assets"
        progress.start("assets")
        bundle_dir = Path(f"input_bundles/{feature_name}")
        bundle_dir.mkdir(parents=True, exist_ok=True)
        if m["video_link"]:
            (bundle_dir / "youtube_link.txt").write_text(m["video_link"])
        failed_downloads = []
        for a in m["assets"]:
            err = download(a["url"], token, bundle_dir / a["file"])
            print(f"  {'Downloaded' if not err else 'FAILED'}: {a['file']}{'' if not err else ' - ' + err}")
            if err:
                failed_downloads.append(f"{a['original']} ({err})")
        got = len(m["assets"]) - len(failed_downloads)
        # Files the material names that never arrived (Slack takes 10 files per
        # message; the rest of a batch is easy to lose). Flagged, never fatal.
        from src.assets import missing_named
        attention = []
        missing = missing_named(m["content"], [a["original"] for a in m["assets"]] + [a["file"] for a in m["assets"]])
        if missing:
            attention.append(f"The material names {len(missing)} file(s) that were never received: "
                             + ", ".join(missing[:12]) + (" ..." if len(missing) > 12 else "")
                             + ". Send them in the thread and ask for a re-run, or remove them from the material.")
        if failed_downloads:
            attention.append("Could not download from Slack: " + "; ".join(failed_downloads)
                             + ". Re-share those files and ask for a re-run.")
        if m["assets"] and not got:
            return fail("none of the files could be downloaded from Slack: " + "; ".join(failed_downloads[:3]))
        videos = sum(1 for a in m["assets"] if a["kind"] == "video")
        progress.note("assets", f"{got - videos} image(s), {videos} video(s)" + (f", {len(failed_downloads)} failed" if failed_downloads else ""))

        # 3. Source document
        stage = "source"
        progress.start("source")
        from src.agents.source import SourceAgent
        src = SourceAgent().build_pm_doc(content=m["content"], comments=m["comments"], reference_link=m["reference_link"])
        if src.get("error"):
            return fail(f"the source agent failed: {src['error']}")
        (bundle_dir / "pm_doc.md").write_text(src["pm_doc"])

        # 4-7. The pipeline, reporting its own stages
        subsections = None
        if env.get("PL_HAS_SUBSECTIONS") == "true" and Path("/tmp/subsections.json").exists():
            subsections = json.loads(Path("/tmp/subsections.json").read_text())

        def on_stage(name):
            nonlocal stage
            stage = name
            progress.start(name)

        from src.orchestrator import run_pipeline
        result = run_pipeline(
            str(bundle_dir), mode=mode, requester_name=env.get("PL_REQUESTER_NAME", ""),
            requester_username=env.get("PL_REQUESTER_USERNAME", ""), slack_channel=channel,
            slack_thread_ts=thread_ts, subsections=subsections, target_section=env.get("PL_TARGET_SECTION", ""),
            allow_new_section=env.get("PL_ALLOW_NEW_SECTION", "") == "true",
            doc_scope=env.get("PL_DOC_SCOPE", ""), asset_audience=m["asset_audience"],
            feature_flag=m["feature_flag"], on_stage=on_stage, attention=attention,
        )

        pr_results = result.get("pr_results") or {}
        errors = list(pr_results.get("errors") or []) + list(result.get("errors") or [])
        draft = result.get("draft") or {}
        # What a person must check, from every stage, for the Slack message.
        if not pr_results.get("needs_attention"):
            pr_results["needs_attention"] = list(dict.fromkeys(list(result.get("attention") or []) + attention))
        from datetime import datetime
        month = datetime.now().strftime("%B-%Y").lower()
        rn_name = (draft.get("release_note") or {}).get("filename") or ""
        pr_results["release_note_file"] = f"{month}/{rn_name}" if rn_name else ""
        pr_results["doc_page_file"] = ((draft.get("doc_page") or {}).get("filename") or "")
        pr_results["feature_flag"] = m["feature_flag"]
        pr_results["errors"] = errors
        Path(RESULTS_FILE).write_text(json.dumps(pr_results))
        annotate_errors(errors)

        missing = expected_prs_missing(mode, pr_results)
        if result.get("status") == "failed" or missing:
            reason = "; ".join(errors[:2]) or (f"no {' or '.join(missing)} was opened" if missing else "the pipeline reported a failure")
            return fail(reason)

        progress.finish()
        write_status("success")
        return 0
    except Exception as e:  # noqa: BLE001 - reported with its stage, then re-raised for the traceback
        fail(f"{type(e).__name__}: {e}")
        raise


def check_status() -> int:
    """Exit non-zero unless the run recorded success. A missing file is a failure."""
    path = Path(STATUS_FILE)
    status = json.loads(path.read_text()) if path.exists() else {"status": "failed", "stage": "startup", "reason": "no status file"}
    print(f"Pipeline: {status}")
    return 0 if status.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(check_status() if "--check-status" in sys.argv else main())
