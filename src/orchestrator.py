"""Orchestrator — coordinates the full doc generation pipeline.

Usage:
    python -m src.orchestrator ./input_bundles/site-pointers
"""

import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from src.config import PROJECT_ROOT, SETTINGS
from src.agents.research import ResearchAgent
from src.agents.vision import VisionAgent
from src.agents.drafting import DraftingAgent
from src.agents.verification import VerificationAgent


def _save_feature_note(draft_result: dict, research_result: dict, pm_doc: str, bundle_name: str) -> None:
    """Auto-generate a feature note from pipeline results.

    Saves key concepts, terminology, and placement info to memory/features/
    so the drafting agent has feature context on future runs.
    """
    features_dir = PROJECT_ROOT / "memory" / "features"
    features_dir.mkdir(parents=True, exist_ok=True)

    slug = bundle_name.lower().replace(" ", "-")
    note_path = features_dir / f"{slug}.md"

    # Extract key info
    rn = draft_result.get("release_note", {})
    dp = draft_result.get("doc_page", {})
    target_ia = research_result.get("target_ia_node", {})
    feature_summary = research_result.get("feature_summary", "")
    impacted = research_result.get("impacted_pages", [])

    feature_title = slug.replace("-", " ").title()

    lines = [
        f"# {feature_title}",
        "",
        f"*Auto-generated on {datetime.now().strftime('%Y-%m-%d')} from pipeline run.*",
        "",
        "## Summary",
        "",
        feature_summary or f"Feature documented from bundle: {bundle_name}",
        "",
        "## Placement",
        "",
        f"- IA Node: {target_ia.get('id', 'unknown')} ({target_ia.get('label', '')})",
        f"- Reasoning: {target_ia.get('reasoning', '')}",
        "",
    ]

    if impacted:
        lines += ["## Related Pages", ""]
        for page in impacted[:5]:
            lines.append(f"- {page.get('source_url', '')} ({page.get('impact_type', '')})")
        lines.append("")

    # Extract key terms from the PM doc (first 3 lines of specs table if present)
    if "| Component" in pm_doc or "| Feature" in pm_doc:
        lines += ["## Key Specifications", ""]
        for line in pm_doc.split("\n"):
            if line.strip().startswith("|") and "---" not in line:
                lines.append(line.strip())
        lines.append("")

    lines += [
        "## Files",
        "",
        f"- Release note: {rn.get('filename', 'N/A')}",
        f"- Doc page: {dp.get('filename', 'N/A')}",
    ]

    note_path.write_text("\n".join(lines) + "\n")
    print(f"  [Memory] Saved feature note: {note_path.name}")


def validate_input_bundle(bundle_path: str) -> dict:
    """Validate the input bundle. Flexible naming — accepts any .md as source doc."""
    path = Path(bundle_path)
    result = {
        "valid": True,
        "pm_doc_path": None,
        "youtube_link": "",
        "transcript": "",
        "asset_paths": [],
        "asset_filenames": [],
        "errors": [],
        "warnings": [],
    }

    # Find source document — flexible naming
    # Priority: pm_doc.md > doc.md > any .md that isn't transcript/readme
    pm_path = path / "pm_doc.md"
    doc_path = path / "doc.md"

    if pm_path.exists():
        result["pm_doc_path"] = str(pm_path)
    elif doc_path.exists():
        result["pm_doc_path"] = str(doc_path)
    else:
        # Fallback: find the largest .md file that isn't transcript or readme
        md_files = [
            f for f in path.glob("*.md")
            if f.stem.lower() not in ("transcript", "readme")
        ]
        if md_files:
            # Pick the largest one
            largest = max(md_files, key=lambda f: f.stat().st_size)
            result["pm_doc_path"] = str(largest)
            result["warnings"].append(f"Using '{largest.name}' as source document (no pm_doc.md found)")
        else:
            # Try .txt files
            txt_files = [
                f for f in path.glob("*.txt")
                if f.stem.lower() not in ("youtube_link",)
            ]
            if txt_files:
                largest = max(txt_files, key=lambda f: f.stat().st_size)
                result["pm_doc_path"] = str(largest)
            else:
                result["valid"] = False
                result["errors"].append("No source document found (.md or .txt)")

    # Find YouTube link
    yt_file = path / "youtube_link.txt"
    if yt_file.exists():
        result["youtube_link"] = yt_file.read_text().strip()
    else:
        result["warnings"].append("No youtube_link.txt found")

    # Find Clueso transcript (optional)
    for transcript_name in ["transcript.md", "transcript.txt"]:
        transcript_path = path / transcript_name
        if transcript_path.exists():
            result["transcript"] = transcript_path.read_text().strip()
            break

    # Find assets — support images, GIFs, and videos
    asset_dir = path / "assets"
    search_dir = asset_dir if asset_dir.exists() else path
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.mp4"):
        result["asset_paths"].extend([str(p) for p in search_dir.glob(ext)])

    # Sort alphabetically for deterministic ordering
    result["asset_paths"].sort(key=lambda p: Path(p).name)
    result["asset_filenames"] = [Path(p).name for p in result["asset_paths"]]

    if not result["asset_paths"]:
        result["warnings"].append("No image/GIF/video assets found")

    return result


