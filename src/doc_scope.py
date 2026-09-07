"""Cloud vs On-Premise documentation scope.

Two documentation sets live in the same repo. Which one a pipeline run writes
to is decided by a button click in Slack and carried through the dispatch
payload - it is never inferred by a model.

This module is the single place that knows what each scope owns. The Slack
worker enforces the same rules for direct edits; keep the two in step.
"""

from dataclasses import dataclass, field

ONPREM_ROOT = "on-premise"

DOCS_REPO = "FlytBaseAILabs/flytbase-docs"
RELEASES_REPO = "FlytBaseAILabs/flytbase-releases"


@dataclass(frozen=True)
class DocScope:
    id: str
    label: str
    repos: tuple
    root: str            # "" means the repo root (cloud); on-prem is nested
    summary_path: str
    ia_file: str         # which information architecture the drafting agent uses
    corpus_namespace: str  # keeps on-prem retrieval away from cloud content
    allows_releases: bool   # on-prem is documentation only
    summary_anchor: str     # heading a new section is inserted before
    url_prefix: str         # path prefix of this scope's live pages
    branch_prefix: str      # PR branches are named so reviewers can tell them apart
    describe: str

    def owns_path(self, path: str) -> bool:
        p = normalize(path)
        if self.root:
            return p == self.root or p.startswith(self.root + "/")
        # Cloud owns everything except the on-premise subtree - including the
        # bare "on-premise" directory itself, not just paths beneath it.
        return not (p == ONPREM_ROOT or p.startswith(ONPREM_ROOT + "/"))

    def rooted(self, path: str) -> str:
        """Place a path inside this scope's root, without doubling it up."""
        p = normalize(path)
        if not self.root:
            return p
        if p == self.root or p.startswith(self.root + "/"):
            return p
        return f"{self.root}/{p}" if p else self.root


def normalize(path: str) -> str:
    """Strip leading ./ and / and collapse doubled slashes."""
    p = (path or "").strip().lstrip("/")
    while p.startswith("./"):
        p = p[2:]
    while "//" in p:
        p = p.replace("//", "/")
    return p.rstrip("/")


CLOUD = DocScope(
    id="cloud",
    label="Cloud",
    repos=(DOCS_REPO, RELEASES_REPO),
    root="",
    summary_path="SUMMARY.md",
    ia_file="config/ia_structure.yaml",
    corpus_namespace="",  # default namespace - where all existing cloud content already lives
    allows_releases=True,
    summary_anchor="## Discover More",
    url_prefix="",
    branch_prefix="docs",
    describe="the cloud docs and releases repos (everything outside on-premise/)",
)

ONPREM = DocScope(
    id="onprem",
    label="On-Premise",
    repos=(DOCS_REPO,),
    root=ONPREM_ROOT,
    summary_path=f"{ONPREM_ROOT}/SUMMARY.md",
    ia_file="config/ia_structure_onprem.yaml",
    corpus_namespace="onprem",
    allows_releases=False,   # there are no on-premise release notes
    summary_anchor="",       # no cloud "Discover More" heading in the on-prem SUMMARY
    url_prefix=f"{ONPREM_ROOT}/",
    branch_prefix=ONPREM_ROOT,
    describe="on-premise/ in the docs repo (documentation only, no release notes)",
)

SCOPES = {CLOUD.id: CLOUD, ONPREM.id: ONPREM}


def get_scope(scope_id: str) -> DocScope:
    """Resolve a scope id.

    Missing means cloud - that is where every page lived before on-premise
    existed, so old dispatches keep working. An id that is present but not
    recognised is a wiring bug, and guessing which documentation set to write
    to is exactly the failure this module exists to prevent.
    """
    if not scope_id:
        return CLOUD
    key = str(scope_id).strip().lower()
    if key not in SCOPES:
        raise ValueError(
            f"Unknown doc scope '{scope_id}'. Expected one of: "
            + ", ".join(sorted(SCOPES))
        )
    return SCOPES[key]


def scope_for_url(url: str) -> DocScope:
    """Which documentation set a live page URL belongs to.

    docs.flytbase.com/on-premise/... is on-premise; everything else is cloud.
    """
    u = (url or "").strip()
    if "docs.flytbase.com" in u:
        tail = normalize(u.split("docs.flytbase.com", 1)[-1])
        if ONPREM.owns_path(tail):
            return ONPREM
    return CLOUD


def assert_in_scope(scope: DocScope, path: str, repo: str = None) -> None:
    """Raise if a write would land outside the scope."""
    if repo and repo not in scope.repos:
        raise ValueError(
            f"{scope.label} does not cover '{repo}' - it covers {scope.describe}."
        )
    if not scope.owns_path(path):
        raise ValueError(
            f"'{path}' is outside the {scope.label} scope ({scope.describe})."
        )


def assert_mode_allowed(scope: DocScope, mode: str) -> None:
    """On-premise covers documentation only.

    A release note request in on-premise mode is refused rather than quietly
    written into the cloud releases repo.
    """
    if scope.allows_releases:
        return
    if mode in ("both", "release_only"):
        raise ValueError(
            f"{scope.label} covers documentation only - there are no "
            f"{scope.label} release notes. Use doc_only, or switch the thread "
            "to Cloud for a release note."
        )


def effective_mode(scope: DocScope, mode: str) -> str:
    """Narrow a run to what the scope actually supports."""
    if scope.allows_releases:
        return mode
    return "doc_only"
