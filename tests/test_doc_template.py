"""Doc master template (2026-10-22): the absolute truth on how to use a
feature. 20 sections in a fixed order; a section the material does not cover
is left out, never padded; every fact is stated once."""
import re

from src.doc_template import DOC_PAGE_PROMPT, GAP, SECTIONS, check_fields, render, render_master, title_case

ASSETS = ["01-open-panel.png", "02-zone-overlays.gif"]

FULL = {
    "filename": "video-overlays.md", "target_path": "in-flight-modules",
    "title": "Zone Overlays", "description": "Show No-Fly Zones and the Geofence on the live video.",
    "overview": "Zone Overlays draws No-Fly Zones (NFZs) and the Geofence on the live video.",
    "when_to_use": ["Flying near NFZs."], "when_not_to_use": ["Split-screen is on; use the map instead."],
    "availability": "Behind a feature flag. Contact the FlytBase team to enable it.",
    "compatibility": ["Dock 2 and Dock 3 payload cameras."],
    "prerequisites": ["Zones are synced to the drone."], "dependencies": ["Zone sync to the drone."],
    "roles_and_permissions": ["Any user with access to the drone."],
    "navigation": ["**Video overlays** at the bottom-right of the video"],
    "controls_and_indicators": [{"name": "**Zone overlays**", "description": "Toggle that shows or hides zones on the video."}],
    "procedures": [{"task": "show zones on the video", "steps": [
        {"text": "**Click** **Video overlays**. The panel opens.", "asset": "01-open-panel.png"},
        {"text": "**Turn on** **Zone overlays**. Zones appear on the video.", "asset": "02-zone-overlays.gif"},
    ]}],
    "expected_behavior": ["NFZs appear as red shaded areas; the Geofence appears as a green outline."],
    "operating_constraints": ["Only zones synced to the drone are drawn."],
    "edge_cases": [{"condition": "The drone has no synced zones", "behavior": "Nothing is drawn on the video."}],
    "system_messages": [{"message": "No zones synced to this drone.", "meaning": "No zone data for this drone.", "action": "Sync the zones."}],
    "faqs": [{"question": "Why is the Geofence missing", "answer": "See Operating Constraints."}],
    "after_completion": ["Cockpit remembers the toggle state."],
    "glossary": [{"term": "Geofence", "definition": "The boundary the drone must stay inside."}],
    "related_pages": [{"title": "Zones", "url": "../pre-flight-modules/zones.md"}],
}


def _h2(content):
    return re.findall(r"^## (.+)$", content, re.M)


def test_full_page_has_all_sections_in_master_order_with_video_first():
    page, omitted = render(FULL, ASSETS, youtube_link="https://youtu.be/v")
    c = page["content"]
    assert _h2(c) == [h for _, h in SECTIONS]
    assert c.splitlines()[2] == '{% embed url="https://youtu.be/v" %}'
    assert omitted == []
    assert "### Show Zones on the Video" in c


def test_missing_sections_are_left_out_not_padded():
    sparse = {k: FULL[k] for k in ("filename", "title", "description", "overview", "procedures")}
    page, omitted = render(sparse, ASSETS)
    assert _h2(page["content"]) == ["Overview", "Procedures"]
    assert GAP not in page["content"]
    assert "Operating Constraints" in omitted and "Frequently Asked Questions" in omitted
    assert "Related Pages" not in omitted


def test_tables_and_faq_format():
    c = render(FULL, ASSETS)[0]["content"]
    assert "| Control | Description |" in c
    assert "| Condition | What Happens |\n|---|---|\n| The drone has no synced zones | Nothing is drawn on the video. |" in c
    assert '| "No zones synced to this drone." | No zone data for this drone. | Sync the zones. |' in c
    assert "**Why is the Geofence missing?**\n\nSee Operating Constraints." in c
    assert "| Term | Definition |" in c


def test_images_sit_right_after_their_step_and_none_are_lost():
    c = render(FULL, ASSETS)[0]["content"]
    assert re.search(r"1\. \*\*Click\*\* \*\*Video overlays\*\*\. The panel opens\.\n\n!\[.*\]\(assets/01-open-panel\.png\)", c)
    forgot = {**FULL, "procedures": [{"task": "T", "steps": [{"text": "**Click** **Video overlays**.", "asset": ""}]}],
              "section_assets": {"nonexistent_section": ["02-zone-overlays.gif"]}}
    c = render(forgot, ASSETS)[0]["content"]
    for a in ASSETS:
        assert c.count(f"](assets/{a})") == 1


def test_required_sections():
    problems = check_fields({**FULL, "overview": "", "procedures": []}, ASSETS)
    assert 'doc page field "overview" is empty (required)' in problems
    assert "doc page has no procedures (at least one is required)" in problems
    assert check_fields(FULL, ASSETS) == []


def test_faq_answers_are_short():
    long = {**FULL, "faqs": [{"question": "Why?", "answer": "One. Two. Three."}]}
    assert any("3-sentence answer (max 2)" in p for p in check_fields(long, ASSETS))


def test_no_caps_on_detail():
    many = {**FULL, "edge_cases": [{"condition": f"Condition number {i} happens", "behavior": f"Result {i}."} for i in range(30)]}
    assert check_fields(many, ASSETS) == []
    assert render(many, ASSETS)[0]["content"].count("| Condition number ") == 30


def test_asset_checks():
    problems = "\n".join(check_fields({**FULL, "section_assets": {"overview": ["01-open-panel.png", "ghost.png"]}}, ASSETS))
    assert "uses asset 'ghost.png' that is not in DOC ASSETS" in problems
    assert "uses asset '01-open-panel.png' more than once" in problems


def test_prompt_carries_homes_tone_and_us_english():
    assert "__HOMES__" not in DOC_PAGE_PROMPT
    assert "Defaults, thresholds, timings and rules: Expected Behavior" in DOC_PAGE_PROMPT
    assert "The system is the subject" in DOC_PAGE_PROMPT and "US English" in DOC_PAGE_PROMPT
    assert "NEVER invent" in DOC_PAGE_PROMPT


def test_old_how_to_field_still_renders():
    old = {k: v for k, v in FULL.items() if k != "procedures"}
    old["how_to"] = FULL["procedures"]
    assert "## Procedures" in render(old, ASSETS)[0]["content"]


def test_master_page():
    c = render_master("Cockpit 3.0", "Everything new in Cockpit 3.0.",
                      [{"title": "Zone Overlays", "path": "zone-overlays.md", "summary": "Zones on the video."}],
                      navigation="**Cockpit**")
    assert re.findall(r"^#{1,2} .+$", c, re.M) == ["# Cockpit 3.0", "## Overview", "## What's in This Section", "## Navigation"]
    assert "- [Zone Overlays](zone-overlays.md): Zones on the video." in c


def test_task_headings_are_title_case_without_touching_labels():
    assert title_case("send the drone to a point on the video") == "Send the Drone to a Point on the Video"
    assert title_case("turn on Zone overlays in the NFZ panel") == "Turn on Zone Overlays in the NFZ Panel"