def load_ia_summary() -> str:
    """Load a condensed IA structure for the research agent."""
    ia_path = PROJECT_ROOT / "config" / "ia_structure.yaml"
    with open(ia_path) as f:
        data = yaml.safe_load(f)

    lines = []

    def _walk(nodes, indent=0):
        for node in nodes:
            prefix = "  " * indent
            lines.append(f"{prefix}- {node['id']}: {node['label']} ({node['path']})")
            if "children" in node:
                _walk(node["children"], indent + 1)

    _walk(data["ia_nodes"])
    return "\n".join(lines)


def fetch_exemplar(research_result: dict) -> str:
    """Fetch a similar existing page from corpus to use as a style exemplar."""
    from src.tools.corpus_store import search as corpus_search
    from sentence_transformers import SentenceTransformer

    feature_summary = research_result.get("feature_summary", "")
    if not feature_summary:
        return ""

    try:
        model = SentenceTransformer(SETTINGS["pinecone"]["embedding_model"])
        embedding = model.encode(feature_summary, normalize_embeddings=True).tolist()
        results = corpus_search(embedding, top_k=3)

        # Pick the longest chunk as the exemplar (more detailed = better example)
        best = None
        best_len = 0
        for r in results:
            content = r["metadata"].get("content", "")
            if len(content) > best_len and r["metadata"].get("source_type") != "release":
                best = content
                best_len = len(content)

        return best or ""
    except Exception:
        return ""


