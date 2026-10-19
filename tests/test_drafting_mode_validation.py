"""Length checks must only apply to the outputs the mode asked for."""
import sys
import types

# CI installs only pytest and pyyaml. src.config needs python-dotenv just to
# read a local .env, so stand it in when it is absent.
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from src.agents.drafting import DraftingAgent  # noqa: E402


def _validate(mode, release="", doc=""):
    agent = DraftingAgent.__new__(DraftingAgent)
    result = {"release_note": {"content": release}, "doc_page": {"content": doc}}
    return agent._validate_output(result, [], "", "", mode)


def _too_short(warnings, label):
    return [w for w in warnings if w.startswith(f"{label} too short")]


def test_doc_only_does_not_require_a_release_note():
    warnings = _validate("doc_only", doc="x" * 5000)
    assert not _too_short(warnings, "Release note")
    assert not _too_short(warnings, "Doc page")


def test_release_only_does_not_require_a_doc_page():
    warnings = _validate("release_only", release="x" * 5000)
    assert not _too_short(warnings, "Doc page")
    assert not _too_short(warnings, "Release note")


def test_both_flags_a_short_doc_page_but_never_a_short_release_note():
    """The doc page is the full reference and has a floor. The release note is
    brief by template and has no length limit in either direction."""
    warnings = _validate("both", release="short", doc="short")
    assert not _too_short(warnings, "Release note")
    assert _too_short(warnings, "Doc page")


def test_doc_only_still_flags_a_short_doc_page():
    assert _too_short(_validate("doc_only", doc="short"), "Doc page")


YT = "https://youtu.be/hJ36_XdT8og"


def _validate_yt(mode, doc="", release=""):
    agent = DraftingAgent.__new__(DraftingAgent)
    result = {"release_note": {"content": release}, "doc_page": {"content": doc}}
    return agent._validate_output(result, [], YT, "", mode)


def test_doc_page_with_a_plain_video_link_is_sent_back():
    """Smart Flight Logs: the video came out as a plain link above the Overview."""
    doc = f"# Smart Flight Logs\n\n[Watch the video]({YT})\n\n## Overview\n\n" + "x" * 5000
    warnings = _validate_yt("doc_only", doc=doc)
    assert any("YouTube embed missing from doc page" in w for w in warnings)


def test_doc_page_with_the_embed_passes():
    doc = f"# Smart Flight Logs\n\n## Overview\n\nText.\n\n{{% embed url=\"{YT}\" %}}\n\n" + "x" * 5000
    assert not any("YouTube" in w for w in _validate_yt("doc_only", doc=doc))


def test_doc_only_does_not_ask_for_the_video_in_a_release_note():
    doc = f"## Overview\n\n{{% embed url=\"{YT}\" %}}\n" + "x" * 5000
    assert not any("release note" in w.lower() for w in _validate_yt("doc_only", doc=doc))
