"""GitHub publisher — creates a PR with the pipeline output.

Creates a branch, writes new files, patches impacted pages, opens a PR.

Atomic publishing (P1, 2026-10-22): every file for a repo (pages, SUMMARY.md,
assets, impacted-page patches) is staged in memory and lands in ONE commit
through the git data API. The branch exists only once that commit exists, so
a failure never leaves a half-written branch, and a branch this run created
that ends without a PR is deleted. The docs repo is published first; the
release note links to where the doc page actually landed, or says
"Documentation: coming soon" when there is no doc PR. It never links a 404.

Usage:
    from src.tools.github_publisher import publish_pr
    result = publish_pr(pipeline_output_dir="output/run_xxx", draft_result=draft_result)
"""

import base64
import os
import posixpath
import re
import time
from pathlib import Path

import requests

from src.config import GITHUB_TOKEN
from src.diag import report_exception
from src.link_check import check_page, repair_links
from src.release_template import DOC_LINK_PLACEHOLDER, docs_url, finalize_doc_link
from src.summary_placement import (
    add_group, insert_entries, insert_entry, nearest_ancestor, relative_to_summary, repo_path_for, resolve_exact,
    resolve_section, validate_insert,
)
from src.doc_scope import CLOUD, assert_in_scope, get_scope, scope_for_url


# Network failures worth a retry (tests replace requests with a stub without them).
_NET_ERRORS = tuple(getattr(requests, n) for n in ("ConnectionError", "Timeout") if hasattr(requests, n)) or (OSError,)

DOCS_REPO = "FlytBaseAILabs/flytbase-docs"
RELEASES_REPO = "FlytBaseAILabs/flytbase-releases"
BASE_BRANCH = "main"
DOCS_COMING_SOON = "Documentation: coming soon."


class _Stage:
    """Files for one repo's run branch, held until a single commit."""

    def __init__(self, branch: str, base: str, existed: bool):
        self.branch = branch
        self.base = base            # commit the run's reads were made against
        self.existed = existed      # the branch was there before this run (a re-run)
        self.created = False        # this run created the branch
        self.files: dict[str, str | bytes] = {}


class PublishError(Exception):
    """A repo could not be published; the message says why, for Slack."""


def _coming_soon(content: str) -> str:
    """The release note's doc-link line, when there is no doc page to link."""
    return "\n".join(DOCS_COMING_SOON if DOC_LINK_PLACEHOLDER in line else line for line in content.split("\n"))


def _sanitize_target_path(raw: str) -> str:
    """Coerce a model-supplied target_path into a real repo directory path.

    The Drafting agent sometimes returns a human-readable breadcrumb instead
    of a path — e.g. "In-Flight Modules > How to Manage Your Flight Operations
    > Multi-View Dashboard". Used verbatim that becomes a literal directory of
    that name, which is how the Cockpit 3.0 page ended up in a junk folder.

    Slugify each segment: split on "/" or ">", lowercase, spaces to hyphens,
    drop anything that isn't a safe path character.
    """
    if not raw:
        return ""
    raw = raw.strip().strip("/")
    segments = re.split(r"[/>]+", raw)
    cleaned = []
    for seg in segments:
        seg = seg.strip().lower()
        seg = re.sub(r"[^a-z0-9\s-]", "", seg)
        seg = re.sub(r"\s+", "-", seg)
        seg = re.sub(r"-+", "-", seg).strip("-")
        if seg:
            cleaned.append(seg)
    return "/".join(cleaned)


_SANDBOX_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def sandbox_prefix(tag: str) -> str:
    """Branch prefix of a sandbox run. Everything under it is disposable."""
    return f"sandbox/{tag}"


