"""The drafting agent end to end, with the model replaced by scripted replies."""
import json
import sys
import types

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from src.agents.drafting import DraftingAgent  # noqa: E402

ASSETS = ["zone-overlays.gif", "live-annotations.gif"]
VISION = [{"file_name": "live-annotations.gif", "description": "Pilot draws a red circle on the live video."}]

GOOD_FIELDS = {
    "filename": "cockpit-3-0.md",
    "title": "Cockpit 3.0 Video Overlays",
    "description": "See zones on the live video and draw on it for your team.",
    "summary": "Cockpit 3.0 adds video overlays. They help pilots stay clear of no-fly zones.",
    "features": [
        {"name": "Zone Overlays", "what": "Zones are drawn on the live video — in real time.",
         "how_to_use": ["Turn on **Zone overlays** in **Video overlays**."],
         "assets": [{"file": "zone-overlays.gif", "alt": "Zones on the video"}]},
        {"name": "Live Annotations", "what": "Draw on the live video.", "how_to_use": ["Turn on **Live annotations**."],
         "assets": [{"file": "live-annotations.gif", "alt": "Annotations"}]},
    ],
    "good_to_know": [],
}
DOC = {
    "filename": "cockpit-video-overlays.md",
    "frontmatter": "---\ndescription: >-\n  Video overlays in Cockpit 3.0.\n---",
    "content": "# Cockpit Video Overlays\n\n## Overview\n\n" + "Zones are shown on the video. " * 200
               + '\n\n{% embed url="https://youtu.be/v" %}\n\n## Zone Overlays\n\n## Live Annotations'
               + "\n\n![a](assets/zone-overlays.gif)\n\n![b](assets/live-annotations.gif)\n",
    "target_path": "in-flight-modules",
    "impacted_page_edits": [],
}


def _agent(replies):
    agent = DraftingAgent.__new__(DraftingAgent)
    agent.conversation_history = []
    calls = []

    def run(message):
        calls.append(message)
        return json.dumps(replies.pop(0))

    agent.run = run
    return agent, calls


def _draft(agent, mode="both"):
    return agent.draft("## Zone Overlays\nZones on video.\n## Live Annotations\nDraw.", {}, VISION, ASSETS,
                       youtube_link="https://youtu.be/v", mode=mode)


def test_release_note_is_rendered_from_the_template_and_auto_fixed():
    agent, calls = _agent([GOOD_FIELDS, DOC])
    out = _draft(agent)
    rn = out["release_note"]
    assert rn["filename"] == "cockpit-3-0.md"
    c = rn["content"]
    assert c.startswith('# Cockpit 3.0 Video Overlays\n\n{% embed url="https://youtu.be/v" %}')
    assert "## What's New" in c and "### Zone Overlays" in c and "## Learn More" in c
    assert "[Cockpit Video Overlays]({{DOC_PAGE_URL}})" in c
    assert "—" not in c and "Zones are drawn on the live video, in real time." in c  # em dash auto-fixed
    assert len(calls) == 2  # no retry needed


def test_the_prompts_carry_the_rules_not_the_memory_dump():
    agent, calls = _agent([GOOD_FIELDS, DOC])
    _draft(agent)
    release_msg, doc_msg = calls
    assert "WRITING RULES" in release_msg and "DOC PAGE RULES" not in release_msg
    assert "DOC PAGE RULES" in doc_msg
    assert "--- MEMORY FILES ---" not in release_msg + doc_msg
    # CLAUDE.md's old release voice never reaches the model, for either output.
    assert "treat release notes as storytelling" not in (release_msg + doc_msg).lower()
    assert "Release Notes (releases.flytbase.com)" not in doc_msg


def test_banned_language_gets_one_targeted_retry():
    hype = json.loads(json.dumps(GOOD_FIELDS))
    hype["summary"] = "This game-changer makes Cockpit seamless."
    agent, calls = _agent([hype, DOC, GOOD_FIELDS])
    out = _draft(agent)
    assert len(calls) == 3
    assert "game-changer" in calls[2] and "Fix ONLY these problems" in calls[2]
    assert "game-changer" not in out["release_note"]["content"]
    assert "release_note" not in out.get("_style_report", {})


def test_a_missing_asset_is_fixed_in_the_fields_not_by_rewriting_the_note():
    forgot = json.loads(json.dumps(GOOD_FIELDS))
    forgot["features"][1]["assets"] = []
    agent, calls = _agent([forgot, GOOD_FIELDS, DOC])
    out = _draft(agent)
    assert "asset 'live-annotations.gif' missing from release note" in calls[1]
    assert out["release_note"]["content"].count("](assets/live-annotations.gif)") == 1


def test_leftover_style_issues_are_reported_for_the_reviewer():
    still = json.loads(json.dumps(GOOD_FIELDS))
    still["summary"] = "We built this."
    agent, _ = _agent([still, DOC, still])  # the retry does not fix it
    out = _draft(agent)
    report = out["_style_report"]["release_note"]
    assert any('"We"' in r for r in report)


def test_doc_only_drafts_no_release_note():
    agent, calls = _agent([DOC])
    out = _draft(agent, mode="doc_only")
    assert out["release_note"] == {}
    assert len(calls) == 1
