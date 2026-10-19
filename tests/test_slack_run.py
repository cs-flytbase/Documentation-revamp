"""The Slack pipeline run: material, assets, progress, and honest failure."""
import io
import json
import sys
import types
from pathlib import Path

import pytest

from src import slack_run
from src.slack_run import build_material, check_status, expected_prs_missing, progress_text, safe_filename


def test_original_file_names_are_kept_and_made_safe():
    taken = set()
    assert safe_filename("03-Configure Migration.PNG", taken, "x") == "03-configure-migration.png"
    assert safe_filename("03-configure-migration.png", taken, "x") == "03-configure-migration-2.png"
    assert safe_filename("Screenshot 2026-10-07 at 1.01.04 PM.png", set(), "x") == "screenshot-2026-10-07-at-1-01-04-pm.png"


def test_material_new_format_with_intake():
    kv = {
        "content": "Ship it.", "links": ["<https://youtu.be/v|v>"],
        "text_files": [{"name": "notes.md", "content": "# Notes"}],
        "assets": [{"url": "https://files.slack.com/a", "name": "Zone Overlays.gif"},
                   {"url": "https://files.slack.com/b", "name": "walkthrough.mp4"},
                   {"url": "https://files.slack.com/c", "name": "hero.png"}],
        "intake": {"asset_audience": {"Zone Overlays.gif": "both", "hero.png": "release", "ghost.png": "doc"},
                   "feature_flag": " video_overlays "},
    }
    m = build_material(kv)
    assert [a["file"] for a in m["assets"]] == ["zone-overlays.gif", "walkthrough.mp4", "hero.png"]
    assert [a["kind"] for a in m["assets"]] == ["image", "video", "image"]
    assert m["asset_audience"] == {"zone-overlays.gif": "both", "hero.png": "release"}  # unknown files ignored
    assert m["feature_flag"] == "video_overlays"
    assert m["video_link"] == "https://youtu.be/v"
    assert "# Notes" in m["content"] and "Ship it." in m["content"]


def test_material_old_format_still_works():
    m = build_material({"content": "x", "image_urls": ["https://files.slack.com/files-pri/T/F/image.png", ""]})
    assert [a["file"] for a in m["assets"]] == ["image.png"]
    assert m["asset_audience"] == {} and m["feature_flag"] == ""


def test_progress_message_shows_every_stage():
    text = progress_text("Video Overlays", ["material", "assets"], "source", None, {"assets": "3 image(s), 1 video(s)"}, "RUN")
    assert ":white_check_mark: Fetch the images and videos (3 image(s), 1 video(s))" in text
    assert ":hourglass_flowing_sand: Write the source document..." in text
    assert ":white_small_square: Open the PRs" in text


def test_prs_that_should_exist():
    assert expected_prs_missing("both", {"docs_pr": "x"}) == ["release note PR"]
    assert expected_prs_missing("doc_only", {"docs_pr": "x"}) == []
    assert expected_prs_missing("release_only", {}) == ["release note PR"]


def test_download_rejects_an_html_page(monkeypatch, tmp_path):
    monkeypatch.setattr(slack_run.time, "sleep", lambda s: None)
    monkeypatch.setattr(slack_run.urllib.request, "urlopen", lambda req, timeout=0: io.BytesIO(b"<!DOCTYPE html><html>login</html>" + b" " * 200))
    err = slack_run.download("https://files.slack.com/x", "t", tmp_path / "x.png")
    assert "returned a page instead of the file" in err


def test_check_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert check_status() == 1  # no status file is a failure
    Path("pipeline_status.json").write_text(json.dumps({"status": "success"}))
    assert check_status() == 0
    Path("pipeline_status.json").write_text(json.dumps({"status": "failed", "stage": "drafting"}))
    assert check_status() == 1


# ── main() end to end, with GitHub, Slack and the agents stubbed ──────────

@pytest.fixture
def run_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(slack_run.time, "sleep", lambda s: None)
    for k, v in {"PL_FEATURE_NAME": "video-overlays", "PL_MODE": "both", "PL_WORKER_URL": "https://w",
                 "PL_CONTENT_KEY": "k", "SLACK_BOT_TOKEN": "", "PL_SLACK_CHANNEL": "", "PL_SLACK_THREAD_TS": ""}.items():
        monkeypatch.setenv(k, v)
    kv = {"content": "Video overlays draw zones.", "assets": [{"url": "https://files/x", "name": "zones.gif"}],
          "intake": {"feature_flag": "video_overlays", "asset_audience": {"zones.gif": "both"}}}

    def urlopen(req, timeout=0):
        url = req.full_url if hasattr(req, "full_url") else req
        if "/content?key=" in url:
            return io.BytesIO(json.dumps(kv).encode())
        return io.BytesIO(b"GIF89a" + b"\0" * 200)

    monkeypatch.setattr(slack_run.urllib.request, "urlopen", urlopen)
    sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
    monkeypatch.setitem(sys.modules, "src.agents.source", types.SimpleNamespace(
        SourceAgent=lambda: types.SimpleNamespace(build_pm_doc=lambda **k: {"pm_doc": "# PM"})))
    calls = {}

    def install(result):
        def run_pipeline(bundle, **kw):
            calls.update(kw, bundle=bundle)
            kw["on_stage"]("analysis")
            kw["on_stage"]("drafting")
            return result
        monkeypatch.setitem(sys.modules, "src.orchestrator", types.SimpleNamespace(run_pipeline=run_pipeline))
    return install, calls


def test_main_success_passes_routing_and_flag(run_env):
    install, calls = run_env
    install({"status": "success", "pr_results": {"docs_pr": "d", "releases_pr": "r", "errors": []},
             "draft": {"release_note": {"filename": "video-overlays.md"}}})
    assert slack_run.main() == 0
    assert json.loads(Path("pipeline_status.json").read_text())["status"] == "success"
    assert calls["asset_audience"] == {"zones.gif": "both"} and calls["feature_flag"] == "video_overlays"
    assert Path("input_bundles/video-overlays/zones.gif").exists()
    assert json.loads(Path("pr_results.json").read_text())["release_note_file"].endswith("/video-overlays.md")


def test_main_fails_honestly_when_a_pr_is_missing(run_env):
    install, _ = run_env
    install({"status": "success", "pr_results": {"docs_pr": "d", "errors": ["Releases repo failed: 422"]}, "draft": {}})
    assert slack_run.main() == 1
    status = json.loads(Path("pipeline_status.json").read_text())
    assert status == {"status": "failed", "stage": "drafting", "reason": "Releases repo failed: 422"}


def test_main_reports_the_stage_of_an_exception(run_env, monkeypatch):
    install, _ = run_env

    def boom(bundle, **kw):
        kw["on_stage"]("analysis")
        raise RuntimeError("vision exploded")
    monkeypatch.setitem(sys.modules, "src.orchestrator", types.SimpleNamespace(run_pipeline=boom))
    with pytest.raises(RuntimeError):
        slack_run.main()
    status = json.loads(Path("pipeline_status.json").read_text())
    assert status["stage"] == "analysis" and "vision exploded" in status["reason"]
