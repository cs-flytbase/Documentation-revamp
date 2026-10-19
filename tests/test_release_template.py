"""The release note template is enforced by code: same structure every time,
brief per field, no total length limit, every asset embedded exactly once."""
import re

from src.release_template import (
    DOC_LINK_PLACEHOLDER, check_fields, docs_url, finalize_doc_link, render,
)

ASSETS = ["zone-overlays.gif", "zone-proximity.gif", "live-annotations.gif"]

FIELDS = {
    "filename": "cockpit-3-0-video-overlays.md",
    "title": "Cockpit 3.0: Zone Overlays, Proximity Warnings and Live Annotations",
    "description": "See zones on the live video, get warned near no-fly zones, and draw on the feed for your team.",
    "summary": "Cockpit 3.0 adds three video overlays. They help pilots keep clear of no-fly zones and brief people who watch the flight.",
    "features": [
        {"name": "Zone Overlays", "what": "No-fly zones and the geofence are drawn on the live video.",
         "how_to_use": ["Click **Video overlays** at the bottom-right of the video.", "Turn on **Zone overlays**."],
         "assets": [{"file": "zone-overlays.gif", "alt": "No-fly zone in red and geofence in green on the live video"}]},
        {"name": "Zone Proximity Warnings", "what": "The video glows red and shows the distance when the drone nears a no-fly zone.",
         "how_to_use": "Turn on **Zone proximity warnings** in **Video overlays**.",
         "assets": [{"file": "zone-proximity.gif", "alt": "Red glow and NFZ distance badge"}]},
        {"name": "Live Annotations", "what": "Draw on the live video so everyone in the cockpit sees it.",
         "how_to_use": ["Turn on **Live annotations**.", "Click the annotation button and pick a tool."],
         "assets": []},
    ],
    "good_to_know": ["Zone overlays show zones that are set up for the site."],
}


def _render(**kw):
    return render(FIELDS, ASSETS, **kw)


def test_fixed_structure_in_fixed_order():
    out = _render(youtube_link="https://youtu.be/x", doc_title="Cockpit 3.0 Video Overlays")["content"]
    headings = re.findall(r"^#{1,3} .+$", out, re.M)
    assert headings == [
        "# Cockpit 3.0: Zone Overlays, Proximity Warnings and Live Annotations",
        "## What's New",
        "### Zone Overlays", "### Zone Proximity Warnings", "### Live Annotations",
        "## Good to Know",
        "## Learn More",
    ]
    lines = out.splitlines()
    assert lines[2] == '{% embed url="https://youtu.be/x" %}'


def test_nothing_doc_like_can_appear():
    out = _render()["content"]
    for banned in ("Why This Matters", "Constraints", "Troubleshooting", "| ", "{% hint", "\n---\n"):
        assert banned not in out


def test_every_asset_embedded_exactly_once_even_if_the_model_forgot_one():
    out = _render()["content"]
    for a in ASSETS:
        assert out.count(f"](assets/{a})") == 1
    # The forgotten GIF lands under the last feature with a usable alt text.
    assert out.index("](assets/live-annotations.gif)") > out.index("### Live Annotations")


def test_steps_render_as_one_line_or_a_numbered_list():
    out = _render()["content"]
    assert "**How to use it:** Turn on **Zone proximity warnings** in **Video overlays**." in out
    assert "**How to use it:**\n\n1. Click **Video overlays** at the bottom-right of the video.\n2. Turn on **Zone overlays**." in out


def test_single_feature_has_no_redundant_subheading():
    one = {**FIELDS, "features": [FIELDS["features"][0]]}
    out = render(one, ["zone-overlays.gif"])["content"]
    assert "### " not in out and "## What's New" in out


def test_no_length_limit_many_features_all_kept():
    many = {**FIELDS, "features": [{"name": f"Feature {i}", "what": "Does a thing. " * 3, "how_to_use": ["Step."] * 5} for i in range(12)]}
    out = render(many, [])["content"]
    assert out.count("### Feature ") == 12
    assert not check_fields(many, [])


def test_field_checks_ask_for_targeted_fixes():
    bad = {**FIELDS, "description": "x" * 151, "features": [
        {"name": "Zone Overlays", "what": "One. Two. Three. Four.", "how_to_use": ["s"] * 6,
         "assets": [{"file": "nope.gif"}]}]}
    problems = check_fields(bad, ASSETS)
    joined = "\n".join(problems)
    assert "description is 151 characters" in joined
    assert '"what" has more than 3 sentences' in joined
    assert "6 steps (max 5)" in joined
    assert "unknown asset 'nope.gif'" in joined
    assert "asset 'zone-proximity.gif' missing from release note" in joined


def test_structure_slipped_into_fields_is_removed():
    sneaky = {**FIELDS, "summary": "## Why This Matters\nIt matters.\n---\n{% hint style=\"info\" %}x{% endhint %}"}
    out = render(sneaky, ASSETS)["content"]
    assert "Why This Matters" in out  # the words survive as prose...
    assert "## Why This Matters" not in out and "{% hint" not in out  # ...the structure does not


def test_learn_more_links_to_the_doc_page_or_disappears():
    out = _render(doc_title="Cockpit 3.0 Video Overlays")["content"]
    assert DOC_LINK_PLACEHOLDER in out
    linked = finalize_doc_link(out, "https://docs.flytbase.com/in-flight-modules/cockpit-video-overlays")
    assert "[Cockpit 3.0 Video Overlays](https://docs.flytbase.com/in-flight-modules/cockpit-video-overlays)" in linked
    dropped = finalize_doc_link(out, None)
    assert "Learn More" not in dropped and DOC_LINK_PLACEHOLDER not in dropped
    assert dropped.rstrip().endswith("- Zone overlays show zones that are set up for the site.")


def test_no_learn_more_without_a_doc_page():
    assert "Learn More" not in _render()["content"]


def test_docs_urls():
    assert docs_url("flinks-and-flows/flex-factory/smart-flight-logs.md") == "https://docs.flytbase.com/flinks-and-flows/flex-factory/smart-flight-logs"
    assert docs_url("flinks-and-flows/flex-factory/README.md") == "https://docs.flytbase.com/flinks-and-flows/flex-factory"
    assert docs_url("on-premise/installation/x.md") == "https://docs.flytbase.com/on-premise/installation/x"


def test_frontmatter_shape():
    fm = _render()["frontmatter"]
    assert fm.startswith("---\ndescription: >-\n  See zones") and fm.endswith("\n---")
