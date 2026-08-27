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
    def __init__(self):
        if not GITHUB_TOKEN:
            raise ValueError("GITHUB_TOKEN is not set in .env")
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
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha
        self._api("PUT", repo, f"contents/{file_path}", json=payload)

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

    def _upload_asset(self, repo: str, asset_path: str, target_path: str,
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
            return f"Asset upload failed ({asset_name}): {e}"

    def _url_to_repo_path(self, source_url: str) -> tuple[str, str]:
        """Convert a source URL to (repo, file_path).

        https://releases.flytbase.com/february-2026/some-page
        → (RELEASES_REPO, "february-2026/some-page.md")

        https://docs.flytbase.com/some/path
        → (DOCS_REPO, "some/path.md")
        """
        if "releases.flytbase.com" in source_url:
            path = source_url.split("releases.flytbase.com/", 1)[-1].rstrip("/")
            return RELEASES_REPO, f"{path}.md"
        elif "docs.flytbase.com" in source_url:
            path = source_url.split("docs.flytbase.com/", 1)[-1].rstrip("/")
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

    def _update_summary(self, repo: str, branch: str, section_title: str,
                        file_path: str, page_title: str) -> None:
        """Add a new page entry to SUMMARY.md under the matching section.

        GitBook SUMMARY.md format:
          ## Section Title
          * [Page Title](path/to/file.md)

        If the section doesn't exist, creates it as the first section
        (most recent month goes on top).
        """
        summary_content, summary_sha = self._get_file(repo, "SUMMARY.md", branch)
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
                # Section not found — create it as the FIRST section
                # Find the first ## heading to insert before it
                first_section_idx = None
                for i, line in enumerate(lines):
                    if line.strip().startswith("## "):
                        first_section_idx = i
                        break

                if first_section_idx is not None:
                    # Insert new section heading + entry + blank line BEFORE first existing section
                    lines.insert(first_section_idx, "")
                    lines.insert(first_section_idx, entry)
                    lines.insert(first_section_idx, "")
                    lines.insert(first_section_idx, f"## {section_title}")
                    lines.insert(first_section_idx, "")
                else:
                    # No sections exist at all — append at end
                    lines.append("")
                    lines.append(f"## {section_title}")
                    lines.append("")
                    lines.append(entry)

                updated = "\n".join(lines) + "\n"
                self._write_file(
                    repo, "SUMMARY.md", updated, branch,
                    f"docs: add {page_title} to SUMMARY.md",
                    existing_sha=summary_sha,
                )
                return

        lines.insert(insert_idx, entry)
        updated = "\n".join(lines) + "\n"

        self._write_file(
            repo, "SUMMARY.md", updated, branch,
            f"docs: add {page_title} to SUMMARY.md",
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
        summary_content, summary_sha = self._get_file(repo, "SUMMARY.md", branch)
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
                first_section_idx = None
                for i, line in enumerate(lines):
                    if line.strip().startswith("## "):
                        first_section_idx = i
                        break

                if first_section_idx is not None:
                    for entry in reversed(all_entries):
                        lines.insert(first_section_idx, entry)
                    lines.insert(first_section_idx, "")
                    lines.insert(first_section_idx, f"## {section_title}")
                    lines.insert(first_section_idx, "")
                else:
                    lines.append("")
                    lines.append(f"## {section_title}")
                    lines.append("")
                    lines.extend(all_entries)

                updated = "\n".join(lines) + "\n"
                self._write_file(
                    repo, "SUMMARY.md", updated, branch,
                    f"docs: add {parent_title} (subsections) to SUMMARY.md",
                    existing_sha=summary_sha,
                )
                return

        for entry in reversed(all_entries):
            lines.insert(insert_idx, entry)

        updated = "\n".join(lines) + "\n"
        self._write_file(
            repo, "SUMMARY.md", updated, branch,
            f"docs: add {parent_title} (subsections) to SUMMARY.md",
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
    ) -> dict:
        """Full publish flow: create branches, write files, patch impacted pages, open PRs.

        Returns dict with PR URLs and any errors.
        """
        branch = f"docs/{feature_slug}-{int(time.time())}"
        output_path = Path(output_dir)
        results = {"docs_pr": None, "releases_pr": None, "errors": []}
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
                    folder_path = f"{release_month}/{parent_slug}"

                    # Write parent overview as README.md
                    self._write_file(
                        RELEASES_REPO, f"{folder_path}/README.md",
                        subsections["parent_content"], branch,
                        f"docs: add {parent_slug} parent overview",
                    )

                    # Write each child
                    summary_children = []
                    for child_result in subsections["children"]:
                        child_slug = child_result["slug"]
                        child_draft = child_result["draft"]
                        child_rn = child_draft.get("release_note", {})
                        if not child_rn:
                            continue
                        child_content = child_rn.get("frontmatter", "") + "\n\n" + child_rn.get("content", "")
                        child_path = f"{folder_path}/{child_slug}.md"
                        self._write_file(
                            RELEASES_REPO, child_path, child_content, branch,
                            f"docs: add {child_slug} release note",
                        )
                        child_title = self._extract_title(child_rn.get("content", ""), child_slug.replace("-", " ").title())
                        summary_children.append({"title": child_title, "path": child_path})

                    # Update SUMMARY.md with nested structure
                    section_title = release_month.replace("-", " ").upper()
                    self._update_summary_nested(
                        RELEASES_REPO, branch, section_title,
                        f"{folder_path}/README.md", parent_title,
                        summary_children,
                    )

                    # Upload assets to shared folder
                    for asset_path in bundle_asset_paths:
                        cached = self._asset_cache.get(asset_path)
                        err = self._upload_asset(RELEASES_REPO, asset_path, f"{folder_path}/assets", branch, feature_slug, precompressed=cached)
                        if err:
                            results["errors"].append(err)

                    # Build PR body for subsections
                    child_list = "\n".join(f"- `{c['path']}` — {c['title']}" for c in summary_children)
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
                    full_content = release_note.get("frontmatter", "") + "\n\n" + release_note.get("content", "")
                    file_path = f"{release_month}/{filename}"
                    self._write_file(RELEASES_REPO, file_path, full_content, branch, f"docs: add {feature_slug} release note")

                    slug_title = feature_slug.replace("-", " ").title()
                    feature_title = self._extract_title(release_note.get("content", ""), slug_title)
                    section_title = release_month.replace("-", " ").upper()
                    self._update_summary(RELEASES_REPO, branch, section_title, file_path, feature_title)

                    for asset_path in bundle_asset_paths:
                        cached = self._asset_cache.get(asset_path)
                        err = self._upload_asset(RELEASES_REPO, asset_path, f"{release_month}/assets", branch, feature_slug, precompressed=cached)
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
                    except Exception as e:
                        results["errors"].append(f"Failed to patch {url}: {e}")

                self._protect_readme(RELEASES_REPO, branch)

                if not subsections:
                    slug_title = feature_slug.replace("-", " ").title()
                    rn_title = self._extract_title(release_note.get("content", ""), slug_title)
                    pr_body = self._build_pr_body(rn_title, release_note, impacted_edits, "releases", requester_name, requester_username, slack_channel, slack_thread_ts)
                    results["releases_pr"] = self._create_pr(RELEASES_REPO, branch, f"✍️ New Release Note: {rn_title}", pr_body)
            except Exception as e:
                results["errors"].append(f"Releases repo failed: {e}")

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
                    folder_path = f"{target_path}/{parent_slug}" if target_path else parent_slug

                    self._write_file(
                        DOCS_REPO, f"{folder_path}/README.md",
                        subsections["parent_content"], branch,
                        f"docs: add {parent_slug} parent overview",
                    )

                    summary_children = []
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

                    if target_path:
                        parent_section = target_path.replace("-", " ").replace("/", " > ").title().split(" > ")[-1]
                        self._update_summary_nested(
                            DOCS_REPO, branch, parent_section,
                            f"{folder_path}/README.md", parent_title,
                            summary_children,
                        )

                    for asset_path in bundle_asset_paths:
                        cached = self._asset_cache.get(asset_path)
                        err = self._upload_asset(DOCS_REPO, asset_path, f"{folder_path}/assets", branch, feature_slug, precompressed=cached)
                        if err:
                            results["errors"].append(err)

                    pr_body = self._build_pr_body_subsections(
                        parent_title, f"{folder_path}/README.md", summary_children,
                        impacted_edits, "docs",
                        requester_name, requester_username, slack_channel, slack_thread_ts,
                    )
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
                    file_path = f"{target_path}/{filename}" if target_path else filename
                    self._write_file(DOCS_REPO, file_path, full_content, branch, f"docs: add {feature_slug} doc page")

                    slug_title = feature_slug.replace("-", " ").title()
                    doc_title = self._extract_title(doc_page.get("content", ""), slug_title)
                    parent_section = target_path.replace("-", " ").replace("/", " > ").title() if target_path else ""
                    if parent_section:
                        self._update_summary(DOCS_REPO, branch, parent_section.split(" > ")[-1], file_path, doc_title)

                    for asset_path in bundle_asset_paths:
                        cached = self._asset_cache.get(asset_path)
                        err = self._upload_asset(DOCS_REPO, asset_path, f"{target_path}/assets", branch, feature_slug, precompressed=cached)
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
                    except Exception as e:
                        results["errors"].append(f"Failed to patch {url}: {e}")

                self._protect_readme(DOCS_REPO, branch)

                if not subsections:
                    slug_title = feature_slug.replace("-", " ").title()
                    dp_title = self._extract_title(doc_page.get("content", ""), slug_title)
                    pr_body = self._build_pr_body(dp_title, doc_page, impacted_edits, "docs", requester_name, requester_username, slack_channel, slack_thread_ts)
                    results["docs_pr"] = self._create_pr(DOCS_REPO, branch, f"📄 New Doc Page: {dp_title}", pr_body)
            except Exception as e:
                results["errors"].append(f"Docs repo failed: {e}")

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