class GitHubPublisher:
    def __init__(self, doc_scope=CLOUD, sandbox: str | None = None):
        """sandbox (or env PIPELINE_SANDBOX) runs the full publish flow on
        disposable branches under sandbox/<tag>/ and never opens a PR, labels
        anything or edits an existing page. The weekly end-to-end check
        (src/e2e_check.py) uses it and deletes the branches afterwards."""
        if not GITHUB_TOKEN:
            raise ValueError("GITHUB_TOKEN is not set in .env")
        tag = (os.environ.get("PIPELINE_SANDBOX", "") if sandbox is None else sandbox).strip()
        if tag and not _SANDBOX_TAG.match(tag):
            raise ValueError(f"invalid sandbox tag {tag!r}: use lowercase letters, digits and dashes")
        self.sandbox = tag
        self.sandbox_branches: list[tuple[str, str]] = []
        self.scope = doc_scope
        self._written: dict[str, set] = {}      # repo -> paths written on this run's branch
        self._tree_cache: dict[str, set] = {}   # repo -> paths on main
        self._stages: dict[str, _Stage] = {}    # repo -> files waiting for the single commit
        self._sleep = time.sleep
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    API_ATTEMPTS = 3

    def _api(self, method: str, repo: str, path: str, **kwargs) -> dict:
        """GitHub REST call with a timeout, retried on rate limits, 5xx and network errors.

        4xx answers other than 429 are real answers (404 = absent, 422 = exists)
        and are raised at once for the caller to handle.
        """
        url = f"https://api.github.com/repos/{repo}/{path}"
        kwargs.setdefault("timeout", 60)
        for attempt in range(self.API_ATTEMPTS):
            try:
                resp = self.session.request(method, url, **kwargs)
            except _NET_ERRORS as e:
                if attempt + 1 == self.API_ATTEMPTS:
                    raise
                print(f"    GitHub {method} {path}: {e}; retrying")
                self._sleep(2 ** attempt)
                continue
            status = getattr(resp, "status_code", 200)
            if (status == 429 or status >= 500) and attempt + 1 < self.API_ATTEMPTS:
                print(f"    GitHub {method} {path}: HTTP {status}; retrying")
                self._sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json() if resp.content else {}
        return {}

    def _get_branch_sha(self, repo: str, branch: str) -> str:
        """Get the latest commit SHA for a branch."""
        data = self._api("GET", repo, f"git/ref/heads/{branch}")
        return data["object"]["sha"]

    def _create_branch(self, repo: str, branch: str, from_sha: str) -> None:
        """Create a branch from a SHA. An existing branch (a re-run of the same
        request) is reused, so a re-run never makes a second branch or PR."""
        try:
            self._api("POST", repo, "git/refs", json={"ref": f"refs/heads/{branch}", "sha": from_sha})
        except requests.HTTPError as e:
            if getattr(getattr(e, "response", None), "status_code", None) != 422:
                raise
            print(f"    Branch {branch} already exists in {repo}; reusing it")

    def _begin(self, repo: str, branch: str) -> None:
        """Start staging a repo's files for `branch`. Nothing is written yet.

        A re-run finds the branch already there and builds on it; otherwise the
        run reads main and the branch is created by the commit itself.
        """
        main_sha = self._get_branch_sha(repo, BASE_BRANCH)
        try:
            head, existed = self._get_branch_sha(repo, branch), True
        except requests.HTTPError as e:
            if getattr(getattr(e, "response", None), "status_code", None) != 404:
                raise
            head, existed = main_sha, False
        self._stages[repo] = _Stage(branch, head, existed)

    def _commit(self, repo: str, message: str, attention: list) -> None:
        """Commit every staged file for `repo` in one commit, creating or
        fast-forwarding the run branch. A file whose blob cannot be created
        (after the API's own retries) is left out and listed under Needs
        attention; the rest still lands. The commit is retried once from a
        fresh base. Raises PublishError if nothing could be committed."""
        st = self._stages[repo]
        if not st.files:
            raise PublishError(f"nothing was staged for {repo}")
        tree = []
        for path, content in st.files.items():
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            try:
                blob = self._api("POST", repo, "git/blobs", json={
                    "content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
                tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
            except Exception as e:  # noqa: BLE001 - one bad file must not sink the PR
                attention.append(f"`{path}` could not be uploaded and is not in this PR: "
                                 f"{report_exception(f'Blob for {path}', e)}. Add it by hand.")
                self._written.get(repo, set()).discard(path)
        if not tree:
            raise PublishError(f"no file could be uploaded to {repo}")
        last = None
        for attempt in range(2):
            try:
                if attempt and st.existed:  # fresh base: the branch may have moved
                    st.base = self._get_branch_sha(repo, st.branch)
                base_tree = self._api("GET", repo, f"git/commits/{st.base}")["tree"]["sha"]
                new_tree = self._api("POST", repo, "git/trees", json={"base_tree": base_tree, "tree": tree})
                commit = self._api("POST", repo, "git/commits", json={
                    "message": message, "tree": new_tree["sha"], "parents": [st.base]})
                if st.existed:
                    self._api("PATCH", repo, f"git/refs/heads/{st.branch}", json={"sha": commit["sha"], "force": False})
                else:
                    try:
                        self._api("POST", repo, "git/refs", json={"ref": f"refs/heads/{st.branch}", "sha": commit["sha"]})
                        st.created = True
                    except requests.HTTPError as e:
                        if getattr(getattr(e, "response", None), "status_code", None) != 422:
                            raise
                        st.existed = True  # created meanwhile: build on it next attempt
                        raise
                st.existed = True
                st.base = commit["sha"]
                st.files.clear()
                return
            except Exception as e:  # noqa: BLE001
                last = e
                print(f"    Commit to {repo} failed (attempt {attempt + 1}): {e}")
        raise PublishError(f"could not commit to {repo} after a retry: {report_exception(f'Commit to {repo}', last)}")

    def _discard(self, repo: str) -> None:
        """Delete the run branch if this run created it and it has no PR."""
        st = self._stages.pop(repo, None)
        if not st or not st.created:
            return
        try:
            self._api("DELETE", repo, f"git/refs/heads/{st.branch}")
            print(f"    Deleted {st.branch} in {repo}: it ended without a PR")
        except Exception as e:  # noqa: BLE001
            print(f"    Could not delete {st.branch} in {repo}: {e}")

    def _get_file(self, repo: str, file_path: str, branch: str) -> tuple[str, str] | tuple[None, None]:
        """Fetch file content and its SHA from GitHub. Returns (content, sha) or (None, None).

        On a staged run branch, a file staged by this run is returned as it
        will be committed; a branch that does not exist yet reads from main.
        """
        st = self._stages.get(repo)
        if st and branch == st.branch:
            if file_path in st.files:
                v = st.files[file_path]
                return (v.decode("utf-8", "replace") if isinstance(v, bytes) else v), "staged"
            if not st.existed:
                branch = BASE_BRANCH
        try:
            data = self._api("GET", repo, f"contents/{file_path}", params={"ref": branch})
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data["sha"]
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None, None
            raise

    def _write_file(self, repo: str, file_path: str, content: str, branch: str,
                    message: str, existing_sha: str | None = None) -> None:
        """Create or update a file on a branch.

        On a re-run the file may already exist on the branch; its sha is looked
        up so the write updates it instead of failing with 422.
        """
        assert_in_scope(self.scope, file_path, repo)
        st = self._stages.get(repo)
        if st and branch == st.branch:
            st.files[file_path] = content
            self._written.setdefault(repo, set()).add(file_path)
            return
        if not existing_sha:
            _, existing_sha = self._get_file(repo, file_path, branch)
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha
        self._api("PUT", repo, f"contents/{file_path}", json=payload)
        self._written.setdefault(repo, set()).add(file_path)

    def _compress_gif(self, file_path: str, target_mb: int = 20) -> bytes:
        """Compress a GIF to fit under the target size.

        Reduces frames (skips every other), scales down resolution, and
        reduces color palette until the file fits.
        """
        from PIL import Image
        import io

        img = Image.open(file_path)
        total_frames = getattr(img, "n_frames", 1)
        original_size = Path(file_path).stat().st_size
        print(f"    Compressing GIF: {Path(file_path).name} ({original_size / 1024 / 1024:.1f}MB, {total_frames} frames)")

        # Step 1: Extract frames, skip every other one
        frames = []
        durations = []
        skip = max(1, total_frames // 60)  # Keep max ~60 frames
        for i in range(0, total_frames, skip):
            img.seek(i)
            frame = img.convert("RGBA")
            durations.append(img.info.get("duration", 100) * skip)
            frames.append(frame)

        # Step 2: Scale down if needed — aggressive scaling for large files
        width, height = frames[0].size
        scale = 1.0
        if original_size > target_mb * 4 * 1024 * 1024:
            scale = 0.35  # 80MB+ → 35% scale
        elif original_size > target_mb * 2 * 1024 * 1024:
            scale = 0.5   # 40MB+ → 50% scale
        elif original_size > target_mb * 1024 * 1024:
            scale = 0.65  # 20MB+ → 65% scale

        if scale < 1.0:
            new_w, new_h = int(width * scale), int(height * scale)
            frames = [f.resize((new_w, new_h), Image.LANCZOS) for f in frames]
            print(f"    Scaled: {width}x{height} → {new_w}x{new_h}")

        # Step 3: Convert to palette mode and save with optimization
        palette_frames = []
        for f in frames:
            p = f.convert("RGB").quantize(colors=128)
            palette_frames.append(p)

        buf = io.BytesIO()
        palette_frames[0].save(
            buf, format="GIF", save_all=True,
            append_images=palette_frames[1:],
            duration=durations, loop=0, optimize=True,
        )
        compressed = buf.getvalue()
        print(f"    Compressed: {original_size / 1024 / 1024:.1f}MB → {len(compressed) / 1024 / 1024:.1f}MB ({len(palette_frames)} frames)")
        return compressed

    def _upload_asset_via_blobs(self, repo: str, file_path: str, content_bytes: bytes,
                                branch: str, commit_message: str) -> None:
        """Upload a file using GitHub's Git Blobs API (supports up to 100MB).

        Falls back to this when the Contents API rejects large files.
        """
        # Step 1: Create a blob
        blob_b64 = base64.b64encode(content_bytes).decode("utf-8")
        blob = self._api("POST", repo, "git/blobs", json={
            "content": blob_b64,
            "encoding": "base64",
        })
        blob_sha = blob["sha"]

        # Step 2: Get the latest commit and tree
        branch_data = self._api("GET", repo, f"git/ref/heads/{branch}")
        commit_sha = branch_data["object"]["sha"]
        commit_data = self._api("GET", repo, f"git/commits/{commit_sha}")
        tree_sha = commit_data["tree"]["sha"]

        # Step 3: Create a new tree with the file
        new_tree = self._api("POST", repo, "git/trees", json={
            "base_tree": tree_sha,
            "tree": [{
                "path": file_path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_sha,
            }],
        })

        # Step 4: Create a new commit
        new_commit = self._api("POST", repo, "git/commits", json={
            "message": commit_message,
            "tree": new_tree["sha"],
            "parents": [commit_sha],
        })

        # Step 5: Update the branch reference
        self._api("PATCH", repo, f"git/refs/heads/{branch}", json={
            "sha": new_commit["sha"],
        })

    def _upload_asset(self, repo: str, asset_path: str, target_path: str, *args, **kwargs):
        """Upload an asset and remember its path for the link check."""
        err = self._upload_asset_raw(repo, asset_path, target_path, *args, **kwargs)
        if not err:
            self._written.setdefault(repo, set()).add(f"{target_path}/{Path(asset_path).name}")
        return err

    def _exists_fn(self, repo: str):
        """A path check against main plus everything this run wrote."""
        if repo not in self._tree_cache:
            try:
                tree = self._api("GET", repo, f"git/trees/{BASE_BRANCH}?recursive=1")
                self._tree_cache[repo] = {t["path"] for t in tree.get("tree", []) if t.get("type") == "blob"}
            except Exception as e:  # noqa: BLE001 - a failed check must not block publishing
                print(f"    Link check skipped for {repo}: {e}")
                return None
        known = self._tree_cache[repo]
        written = self._written.get(repo, set())
        return lambda path: path in known or path in written

    def _check_links(self, repo: str, page_path: str, content: str, results: dict, also_exists=()) -> list:
        """Report broken links and images still in a page (after repair), for the PR and Slack."""
        exists = self._exists_fn(repo)
        if exists is None:
            return []
        docs = self._exists_fn(DOCS_REPO) if repo != DOCS_REPO else exists
        extra = set(also_exists)  # pages this run is about to create in the other repo

        def docs_exists(path):
            return path in extra or (docs is not None and docs(path))
        found = [f"{page_path}: {problem}" for problem in check_page(content, page_path, exists, docs_exists)]
        results.setdefault("warnings", []).extend(found)
        return found

    def _upload_asset_raw(self, repo: str, asset_path: str, target_path: str,
                      branch: str, feature_slug: str,
                      precompressed: bytes | None = None) -> str | None:
        """Upload an asset file to GitHub. Handles compression and fallback.

        Args:
            precompressed: If provided, skip reading/compressing and use these bytes directly.
                           Used to avoid re-compressing GIFs when uploading to the second repo.

        Returns None on success, error string on failure.
        """
        asset_name = Path(asset_path).name
        is_gif = asset_name.lower().endswith(".gif")
        is_binary = asset_name.lower().endswith((".gif", ".png", ".jpg", ".jpeg", ".webp", ".mp4"))
        # Contents API is unreliable for binary files > 5MB due to base64 inflation
        max_contents_api = 5 * 1024 * 1024  # 5MB for binary via Contents API

        try:
            # Step 1: Get content bytes
            if precompressed is not None:
                content_bytes = precompressed
                print(f"    Using pre-compressed bytes for {asset_name} ({len(content_bytes) / 1024 / 1024:.1f}MB)")
            else:
                file_size = Path(asset_path).stat().st_size
                if is_gif and file_size > max_contents_api:
                    print(f"    GIF too large ({file_size / 1024 / 1024:.1f}MB), compressing...")
                    content_bytes = self._compress_gif(asset_path)
                else:
                    content_bytes = Path(asset_path).read_bytes()
                # Cache for reuse when uploading to second repo
                if is_gif and hasattr(self, '_asset_cache'):
                    self._asset_cache[asset_path] = content_bytes

            # Staged run: the bytes go into the single commit with everything else.
            st = self._stages.get(repo)
            if st and branch == st.branch:
                st.files[f"{target_path}/{asset_name}"] = content_bytes
                return None

            # Step 2: GIFs always use Blobs API — binary files are unreliable via Contents API
            if is_gif:
                print(f"    Using Blobs API for GIF {asset_name} ({len(content_bytes) / 1024 / 1024:.1f}MB)...")
                self._upload_asset_via_blobs(
                    repo, f"{target_path}/{asset_name}", content_bytes,
                    branch, f"docs: add assets for {feature_slug}",
                )
                return None  # Success

            # Step 3: For non-GIF files, try Contents API if small enough
            if len(content_bytes) <= max_contents_api:
                asset_b64 = base64.b64encode(content_bytes).decode("utf-8")
                try:
                    self._api("PUT", repo, f"contents/{target_path}/{asset_name}", json={
                        "message": f"docs: add assets for {feature_slug}",
                        "content": asset_b64,
                        "branch": branch,
                    })
                    return None  # Success
                except requests.HTTPError:
                    print(f"    Contents API failed for {asset_name}, falling back to Blobs API...")

            # Step 4: Fallback to Blobs API for large or failed non-GIF files
            print(f"    Using Blobs API for {asset_name} ({len(content_bytes) / 1024 / 1024:.1f}MB)...")
            self._upload_asset_via_blobs(
                repo, f"{target_path}/{asset_name}", content_bytes,
                branch, f"docs: add assets for {feature_slug}",
            )
            return None  # Success

        except Exception as e:
            return report_exception(f"Asset upload failed ({asset_name})", e)

    def _existing_page(self, repo: str, file_path: str) -> tuple[str, str | None, str | None]:
        """(path, content, sha) of the page a URL means. docs.flytbase.com/a/b is
        either a/b.md or the folder page a/b/README.md; the first that exists
        wins. (path, None, None) if neither does."""
        candidates = [file_path]
        if file_path.endswith(".md") and not file_path.endswith("/README.md"):
            candidates.append(file_path[:-3] + "/README.md")
        for path in candidates:
            content, sha = self._get_file(repo, path, BASE_BRANCH)
            if content is not None:
                return path, content, sha
        return file_path, None, None

    def _url_to_repo_path(self, source_url: str) -> tuple[str, str]:
        """Convert a source URL to (repo, file_path).

        https://releases.flytbase.com/february-2026/some-page
        → (RELEASES_REPO, "february-2026/some-page.md")

        https://docs.flytbase.com/some/path
        → (DOCS_REPO, "some/path.md")

        https://docs.flytbase.com/on-premise/some/path
        → (DOCS_REPO, "on-premise/some/path.md")

        The on-premise pages live under the same hostname, so the hostname
        alone does not identify the documentation set. A URL belonging to the
        other scope is refused here rather than cross-linked into this run.
        """
        if "releases.flytbase.com" in source_url:
            path = source_url.split("releases.flytbase.com/", 1)[-1].rstrip("/")
            return RELEASES_REPO, f"{path}.md"
        elif "docs.flytbase.com" in source_url:
            path = source_url.split("docs.flytbase.com/", 1)[-1].rstrip("/")
            url_scope = scope_for_url(source_url)
            if url_scope.id != self.scope.id:
                raise ValueError(
                    f"{source_url} is a {url_scope.label} page, but this run is "
                    f"{self.scope.label}. Cross-scope edits are not allowed."
                )
            return DOCS_REPO, f"{path}.md"
        else:
            raise ValueError(f"Cannot determine repo for URL: {source_url}")

    def _apply_patch(self, file_content: str, section_heading: str,
                     patch_mode: str, patch_content: str) -> str:
        """Find the section heading in the file and apply the patch.

        patch_mode "append": inserts patch_content after the section's content,
                             before the next same-or-higher-level heading.
        patch_mode "replace": replaces everything between the heading and the
                              next same-or-higher-level heading with patch_content.
        """
        lines = file_content.splitlines(keepends=True)

        # Detect heading level from the section_heading string
        heading_match = re.match(r'^(#{1,6})\s', section_heading.strip())
        heading_level = len(heading_match.group(1)) if heading_match else 2

        # Find the line index of the section heading
        heading_line_idx = None
        for i, line in enumerate(lines):
            if line.strip() == section_heading.strip():
                heading_line_idx = i
                break

        if heading_line_idx is None:
            # Heading not found — append at end of file with a note
            return file_content.rstrip() + f"\n\n{patch_content}\n"

        # Find where this section ends (next heading of same or higher level)
        section_end_idx = len(lines)
        for i in range(heading_line_idx + 1, len(lines)):
            m = re.match(r'^(#{1,6})\s', lines[i])
            if m and len(m.group(1)) <= heading_level:
                section_end_idx = i
                break

        if patch_mode == "replace":
            # Replace everything between heading and next heading
            new_lines = (
                lines[:heading_line_idx + 1]
                + ["\n", patch_content.rstrip() + "\n", "\n"]
                + lines[section_end_idx:]
            )
        else:
            # Append after the section's existing content
            new_lines = (
                lines[:section_end_idx]
                + ["\n", patch_content.rstrip() + "\n", "\n"]
                + lines[section_end_idx:]
            )

        return "".join(new_lines)

    def _resolve_placement(self, repo: str, branch: str, target_section: str, guessed_path: str, results: dict,
                           guess_label: str = ""):
        """Where a new page goes, read from the live SUMMARY.md. Never a new section.

        Returns (placement or None, summary text, summary sha, confirmed, notes).
        In order:
          1. the section the requester confirmed (a page may become a parent);
          2. the research agent's pick, copied from the live sidebar, resolved exactly;
          3. the nearest existing section along the drafting agent's guessed path.
        Anything but (1) is "unconfirmed" and listed under Needs attention with
        how to move it. None means no existing section fits: the page is not
        added to the sidebar and the PR says so. A new top-level "##" group is
        never created in the docs (P0, 2026-10-22: that is how the Cockpit 3.0
        page got its own "## Live Video Feed" group).
        """
        notes: list[str] = []
        summary_path = self.scope.summary_path
        text, sha = self._get_file(repo, summary_path, branch)
        if text is None:
            notes.append(f"Could not read {summary_path}, so the page was not added to the sidebar. Add it before merging.")
            return None, None, None, False, notes
        if target_section:
            placement = resolve_section(text, target_section, allow_leaf=True)
            if placement:
                return placement, text, sha, True, notes
            # The requester asked for a new section by name: the only way a
            # new top-level group is ever created in the docs.
            if getattr(self, "_allow_new_section", False):
                name = re.split(r"\s*>\s*", target_section.strip())[-1].strip()
                if name:
                    text, placement = add_group(text, name, self.scope.summary_anchor)
                    notes.append(f'New sidebar section **{name}** created, as requested in the Slack thread.')
                    return placement, text, sha, True, notes
            notes.append(f'The section you asked for, "{target_section}", is not in the sidebar (or matches more than '
                         "one place), so the page was placed by the pipeline instead.")
        placement = resolve_exact(text, guess_label) if guess_label else None
        if not placement and guessed_path:
            guess = " > ".join(seg.replace("-", " ") for seg in guessed_path.split("/"))
            placement = resolve_section(text, guess, allow_leaf=False) or nearest_ancestor(text, guess)
        if placement:
            notes.append(f"Placement unconfirmed: the page is under **{placement.label}**, chosen by the pipeline. "
                         "If that is wrong, ask Son of Anton to move it (move_page) before merging.")
            return placement, text, sha, False, notes
        notes.append("No existing sidebar section fits this page, so it is NOT in the sidebar yet. Tell Son of Anton "
                     "which section it belongs under and it will move it (move_page) before merging.")
        return None, text, sha, False, notes

    def _write_summary_entry(self, branch: str, placement, summary_text: str, summary_sha: str, updated: str,
                             message: str, attention: list) -> None:
        """Write a sidebar edit only if it is exactly the new entries at the placement.

        Checked against main: only "* [..](..)" lines added, no "##", under the
        chosen parent, no duplicate, linked files present. A bad edit is rebuilt
        once from main; if that is still wrong, nothing is written and the PR
        says so, instead of a broken sidebar.
        """
        summary_path = self.scope.summary_path
        base = posixpath.dirname(summary_path)
        exists = self._exists_fn(DOCS_REPO)
        problems = validate_insert(summary_text, updated, placement, exists, base)
        if problems:
            main_text, main_sha = self._get_file(DOCS_REPO, summary_path, BASE_BRANCH)
            added = [l for l in updated.splitlines() if l not in set(summary_text.splitlines())]
            entries = []
            for line in added:
                m = re.match(r"^(\s*)[*-] \[(.+?)\]\((.+?)\)", line)
                if m:
                    entries.append((m.group(2), m.group(3), max(0, (len(m.group(1)) - placement.indent) // 2)))
            fixed = insert_entries(main_text, placement, entries) if main_text and entries else None
            if fixed and not validate_insert(main_text, fixed, placement, exists, base):
                updated, summary_sha = fixed, main_sha
                print(f"    Sidebar edit rebuilt from main ({'; '.join(problems)})")
            else:
                attention.append("The sidebar (SUMMARY.md) was NOT updated, because the edit failed its check: "
                                 + "; ".join(problems) + ". Add the entry by hand or ask Son of Anton to place it.")
                return
        if updated != summary_text:
            self._write_file(DOCS_REPO, summary_path, updated, branch, message, existing_sha=summary_sha)

    def _repair_page_links(self, repo: str, page_path: str, content: str, docs_summary: str,
                           attention: list, fixes: list, also_exists=()) -> str:
        """Re-point broken page links at the page they name; unlink what cannot be found."""
        exists = self._exists_fn(repo)
        if exists is None or not docs_summary:
            return content
        docs = self._exists_fn(DOCS_REPO) if repo != DOCS_REPO else exists
        extra = set(also_exists)
        ok = lambda p: p in extra or exists(p)
        docs_ok = lambda p: p in extra or (docs is not None and docs(p))
        content, fixed, unlinked = repair_links(content, page_path, ok, docs_summary, self.scope.summary_path, docs_ok)
        fixes += [f"{page_path}: {f}" for f in fixed]
        attention += [f"{page_path}: {u}" for u in unlinked]
        return content

    @staticmethod
    def _attention_block(items: list) -> str:
        """What a person must check before merging, at the very top of the PR."""
        items = list(dict.fromkeys(i for i in items if i))
        if not items:
            return ""
        return ("## ⚠️ Needs attention\n\nThe pipeline fixed what it could. These need a person before merging:\n\n"
                + "\n".join(f"- [ ] {i}" for i in items) + "\n\n---\n\n")

    @staticmethod
    def _fixes_block(items: list) -> str:
        items = list(dict.fromkeys(i for i in items if i))
        if not items:
            return ""
        return ("<details><summary>Fixed automatically (" + str(len(items)) + ")</summary>\n\n"
                + "\n".join(f"- {i}" for i in items) + "\n\n</details>\n\n---\n\n")

    def _label_needs_attention(self, repo: str, pr_url: str) -> None:
        """Tag the PR so it stands out in the list. Never fails the publish."""
        if self.sandbox:
            return
        try:
            number = int(pr_url.rstrip("/").rsplit("/", 1)[-1])
            try:
                self._api("POST", repo, "labels", json={"name": "needs-attention", "color": "d93f0b",
                                                        "description": "The pipeline flagged something to check before merging"})
            except Exception:  # noqa: BLE001 - it already exists
                pass
            self._api("POST", repo, f"issues/{number}/labels", json={"labels": ["needs-attention"]})
        except Exception as e:  # noqa: BLE001
            print(f"    Could not label {pr_url}: {e}")

    def _open_pr(self, repo: str, branch: str, title: str, body: str, attention: list) -> str:
        if self.sandbox:
            # No PR and no label: the sandbox branch is the result, read back
            # and deleted by the end-to-end check.
            self.sandbox_branches.append((repo, branch))
            print(f"    Sandbox: {repo}@{branch} committed; no PR opened")
            return f"sandbox:{repo}@{branch}"
        url = self._create_pr(repo, branch, title, body)
        if attention:
            self._label_needs_attention(repo, url)
        return url

    @staticmethod
    def _used_assets(bundle_asset_paths: list[str], content: str) -> list[str]:
        """Only the assets this page references. An image meant only for the doc
        page must not be uploaded to the releases repo, and vice versa."""
        return [p for p in bundle_asset_paths if f"](assets/{Path(p).name})" in (content or "")]

    @staticmethod
    def _subsection_text(subsections: dict, key: str) -> str:
        parts = [subsections.get("parent_content", "")]
        for child in subsections.get("children", []):
            parts.append((child.get("draft", {}).get(key) or {}).get("content", ""))
        return "\n".join(parts)

    @staticmethod
    def _gaps_block(draft_result: dict) -> str:
        """Master template sections left off the page, for the reviewer."""
        gaps = draft_result.get("_doc_gaps") or []
        if not gaps:
            return ""
        return ("### Sections left out\n\nThe source material did not cover these master template sections, "
                "so they are not on the page. Add the information and re-run if any of them should be there:\n\n"
                + "\n".join(f"- {g}" for g in gaps) + "\n\n---\n\n")

    @staticmethod
    def _style_block(draft_result: dict, key: str) -> str:
        """Style checker findings that survived the automatic fixes, for the PR."""
        items = (draft_result.get("_style_report") or {}).get(key) or []
        if not items:
            return ""
        return "### Style check\n\nCheck these before merging:\n\n" + "\n".join(f"- {i}" for i in items) + "\n\n---\n\n"

    def _update_summary(self, repo: str, branch: str, section_title: str,
                        file_path: str, page_title: str, allow_new_group: bool = False) -> None:
        """Add a new page entry to SUMMARY.md under the matching section.

        GitBook SUMMARY.md format:
          ## Section Title
          * [Page Title](path/to/file.md)

        If the section doesn't exist, creates it as the first section
        (most recent month goes on top).
        """
        summary_path = self.scope.summary_path
        summary_content, summary_sha = self._get_file(repo, summary_path, branch)
        if summary_content is None:
            return

        entry = f"* [{page_title}]({file_path})"

        # Check if entry already exists
        if file_path in summary_content:
            return

        lines = summary_content.splitlines()
        insert_idx = None

        # Find the section and the end of its entries
        in_section = False
        for i, line in enumerate(lines):
            # Match section headers (## July 2026, ## May 2026, etc.)
            if line.strip().startswith("## ") and section_title.lower() in line.lower():
                in_section = True
                continue
            if in_section:
                # We're past the section header — look for the end of this section
                if line.strip().startswith("## "):
                    # Hit the next section — insert before it
                    insert_idx = i
                    break
                if line.strip().startswith("* ["):
                    # Track the last entry in this section
                    insert_idx = i + 1

        if insert_idx is None:
            if in_section:
                # Section found but no entries yet — append at end
                insert_idx = len(lines)
            else:
                # Section not found. Only release notes may open a new group
                # (the month); a docs page never invents a sidebar section.
                if not allow_new_group:
                    raise ValueError(f'"{section_title}" is not a section in {summary_path}; '
                                     "new sidebar groups are only created for release months")
                # Section not found — create it.
                # Releases repo: newest month on top (before the first ## heading).
                # Docs repo: before "## Discover More", so a new section lands at
                # the end of the sidebar rather than above Introduction to FlytBase.
                anchor_idx = None
                anchor = self.scope.summary_anchor.strip().lower()
                if repo == DOCS_REPO and anchor:
                    for i, line in enumerate(lines):
                        if line.strip().lower().startswith(anchor):
                            anchor_idx = i
                            break

                if anchor_idx is None:
                    for i, line in enumerate(lines):
                        if line.strip().startswith("## "):
                            anchor_idx = i
                            break

                if anchor_idx is not None:
                    lines.insert(anchor_idx, "")
                    lines.insert(anchor_idx, entry)
                    lines.insert(anchor_idx, "")
                    lines.insert(anchor_idx, f"## {section_title}")
                    lines.insert(anchor_idx, "")
                else:
                    # No sections exist at all — append at end
                    lines.append("")
                    lines.append(f"## {section_title}")
                    lines.append("")
                    lines.append(entry)

                updated = "\n".join(lines) + "\n"
                self._write_file(
                    repo, summary_path, updated, branch,
                    f"docs: add {page_title} to {summary_path}",
                    existing_sha=summary_sha,
                )
                return

        lines.insert(insert_idx, entry)
        updated = "\n".join(lines) + "\n"

        self._write_file(
            repo, summary_path, updated, branch,
            f"docs: add {page_title} to {summary_path}",
            existing_sha=summary_sha,
        )

    @staticmethod
    def _extract_title(content: str, fallback: str = "") -> str:
        """Extract the H1 title from markdown content.

        Looks for the first '# ' line (H1 heading) and returns its text.
        Falls back to the provided fallback if no H1 is found.
        """
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("## "):
                return stripped[2:].strip()
        return fallback

    def _protect_readme(self, repo: str, branch: str) -> None:
        """No-op. README.md is not used in the releases or docs repos.

        GitBook navigation is fully controlled by SUMMARY.md.
        The pipeline never creates or modifies README.md.
        """
        pass

    def _create_pr(self, repo: str, branch: str, title: str, body: str) -> str:
        """Open a pull request, or update the open one for this branch (a re-run).
        Returns the PR URL."""
        if self.sandbox:
            raise PublishError("a sandbox run must never open a pull request")
        try:
            data = self._api("POST", repo, "pulls", json={
                "title": title, "body": body, "head": branch, "base": BASE_BRANCH,
            })
            return data["html_url"]
        except requests.HTTPError as e:
            if getattr(getattr(e, "response", None), "status_code", None) != 422:
                raise
            owner = repo.split("/")[0]
            open_prs = self._api("GET", repo, "pulls", params={"head": f"{owner}:{branch}", "state": "open"})
            if not open_prs:
                raise
            number = open_prs[0]["number"]
            self._api("PATCH", repo, f"pulls/{number}", json={"title": title, "body": body})
            print(f"    Updated the open PR for {branch} instead of opening a second one")
            return open_prs[0]["html_url"]

    def _update_summary_nested(self, repo: str, branch: str, section_title: str,
                               parent_path: str, parent_title: str,
                               children: list[dict], allow_new_group: bool = False) -> None:
        """Add a parent page with nested child entries to SUMMARY.md.

        Creates entries like:
          ## July 2026
          * [Parent Title](july-2026/parent-slug/README.md)
            * [Child Title](july-2026/parent-slug/child-slug.md)
        """
        summary_path = self.scope.summary_path
        summary_content, summary_sha = self._get_file(repo, summary_path, branch)
        if summary_content is None:
            return

        # Check if parent entry already exists
        if parent_path in summary_content:
            return

        parent_entry = f"* [{parent_title}]({parent_path})"
        child_entries = [
            f"  * [{c['title']}]({c['path']})" for c in children
        ]
        all_entries = [parent_entry] + child_entries

        lines = summary_content.splitlines()
        insert_idx = None

        in_section = False
        for i, line in enumerate(lines):
            if line.strip().startswith("## ") and section_title.lower() in line.lower():
                in_section = True
                continue
            if in_section:
                if line.strip().startswith("## "):
                    insert_idx = i
                    break
                if line.strip().startswith("* ["):
                    insert_idx = i + 1

        if insert_idx is None:
            if in_section:
                insert_idx = len(lines)
            else:
                if not allow_new_group:
                    raise ValueError(f'"{section_title}" is not a section in {summary_path}; '
                                     "new sidebar groups are only created for release months")
                anchor_idx = None
                anchor = self.scope.summary_anchor.strip().lower()
                if repo == DOCS_REPO and anchor:
                    for i, line in enumerate(lines):
                        if line.strip().lower().startswith(anchor):
                            anchor_idx = i
                            break

                if anchor_idx is None:
                    for i, line in enumerate(lines):
                        if line.strip().startswith("## "):
                            anchor_idx = i
                            break

                if anchor_idx is not None:
                    for entry in reversed(all_entries):
                        lines.insert(anchor_idx, entry)
                    lines.insert(anchor_idx, "")
                    lines.insert(anchor_idx, f"## {section_title}")
                    lines.insert(anchor_idx, "")
                else:
                    lines.append("")
                    lines.append(f"## {section_title}")
                    lines.append("")
                    lines.extend(all_entries)

                updated = "\n".join(lines) + "\n"
                self._write_file(
                    repo, summary_path, updated, branch,
                    f"docs: add {parent_title} (subsections) to {summary_path}",
                    existing_sha=summary_sha,
                )
                return

        for entry in reversed(all_entries):
            lines.insert(insert_idx, entry)

        updated = "\n".join(lines) + "\n"
        self._write_file(
            repo, summary_path, updated, branch,
            f"docs: add {parent_title} (subsections) to {summary_path}",
            existing_sha=summary_sha,
        )

    def publish(
        self,
        draft_result: dict,
        output_dir: str,
        feature_slug: str,
        bundle_asset_paths: list[str],
        release_month: str = "",
        mode: str = "both",
        requester_name: str = "",
        requester_username: str = "",
        slack_channel: str = "",
        slack_thread_ts: str = "",
        subsections: dict = None,
        target_section: str = "",
        doc_scope: str = "",
        attention: list | None = None,
        fixes: list | None = None,
        allow_new_section: bool = False,
    ) -> dict:
        """Full publish flow: branches, files, sidebar, assets, impacted pages, PRs.

        doc_scope selects which documentation set this run writes to ("cloud"
        or "onprem"). It comes from the Slack button click, never from the
        model.

        Self-healing (P0, 2026-10-22): the only things that stop a PR are
        failing to create its branch, write its page, or open it. Everything
        else (placement, sidebar, links, assets, labels, impacted pages) is
        repaired where possible and otherwise listed under "Needs attention"
        at the top of the PR. attention holds items from earlier stages (fact
        check, image descriptions, missing files) and goes on every PR.

        A re-run of the same Slack request reuses its branch and updates its
        open PR, so it never makes a duplicate branch, PR or sidebar entry.

        Returns dict with PR URLs, needs_attention, fixes and any errors.
        """
        self._allow_new_section = bool(allow_new_section)
        if doc_scope:
            self.scope = get_scope(doc_scope)

        run_key = (slack_thread_ts or "").replace(".", "") or str(int(time.time()))
        branch = f"{self.scope.branch_prefix}/{feature_slug}-{run_key}"
        if self.sandbox:
            branch = f"{sandbox_prefix(self.sandbox)}/{feature_slug}"
        results = {"docs_pr": None, "releases_pr": None, "errors": []}
        if draft_result.get("_doc_gaps"):
            results["doc_gaps"] = list(draft_result["_doc_gaps"])
        shared = list(attention or [])
        fixes = list(fixes or [])
        att_rel: list[str] = []
        att_docs: list[str] = []
        summary_path = self.scope.summary_path

        # Cache compressed GIF bytes so we don't re-compress for the second repo
        self._asset_cache: dict[str, bytes] = {}

        release_note = draft_result.get("release_note", {})
        doc_page = draft_result.get("doc_page", {})
        impacted_edits = draft_result.get("impacted_page_edits", [])
        guess_label = draft_result.get("_placement_guess", "") or ""

        docs_summary_cache: list = []

        def docs_summary() -> str:
            """The live docs sidebar on main, for link repair. Empty if unreadable."""
            if not docs_summary_cache:
                try:
                    docs_summary_cache.append(self._get_file(DOCS_REPO, summary_path, BASE_BRANCH)[0] or "")
                except Exception as e:  # noqa: BLE001
                    print(f"    Could not read the docs sidebar for link repair: {e}")
                    docs_summary_cache.append("")
            return docs_summary_cache[0]

        def safe(label: str, att: list, fn, *a, **kw):
            """Run a non-essential step; a failure becomes a Needs attention item."""
            try:
                return fn(*a, **kw)
            except Exception as e:  # noqa: BLE001
                att.append(f"{label} failed: {report_exception(label, e)}")
                return None

        def repaired(repo, path, content, att, extra=()):
            out = safe(f"Link repair for {path}", att, self._repair_page_links,
                       repo, path, content, docs_summary(), att, fixes, extra)
            return content if out is None else out

        def upload_assets(repo, content, folder, att):
            for asset_path in self._used_assets(bundle_asset_paths, content):
                cached = self._asset_cache.get(asset_path)
                err = safe(f"Uploading {Path(asset_path).name}", att, self._upload_asset,
                           repo, asset_path, folder, branch, feature_slug, precompressed=cached)
                if err:
                    att.append(f"{Path(asset_path).name} could not be uploaded to {folder}: {err}. Add it by hand.")

        def patch_impacted(repo, host, att, key):
            if self.sandbox:  # never edit a real page, even on a disposable branch
                results.setdefault("sandbox_skipped_edits", 0)
                results["sandbox_skipped_edits"] += sum(1 for e in impacted_edits if host in e.get("source_url", ""))
                return
            for edit in impacted_edits:
                url = edit.get("source_url", "")
                if host not in url:
                    continue
                try:
                    _, file_path = self._url_to_repo_path(url)
                    file_path, existing_content, existing_sha = self._existing_page(repo, file_path)
                    if existing_content is None:
                        att.append(f"Cross-link not added: {file_path} does not exist.")
                        continue
                    patched = self._apply_patch(existing_content, edit.get("section_heading", ""),
                                                edit.get("patch_mode", "append"), edit.get("patch_content", ""))
                    self._write_file(repo, file_path, patched, branch,
                                     f"docs: update {file_path} - cross-reference {feature_slug}", existing_sha=existing_sha)
                    results.setdefault(key, []).append(file_path)
                except Exception as e:  # noqa: BLE001
                    att.append(f"Cross-link to {url} not added: {report_exception(f'Failed to patch {url}', e)}")

        def doc_link(content: str, doc_path: str | None, has_doc: bool) -> str:
            """The release note's link to its doc page: where the page actually
            landed (the docs repo is published first), or "coming soon" when a
            doc page was expected but has no PR. Never a URL that would 404."""
            if doc_path:
                return finalize_doc_link(content, docs_url(doc_path))
            if mode != "release_only" and has_doc and DOC_LINK_PLACEHOLDER in content:
                att_rel.append("The doc page was not published, so the release note says "
                               f"\"{DOCS_COMING_SOON}\" instead of linking it. Add the link once the doc page is live.")
                return _coming_soon(content)
            return finalize_doc_link(content, None)

        # Docs first: the release note links to where the doc page actually landed.
        # ── Docs repo ──────────────────────────────────────────────────────
        if mode != "release_only":
            docs_file, docs_children = None, {}
            try:
                self._begin(DOCS_REPO, branch)

                if subsections:
                    parent_slug = subsections["parent_slug"]
                    parent_title = subsections["parent_title"]
                    first_child_dp = subsections["children"][0]["draft"].get("doc_page", {}) if subsections["children"] else {}
                    target_path = _sanitize_target_path(first_child_dp.get("target_path", ""))
                    placed = safe("Resolving placement", att_docs, self._resolve_placement,
                                  DOCS_REPO, branch, target_section, target_path, results, guess_label)
                    placement, summary_text, summary_sha, _, notes = placed or (None, None, None, False, [])
                    att_docs += notes
                    folder_path = repo_path_for(placement.folder if placement else "", parent_slug, summary_path)
                    readme = f"{folder_path}/README.md"
                    child_paths = [f"{folder_path}/{c['slug']}.md" for c in subsections["children"]]

                    self._write_file(DOCS_REPO, readme, repaired(DOCS_REPO, readme, subsections["parent_content"], att_docs,
                                                                  child_paths), branch, f"docs: add {parent_slug} parent overview")
                    summary_children, to_check = [], []
                    for child_result, child_path in zip(subsections["children"], child_paths):
                        child_dp = child_result["draft"].get("doc_page", {})
                        if not child_dp:
                            continue
                        content = repaired(DOCS_REPO, child_path, child_dp.get("frontmatter", "") + "\n\n" + child_dp.get("content", ""),
                                           att_docs, child_paths + [readme])
                        self._write_file(DOCS_REPO, child_path, content, branch, f"docs: add {child_result['slug']} doc page")
                        title = self._extract_title(child_dp.get("content", ""), child_result["slug"].replace("-", " ").title())
                        summary_children.append({"title": title, "path": child_path})
                        to_check.append((child_path, content, []))

                    if placement:
                        rel = lambda p: relative_to_summary(p, summary_path)
                        entries = [(parent_title, rel(readme), 0)] + [(c["title"], rel(c["path"]), 1) for c in summary_children]
                        safe("Updating the docs sidebar", att_docs, self._write_summary_entry, branch, placement, summary_text,
                             summary_sha, insert_entries(summary_text, placement, entries),
                             f"docs: add {parent_title} (subsections) to {summary_path}", att_docs)
                        results["docs_section"] = placement.label
                    else:
                        results["docs_section"] = "(not in the sidebar yet)"
                    docs_file = readme
                    docs_children = dict(zip((c["slug"] for c in subsections["children"]), child_paths))

                    upload_assets(DOCS_REPO, self._subsection_text(subsections, "doc_page"), f"{folder_path}/assets", att_docs)
                    patch_impacted(DOCS_REPO, "docs.flytbase.com", att_docs, "docs_impacted")
                    for path, text, extra in to_check:
                        att_docs += safe("Link check", att_docs, self._check_links, DOCS_REPO, path, text, results, extra) or []
                    body = self._build_pr_body_subsections(parent_title, readme, summary_children, impacted_edits, "docs",
                                                           requester_name, requester_username, slack_channel, slack_thread_ts)
                    self._commit(DOCS_REPO, f"docs: add {parent_title} ({len(summary_children)} subsections)", att_docs)
                    items = shared + att_docs
                    results["docs_pr"] = self._open_pr(
                        DOCS_REPO, branch, f"📄 New Doc Pages: {parent_title} ({len(summary_children)} subsections)",
                        self._attention_block(items) + self._placement_block(results) + self._fixes_block(fixes)
                        + self._gaps_block(draft_result) + self._style_block(draft_result, "doc_page") + body, items)

                elif doc_page:
                    filename = Path(doc_page.get("filename", "doc.md")).name
                    target_path = _sanitize_target_path(doc_page.get("target_path", ""))
                    slug_title = feature_slug.replace("-", " ").title()
                    doc_title = self._extract_title(doc_page.get("content", ""), slug_title)
                    placed = safe("Resolving placement", att_docs, self._resolve_placement,
                                  DOCS_REPO, branch, target_section, target_path, results, guess_label)
                    placement, summary_text, summary_sha, _, notes = placed or (None, None, None, False, [])
                    att_docs += notes
                    # The file path comes only from a real sidebar section, never from the guess.
                    file_path = repo_path_for(placement.folder if placement else "", filename, summary_path)
                    scoped_dir = posixpath.dirname(file_path)
                    full_content = repaired(DOCS_REPO, file_path, doc_page.get("frontmatter", "") + "\n\n" + doc_page.get("content", ""), att_docs)
                    self._write_file(DOCS_REPO, file_path, full_content, branch, f"docs: add {feature_slug} doc page")

                    if placement:
                        safe("Updating the docs sidebar", att_docs, self._write_summary_entry, branch, placement, summary_text,
                             summary_sha, insert_entry(summary_text, placement, doc_title, relative_to_summary(file_path, summary_path)),
                             f"docs: add {doc_title} to {summary_path}", att_docs)
                        results["docs_section"] = placement.label
                    else:
                        results["docs_section"] = "(not in the sidebar yet)"
                    docs_file = file_path

                    upload_assets(DOCS_REPO, full_content, f"{scoped_dir}/assets" if scoped_dir else "assets", att_docs)
                    patch_impacted(DOCS_REPO, "docs.flytbase.com", att_docs, "docs_impacted")
                    att_docs += safe("Link check", att_docs, self._check_links, DOCS_REPO, file_path, full_content, results) or []
                    body = self._build_pr_body(doc_title, doc_page, impacted_edits, "docs", requester_name, requester_username,
                                               slack_channel, slack_thread_ts)
                    self._commit(DOCS_REPO, f"docs: add {doc_title}", att_docs)
                    items = shared + att_docs
                    results["docs_pr"] = self._open_pr(
                        DOCS_REPO, branch, f"📄 New Doc Page: {doc_title}",
                        self._attention_block(items) + self._placement_block(results) + self._fixes_block(fixes)
                        + self._gaps_block(draft_result) + self._style_block(draft_result, "doc_page") + body, items)
                # Only a doc page with a PR is linked from the release note.
                if results.get("docs_pr") and docs_file:
                    results["docs_file"] = docs_file
                    results["docs_child_files"] = docs_children
            except Exception as e:
                results["errors"].append(report_exception("Docs repo failed", e))
            finally:
                if not results.get("docs_pr"):
                    self._discard(DOCS_REPO)

        # ── Releases repo ──────────────────────────────────────────────────
        if mode != "doc_only":
            try:
                self._begin(RELEASES_REPO, branch)

                if subsections:
                    parent_slug = subsections["parent_slug"]
                    parent_title = subsections["parent_title"]
                    folder_path = f"{self.scope.rooted(release_month)}/{parent_slug}"
                    self._write_file(RELEASES_REPO, f"{folder_path}/README.md", subsections["parent_content"], branch,
                                     f"docs: add {parent_slug} parent overview")

                    summary_children, to_check = [], []
                    for child_result in subsections["children"]:
                        child_slug = child_result["slug"]
                        child_draft = child_result["draft"]
                        child_rn = child_draft.get("release_note", {})
                        if not child_rn:
                            continue
                        child_doc_path = (results.get("docs_child_files") or {}).get(child_slug)
                        child_body = doc_link(child_rn.get("content", ""), child_doc_path, bool(child_draft.get("doc_page")))
                        child_path = f"{folder_path}/{child_slug}.md"
                        child_content = repaired(RELEASES_REPO, child_path, child_rn.get("frontmatter", "") + "\n\n" + child_body,
                                                 att_rel, [child_doc_path] if child_doc_path else [])
                        self._write_file(RELEASES_REPO, child_path, child_content, branch, f"docs: add {child_slug} release note")
                        child_title = self._extract_title(child_rn.get("content", ""), child_slug.replace("-", " ").title())
                        summary_children.append({"title": child_title, "path": child_path})
                        to_check.append((child_path, child_content, [child_doc_path] if child_doc_path else []))

                    safe("Updating the releases sidebar", att_rel, self._update_summary_nested,
                         RELEASES_REPO, branch, release_month.replace("-", " ").upper(),
                         relative_to_summary(f"{folder_path}/README.md", summary_path), parent_title,
                         [{**c, "path": relative_to_summary(c["path"], summary_path)} for c in summary_children],
                         allow_new_group=True)
                    upload_assets(RELEASES_REPO, self._subsection_text(subsections, "release_note"), f"{folder_path}/assets", att_rel)
                    for path, text, extra in to_check:
                        att_rel += safe("Link check", att_rel, self._check_links, RELEASES_REPO, path, text, results, extra) or []
                    patch_impacted(RELEASES_REPO, "releases.flytbase.com", att_rel, "releases_impacted")
                    body = self._build_pr_body_subsections(parent_title, f"{folder_path}/README.md", summary_children,
                                                           impacted_edits, "releases", requester_name, requester_username,
                                                           slack_channel, slack_thread_ts)
                    self._commit(RELEASES_REPO, f"docs: add {parent_title} release notes ({len(summary_children)} subsections)", att_rel)
                    items = shared + att_rel
                    results["releases_pr"] = self._open_pr(
                        RELEASES_REPO, branch, f"✍️ New Release Notes: {parent_title} ({len(summary_children)} subsections)",
                        self._attention_block(items) + self._fixes_block(fixes) + body, items)

                elif release_note:
                    filename = Path(release_note.get("filename", "release.md")).name
                    doc_path = results.get("docs_file")
                    release_body = doc_link(release_note.get("content", ""), doc_path, bool(doc_page))
                    file_path = f"{self.scope.rooted(release_month)}/{filename}"
                    full_content = repaired(RELEASES_REPO, file_path, release_note.get("frontmatter", "") + "\n\n" + release_body,
                                            att_rel, [doc_path] if doc_path else [])
                    self._write_file(RELEASES_REPO, file_path, full_content, branch, f"docs: add {feature_slug} release note")

                    slug_title = feature_slug.replace("-", " ").title()
                    rn_title = self._extract_title(release_note.get("content", ""), slug_title)
                    safe("Updating the releases sidebar", att_rel, self._update_summary,
                         RELEASES_REPO, branch, release_month.replace("-", " ").upper(),
                         relative_to_summary(file_path, summary_path), rn_title, allow_new_group=True)
                    upload_assets(RELEASES_REPO, full_content, f"{self.scope.rooted(release_month)}/assets", att_rel)
                    patch_impacted(RELEASES_REPO, "releases.flytbase.com", att_rel, "releases_impacted")
                    att_rel += safe("Link check", att_rel, self._check_links, RELEASES_REPO, file_path, full_content, results,
                                    [doc_path] if doc_path else []) or []
                    body = self._style_block(draft_result, "release_note") + self._build_pr_body(
                        rn_title, release_note, impacted_edits, "releases", requester_name, requester_username,
                        slack_channel, slack_thread_ts)
                    self._commit(RELEASES_REPO, f"docs: add {rn_title} release note", att_rel)
                    items = shared + att_rel
                    results["releases_pr"] = self._open_pr(
                        RELEASES_REPO, branch, f"✍️ New Release Note: {rn_title}",
                        self._attention_block(items) + self._fixes_block(fixes) + body, items)
            except Exception as e:
                results["errors"].append(report_exception("Releases repo failed", e))
            finally:
                if not results.get("releases_pr"):
                    self._discard(RELEASES_REPO)

        if self.sandbox:
            results["sandbox_branches"] = [list(x) for x in self.sandbox_branches]
        results["needs_attention"] = list(dict.fromkeys(shared + att_rel + att_docs))
        results["fixes"] = list(dict.fromkeys(fixes))
        return results

    def _build_pr_body_subsections(self, parent_title: str, parent_path: str,
                                    children: list[dict], impacted_edits: list, repo_type: str,
                                    requester_name: str = "", requester_username: str = "",
                                    slack_channel: str = "", slack_thread_ts: str = "") -> str:
        is_releases = repo_type == "releases"
        page_type = "Release Notes" if is_releases else "Doc Pages"

        relevant_edits = [
            e for e in impacted_edits
            if (is_releases and "releases.flytbase.com" in e.get("source_url", ""))
            or (not is_releases and "docs.flytbase.com" in e.get("source_url", ""))
        ]

        lines = [
            f"## {parent_title}",
            "",
            f"> This PR was automatically generated by the FlytBase Documentation Pipeline (subsections mode).",
            "",
            "---",
            "",
            f"### 📁 New {page_type} (Grouped)",
            f"**Parent:** `{parent_path}`",
            "",
            f"**Subsections ({len(children)}):**",
        ]
        for c in children:
            lines.append(f"- `{c['path']}` — {c['title']}")

        lines += [
            "",
            f"This PR creates a grouped {'release note' if is_releases else 'documentation'} page with "
            f"{len(children)} subsections under **{parent_title}**.",
            "",
        ]

        if relevant_edits:
            lines += [
                "---",
                "",
                f"### ✏️ Existing Pages Updated ({len(relevant_edits)})",
                "",
            ]
            for e in relevant_edits:
                url = e.get("source_url", "")
                description = e.get("edit_description", "")
                lines += [f"**{url}**", f"_{description}_", ""]

        lines += [
            "---",
            "",
            "### ✅ Review Checklist",
            "",
            f"- [ ] Parent overview page links to all subsections",
            f"- [ ] Each subsection covers its scoped content",
            "- [ ] All images render correctly (check the Preview tab)",
            "- [ ] Tone and terminology match existing pages",
        ]

        if relevant_edits:
            lines += [
                "- [ ] Patched sections read naturally in their existing pages",
                "- [ ] Cross-reference links are correct",
            ]

        lines += [
            "",
            "**If anything looks wrong**, leave a comment explaining the issue "
            "— the pipeline will learn from it for next time.",
            "",
            "---",
            "",
        ]

        if requester_name:
            lines.append(f"**Requested by:** {requester_name} (@{requester_username})")
        from datetime import datetime
        lines.append(f"**Generated at:** {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
        lines.append("**Via:** FlytBase Documentation Pipeline")
        if slack_channel and slack_thread_ts:
            thread_id = slack_thread_ts.replace(".", "")
            lines.append(f"**Slack thread:** https://flytbase.slack.com/archives/{slack_channel}/p{thread_id}")

        return "\n".join(lines)

    @staticmethod
    def _placement_block(results: dict) -> str:
        """Where the page landed, and any placement warning, at the top of the PR."""
        lines = []
        if results.get("docs_section"):
            lines.append(f"**Placed under:** {results['docs_section']}")
        if results.get("docs_file"):
            lines.append(f"**Path:** `{results['docs_file']}`")
        return ("\n\n".join(lines) + "\n\n---\n\n") if lines else ""

    def _build_pr_body(self, feature_title: str, page: dict, impacted_edits: list, repo_type: str,
                       requester_name: str = "", requester_username: str = "",
                       slack_channel: str = "", slack_thread_ts: str = "") -> str:
        is_releases = repo_type == "releases"
        page_type = "Release Note" if is_releases else "Doc Page"
        filename = page.get("filename", "N/A")

        relevant_edits = [
            e for e in impacted_edits
            if (is_releases and "releases.flytbase.com" in e.get("source_url", ""))
            or (not is_releases and "docs.flytbase.com" in e.get("source_url", ""))
        ]

        lines = [
            f"## {feature_title}",
            "",
            f"> This PR was automatically generated by the FlytBase Documentation Pipeline.",
            "",
            "---",
            "",
            f"### 🆕 New {page_type}",
            f"**File:** `{filename}`",
            "",
            f"This is the new {'release note' if is_releases else 'documentation page'} for **{feature_title}**. "
            f"Please read through it carefully and check that the content is accurate, complete, and matches the tone of existing {'release notes' if is_releases else 'docs pages'}.",
            "",
        ]

        if relevant_edits:
            lines += [
                "---",
                "",
                f"### ✏️ Existing Pages Updated ({len(relevant_edits)})",
                "",
                "The following existing pages have been automatically patched to reference this new feature. "
                "Please verify the added sections are accurate and fit naturally into each page.",
                "",
            ]
            for e in relevant_edits:
                url = e.get("source_url", "")
                description = e.get("edit_description", "")
                lines += [
                    f"**{url}**",
                    f"_{description}_",
                    "",
                ]

        lines += [
            "---",
            "",
            "### ✅ Review Checklist",
            "",
            f"- [ ] {'Release note' if is_releases else 'Doc page'} covers all features from the PM doc",
            "- [ ] All images render correctly (check the Preview tab)",
            "- [ ] Tone and terminology match existing pages",
        ]

        if relevant_edits:
            lines += [
                "- [ ] Patched sections read naturally in their existing pages",
                "- [ ] Cross-reference links are correct",
            ]

        lines += [
            "",
            "**If anything looks wrong**, leave a comment explaining the issue "
            "— the pipeline will learn from it for next time.",
            "",
            "---",
            "",
        ]

        # Attribution footer
        if requester_name:
            lines.append(f"**Requested by:** {requester_name} (@{requester_username})")
        from datetime import datetime
        lines.append(f"**Generated at:** {datetime.now().strftime('%Y-%m-%d %H:%M')} UTC")
        lines.append("**Via:** FlytBase Documentation Pipeline")
        if slack_channel and slack_thread_ts:
            thread_id = slack_thread_ts.replace(".", "")
            lines.append(f"**Slack thread:** https://flytbase.slack.com/archives/{slack_channel}/p{thread_id}")

        return "\n".join(lines)
