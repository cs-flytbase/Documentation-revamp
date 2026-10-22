"""Findings from the first live end-to-end run (2026-10-22)."""
import json

from src.doc_checks import redundancy_problems
from src.invariants import Published, RepoView, no_duplicate_assets
from tests.test_drafting import ASSETS, DOC, RELEASE, VISION, _agent


def test_old_assets_already_in_the_folder_are_not_this_runs_duplicates():
    docs = RepoView(paths={"a/assets/Fleet view.png", "a/assets/Fleet view-1.png", "a/assets/zone.gif"},
                    pages={"a/zone.md": "![Zones](assets/zone.gif)"})
    p = Published(docs=docs, releases=RepoView(), named_media=[], needs_attention=[])
    assert no_duplicate_assets(p).ok
    docs.pages["a/zone.md"] += "\n![Zones again](assets/zone-2.gif)"
    docs.paths.add("a/assets/zone-2.gif")
    assert not no_duplicate_assets(p).ok


def test_a_repeated_number_names_the_section_that_keeps_it():
    data = {**DOC, "expected_behavior": ["A warning starts 40 m from the NFZ."],
            "edge_cases": [{"condition": "The drone hovers at 40 m from the NFZ", "behavior": "The warning stays on."}]}
    msg = "\n".join(redundancy_problems(data))
    assert "Keep it ONLY in Expected Behavior. In Edge Cases, remove the number" in msg


def test_repetition_left_after_the_first_retry_gets_one_more_focused_pass():
    repeated = json.loads(json.dumps(DOC))
    repeated["expected_behavior"] = ["A warning starts 40 m from the NFZ."]
    repeated["edge_cases"] = [{"condition": "The drone hovers 40 m from the NFZ", "behavior": "The warning stays on."}]
    agent, calls = _agent([RELEASE, repeated, repeated, DOC])
    agent.draft("## Video Overlays\nZones on video.", {}, VISION, ASSETS, youtube_link="https://youtu.be/v", mode="both")
    assert len(calls) == 4
    assert "Keep it ONLY in Expected Behavior" in calls[3]["message"]
