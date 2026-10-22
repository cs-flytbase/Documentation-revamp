"""Redundancy checks: a fact lives in one section. The first Cockpit 3.0 doc
page said the same things up to four times; these catch that in code."""
from src.doc_checks import duplicate_sentences, label_variants, redundancy_problems, repeated_quantities
from tests.test_doc_template import ASSETS, FULL
from src.doc_template import check_fields


def test_same_sentence_in_two_sections():
    data = {**FULL,
            "expected_behavior": ["Freehand lines fade about 4 seconds after the operator finishes drawing them."],
            "faqs": [{"question": "Why did my line disappear", "answer": "Freehand lines fade about 4 seconds after the operator finishes drawing them."}]}
    found = duplicate_sentences({"Expected Behavior": data["expected_behavior"], "Frequently Asked Questions": [data["faqs"][0]["answer"]]})
    assert found and found[0][:2] == ("Expected Behavior", "Frequently Asked Questions")
    problems = "\n".join(check_fields(data, ASSETS))
    assert 'the same fact is in "Expected Behavior" and "Frequently Asked Questions"' in problems


def test_same_quantity_in_two_sections():
    sections = {"Expected Behavior": ["A warning starts 40 m from the NFZ."],
                "Edge Cases": ["Hovering at 40m keeps the warning on."],
                "Operating Constraints": ["The point must be within 5 km."]}
    assert repeated_quantities(sections) == {"40 m": ["Expected Behavior", "Edge Cases"]}
    assert repeated_quantities({"A": ["Marks fade after 10 seconds."], "B": ["Up to 10 symbols."]}) == {}
    assert repeated_quantities({"A": ["about 4 seconds"], "B": ["after 4 s"]}) == {"4 s": ["A", "B"]}


def test_label_spelled_two_ways():
    sections = {"Procedures": ["**Click** **Video overlays**."], "Controls and Indicators": ["**Video Overlays** opens the panel."]}
    assert label_variants(sections) == [["Video Overlays", "Video overlays"]]


def test_short_sentences_and_different_facts_are_not_flagged():
    sections = {"Expected Behavior": ["Off by default."], "After Completion": ["Off by default."],
                "Operating Constraints": ["Warnings cover NFZs only, not Geofences."],
                "Edge Cases": ["The drone enters an NFZ while the camera points away."]}
    assert duplicate_sentences(sections) == []


def test_a_clean_page_has_no_problems():
    assert redundancy_problems(FULL) == []


def test_a_step_naming_features_is_not_the_overview_listing_them():
    """Found by the Cockpit 3.0 replay: overlap measured against the shorter
    sentence flagged every short step that named a feature."""
    sections = {
        "Overview": ["The Video overlays panel adds four capabilities to the live video: Zone Overlays, "
                     "Zone Proximity Warnings, Live Annotation and Go to Location from Video."],
        "Procedures": ["**Turn on** **Zone proximity warnings** in **Video overlays**."],
    }
    assert duplicate_sentences(sections) == []


def test_a_restated_rule_with_a_few_extra_words_is_still_caught():
    sections = {"Expected Behavior": ["Freehand lines fade after about 4 seconds on every screen."],
                "Frequently Asked Questions": ["Freehand lines fade after about 4 seconds on every screen of the drone."]}
    assert duplicate_sentences(sections)
