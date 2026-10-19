"""Guard against the failure that produced two silent 'no PRs created' runs.

The drafting prompt mandated sections ("follow this order exactly") that the
memory files simultaneously forbade. Given an impossible instruction set the
model refused outright, and the refusal was reported as a parse error.

These tests fail loudly if a prompt and a memory rule ever contradict again.
"""
import re
from pathlib import Path

# These pointed at /tmp scratch copies left over from the session that wrote
# them, so the module raised FileNotFoundError at collection and none of these
# tests had ever run. They read the real files now.
ROOT = Path(__file__).resolve().parents[1]
DRAFTING = (ROOT / "src/agents/drafting.py").read_text()
MEMORY = (ROOT / "memory/formatting_corrections.md").read_text()
SOURCE = (ROOT / "src/agents/source.py").read_text()
PIPELINE = (ROOT / ".github/workflows/slack-pipeline.yml").read_text()


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


def test_release_note_has_no_length_floor_or_storytelling():
    """Release notes are brief by template (2026-10-19). The old prompt demanded
    3000+ characters, every PM section and a problem-first story, which made
    them as long as doc pages; the director asked for plain technical English."""
    from src.release_template import RELEASE_NOTE_PROMPT
    p = RELEASE_NOTE_PROMPT.lower()
    assert "3000" not in p and "storytell" not in p and "every section" not in p
    assert "need 3000" not in DRAFTING


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


def test_release_embed_is_the_real_convention():
    """The published convention is the self-closing form on its own line, right
    after the H1. The release template now writes it, so it cannot drift."""
    from src.release_template import render
    out = render({"title": "T", "description": "d", "summary": "s", "features": [{"name": "F", "what": "w"}]},
                 [], youtube_link="https://youtu.be/abc")["content"]
    assert out.splitlines()[:3] == ["# T", "", '{% embed url="https://youtu.be/abc" %}']


def test_no_video_means_no_embed():
    """Two live release notes carry a literal YOUTUBE_URL / empty embed. With no
    video the template writes no embed at all, and the model never writes one."""
    from src.release_template import RELEASE_NOTE_PROMPT, render
    out = render({"title": "T", "description": "d", "summary": "s", "features": [{"name": "F", "what": "w"}]}, [])["content"]
    assert "{% embed" not in out and "YOUTUBE_URL" not in out
    assert "No tables, headings, horizontal rules, hint blocks or video embeds inside any" in RELEASE_NOTE_PROMPT


def test_validation_catches_broken_embeds():
    d = DRAFTING
    assert 'url="YOUTUBE_URL"' in d and "CRITICAL" in d
    assert "empty embed url" in d
