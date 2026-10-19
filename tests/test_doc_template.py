"""Doc template (senior review, 2026-10-19): the exhaustive technical reference,
every section present, gaps marked instead of invented."""
import re

from src.doc_template import GAP, check_fields, interface_gaps, render, render_master

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
        {"element": "**Video overlays**", "location": "Bottom-right of the video", "what_it_does": "Opens the overlay panel", "effect": "Shows the toggles"},
        {"element": "**Zone overlays**", "location": "Video overlays panel", "what_it_does": "Turns zone drawing on or off", "effect": "NFZs and geofence appear on the video"},
    ],
    "how_to": [{"task": "Show Zones on the Video", "steps": [
        {"text": "**Click** **Video overlays**. The panel opens.", "asset": "01-open-panel.png"},
        {"text": "**Turn on** **Zone overlays**. Zones appear on the video.", "asset": "02-zone-overlays.gif"},
    ]}],
    "rules": ["If Zone overlays is off, no zones are drawn."],
    "expected_behaviour": ["NFZs are red, the geofence is green."],
    "limitations": [{"component": "Zone types", "details": "NFZs and geofence only"}],
    "edge_cases": [{"situation": "No zones appear", "cause": "No zones for the site", "what_happens": "Nothing is drawn", "what_to_do": "Add zones"}],
    "after_you_finish": ["The toggle state is remembered."],
    "related_pages": [{"title": "Zones", "url": "../pre-flight-modules/zones.md"}],
}

ORDER = ["# Video Overlays", "## Overview", "## Problem Statement", "## Use Cases", "### When to Use", "### When Not to Use",
         "## Before You Start", "### Prerequisites", "### Dependencies", "## Navigation", "## Interface Reference",
         "## How To", "### Show Zones on the Video", "## Behaviour and Rules", "### Rules", "### Expected Behaviour",
         "## Limitations, Edge Cases and Troubleshooting", "### Limitations", "### Edge Cases and Troubleshooting",
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


def test_tables_for_interface_limitations_and_edge_cases():
    c = render(FIELDS, ASSETS)[0]["content"]
    assert "| Element | Location | What It Does | Effect |" in c
    assert "| **Zone overlays** | Video overlays panel | Turns zone drawing on or off | NFZs and geofence appear on the video |" in c
    assert "| Component | Details |" in c and "| Situation | Cause | What Happens | What to Do |" in c


def test_missing_sections_are_marked_never_dropped():
    sparse = {k: v for k, v in FIELDS.items() if k not in ("edge_cases", "dependencies", "after_you_finish", "navigation")}
    page, gaps = render(sparse, ASSETS)
    assert set(gaps) == {"Edge Cases and Troubleshooting", "Dependencies", "After You Finish", "Navigation"}
    assert page["content"].count(GAP) == 4
    assert "## Navigation" in page["content"]


def test_every_doc_asset_lands_once_even_if_forgotten():
    forgot = {**FIELDS, "how_to": [{"task": "T", "steps": [{"text": "**Click** **Video overlays**.", "asset": ""}]}]}
    c = render(forgot, ASSETS)[0]["content"]
    for a in ASSETS:
        assert c.count(f"](assets/{a})") == 1


def test_steps_must_only_use_defined_elements():
    undefined = {**FIELDS, "how_to": [{"task": "T", "steps": [{"text": "**Click** the **Layers** icon, then **Settings > Telemetry**."}]}]}
    assert interface_gaps(undefined) == ["layers", "settings", "telemetry"]
    assert interface_gaps(FIELDS) == []
    problems = check_fields(undefined, [])
    assert 'doc page step uses "layers" but interface_reference does not define it' in problems


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
