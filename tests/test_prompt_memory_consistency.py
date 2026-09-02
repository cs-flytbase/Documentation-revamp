"""Guard against the failure that produced two silent 'no PRs created' runs.

The drafting prompt mandated sections ("follow this order exactly") that the
memory files simultaneously forbade. Given an impossible instruction set the
model refused outright, and the refusal was reported as a parse error.

These tests fail loudly if a prompt and a memory rule ever contradict again.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]
DRAFTING = Path("/tmp/draft.py").read_text()
MEMORY = Path("/tmp/fmt.md").read_text()


def test_prompt_does_not_mandate_hardware_compatibility():
    """Memory bans this section (the link 404s), so no prompt may require it."""
    assert "hardware compatibility table" not in DRAFTING.lower(), (
        "The drafting prompt still mandates a Hardware Compatibility table, "
        "which memory forbids. Contradictory instructions make the model refuse."
    )


def test_prompt_does_not_use_banned_slash_heading():
    """Memory bans slash headings and names this one specifically."""
    assert "how it generalizes /" not in DRAFTING.lower()
    assert "generalizes / why" not in DRAFTING.lower()


def test_prompt_uses_the_approved_heading():
    assert "Why This Matters" in DRAFTING, (
        "Section 5 should use the heading memory mandates."
    )


def test_memory_rules_obey_the_em_dash_ban():
    """The voice rules ban em dashes; the formatting rules must not contain them."""
    assert "—" not in MEMORY, (
        "formatting_corrections.md contains em dashes, which voice_corrections.md "
        "forbids in the same prompt."
    )


def test_refusal_detection_exists_and_works():
    """A refusal must never again be reported as a parse error."""
    assert "_looks_like_refusal" in DRAFTING
    assert "_diagnose_bad_output" in DRAFTING
    ns = {}
    src = DRAFTING[DRAFTING.index("_REFUSAL_MARKERS"):DRAFTING.index("def _extract_json")]
    exec(src, ns)
    looks = ns["_looks_like_refusal"]
    diagnose = ns["_diagnose_bad_output"]

    # The exact string that broke the pipeline.
    assert looks("I'm sorry, but I can't assist with that request.")
    assert looks("I cannot assist with that.")
    assert not looks('{"release_note": {"content": "..."}}')
    assert not looks("")

    msg = diagnose("I'm sorry, but I can't assist with that request.")
    assert "REFUSED" in msg
    assert "contradictory" in msg
    assert "empty" in diagnose("")
    assert "prose instead of JSON" in diagnose("Here is your release note, all done!")


def test_both_parse_sites_retry_before_giving_up():
    assert DRAFTING.count("_looks_like_refusal(") >= 3, (
        "Both the release-note and doc-page paths must detect refusals."
    )
    assert "Failed to parse release note output" not in DRAFTING, (
        "The misleading error message is back."
    )


SOURCE = Path("/tmp/source.py").read_text()
PIPELINE = Path("/tmp/sp.yml").read_text()


def test_source_agent_never_scrapes_video_pages():
    """Scraping a YouTube watch page poisons the PM doc and triggers refusals."""
    assert "VIDEO_HOSTS" in SOURCE
    assert "Skipping video URL" in SOURCE
    ns = {}
    exec("VIDEO_HOSTS = " + SOURCE.split("VIDEO_HOSTS = ")[1].split("\n")[0], ns)
    hosts = ns["VIDEO_HOSTS"]
    for url in ["https://youtu.be/W_wFyUl55Uo",
                "https://www.youtube.com/watch?v=czWJlafopDk",
                "https://vimeo.com/12345"]:
        assert any(h in url.lower() for h in hosts), f"{url} must be treated as a video"
    # Real doc pages must still be scraped.
    assert not any(h in "https://docs.flytbase.com/some-page".lower() for h in hosts)


def test_pipeline_routes_video_links_to_the_embed_file():
    """youtube_link.txt was never written on the Slack path, so the embed was always empty."""
    assert "youtube_link.txt" in PIPELINE
    assert "video_links" in PIPELINE
    assert "page_links" in PIPELINE


def test_pipeline_strips_slack_link_decoration():
    """Slack sends '<url|display>' - the raw form broke matching."""
    assert "_clean" in PIPELINE
    assert "split('|', 1)" in PIPELINE


def test_failure_reasons_reach_slack():
    """A failed run used to drop its errors, leaving only 'check the Actions log'."""
    assert "Carry failure reasons through to Slack" in PIPELINE
    assert "Pipeline completed but no PRs were created" not in PIPELINE
