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

ROOT = Path(__file__).resolve().parents[1]

# Note the parentheses. Without them the `/` binds after `.read_text()`, which
# calls read_text() on a str and raises AttributeError at import - the whole
# module then errors out instead of testing anything. That is how this file
# shipped originally.
PUB = (ROOT / "src/tools/github_publisher.py").read_text()
ORCH = (ROOT / "src/orchestrator.py").read_text()
RESEARCH = (ROOT / "src/agents/research.py").read_text()
WORKFLOW = (ROOT / ".github/workflows/slack-pipeline.yml").read_text()


def _fn_args(src, name):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
    raise AssertionError(f"{name}() not found")


def test_publisher_and_orchestrator_agree():
    """The single check that would have caught the regression outright."""
    pub_args = _fn_args(PUB, "publish")
    # Every keyword the orchestrator passes must exist on publish().
    for kw in ["mode", "subsections", "target_section", "doc_scope"]:
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
    """The Aeroflo bug: without this a new section becomes the FIRST section.

    The anchor now comes from the scope (cloud anchors on "## Discover More";
    on-premise has no such heading), so assert the value at its source.
    """
    from src.doc_scope import CLOUD
    assert CLOUD.summary_anchor.lower() == "## discover more", (
        "the Discover More anchor is gone - new cloud sections will be inserted "
        "at the top of the sidebar again"
    )
    assert PUB.count("anchor_idx") >= 6, "anchor logic missing from one of the summary writers"


def test_both_summary_writers_have_the_anchor():
    for fn in ["_update_summary", "_update_summary_nested"]:
        i = PUB.index(f"def {fn}(")
        j = PUB.find("\n    def ", i + 10)
        body = PUB[i:j if j != -1 else len(PUB)]
        assert "self.scope.summary_anchor" in body, f"{fn} lost the section anchor"
        assert "self.scope.summary_path" in body, (
            f"{fn} writes a hardcoded SUMMARY.md - on-premise pages would be "
            "listed in the cloud sidebar"
        )


def test_sanitizer_survived_the_restore():
    """The restore must not undo the fix that came after it."""
    assert "_sanitize_target_path" in PUB
    assert PUB.count("_sanitize_target_path") >= 3


# ── Cloud / on-premise scope, checked at every hop ──────────────────────────
#
# target_section died at the final hop and stayed dead for 11 days because
# nothing asserted the whole chain. doc_scope crosses more hops than
# target_section did, so each one gets its own test.


def test_workflow_forwards_doc_scope():
    assert "client_payload.doc_scope" in WORKFLOW, (
        "the workflow drops doc_scope from the dispatch - every on-premise run "
        "would silently publish to the cloud docs"
    )
    assert "doc_scope=doc_scope" in WORKFLOW, "workflow reads doc_scope but never passes it on"


def test_orchestrator_forwards_doc_scope_to_publish():
    assert "doc_scope=scope.id" in ORCH


def test_orchestrator_scopes_retrieval_and_ia():
    assert "ResearchAgent(doc_scope=scope)" in ORCH, "research would query the cloud corpus"
    assert "fetch_exemplar(research_result, doc_scope=scope)" in ORCH
    assert "load_ia_summary(doc_scope=scope)" in ORCH
    assert "GitHubPublisher(doc_scope=scope)" in ORCH


def test_research_confines_every_corpus_query_to_the_namespace():
    body = RESEARCH[RESEARCH.index("class ResearchAgent"):]
    calls = body.count("search(embedding")
    scoped = body.count("namespace=self.namespace")
    assert calls > 0 and scoped == calls, (
        f"{calls} corpus queries but {scoped} are namespaced - an on-premise run "
        "would retrieve cloud pages as context"
    )


def test_every_write_passes_through_the_scope_gate():
    """One choke point, so a new write path cannot skip the check."""
    i = PUB.index("def _write_file(")
    j = PUB.find("\n    def ", i + 10)
    assert "assert_in_scope(self.scope" in PUB[i:j], (
        "_write_file no longer checks scope - this is the only thing standing "
        "between an on-premise run and the cloud docs"
    )


def test_docs_paths_are_rooted_in_the_scope():
    assert PUB.count("self.scope.rooted(") >= 2, (
        "target_path is used unrooted - on-premise pages would be written to "
        "cloud paths"
    )
    assert 'f"{target_path}/{filename}"' not in PUB
    assert 'f"{target_path}/assets"' not in PUB


def test_url_routing_is_scope_aware():
    """docs.flytbase.com serves both sets, so the hostname is not enough."""
    i = PUB.index("def _url_to_repo_path(")
    j = PUB.find("\n    def ", i + 10)
    assert "scope_for_url(source_url)" in PUB[i:j]


def test_release_paths_are_rooted_in_the_scope():
    """On-premise release notes live in on-premise/ inside the releases repo."""
    assert 'f"{release_month}/{filename}"' not in PUB
    assert 'f"{release_month}/assets"' not in PUB
    assert 'f"{release_month}/{parent_slug}"' not in PUB
    assert PUB.count("self.scope.rooted(release_month)") >= 3


def test_the_summary_section_heading_is_not_rooted():
    """Files move under on-premise/; the sidebar heading stays "SEPTEMBER 2026"."""
    assert 'section_title = release_month.replace("-", " ").upper()' in PUB
