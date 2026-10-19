"""Doc template (senior review, 2026-10-19): the technical reference, every
section present, gaps marked instead of invented, and short: the reference
sections are capped so a page is readable."""
import re

from src.doc_template import (GAP, MAX_CONTROLS, MAX_RULES, MAX_TROUBLESHOOTING, MAX_WORDS, check_fields,
                              render, render_master)

ASSETS = ["01-open-panel.png", "02-zone-overlays.gif"]

FIELDS = {
    "filename": "video-overlays.md", "target_path": "in-flight-modules",
    "title": "Video Overlays", "description": "Zone overlays, proximity warnings and annotations on the live video.",
    "overview": "Video overlays draw information on the live drone video.",
    "problem_statement": "Pilots had to cross-check the map to know where zones were.",
    "when_to_use": ["Flying near no-fly zones."], "when_not_to_use": ["Split-screen is on; use the map instead."],
    "prerequisites": ["Zones are set up for the site."], "dependencies": ["Fast telemetry link."],
    "navigation": ["**Cockpit > Video overlays** (layers icon, bottom-right of the video)"],
    "interface_reference": [
        {"element": "**Video overlays**", "what_it_does": "Opens the overlay panel, bottom-right of the video."},
        {"element": "**Zone overlays**", "what_it_does": "Shows or hides NFZs and the geofence on the video."},
    ],
    "how_to": [{"task": "Show Zones on the Video", "steps": [
        {"text": "**Click** **Video overlays**. The panel opens.", "asset": "01-open-panel.png"},
        {"text": "**Turn on** **Zone overlays**. Zones appear on the video.", "asset": "02-zone-overlays.gif"},
    ]}],
    "rules": ["If Zone overlays is off, no zones are drawn.", "NFZs are red, the geofence is green."],
    "limitations": ["Shows NFZs and the geofence only."],
    "troubleshooting": [{"problem": "No zones appear", "fix": "Sync the zones to the drone."}],
    "after_you_finish": ["The toggle state is remembered."],
    "related_pages": [{"title": "Zones", "url": "../pre-flight-modules/zones.md"}],
}

ORDER = ["# Video Overlays", "## Overview", "## Problem Statement", "## Use Cases", "### When to Use", "### When Not to Use",
         "## Before You Start", "### Prerequisites", "### Dependencies", "## Navigation", "## Interface Reference",
         "## How To", "### Show Zones on the Video", "## Behaviour and Rules",
         "## Limitations, Edge Cases and Troubleshooting", "### Limitations", "### Troubleshooting",
         "## After You Finish", "## Related Pages"]


def test_every_section_in_fixed_order_with_video_first():
    page, gaps = render(FIELDS, ASSETS, youtube_link="https://youtu.be/v")
    c = page["content"]
    assert re.findall(r"^#{1,3} .+$", c, re.M) == ORDER
    assert c.splitlines()[2] == '{% embed url="https://youtu.be/v" %}'
    assert gaps == []


def test_images_sit_right_after_their_step():
    c = render(FIELDS, ASSETS)[0]["content"]
    assert re.search(r"1\. \*\*Click\*\* \*\*Video overlays\*\*\. The panel opens\.\n\n!\[.*\]\(assets/01-open-panel\.png\)", c)
    assert c.count("](assets/02-zone-overlays.gif)") == 1


def test_short_tables_and_lists():
    c = render(FIELDS, ASSETS)[0]["content"]
    assert "| Control | What It Does |" in c
    assert "| **Zone overlays** | Shows or hides NFZs and the geofence on the video. |" in c
    assert "- Shows NFZs and the geofence only." in c
    assert "| Problem | What to Do |\n|---|---|\n| No zones appear | Sync the zones to the drone. |" in c


def test_old_style_limitations_still_render():
    c = render({**FIELDS, "limitations": [{"component": "Zone types", "details": "NFZs and geofence only"}]}, ASSETS)[0]["content"]
    assert "- Zone types: NFZs and geofence only" in c


