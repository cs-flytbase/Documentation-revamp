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

    def _create_pr(self, repo: str, branch: str, title: str, body: str) -> str:
        """Open a pull request. Returns the PR URL."""
        data = self._api("POST", repo, "pulls", json={
            "title": title,
            "body": body,
            "head": branch,
            "base": BASE_BRANCH,
        })
        return data["html_url"]

    def publish(
        self,
        draft_result: dict,
        output_dir: str,
        feature_slug: str,
        bundle_asset_paths: list[str],
    ) -> dict:
        """Full publish flow: create branches, write files, patch impacted pages, open PRs.

        Returns dict with PR URLs and any errors.
        """
        branch = f"docs/{feature_slug}-{int(time.time())}"
        output_path = Path(output_dir)
        results = {"docs_pr": None, "releases_pr": None, "errors": []}

        release_note = draft_result.get("release_note", {})
        doc_page = draft_result.get("doc_page", {})
        impacted_edits = draft_result.get("impacted_page_edits", [])

        # ── Releases repo ──────────────────────────────────────────────────
        try:
            releases_sha = self._get_branch_sha(RELEASES_REPO, BASE_BRANCH)
            self._create_branch(RELEASES_REPO, branch, releases_sha)

            # Write the new release note
            if release_note:
                filename = Path(release_note.get("filename", "release.md")).name
                full_content = (
                    release_note.get("frontmatter", "") + "\n\n" +
                    release_note.get("content", "")
                )
                file_path = f"may-2026/{filename}"
                self._write_file(
                    RELEASES_REPO, file_path, full_content, branch,
                    f"docs: add {feature_slug} release note",
                )

                # Write assets
                for asset_path in bundle_asset_paths:
                    asset_name = Path(asset_path).name
                    asset_content = Path(asset_path).read_bytes()
                    asset_b64 = base64.b64encode(asset_content).decode("utf-8")
                    try:
                        self._api("PUT", RELEASES_REPO, f"contents/may-2026/assets/{asset_name}", json={
                            "message": f"docs: add assets for {feature_slug}",
                            "content": asset_b64,
                            "branch": branch,
                        })
                    except requests.HTTPError as e:
                        results["errors"].append(f"Asset upload failed ({asset_name}): {e}")

            # Patch impacted release pages
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
                    patched = self._apply_patch(
                        existing_content,
                        edit.get("section_heading", ""),
                        edit.get("patch_mode", "append"),
                        edit.get("patch_content", ""),
                    )
                    self._write_file(
                        RELEASES_REPO, file_path, patched, branch,
                        f"docs: update {file_path} — cross-reference {feature_slug}",
                        existing_sha=existing_sha,
                    )
                except Exception as e:
                    results["errors"].append(f"Failed to patch {url}: {e}")

            pr_body = self._build_pr_body(feature_slug, release_note, impacted_edits, "releases")
            results["releases_pr"] = self._create_pr(
                RELEASES_REPO, branch,
                f"[Release Note] {feature_slug}",
                pr_body,
            )
        except Exception as e:
            results["errors"].append(f"Releases repo failed: {e}")

        # ── Docs repo ──────────────────────────────────────────────────────
        try:
            docs_sha = self._get_branch_sha(DOCS_REPO, BASE_BRANCH)
            self._create_branch(DOCS_REPO, branch, docs_sha)

            # Write the new doc page
            if doc_page:
                filename = Path(doc_page.get("filename", "doc.md")).name
                full_content = (
                    doc_page.get("frontmatter", "") + "\n\n" +
                    doc_page.get("content", "")
                )
                target_path = doc_page.get("target_path", "").strip("/")
                file_path = f"{target_path}/{filename}" if target_path else filename
                self._write_file(
                    DOCS_REPO, file_path, full_content, branch,
                    f"docs: add {feature_slug} doc page",
                )

                # Write assets
                for asset_path in bundle_asset_paths:
                    asset_name = Path(asset_path).name
                    asset_content = Path(asset_path).read_bytes()
                    asset_b64 = base64.b64encode(asset_content).decode("utf-8")
                    try:
                        self._api("PUT", DOCS_REPO, f"contents/{target_path}/assets/{asset_name}", json={
                            "message": f"docs: add assets for {feature_slug}",
                            "content": asset_b64,
                            "branch": branch,
                        })
                    except requests.HTTPError as e:
                        results["errors"].append(f"Asset upload failed ({asset_name}): {e}")

            # Patch impacted docs pages
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
                    patched = self._apply_patch(
                        existing_content,
                        edit.get("section_heading", ""),
                        edit.get("patch_mode", "append"),
                        edit.get("patch_content", ""),
                    )
                    self._write_file(
                        DOCS_REPO, file_path, patched, branch,
                        f"docs: update {file_path} — cross-reference {feature_slug}",
                        existing_sha=existing_sha,
                    )
                except Exception as e:
                    results["errors"].append(f"Failed to patch {url}: {e}")

            pr_body = self._build_pr_body(feature_slug, doc_page, impacted_edits, "docs")
            results["docs_pr"] = self._create_pr(
                DOCS_REPO, branch,
                f"[Doc Page] {feature_slug}",
                pr_body,
            )
        except Exception as e:
            results["errors"].append(f"Docs repo failed: {e}")

        return results

    def _build_pr_body(self, feature_slug: str, page: dict, impacted_edits: list, repo_type: str) -> str:
        lines = [
            f"## {feature_slug}",
            "",
            "Generated by the FlytBase Documentation Pipeline.",
            "",
            "### New file",
            f"- `{page.get('filename', 'N/A')}`",
            "",
        ]
        relevant_edits = [
            e for e in impacted_edits
            if (repo_type == "releases" and "releases.flytbase.com" in e.get("source_url", ""))
            or (repo_type == "docs" and "docs.flytbase.com" in e.get("source_url", ""))
        ]
        if relevant_edits:
            lines += ["### Impacted pages patched", ""]
            for e in relevant_edits:
                lines.append(f"- `{e.get('source_url', '')}` — {e.get('edit_description', '')}")
            lines.append("")
        lines += [
            "### Review checklist",
            "- [ ] Release note covers all PM doc sections",
            "- [ ] All images render correctly",
            "- [ ] Impacted page patches are accurate",
            "- [ ] Tone and terminology are correct",
        ]
        return "\n".join(lines)
