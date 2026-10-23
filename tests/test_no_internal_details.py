"""2026-10-22: the Terrain Aware Grid doc page had an Availability section full
of internal release notes. Availability now comes from the flag answer only,
and internal details fail the style check."""
from src.doc_template import DOC_PAGE_PROMPT, render
from src.style_check import errors, lint

FIELDS = {"title": "Terrain Aware Grid Mission Planning", "description": "x", "overview": "Plans grids over terrain.",
          "procedures": [{"task": "Plan", "steps": [{"text": "**Click** **Plan**."}]}],
          "availability": "Available on the US and EU clusters. The US release was at 13:12 UTC."}


def test_no_flag_means_no_availability_section_whatever_the_model_wrote():
    page, omitted = render(FIELDS, [])
    assert "## Availability" not in page["content"] and "13:12" not in page["content"]
    assert "Availability" not in omitted  # not a gap either


def test_a_flag_gives_one_customer_sentence():
    page, _ = render(FIELDS, [], feature_flag="contact FlytBase team for enablement")
    assert ("## Availability\n\nTerrain Aware Grid Mission Planning is behind a feature flag and is not enabled by default. "
            "Contact the FlytBase team to enable it for your organization.") in page["content"]
    assert "## Availability" not in render(FIELDS, [], feature_flag="none")[0]["content"]


def test_internal_details_and_source_remarks_fail_the_style_check():
    leaked = ("The US release was recorded at 13:12 UTC. The source does not record the EU release time. "
              "The THREE_D_GRID and SURVEY_GRID_ALGORITHM feature flags were removed.")
    found = " ".join(i.message for i in errors(lint(leaked, "doc")) + errors(lint(leaked, "release")))
    for bit in ("THREE_D_GRID", "13:12 UTC", "source"):
        assert bit in found
    assert not errors(lint("Set **RTK** and the AGL altitude to 80 m.", "doc"))


def test_prompt_forbids_internal_details():
    assert '"availability"' not in DOC_PAGE_PROMPT
    assert "NEVER include internal details" in DOC_PAGE_PROMPT
