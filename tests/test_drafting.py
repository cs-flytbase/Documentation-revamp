"""The drafting agent end to end, with the model replaced by scripted replies."""
import json
import sys
import types

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from src.agents.drafting import DraftingAgent, route_assets  # noqa: E402

ASSETS = ["hero.gif", "01-open-panel.png", "02-zone-overlays.gif"]
VISION = [{"file_name": "hero.gif", "description": "Zones drawn on the live drone video."}]

RELEASE = {
    "filename": "video-overlays.md", "title": "Cockpit 3.0 Video Overlays", "feature_name": "Video Overlays",
    "description": "See no-fly zones on the live video.",
    "problem": "Pilots had to compare the video with the map — constantly.",
    "introduction": "Zones are now drawn on the live video.",
    "capabilities": [{"name": "Zone Overlays", "description": "NFZs and geofence on the video.", "asset": ""}],
    "why_it_matters": [{"benefit": "One screen", "detail": "No map switching."},
                       {"benefit": "Fewer mistakes", "detail": "Zones are where the pilot is looking."}],
    "who_its_for": [{"audience": "Pilots", "value": "Stay clear of NFZs."}],
    "key_takeaways": ["Zones on video.", "Less switching."],
    "hero_asset": "hero.gif", "access": "",
}
DOC = {
    "filename": "video-overlays.md", "target_path": "in-flight-modules", "title": "Video Overlays",
    "description": "Overlays on the live video.", "overview": "Overlays draw zones on the video.",
    "problem_statement": "Zones were only on the map.",
    "navigation": ["**Video overlays**"],
    "interface_reference": [{"element": "**Video overlays**", "what_it_does": "opens panel"}],
    "how_to": [{"task": "Open Overlays", "steps": [
        {"text": "**Click** **Video overlays**.", "asset": "01-open-panel.png"},
        {"text": "**Look** at the video.", "asset": "02-zone-overlays.gif"}]}],
    "section_assets": {"overview": ["hero.gif"]},
    "impacted_page_edits": [{"source_url": "https://docs.flytbase.com/x", "section_heading": "## A", "patch_mode": "append", "patch_content": "x"}],
}


def _agent(replies):
    agent = DraftingAgent.__new__(DraftingAgent)
    agent.conversation_history = []
    calls = []

    def run(message):
        calls.append({"system": agent.system_prompt, "message": message})
        return json.dumps(replies.pop(0))

    agent.run = run
    return agent, calls


def _draft(agent, **kw):
    kw.setdefault("mode", "both")
    return agent.draft("## Video Overlays\nZones on video.", {}, VISION, ASSETS, youtube_link="https://youtu.be/v", **kw)


def test_both_outputs_from_one_source_in_their_own_templates():
    agent, calls = _agent([RELEASE, DOC])
    out = _draft(agent)
    rn, dp = out["release_note"]["content"], out["doc_page"]["content"]
    assert "## Why It Matters" in rn and "## How To" not in rn
    assert "## How To" in dp and "## Why It Matters" not in dp
    assert rn.splitlines()[2] == dp.splitlines()[2] == '{% embed url="https://youtu.be/v" %}'  # video first in both
    assert "—" not in rn  # auto-fixed
    assert "[Video Overlays]({{DOC_PAGE_URL}})" in rn
    assert out["impacted_page_edits"][0]["section_heading"] == "## A"
    assert len(calls) == 2


def test_doc_gaps_are_reported_not_invented():
    agent, _ = _agent([RELEASE, DOC])
    gaps = _draft(agent)["_doc_gaps"]
    assert "Troubleshooting" in gaps and "Prerequisites" in gaps


def test_each_output_gets_its_own_prompt_and_rules():
    agent, calls = _agent([RELEASE, DOC])
    _draft(agent)
    rel, doc = calls
    assert "RELEASE NOTE" in rel["system"] and "DOC PAGE" in doc["system"]
    assert "TONE: a marketable note" in rel["message"] and "TONE: a technical reference" in doc["message"]
    assert "--- MEMORY FILES ---" not in rel["message"] + doc["message"]
    assert "treat release notes as storytelling" not in (rel["message"] + doc["message"]).lower()


def test_image_routing():
    doc_assets, required, optional = route_assets(ASSETS, {"hero.gif": "release", "02-zone-overlays.gif": "both"})
    assert doc_assets == ["01-open-panel.png", "02-zone-overlays.gif"]
    assert required == ["hero.gif", "02-zone-overlays.gif"]
    assert optional == ["01-open-panel.png"]
    assert route_assets(ASSETS, None) == (ASSETS, [], ASSETS)