def test_missing_sections_are_marked_never_dropped():
    sparse = {k: v for k, v in FIELDS.items() if k not in ("troubleshooting", "dependencies", "after_you_finish", "navigation")}
    page, gaps = render(sparse, ASSETS)
    assert set(gaps) == {"Troubleshooting", "Dependencies", "After You Finish", "Navigation"}
    assert page["content"].count(GAP) == 4
    assert "## Navigation" in page["content"]


def test_every_doc_asset_lands_once_even_if_forgotten():
    forgot = {**FIELDS, "how_to": [{"task": "T", "steps": [{"text": "**Click** **Video overlays**.", "asset": ""}]}]}
    c = render(forgot, ASSETS)[0]["content"]
    for a in ASSETS:
        assert c.count(f"](assets/{a})") == 1


def test_reference_sections_are_capped():
    big = {**FIELDS,
           "interface_reference": [{"element": f"**Control {i}**", "what_it_does": "Does a thing."} for i in range(25)],
           "rules": [f"Rule {i}." for i in range(30)],
           "troubleshooting": [{"problem": f"P{i}", "fix": "F"} for i in range(12)]}
    problems = "\n".join(check_fields(big, ASSETS))
    assert f"interface_reference has 25 entries (max {MAX_CONTROLS})" in problems
    assert f"rules has 30 entries (max {MAX_RULES})" in problems
    assert f"troubleshooting has 12 entries (max {MAX_TROUBLESHOOTING})" in problems
    c = render(big, ASSETS)[0]["content"]
    assert c.count("| **Control ") == MAX_CONTROLS
    assert c.count("\n- Rule ") == MAX_RULES
    assert len(re.findall(r"^\| P\d+ \|", c, re.M)) == MAX_TROUBLESHOOTING


def test_long_entries_are_sent_back():
    wordy = {**FIELDS, "rules": [" ".join(["word"] * (MAX_WORDS + 5))]}
    assert any(f"has {MAX_WORDS + 5} words" in p for p in check_fields(wordy, ASSETS))
    assert check_fields(FIELDS, ASSETS) == []


def test_step_labels_do_not_need_a_table_row():
    # The old rule (every bold label in a step needs an Interface Reference row)
    # is what made the table 60 rows long.
    extra = {**FIELDS, "how_to": [{"task": "T", "steps": [{"text": "**Click** the **Layers** icon, then **Settings > Telemetry**.",
                                                          "asset": "01-open-panel.png"}, {"text": "**Look**.", "asset": "02-zone-overlays.gif"}]}]}
    assert check_fields(extra, ASSETS) == []


def test_prompt_asks_for_short():
    from src.doc_template import DOC_PAGE_PROMPT
    assert "exhaustive" not in DOC_PAGE_PROMPT and "{max_" not in DOC_PAGE_PROMPT
    assert f"only the MAIN controls, at most {MAX_CONTROLS}" in DOC_PAGE_PROMPT


def test_asset_checks():
    problems = check_fields({**FIELDS, "section_assets": {"overview": ["01-open-panel.png", "ghost.png"]}}, ASSETS)
    joined = "\n".join(problems)
    assert "uses asset 'ghost.png' that is not in DOC ASSETS" in joined
    assert "uses asset '01-open-panel.png' more than once" in joined


def test_master_page():
    c = render_master("Cockpit 3.0", "Everything new in Cockpit 3.0.",
                      [{"title": "Zone Overlays", "path": "zone-overlays.md", "summary": "Zones on the video."}],
                      navigation="**Cockpit**")
    assert re.findall(r"^#{1,2} .+$", c, re.M) == ["# Cockpit 3.0", "## Overview", "## What's in This Section", "## Navigation"]
    assert "- [Zone Overlays](zone-overlays.md): Zones on the video." in c


def test_task_headings_are_title_case_without_touching_labels():
    from src.doc_template import title_case
    assert title_case("send the drone to a point on the video") == "Send the Drone to a Point on the Video"
    assert title_case("turn on Zone overlays in the NFZ panel") == "Turn on Zone Overlays in the NFZ Panel"