def run_pipeline(bundle_path: str, mode: str = "both", requester_name: str = "", requester_username: str = "", slack_channel: str = "", slack_thread_ts: str = "", subsections: dict = None, target_section: str = "") -> dict:
    """Run the full documentation generation pipeline."""
    print("=" * 60)
    print("FlytBase Documentation Pipeline")
    print(f"Bundle: {bundle_path}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Step 1: Validate
    print("\n[Step 1] Validating input bundle...")
    bundle = validate_input_bundle(bundle_path)

    if not bundle["valid"]:
        print(f"  FAILED: {bundle['errors']}")
        return {"status": "failed", "errors": bundle["errors"]}

    for w in bundle["warnings"]:
        print(f"  WARNING: {w}")

    pm_doc = Path(bundle["pm_doc_path"]).read_text()
    print(f"  Source doc: {bundle['pm_doc_path']} ({len(pm_doc)} chars)")
    print(f"  Assets: {len(bundle['asset_paths'])} files — {bundle['asset_filenames']}")
    print(f"  YouTube: {bundle['youtube_link'] or 'None'}")
    print(f"  Transcript: {len(bundle['transcript'])} chars" if bundle["transcript"] else "  Transcript: None")

    # Step 2: Load IA summary
    ia_summary = load_ia_summary()

    # Step 3: Research + Vision in parallel
    print("\n[Step 3] Running Research and Vision agents in parallel...")

    research_result = None
    vision_result = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {}

        research_agent = ResearchAgent()
        futures[executor.submit(research_agent.research, pm_doc, ia_summary)] = "research"

        if bundle["asset_paths"]:
            vision_agent = VisionAgent()
            # Pass FULL pm_doc — not truncated
            futures[executor.submit(
                vision_agent.describe_all_assets,
                bundle["asset_paths"],
                pm_doc,
            )] = "vision"

        for future in as_completed(futures):
            agent_name = futures[future]
            try:
                result = future.result()
                if agent_name == "research":
                    research_result = result
                    print(f"  Research agent done.")
                    if "error" not in result:
                        print(f"    Target IA: {result.get('target_ia_node', {}).get('label', 'N/A')}")
                        print(f"    Impacted pages: {len(result.get('impacted_pages', []))}")
                        print(f"    Relevant chunks: {len(result.get('relevant_chunks', []))}")
                else:
                    vision_result = result
                    print(f"  Vision agent done. Described {len(result)} assets.")
                    for v in result:
                        section = v.get("section_heading", "?")
                        print(f"    - {v.get('file_name', '?')} → {section}: {v.get('description', '')[:60]}...")
            except Exception as e:
                print(f"  {agent_name} agent FAILED: {e}")
                if agent_name == "research":
                    return {"status": "failed", "errors": [f"Research agent failed: {e}"]}

    # Step 3.5: Fetch exemplar from corpus
    print("\n[Step 3.5] Fetching exemplar page from corpus...")
    exemplar = fetch_exemplar(research_result)
    if exemplar:
        print(f"  Found exemplar ({len(exemplar)} chars)")
    else:
        print("  No suitable exemplar found")

    # Step 4: Drafting
    release_month = datetime.now().strftime("%B-%Y").lower()

    if subsections:
        # ── Subsections mode: draft parent overview + one release note per child ──
        print(f"\n[Step 4] Running Drafting agent (SUBSECTIONS mode: {len(subsections['children'])} children)...")
        print(f"  Parent: {subsections['parent_title']}")
        for child in subsections["children"]:
            print(f"    └─ {child['title']} ({child['slug']})")

        drafting_agent = DraftingAgent()
        child_results = []

        for i, child in enumerate(subsections["children"]):
            print(f"\n  [Drafting] Child {i+1}/{len(subsections['children'])}: {child['title']}...")
            child_draft = drafting_agent.draft(
                pm_doc=pm_doc,
                research_output=research_result,
                vision_output=vision_result,
                asset_filenames=bundle["asset_filenames"],
                youtube_link=bundle["youtube_link"],
                release_month=release_month,
                transcript=bundle["transcript"],
                exemplar=exemplar,
                mode=mode,
                subsection_focus=child,
            )
            if "error" in child_draft:
                print(f"    Child draft FAILED: {child_draft['error']}")
                return {"status": "failed", "errors": [f"Subsection '{child['title']}' failed: {child_draft['error']}"]}

            child_results.append({
                "slug": child["slug"],
                "title": child["title"],
                "draft": child_draft,
            })
            rc = child_draft.get("release_note", {}).get("content", "")
            print(f"    Done: {child['slug']}.md ({len(rc)} chars)")

        # Build parent overview page
        print(f"\n  [Drafting] Generating parent overview: {subsections['parent_title']}...")
        child_links = "\n".join(
            f"* [{c['title']}]({c['slug']}.md)" for c in subsections["children"]
        )
        parent_content = (
            f"---\ndescription: >-\n  {subsections['parent_summary'][:140]}\n---\n\n"
            f"# {subsections['parent_title']}\n\n"
            f"{subsections['parent_summary']}\n\n"
            f"## In This Section\n\n{child_links}\n"
        )

        # Package as subsections draft result for the publisher
        draft_result = {
            "release_note": {},
            "doc_page": {},
            "impacted_page_edits": [],
            "_subsections": {
                "parent_title": subsections["parent_title"],
                "parent_content": parent_content,
                "parent_slug": Path(bundle_path).name,
                "children": child_results,
            },
        }

        # Collect impacted page edits from all children
        for cr in child_results:
            draft_result["impacted_page_edits"].extend(
                cr["draft"].get("impacted_page_edits", [])
            )

        validation_warnings = []
        for cr in child_results:
            ws = cr["draft"].pop("_validation_warnings", [])
            validation_warnings.extend(ws)

        print(f"  Subsection drafting complete. {len(child_results)} children + 1 parent.")
        print(f"    Impacted page edits: {len(draft_result['impacted_page_edits'])}")

    else:
        # ── Standard single-page mode ──
        print("\n[Step 4] Running Drafting agent (two separate calls)...")
        print(f"  Passing: full source doc ({len(pm_doc)} chars), {len(vision_result)} vision descriptions,")
        print(f"           {len(bundle['asset_filenames'])} asset filenames, YouTube: {'Yes' if bundle['youtube_link'] else 'No'}, Transcript: {'Yes' if bundle['transcript'] else 'No'}")

        drafting_agent = DraftingAgent()
        draft_result = drafting_agent.draft(
            pm_doc=pm_doc,
            research_output=research_result,
            vision_output=vision_result,
            asset_filenames=bundle["asset_filenames"],
            youtube_link=bundle["youtube_link"],
            release_month=release_month,
            transcript=bundle["transcript"],
            exemplar=exemplar,
            mode=mode,
        )

        if "error" in draft_result:
            print(f"  Drafting agent FAILED: {draft_result['error']}")
            raw_path = PROJECT_ROOT / "output" / "draft_raw_response.txt"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(draft_result.get("raw_response", ""))
            print(f"  Raw response saved to {raw_path}")
            return {"status": "failed", "errors": [draft_result["error"]], "raw": draft_result}

        validation_warnings = draft_result.pop("_validation_warnings", [])
        if validation_warnings:
            print(f"\n  VALIDATION WARNINGS:")
            for w in validation_warnings:
                print(f"    ⚠ {w}")

        print("  Drafting agent done.")
        rc = draft_result.get("release_note", {}).get("content", "")
        dc = draft_result.get("doc_page", {}).get("content", "")
        print(f"    Release note: {draft_result.get('release_note', {}).get('filename', 'N/A')} ({len(rc)} chars)")
        print(f"    Doc page: {draft_result.get('doc_page', {}).get('filename', 'N/A')} ({len(dc)} chars)")
        print(f"    Impacted page edits: {len(draft_result.get('impacted_page_edits', []))}")

    # Step 5: Verification — fact-check against sources
    print("\n[Step 5] Running Verification agent...")
    verifier = VerificationAgent()

    if subsections and "_subsections" in draft_result:
        # Combine all child content for verification
        all_release_content = "\n\n---\n\n".join(
            cr["draft"].get("release_note", {}).get("content", "")
            for cr in draft_result["_subsections"]["children"]
        )
        all_doc_content = "\n\n---\n\n".join(
            cr["draft"].get("doc_page", {}).get("content", "")
            for cr in draft_result["_subsections"]["children"]
        )
        rc = all_release_content
        dc = all_doc_content
    else:
        rc = draft_result.get("release_note", {}).get("content", "")
        dc = draft_result.get("doc_page", {}).get("content", "")

    verification_result = verifier.verify(
        release_note_content=rc,
        doc_page_content=dc,
        pm_doc=pm_doc,
        corpus_chunks=research_result.get("relevant_chunks", []),
        transcript=bundle["transcript"],
    )

    verified = verification_result.get("verified", False)
    score = verification_result.get("faithfulness_score", 0)
    issues = verification_result.get("issues", [])
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    warning_issues = [i for i in issues if i.get("severity") == "warning"]

    print(f"  Faithfulness score: {score}/10")
    print(f"  Verified: {verified}")
    if critical_issues:
        print(f"  ❌ Critical issues ({len(critical_issues)}):")
        for i in critical_issues:
            print(f"    - [{i.get('location')}] {i.get('claim', '')[:80]}")
            print(f"      Reason: {i.get('reason', '')[:100]}")
    if warning_issues:
        print(f"  ⚠ Warnings ({len(warning_issues)}):")
        for i in warning_issues:
            print(f"    - [{i.get('location')}] {i.get('claim', '')[:80]}")

    # Step 6: Write outputs
    print("\n[Step 6] Writing outputs...")
    output_dir = PROJECT_ROOT / "output" / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy assets to output
    for asset_path in bundle["asset_paths"]:
        for sub in ["release_note", "doc_page"]:
            asset_dest = output_dir / sub / "assets"
            asset_dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset_path, asset_dest / Path(asset_path).name)
    if bundle["asset_paths"]:
        print(f"    Copied {len(bundle['asset_paths'])} assets to output/*/assets/")

    # Write release note
    release_note = draft_result.get("release_note", {})
    if release_note:
        release_path = output_dir / "release_note"
        release_path.mkdir(parents=True, exist_ok=True)
        filename = Path(release_note.get("filename", "release.md")).name
        full_content = f"{release_note.get('frontmatter', '')}\n\n{release_note.get('content', '')}"
        (release_path / filename).write_text(full_content)
        print(f"    Release note: {release_path / filename}")

    # Write doc page
    doc_page = draft_result.get("doc_page", {})
    if doc_page:
        doc_path = output_dir / "doc_page"
        doc_path.mkdir(parents=True, exist_ok=True)
        filename = Path(doc_page.get("filename", "doc.md")).name
        full_content = f"{doc_page.get('frontmatter', '')}\n\n{doc_page.get('content', '')}"
        (doc_path / filename).write_text(full_content)
        print(f"    Doc page: {doc_path / filename}")

    # Write impacted page edits
    edits = draft_result.get("impacted_page_edits", [])
    if edits:
        edits_path = output_dir / "impacted_edits"
        edits_path.mkdir(parents=True, exist_ok=True)
        (edits_path / "edits.json").write_text(json.dumps(edits, indent=2))
        print(f"    Impacted edits: {edits_path / 'edits.json'} ({len(edits)} pages)")

    # Write verification report
    (output_dir / "verification_report.json").write_text(json.dumps(verification_result, indent=2))
    print(f"    Verification: {output_dir / 'verification_report.json'}")

    # Write full pipeline output
    pipeline_output = {
        "bundle": bundle,
        "research": research_result,
        "vision": vision_result,
        "draft": draft_result,
        "verification": verification_result,
        "validation_warnings": validation_warnings,
        "timestamp": datetime.now().isoformat(),
    }
    (output_dir / "pipeline_output.json").write_text(json.dumps(pipeline_output, indent=2, default=str))
    print(f"    Full output: {output_dir / 'pipeline_output.json'}")

    # Step 7: Publish PR to GitHub (if token is set)
    from src.config import GITHUB_TOKEN
    pr_results = {}
    if not GITHUB_TOKEN:
        print("\n[Step 7] Skipping GitHub publish (GITHUB_TOKEN not set)")
    else:
        print("\n[Step 7] Publishing to GitHub...")
        if not verified:
            print(f"  ⚠ Verification flagged {len(critical_issues)} critical issues — included in PR for reviewer")
        from src.tools.github_publisher import GitHubPublisher
        feature_slug = Path(
            draft_result.get("release_note", {}).get("filename", "feature.md")
        ).stem
        try:
            publisher = GitHubPublisher()
            pr_results = publisher.publish(
                draft_result=draft_result,
                output_dir=str(output_dir),
                feature_slug=feature_slug,
                bundle_asset_paths=bundle["asset_paths"],
                release_month=release_month,
                requester_name=requester_name,
                requester_username=requester_username,
                slack_channel=slack_channel,
                slack_thread_ts=slack_thread_ts,
                mode=mode,
                subsections=draft_result.get("_subsections"),
                target_section=target_section,
            )
            if pr_results.get("releases_pr"):
                print(f"    Releases PR: {pr_results['releases_pr']}")
            if pr_results.get("docs_pr"):
                print(f"    Docs PR: {pr_results['docs_pr']}")
            if pr_results.get("errors"):
                print(f"    Errors:")
                for e in pr_results["errors"]:
                    print(f"      - {e}")
        except Exception as e:
            print(f"    GitHub publish failed: {e}")

    # Step 8: Auto-generate feature note for memory system
    _save_feature_note(draft_result, research_result, pm_doc, Path(bundle_path).name)

    print(f"\n{'=' * 60}")
    print(f"Pipeline complete!")
    print(f"Verification: {'✅ PASSED' if verified else '❌ FAILED'} (score: {score}/10)")
    print(f"Output: {output_dir}")
    print(f"{'=' * 60}")

    return {
        "status": "success" if verified else "verification_failed",
        "output_dir": str(output_dir),
        "validation_warnings": validation_warnings,
        "verification": verification_result,
        "pr_results": pr_results,
        "draft": draft_result,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", help="Path to input bundle directory")
    parser.add_argument("--mode", choices=["both", "release_only", "doc_only"],
                        default="both", help="Output mode")
    parser.add_argument("--requester-name", default="", help="Name of person who requested this run")
    parser.add_argument("--requester-username", default="", help="Username of requester")
    parser.add_argument("--subsections-json", default="", help="JSON string with subsections structure")
    args = parser.parse_args()
    sub = None
    if args.subsections_json:
        sub = json.loads(args.subsections_json)
    run_pipeline(args.bundle, mode=args.mode,
                 requester_name=args.requester_name,
                 requester_username=args.requester_username,
                 subsections=sub)