def test_release_only_image_stays_off_the_doc_page():
    doc = json.loads(json.dumps(DOC))
    doc["section_assets"] = {}  # hero.gif is release-only, so the doc page must not use it
    agent, calls = _agent([RELEASE, doc])
    out = _draft(agent, asset_audience={"hero.gif": "release"})
    assert "assets/hero.gif" in out["release_note"]["content"]
    assert "assets/hero.gif" not in out["doc_page"]["content"]
    assert "hero.gif" not in calls[1]["message"].split("--- DOC ASSETS ---")[1].split("--- END DOC ASSETS ---")[0]


def test_feature_flag_adds_access_only_when_given():
    agent, calls = _agent([RELEASE, DOC])
    out = _draft(agent, feature_flag="video_overlays")
    assert "## Access" in out["release_note"]["content"] and "`video_overlays`" in out["release_note"]["content"]
    assert "behind the `video_overlays` feature flag" in calls[0]["message"]
    agent, _ = _agent([RELEASE, DOC])
    assert "## Access" not in _draft(agent)["release_note"]["content"]


def test_field_problems_get_one_targeted_retry():
    forgot = json.loads(json.dumps(DOC))
    forgot["how_to"][0]["steps"][1]["asset"] = ""
    agent, calls = _agent([RELEASE, forgot, DOC])
    _draft(agent)
    assert len(calls) == 3
    assert "asset '02-zone-overlays.gif' missing from doc page" in calls[2]["message"]


def test_banned_language_gets_a_style_retry():
    hype = json.loads(json.dumps(RELEASE))
    hype["introduction"] = "This game-changer draws zones on the video."
    agent, calls = _agent([hype, DOC, RELEASE])
    out = _draft(agent)
    assert "game-changer" in calls[2]["message"] and "game-changer" not in out["release_note"]["content"]


def test_release_with_no_image_is_sent_back():
    bare = json.loads(json.dumps(RELEASE))
    bare["hero_asset"] = ""
    agent, calls = _agent([bare, RELEASE, DOC])
    _draft(agent)
    assert "uses no image" in calls[1]["message"]


def test_modes():
    agent, calls = _agent([DOC])
    out = _draft(agent, mode="doc_only")
    assert out["release_note"] == {} and len(calls) == 1
    agent, calls = _agent([RELEASE])
    out = _draft(agent, mode="release_only")
    assert out["doc_page"] == {} and out["impacted_page_edits"] == [] and len(calls) == 1
    assert "full details" not in out["release_note"]["content"]  # no doc page, no doc link


# ── Subsections: each page uses only its own feature's images ─────────────

COCKPIT_ASSETS = ["zone-overlays.gif", "zone-overlays-01-video-overlays-panel.png",
                  "live-annotation.gif", "live-annotation-02-toolbar-freehand-selected.png"]
FOCUS = {"title": "Zone Overlays", "summary": "Zones on the video", "slug": "zone-overlays"}


def _zone_doc():
    doc = json.loads(json.dumps(DOC))
    doc["title"] = "Zone Overlays"
    doc["section_assets"] = {"overview": ["zone-overlays.gif"]}
    doc["how_to"] = [{"task": "Show Zones", "steps": [
        {"text": "**Click** **Video overlays**.", "asset": "zone-overlays-01-video-overlays-panel.png"}]}]
    return doc


def test_a_subsection_page_is_not_forced_to_use_other_features_images():
    agent, calls = _agent([_zone_doc()])
    out = agent.draft("## Zone Overlays\nZones.", {}, [], COCKPIT_ASSETS, mode="doc_only", subsection_focus=FOCUS)
    page = out["doc_page"]["content"]
    assert len(calls) == 1  # no retry demanding the Live Annotation images
    assert "live-annotation" not in page  # nothing dumped at the end of How To
    assert page.count("](assets/zone-overlays") == 2
    assert "Use ONLY the ones that show this subsection" in calls[0]["message"]


def test_a_single_page_must_still_use_every_image():
    agent, calls = _agent([_zone_doc(), _zone_doc()])
    out = agent.draft("## Zone Overlays\nZones.", {}, [], COCKPIT_ASSETS, mode="doc_only")
    assert len(calls) == 2 and "asset 'live-annotation.gif' missing from doc page" in calls[1]["message"]
    assert out["doc_page"]["content"].count("](assets/live-annotation.gif)") == 1  # placed, never dropped
