"""Contract tests for the publisher.

Written after a regression: a whole-file commit built from a stale copy of
main silently reverted an earlier merged PR. The result was a publisher whose
publish() no longer accepted target_section, while the orchestrator still
passed it - a TypeError on every run that reached publishing, hidden behind an
unrelated model refusal.

These tests fail loudly if any of that is lost again.
"""
import ast
from pathlib import Path

PUB = Path(__file__).resolve().parents[1] / "src/tools/github_publisher.py".read_text()
ORCH = Path(__file__).resolve().parents[1] / "src/orchestrator.py".read_text()


def _fn_args(src, name):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
    raise AssertionError(f"{name}() not found")


def test_publisher_and_orchestrator_agree():
    """The single check that would have caught the regression outright."""
    pub_args = _fn_args(PUB, "publish")
    # Every keyword the orchestrator passes must exist on publish().
    for kw in ["mode", "subsections", "target_section"]:
        assert kw in pub_args, (
            f"orchestrator calls publish({kw}=...) but publish() does not accept it - "
            "this is a TypeError on every pipeline run"
        )


def test_run_pipeline_still_accepts_target_section():
    assert "target_section" in _fn_args(ORCH, "run_pipeline")


def test_target_section_wins_over_the_per_child_guess():
    """Restoring the parameter is pointless if it is never consulted."""
    assert "if target_section:" in PUB
    assert "parent_section = target_section" in PUB


def test_new_docs_sections_anchor_before_discover_more():
    """The Aeroflo bug: without this a new section becomes the FIRST section."""
    assert "discover more" in PUB.lower(), (
        "the Discover More anchor is gone - new sections will be inserted at the "
        "top of the sidebar again"
    )
    assert PUB.count("anchor_idx") >= 6, "anchor logic missing from one of the summary writers"


def test_both_summary_writers_have_the_anchor():
    for fn in ["_update_summary", "_update_summary_nested"]:
        i = PUB.index(f"def {fn}(")
        j = PUB.find("\n    def ", i + 10)
        body = PUB[i:j if j != -1 else len(PUB)]
        assert "discover more" in body.lower(), f"{fn} lost the Discover More anchor"


def test_sanitizer_survived_the_restore():
    """The restore must not undo the fix that came after it."""
    assert "_sanitize_target_path" in PUB
    assert PUB.count("_sanitize_target_path") >= 3
