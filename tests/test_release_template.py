"""Release note template (senior review, 2026-10-19): a marketable note.
Problem, what it is, why it matters, who it is for, takeaways. No steps."""
import re

from src.release_template import DOC_LINK_PLACEHOLDER, check_fields, docs_url, finalize_doc_link, render

FIELDS = {
    "filename": "cockpit-3-0-video-overlays.md",
    "title": "Cockpit 3.0: See Zones, Get Warned, Brief Your Team on the Live Video",
    "feature_name": "Cockpit 3.0 Video Overlays",
    "description": "No-fly zones on the live video, proximity warnings, and live annotations in Cockpit 3.0.",
    "problem": "Pilots had to compare the video with the map to know where a no-fly zone was. Nothing on the video warned them before they reached one.",
    "introduction": "Cockpit 3.0 adds three overlays to the live video. Zones are drawn on the feed, approaching a no-fly zone triggers a warning, and pilots can draw on the video for everyone watching.",
    "capabilities": [
        {"name": "Zone Overlays", "description": "No-fly zones and the geofence are drawn on the live video.", "asset": "zone-overlays.gif"},
        {"name": "Zone Proximity Warnings", "description": "The video glows red and shows the distance to the NFZ.", "asset": "zone-proximity.gif"},
        {"name": "Live Annotations", "description": "Draw arrows, circles and markers on the feed.", "asset": ""},
    ],
    "key_benefits": [{"benefit": "One screen", "detail": "Zones are on the video, so pilots stop switching to the map."},
                       {"benefit": "Earlier warnings", "detail": "Pilots see the distance before they reach an NFZ."}],
    "intended_users": [{"audience": "Remote pilots", "value": "Keep clear of restricted airspace."},
                    {"audience": "Observers", "value": "See exactly what the pilot is pointing at."}],
    "summary": ["Zones are visible on the video.", "Warnings start before the NFZ."],
    "hero_asset": "hero.gif",
    "availability": "",
}
REQUIRED = ["hero.gif", "zone-overlays.gif", "zone-proximity.gif"]


def test_fixed_structure_and_order():
    out = render(FIELDS, REQUIRED, youtube_link="https://youtu.be/x", doc_title="Cockpit Video Overlays")["content"]
    headings = re.findall(r"^#{1,3} .+$", out, re.M)
    assert headings == [
        "# Cockpit 3.0: See Zones, Get Warned, Brief Your Team on the Live Video",
        "## The Problem", "## Introducing Cockpit 3.0 Video Overlays",
        "### Zone Overlays", "### Zone Proximity Warnings", "### Live Annotations",
        "## Key Benefits", "## Intended Users", "## Summary",
    ]
    lines = out.splitlines()
    assert lines[2] == '{% embed url="https://youtu.be/x" %}'        # video first
    assert lines[4].startswith("![") and "assets/hero.gif" in lines[4]  # then the visual introduction


def test_never_contains_documentation():
    out = render(FIELDS, REQUIRED)["content"]
    for banned in ("Step", "How to use", "| ", "Constraints", "Troubleshooting", "{% hint", "\n1. "):
        assert banned not in out


def test_benefits_and_audiences_render_as_labelled_bullets():
    out = render(FIELDS, REQUIRED)["content"]
    assert "- **One screen:** Zones are on the video, so pilots stop switching to the map." in out
    assert "- **Remote pilots:** Keep clear of restricted airspace." in out
    assert "- Zones are visible on the video." in out


def test_access_only_for_feature_flagged_features():
    assert "## Availability" not in render(FIELDS, REQUIRED)["content"]
    flagged = render(FIELDS, REQUIRED, feature_flag="video_overlays")["content"]
    assert "## Availability" in flagged and "`video_overlays`" in flagged
    assert check_fields({**FIELDS, "availability": "Contact support."}, REQUIRED) == [
        'release note "availability" must be empty: this feature is not behind a feature flag']


def test_required_assets_are_never_dropped():
    forgot = {**FIELDS, "hero_asset": "", "capabilities": [dict(c, asset="") for c in FIELDS["capabilities"]]}
    out = render(forgot, REQUIRED)["content"]
    for a in REQUIRED:
        assert out.count(f"](assets/{a})") == 1


def test_optional_assets_only_when_chosen():
    out = render(FIELDS, ["hero.gif"], optional_assets=["zone-overlays.gif", "zone-proximity.gif", "extra.png"])["content"]
    assert "assets/zone-overlays.gif" in out and "assets/extra.png" not in out


def test_field_checks():
    bad = {**FIELDS, "description": "x" * 151, "problem": "a. b. c. d. e.", "key_benefits": [],
           "hero_asset": "nope.gif", "capabilities": [dict(FIELDS["capabilities"][0], asset="zone-overlays.gif"),
                                                      dict(FIELDS["capabilities"][1], asset="zone-overlays.gif")]}
    joined = "\n".join(check_fields(bad, REQUIRED))
    for expected in ("description is 151 characters", '"problem" has more than 4 sentences',
                     '"key_benefits" needs at least 2 entries', "asset 'nope.gif' that is not in RELEASE ASSETS",
                     "asset 'zone-proximity.gif' missing from release note", "uses asset 'zone-overlays.gif' more than once"):
        assert expected in joined


def test_no_length_limit():
    many = {**FIELDS, "capabilities": [{"name": f"Capability {i}", "description": "Does one thing well."} for i in range(15)]}
    out = render(many, [])["content"]
    assert out.count("### Capability ") == 15


def test_structure_in_fields_is_stripped():
    sneaky = {**FIELDS, "problem": "## Why\n1. Step one\n{% hint style=\"info\" %}x{% endhint %}\nPilots struggled."}
    out = render(sneaky, REQUIRED)["content"]
    assert "## Why\n" not in out and "1. Step" not in out and "{% hint" not in out and "Pilots struggled." in out


def test_doc_link_line_is_filled_or_removed():
    out = render(FIELDS, REQUIRED, doc_title="Cockpit Video Overlays")["content"]
    linked = finalize_doc_link(out, "https://docs.flytbase.com/in-flight-modules/video-overlays")
    assert "For setup steps and full details, see [Cockpit Video Overlays](https://docs.flytbase.com/in-flight-modules/video-overlays)." in linked
    dropped = finalize_doc_link(out, None)
    assert DOC_LINK_PLACEHOLDER not in dropped and "full details" not in dropped
    assert dropped.rstrip().endswith("- Warnings start before the NFZ.")


def test_docs_url():
    assert docs_url("flinks-and-flows/flex-factory/README.md") == "https://docs.flytbase.com/flinks-and-flows/flex-factory"
    assert docs_url("a/b.md") == "https://docs.flytbase.com/a/b"
