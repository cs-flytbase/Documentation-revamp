"""The cloud / on-premise boundary.

Both documentation sets live in the same repo, so nothing about a path or a
hostname tells you which one a write belongs to. This module is the only place
that decides, and these tests pin the decision down. Getting it wrong means
on-premise content published to docs.flytbase.com, which is why every rule here
fails closed rather than guessing.
"""
import pytest

from src.doc_scope import (
    CLOUD,
    DOCS_REPO,
    ONPREM,
    RELEASES_REPO,
    assert_in_scope,
    assert_mode_allowed,
    effective_mode,
    get_scope,
    scope_for_url,
)


# ── Cloud behaviour must be exactly what it was before on-premise existed ──

def test_cloud_is_unchanged():
    assert CLOUD.root == ""
    assert CLOUD.summary_path == "SUMMARY.md"
    assert CLOUD.branch_prefix == "docs"
    assert CLOUD.allows_releases is True
    assert CLOUD.corpus_namespace == ""  # the namespace all existing content is in
    assert CLOUD.rooted("device-management/add-a-device") == "device-management/add-a-device"


def test_missing_scope_means_cloud():
    """Dispatches sent before doc_scope existed must keep working."""
    assert get_scope("").id == "cloud"
    assert get_scope(None).id == "cloud"


def test_an_unrecognised_scope_is_refused_not_guessed():
    with pytest.raises(ValueError, match="Unknown doc scope"):
        get_scope("on-prem")


# ── Rooting ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["install/setup", "/install/setup", "./install/setup"])
def test_rooting_normalises_before_prefixing(raw):
    assert ONPREM.rooted(raw) == "on-premise/install/setup"


def test_rooting_is_idempotent():
    once = ONPREM.rooted("install/setup")
    assert ONPREM.rooted(once) == once == "on-premise/install/setup"


def test_rooting_an_empty_path_lands_at_the_scope_root():
    assert ONPREM.rooted("") == "on-premise"
    assert CLOUD.rooted("") == ""


# ── Ownership: the two sets must not overlap ───────────────────────────────

@pytest.mark.parametrize("path", [
    "on-premise/install/setup.md",
    "on-premise/SUMMARY.md",
    "on-premise",
])
def test_on_premise_paths_belong_only_to_on_premise(path):
    assert ONPREM.owns_path(path)
    assert not CLOUD.owns_path(path)


@pytest.mark.parametrize("path", [
    "device-management/add-a-device.md",
    "SUMMARY.md",
    "README.md",
])
def test_cloud_paths_belong_only_to_cloud(path):
    assert CLOUD.owns_path(path)
    assert not ONPREM.owns_path(path)


def test_a_lookalike_prefix_is_not_on_premise():
    """"on-premise-migration" is a cloud page, not the on-premise root."""
    assert CLOUD.owns_path("on-premise-migration/guide.md")
    assert not ONPREM.owns_path("on-premise-migration/guide.md")


# ── The write gate ─────────────────────────────────────────────────────────

def test_on_premise_run_cannot_write_a_cloud_path():
    with pytest.raises(ValueError, match="outside the On-Premise scope"):
        assert_in_scope(ONPREM, "device-management/add.md", DOCS_REPO)


def test_cloud_run_cannot_write_into_on_premise():
    with pytest.raises(ValueError, match="outside the Cloud scope"):
        assert_in_scope(CLOUD, "on-premise/install.md", DOCS_REPO)


def test_on_premise_run_cannot_touch_the_releases_repo():
    with pytest.raises(ValueError, match="does not cover"):
        assert_in_scope(ONPREM, "on-premise/install.md", RELEASES_REPO)


def test_the_allowed_writes_are_allowed():
    assert_in_scope(ONPREM, "on-premise/install/setup.md", DOCS_REPO)
    assert_in_scope(CLOUD, "device-management/add.md", DOCS_REPO)
    assert_in_scope(CLOUD, "august-2026/feature.md", RELEASES_REPO)


# ── Release notes ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode", ["both", "release_only"])
def test_there_are_no_on_premise_release_notes(mode):
    """Refused outright, rather than quietly written to the cloud releases repo."""
    with pytest.raises(ValueError, match="documentation only"):
        assert_mode_allowed(ONPREM, mode)


def test_doc_only_is_fine_on_premise():
    assert_mode_allowed(ONPREM, "doc_only")
    assert effective_mode(ONPREM, "doc_only") == "doc_only"


def test_cloud_still_publishes_release_notes():
    for mode in ["both", "release_only", "doc_only"]:
        assert_mode_allowed(CLOUD, mode)
        assert effective_mode(CLOUD, mode) == mode


# ── URLs: one hostname, two documentation sets ─────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://docs.flytbase.com/device-management/add", "cloud"),
    ("https://docs.flytbase.com/on-premise/install/setup", "onprem"),
    ("https://docs.flytbase.com/on-premise", "onprem"),
    ("https://releases.flytbase.com/august-2026/feature", "cloud"),
    ("https://docs.flytbase.com/on-premise-migration/guide", "cloud"),
])
def test_url_resolves_to_the_right_documentation_set(url, expected):
    assert scope_for_url(url).id == expected


# ── The two scopes share nothing ───────────────────────────────────────────

def test_the_wiring_is_separate_everywhere_it_matters():
    for attr in ["summary_path", "ia_file", "corpus_namespace", "root",
                 "branch_prefix", "url_prefix"]:
        assert getattr(CLOUD, attr) != getattr(ONPREM, attr), (
            f"cloud and on-premise share {attr} - content from one set can "
            "reach the other"
        )
