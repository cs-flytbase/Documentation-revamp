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


def test_both_still_flags_short_outputs():
    warnings = _validate("both", release="short", doc="short")
    assert _too_short(warnings, "Release note")
    assert _too_short(warnings, "Doc page")


def test_doc_only_still_flags_a_short_doc_page():
    assert _too_short(_validate("doc_only", doc="short"), "Doc page")
