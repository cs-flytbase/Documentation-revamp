"""GitHub publisher — creates a PR with the pipeline output.

Creates a branch, writes new files, patches impacted pages, opens a PR.

Usage:
    from src.tools.github_publisher import publish_pr
    result = publish_pr(pipeline_output_dir="output/run_xxx", draft_result=draft_result)
"""

import base64
import re
import time
from pathlib import Path

import requests

from src.config import GITHUB_TOKEN
from src.diag import report_exception
from src.link_check import check_page
from src.release_template import docs_url, finalize_doc_link
from src.summary_placement import (
    insert_entries, insert_entry, relative_to_summary, repo_path_for, resolve_section,
)
from src.doc_scope import CLOUD, assert_in_scope, get_scope, scope_for_url


DOCS_REPO = "FlytBaseAILabs/flytbase-docs"
RELEASES_REPO = "FlytBaseAILabs/flytbase-releases"
BASE_BRANCH = "main"


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


class GitHubPublisher:
    def __init__(self, doc_scope=CLOUD):
        if not GITHUB_TOKEN:
            raise ValueError("GITHUB_TOKEN is not set in .env")
        self.scope = doc_scope
        self._written: dict[str, set] = {}      # repo -> paths written on this run's branch
        self._tree_cache: dict[str, set] = {}   # repo -> paths on main
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _api(self, method: str, repo: str, path: str, **kwargs) -> dict:
        url = f"https://api.github.com/repos/{repo}/{path}"
        resp = self.session.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _get_branch_sha(self, repo: str, branch: str) -> str:
        """Get the latest commit SHA for a branch."""
        data = self._api("GET", repo, f"git/ref/heads/{branch}")
        return data["object"]["sha"]

    def _create_branch(self, repo: str, branch: str, from_sha: str) -> None:
        """Create a new branch from a given SHA."""
        self._api("POST", repo, "git/refs", json={
            "ref": f"refs/heads/{branch}",
            "sha": from_sha,
        })

    def _get_file(self, repo: str, file_path: str, branch: str) -> tuple[str, str] | tuple[None, None]:
        """Fetch file content and its SHA from GitHub. Returns (content, sha) or (None, None)."""
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
        """Create or update a file on a branch."""
        assert_in_scope(self.scope, file_path, repo)
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

    def _check_links(self, repo: str, page_path: str, content: str, results: dict, also_exists=()) -> None:
        """Report broken links and images in a page as warnings for the PR and Slack."""
        exists = self._exists_fn(repo)
        if exists is None:
            return
        docs = self._exists_fn(DOCS_REPO) if repo != DOCS_REPO else exists
        extra = set(also_exists)  # pages this run is about to create in the other repo

        def docs_exists(path):
            return path in extra or (docs is not None and docs(path))
        for problem in check_page(content, page_path, exists, docs_exists):
            results.setdefault("warnings", []).append(f"{page_path}: {problem}")

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

    def _resolve_placement(self, repo: str, branch: str, target_section: str, guessed_path: str, results: dict):
        """Where a new page goes: the section the user asked for, read from SUMMARY.md.

        Returns (placement or None, summary text, summary sha). A requested
        section that cannot be found is reported in results["warnings"] - never
        silently replaced by the pipeline's own guess. With no request, the
        guess is resolved against SUMMARY.md the same way; None means fall
        back to the legacy heading match.
        """
        summary_path = self.scope.summary_path
        text, sha = self._get_file(repo, summary_path, branch)
        if text is None:
            results.setdefault("warnings", []).append(f"Could not read {summary_path}; the sidebar entry was not added.")
            return None, None, None
        if target_section:
            placement = resolve_section(text, target_section)
            if placement:
                return placement, text, sha
            results.setdefault("warnings", []).append(
                f"Requested section \"{target_section}\" was not found (or is ambiguous) in {summary_path}, "
                f"so the page was placed by the pipeline's own guess instead. Check the location before merging."
            )
        if guessed_path:
            guess = " > ".join(seg.replace("-", " ") for seg in guessed_path.split("/"))
            placement = resolve_section(text, guess, allow_leaf=False)
            if placement:
                return placement, text, sha
        return None, text, sha

    def _planned_doc_path(self, target_section: str, doc_page: dict, parent_slug: str = "", child_slug: str = "") -> str | None:
        """Where the docs side will put this doc page, using the same placement
        rules, so a release note can link to it before the docs PR exists."""
        if not doc_page or not doc_page.get("content"):
            return None
        guessed = _sanitize_target_path(doc_page.get("target_path", ""))
        placement, _, _ = self._resolve_placement(DOCS_REPO, BASE_BRANCH, target_section, guessed, {})
        if parent_slug:
            folder = (repo_path_for(placement.folder, parent_slug, self.scope.summary_path) if placement
                      else "/".join(p for p in (self.scope.rooted(guessed), parent_slug) if p))
            return f"{folder}/{child_slug}.md"
        filename = Path(doc_page.get("filename", "doc.md")).name
        if placement:
            return repo_path_for(placement.folder, filename, self.scope.summary_path)
        scoped = self.scope.rooted(guessed)
        return f"{scoped}/{filename}" if scoped else filename

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
        """Doc sections the source material did not cover, for the reviewer."""
        gaps = draft_result.get("_doc_gaps") or []
        if not gaps:
            return ""
        return ("### Not covered in the source material\n\nThese sections say so on the page. "
                "Add the information before merging, or accept the gap:\n\n"
                + "\n".join(f"- {g}" for g in gaps) + "\n\n---\n\n")

    @staticmethod
    def _style_block(draft_result: dict, key: str) -> str:
        """Style checker findings that survived the automatic fixes, for the PR."""
        items = (draft_result.get("_style_report") or {}).get(key) or []
        if not items:
            return ""
        return "### Style check\n\nCheck these before merging:\n\n" + "\n".join(f"- {i}" for i in items) + "\n\n---\n\n"

    def _update_summary(self, repo: str, branch: str, section_title: str,
                        file_path: str, page_title: str) -> None:
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
        """Open a pull request. Returns the PR URL."""
        data = self._api("POST", repo, "pulls", json={
            "title": title,
            "body": body,
            "head": branch,
            "base": BASE_BRANCH,
        })
        return data["html_url"]

    def _update_summary_nested(self, repo: str, branch: str, section_title: str,
                               parent_path: str, parent_title: str,
                               children: list[dict]) -> None:
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
    ) -> dict:
        """Full publish flow: create branches, write files, patch impacted pages, open PRs.

        doc_scope selects which documentation set this run writes to ("cloud"
        or "onprem"). It comes from the Slack button click, never from the
        model. Both sets have docs and release notes; the scope decides which
        root inside each repo they land in.

        Returns dict with PR URLs and any errors.
        """
        if doc_scope:
            self.scope = get_scope(doc_scope)

        branch = f"{self.scope.branch_prefix}/{feature_slug}-{int(time.time())}"
        output_path = Path(output_dir)
        results = {"docs_pr": None, "releases_pr": None, "errors": []}
        if draft_result.get("_doc_gaps"):
            results["doc_gaps"] = list(draft_result["_doc_gaps"])

        # Cache compressed GIF bytes so we don't re-compress for the second repo
        self._asset_cache: dict[str, bytes] = {}

        release_note = draft_result.get("release_note", {})
        doc_page = draft_result.get("doc_page", {})
        impacted_edits = draft_result.get("impacted_page_edits", [])

        # ── Releases repo ──────────────────────────────────────────────────
        if mode != "doc_only":
            try:
                releases_sha = self._get_branch_sha(RELEASES_REPO, BASE_BRANCH)
                self._create_branch(RELEASES_REPO, branch, releases_sha)

                if subsections:
                    # ── Subsections mode: folder with README.md + child files ──
                    parent_slug = subsections["parent_slug"]
                    parent_title = subsections["parent_title"]
                    folder_path = f"{self.scope.rooted(release_month)}/{parent_slug}"

                    # Write parent overview as README.md
                    self._write_file(
                        RELEASES_REPO, f"{folder_path}/README.md",
                        subsections["parent_content"], branch,
                        f"docs: add {parent_slug} parent overview",
                    )

                    # Write each child
                    summary_children = []
                    to_check = []  # checked after the assets are uploaded
                    for child_result in subsections["children"]:
                        child_slug = child_result["slug"]
                        child_draft = child_result["draft"]
                        child_rn = child_draft.get("release_note", {})
                        if not child_rn:
                            continue
                        child_doc = child_draft.get("doc_page") or {}
                        first_doc = (subsections["children"][0]["draft"].get("doc_page") or {}) if subsections["children"] else {}
                        child_doc_path = (self._planned_doc_path(target_section, {**child_doc, "target_path": first_doc.get("target_path", "")}, parent_slug, child_slug)
                                          if mode in ("both", "doc_only") and child_doc else None)
                        child_body = finalize_doc_link(child_rn.get("content", ""), docs_url(child_doc_path) if child_doc_path else None)
                        child_content = child_rn.get("frontmatter", "") + "\n\n" + child_body
                        child_path = f"{folder_path}/{child_slug}.md"
                        self._write_file(
                            RELEASES_REPO, child_path, child_content, branch,
                            f"docs: add {child_slug} release note",
                        )
                        child_title = self._extract_title(child_rn.get("content", ""), child_slug.replace("-", " ").title())
                        summary_children.append({"title": child_title, "path": child_path})
                        to_check.append((child_path, child_content, [child_doc_path] if child_doc_path else []))

                    # Update SUMMARY.md with nested structure
                    section_title = release_month.replace("-", " ").upper()
                    self._update_summary_nested(
                        RELEASES_REPO, branch, section_title,
                        relative_to_summary(f"{folder_path}/README.md", self.scope.summary_path), parent_title,
                        [{**c, "path": relative_to_summary(c["path"], self.scope.summary_path)} for c in summary_children],
                    )

                    # Upload assets to shared folder
                    for asset_path in self._used_assets(bundle_asset_paths, self._subsection_text(subsections, "release_note")):
                        cached = self._asset_cache.get(asset_path)
                        err = self._upload_asset(RELEASES_REPO, asset_path, f"{folder_path}/assets", branch, feature_slug, precompressed=cached)
                        if err:
                            results["errors"].append(err)

                    # Build PR body for subsections
                    child_list = "\n".join(f"- `{c['path']}` — {c['title']}" for c in summary_children)
                    for path, text, extra in to_check:
                        self._check_links(RELEASES_REPO, path, text, results, also_exists=extra)
                    pr_body = self._build_pr_body_subsections(
                        parent_title, f"{folder_path}/README.md", summary_children,
                        impacted_edits, "releases",
                        requester_name, requester_username, slack_channel, slack_thread_ts,
                    )
                    results["releases_pr"] = self._create_pr(
                        RELEASES_REPO, branch,
                        f"✍️ New Release Notes: {parent_title} ({len(summary_children)} subsections)",
                        pr_body,
                    )

                elif release_note:
                    # ── Standard single-file mode ──
                    filename = Path(release_note.get("filename", "release.md")).name
                    doc_path = self._planned_doc_path(target_section, draft_result.get("doc_page") or {}) if mode in ("both", "doc_only") else None
                    release_body = finalize_doc_link(release_note.get("content", ""), docs_url(doc_path) if doc_path else None)
                    full_content = release_note.get("frontmatter", "") + "\n\n" + release_body
                    file_path = f"{self.scope.rooted(release_month)}/{filename}"
                    self._write_file(RELEASES_REPO, file_path, full_content, branch, f"docs: add {feature_slug} release note")

                    slug_title = feature_slug.replace("-", " ").title()
                    feature_title = self._extract_title(release_note.get("content", ""), slug_title)
                    section_title = release_month.replace("-", " ").upper()
                    self._update_summary(RELEASES_REPO, branch, section_title, relative_to_summary(file_path, self.scope.summary_path), feature_title)

                    for asset_path in self._used_assets(bundle_asset_paths, full_content):
                        cached = self._asset_cache.get(asset_path)
                        err = self._upload_asset(RELEASES_REPO, asset_path, f"{self.scope.rooted(release_month)}/assets", branch, feature_slug, precompressed=cached)
                        if err:
                            results["errors"].append(err)

                for edit in impacted_edits:
                    url = edit.get("source_url", "")
                    if "releases.flytbase.com" not in url:
                        continue
                    try:
                        _, file_path = self._url_to_repo_path(url)
                        existing_content, existing_sha = self._get_file(RELEASES_REPO, file_path, BASE_BRANCH)
                        if existing_content is None:
                            results["errors"].append(f"File not found in repo: {file_path}")
                            continue
                        patched = self._apply_patch(existing_content, edit.get("section_heading", ""), edit.get("patch_mode", "append"), edit.get("patch_content", ""))
                        self._write_file(RELEASES_REPO, file_path, patched, branch, f"docs: update {file_path} — cross-reference {feature_slug}", existing_sha=existing_sha)
                        results.setdefault("releases_impacted", []).append(file_path)
                    except Exception as e:
                        results["errors"].append(report_exception(f"Failed to patch {url}", e))

                self._protect_readme(RELEASES_REPO, branch)

                if not subsections:
                    slug_title = feature_slug.replace("-", " ").title()
                    rn_title = self._extract_title(release_note.get("content", ""), slug_title)
                    pr_body = self._style_block(draft_result, "release_note") + self._build_pr_body(rn_title, release_note, impacted_edits, "releases", requester_name, requester_username, slack_channel, slack_thread_ts)
                    self._check_links(RELEASES_REPO, file_path, full_content, results, also_exists=[doc_path] if doc_path else [])
                    if results.get("warnings"):
                        pr_body = "".join(f"> ⚠️ {w}\n" for w in results["warnings"]) + "\n---\n\n" + pr_body
                    results["releases_pr"] = self._create_pr(RELEASES_REPO, branch, f"✍️ New Release Note: {rn_title}", pr_body)
            except Exception as e:
                results["errors"].append(report_exception("Releases repo failed", e))

        # ── Docs repo ──────────────────────────────────────────────────────
        if mode != "release_only":
            try:
                docs_sha = self._get_branch_sha(DOCS_REPO, BASE_BRANCH)
                self._create_branch(DOCS_REPO, branch, docs_sha)

                if subsections:
                    # ── Subsections mode for docs ──
                    parent_slug = subsections["parent_slug"]
                    parent_title = subsections["parent_title"]
                    # Use the first child's target_path for folder location
                    first_child_dp = subsections["children"][0]["draft"].get("doc_page", {})
                    target_path = _sanitize_target_path(first_child_dp.get("target_path", ""))
                    # target_path stays scope-relative (it names the sidebar
                    # section); scoped_dir is where the files actually land.
                    scoped_dir = self.scope.rooted(target_path)
                    folder_path = f"{scoped_dir}/{parent_slug}" if scoped_dir else parent_slug

                    # The requested section decides the folder too, not just
                    # the sidebar line: files used to land in the child's
                    # guessed folder even when the entry was under Flex Factory.
                    placement, summary_text, summary_sha = self._resolve_placement(
                        DOCS_REPO, branch, target_section, target_path, results)
                    if placement:
                        folder_path = repo_path_for(placement.folder, parent_slug, self.scope.summary_path)

                    self._write_file(
                        DOCS_REPO, f"{folder_path}/README.md",
                        subsections["parent_content"], branch,
                        f"docs: add {parent_slug} parent overview",
                    )

                    summary_children = []
                    to_check = []  # checked after the assets are uploaded
                    for child_result in subsections["children"]:
                        child_slug = child_result["slug"]
                        child_draft = child_result["draft"]
                        child_dp = child_draft.get("doc_page", {})
                        if not child_dp:
                            continue
                        child_content = child_dp.get("frontmatter", "") + "\n\n" + child_dp.get("content", "")
                        child_path = f"{folder_path}/{child_slug}.md"
                        self._write_file(
                            DOCS_REPO, child_path, child_content, branch,
                            f"docs: add {child_slug} doc page",
                        )
                        child_title = self._extract_title(child_dp.get("content", ""), child_slug.replace("-", " ").title())
                        summary_children.append({"title": child_title, "path": child_path})
                        to_check.append((child_path, child_content, []))

                    # An explicit target_section (what the user actually asked for)
                    # wins over the first child's IA guess. A child's own
                    # target_path is not a reliable signal for where the whole
                    # parent section belongs - that is how "Aeroflo" became a
                    # top-level section instead of "Flex Factory".
                    summary_path = self.scope.summary_path
                    rel = lambda p: relative_to_summary(p, summary_path)
                    if placement:
                        entries = [(parent_title, rel(f"{folder_path}/README.md"), 0)] + [
                            (c["title"], rel(c["path"]), 1) for c in summary_children
                        ]
                        updated = insert_entries(summary_text, placement, entries)
                        if updated != summary_text:
                            self._write_file(
                                DOCS_REPO, summary_path, updated, branch,
                                f"docs: add {parent_title} (subsections) to {summary_path}",
                                existing_sha=summary_sha,
                            )
                        results["docs_section"] = placement.label
                    else:
                        if target_section:
                            parent_section = target_section
                        elif target_path:
                            parent_section = target_path.replace("-", " ").replace("/", " > ").title().split(" > ")[-1]
                        else:
                            parent_section = None
                        if parent_section:
                            self._update_summary_nested(
                                DOCS_REPO, branch, parent_section,
                                rel(f"{folder_path}/README.md"), parent_title,
                                [{**c, "path": rel(c["path"])} for c in summary_children],
                            )
                        results["docs_section"] = parent_section or "(not added to the sidebar)"
                    results["docs_file"] = f"{folder_path}/README.md"

                    for asset_path in self._used_assets(bundle_asset_paths, self._subsection_text(subsections, "doc_page")):
                        cached = self._asset_cache.get(asset_path)
                        err = self._upload_asset(DOCS_REPO, asset_path, f"{folder_path}/assets", branch, feature_slug, precompressed=cached)
                        if err:
                            results["errors"].append(err)

                    for path, text, extra in to_check:
                        self._check_links(DOCS_REPO, path, text, results, also_exists=extra)
                    pr_body = self._build_pr_body_subsections(
                        parent_title, f"{folder_path}/README.md", summary_children,
                        impacted_edits, "docs",
                        requester_name, requester_username, slack_channel, slack_thread_ts,
                    )
                    pr_body = self._placement_block(results) + self._gaps_block(draft_result) + self._style_block(draft_result, "doc_page") + pr_body
                    results["docs_pr"] = self._create_pr(
                        DOCS_REPO, branch,
                        f"📄 New Doc Pages: {parent_title} ({len(summary_children)} subsections)",
                        pr_body,
                    )

                elif doc_page:
                    # ── Standard single-file mode ──
                    filename = Path(doc_page.get("filename", "doc.md")).name
                    full_content = doc_page.get("frontmatter", "") + "\n\n" + doc_page.get("content", "")
                    target_path = _sanitize_target_path(doc_page.get("target_path", ""))
                    slug_title = feature_slug.replace("-", " ").title()
                    doc_title = self._extract_title(doc_page.get("content", ""), slug_title)
                    summary_path = self.scope.summary_path

                    # The section the user asked for wins over the model's
                    # target_path guess. This path used to ignore it entirely,
                    # which put "Smart Flight Logs" (asked for under Flex
                    # Factory) under Post-Flight Modules.
                    placement, summary_text, summary_sha = self._resolve_placement(
                        DOCS_REPO, branch, target_section, target_path, results)
                    if placement:
                        file_path = repo_path_for(placement.folder, filename, summary_path)
                    else:
                        scoped = self.scope.rooted(target_path)
                        file_path = f"{scoped}/{filename}" if scoped else filename
                    scoped_dir = str(Path(file_path).parent) if "/" in file_path else ""
                    self._write_file(DOCS_REPO, file_path, full_content, branch, f"docs: add {feature_slug} doc page")

                    rel_path = relative_to_summary(file_path, summary_path)
                    if placement:
                        updated = insert_entry(summary_text, placement, doc_title, rel_path)
                        if updated != summary_text:
                            self._write_file(
                                DOCS_REPO, summary_path, updated, branch,
                                f"docs: add {doc_title} to {summary_path}", existing_sha=summary_sha,
                            )
                        results["docs_section"] = placement.label
                    else:
                        parent_section = target_path.replace("-", " ").replace("/", " > ").title() if target_path else ""
                        if parent_section:
                            self._update_summary(DOCS_REPO, branch, parent_section.split(" > ")[-1], rel_path, doc_title)
                        results["docs_section"] = parent_section or "(not added to the sidebar)"
                    results["docs_file"] = file_path

                    for asset_path in self._used_assets(bundle_asset_paths, full_content):
                        cached = self._asset_cache.get(asset_path)
                        err = self._upload_asset(DOCS_REPO, asset_path, f"{scoped_dir}/assets" if scoped_dir else "assets", branch, feature_slug, precompressed=cached)
                        if err:
                            results["errors"].append(err)

                for edit in impacted_edits:
                    url = edit.get("source_url", "")
                    if "docs.flytbase.com" not in url:
                        continue
                    try:
                        _, file_path = self._url_to_repo_path(url)
                        existing_content, existing_sha = self._get_file(DOCS_REPO, file_path, BASE_BRANCH)
                        if existing_content is None:
                            results["errors"].append(f"File not found in repo: {file_path}")
                            continue
                        patched = self._apply_patch(existing_content, edit.get("section_heading", ""), edit.get("patch_mode", "append"), edit.get("patch_content", ""))
                        self._write_file(DOCS_REPO, file_path, patched, branch, f"docs: update {file_path} — cross-reference {feature_slug}", existing_sha=existing_sha)
                        results.setdefault("docs_impacted", []).append(file_path)
                    except Exception as e:
                        results["errors"].append(report_exception(f"Failed to patch {url}", e))

                self._protect_readme(DOCS_REPO, branch)

                if not subsections:
                    slug_title = feature_slug.replace("-", " ").title()
                    dp_title = self._extract_title(doc_page.get("content", ""), slug_title)
                    self._check_links(DOCS_REPO, file_path, full_content, results)
                    pr_body = self._placement_block(results) + self._gaps_block(draft_result) + self._style_block(draft_result, "doc_page") + self._build_pr_body(dp_title, doc_page, impacted_edits, "docs", requester_name, requester_username, slack_channel, slack_thread_ts)
                    results["docs_pr"] = self._create_pr(DOCS_REPO, branch, f"📄 New Doc Page: {dp_title}", pr_body)
            except Exception as e:
                results["errors"].append(report_exception("Docs repo failed", e))

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
        for w in results.get("warnings", []):
            lines.append(f"> ⚠️ {w}")
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
