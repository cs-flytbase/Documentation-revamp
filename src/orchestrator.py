"""Orchestrator — coordinates the full doc generation pipeline.

Usage:
    python -m src.orchestrator ./input_bundles/site-pointers
"""

import json
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

from src.config import PROJECT_ROOT, SETTINGS
from src.agents.research import ResearchAgent
from src.agents.vision import VisionAgent
from src.agents.drafting import DraftingAgent


def validate_input_bundle(bundle_path: str) -> dict:
    """Validate the input bundle has everything needed."""
    path = Path(bundle_path)
    result = {
        "valid": True,
        "pm_doc_path": None,
        "youtube_link": "",
        "asset_paths": [],
        "asset_filenames": [],
        "errors": [],
        "warnings": [],
    }

    # Find PM doc
    pm_candidates = list(path.glob("pm_doc.*")) + list(path.glob("pm_doc*.*"))
    if not pm_candidates:
        pm_candidates = list(path.glob("*.md")) + list(path.glob("*.txt"))

    if pm_candidates:
        result["pm_doc_path"] = str(pm_candidates[0])
    else:
        result["valid"] = False
        result["errors"].append("No PM document found")

    # Find YouTube link
    yt_file = path / "youtube_link.txt"
    if yt_file.exists():
        result["youtube_link"] = yt_file.read_text().strip()
    else:
        result["warnings"].append("No youtube_link.txt found")

    # Find Clueso transcript (optional)
    transcript_path = path / "transcript.md"
    if not transcript_path.exists():
        transcript_path = path / "transcript.txt"
    result["transcript"] = transcript_path.read_text().strip() if transcript_path.exists() else ""

    # Find assets
    asset_dir = path / "assets"
    search_dir = asset_dir if asset_dir.exists() else path
    for ext in ("*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp"):
        result["asset_paths"].extend([str(p) for p in search_dir.glob(ext)])

    result["asset_filenames"] = [Path(p).name for p in result["asset_paths"]]

    if not result["asset_paths"]:
        result["warnings"].append("No image/GIF assets found")

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


def run_pipeline(bundle_path: str) -> dict:
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
    print(f"  PM doc: {bundle['pm_doc_path']} ({len(pm_doc)} chars)")
    print(f"  Assets: {len(bundle['asset_paths'])} files — {bundle['asset_filenames']}")
    print(f"  YouTube: {bundle['youtube_link'] or 'None'}")
    print(f"  Transcript: {len(bundle['transcript'])} chars" if bundle['transcript'] else "  Transcript: None")

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
            futures[executor.submit(
                vision_agent.describe_all_assets,
                bundle["asset_paths"],
                pm_doc[:1000],
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
                        print(f"    - {v.get('file_name', '?')}: {v.get('description', '')[:80]}...")
            except Exception as e:
                print(f"  {agent_name} agent FAILED: {e}")
                if agent_name == "research":
                    return {"status": "failed", "errors": [f"Research agent failed: {e}"]}

    # Step 4: Drafting — pass EVERYTHING
    print("\n[Step 4] Running Drafting agent...")
    print(f"  Passing: full PM doc ({len(pm_doc)} chars), {len(vision_result)} vision descriptions,")
    print(f"           {len(bundle['asset_filenames'])} asset filenames, YouTube: {'Yes' if bundle['youtube_link'] else 'No'}, Transcript: {'Yes' if bundle['transcript'] else 'No'}")

    release_month = datetime.now().strftime("%B-%Y").lower()

    drafting_agent = DraftingAgent()
    draft_result = drafting_agent.draft(
        pm_doc=pm_doc,
        research_output=research_result,
        vision_output=vision_result,
        asset_filenames=bundle["asset_filenames"],
        youtube_link=bundle["youtube_link"],
        release_month=release_month,
        transcript=bundle["transcript"],
    )

    if "error" in draft_result:
        print(f"  Drafting agent FAILED: {draft_result['error']}")
        raw_path = PROJECT_ROOT / "output" / "draft_raw_response.txt"
        raw_path.write_text(draft_result.get("raw_response", ""))
        print(f"  Raw response saved to {raw_path}")
        return {"status": "failed", "errors": [draft_result["error"]], "raw": draft_result}

    # Show validation warnings
    validation_warnings = draft_result.pop("_validation_warnings", [])
    if validation_warnings:
        print(f"\n  VALIDATION WARNINGS:")
        for w in validation_warnings:
            print(f"    ⚠ {w}")

    print("  Drafting agent done.")
    print(f"    Release note: {draft_result.get('release_note', {}).get('filename', 'N/A')}")
    rc = draft_result.get('release_note', {}).get('content', '')
    print(f"    Release note length: {len(rc)} chars")
    print(f"    Doc page: {draft_result.get('doc_page', {}).get('filename', 'N/A')}")
    dc = draft_result.get('doc_page', {}).get('content', '')
    print(f"    Doc page length: {len(dc)} chars")
    print(f"    Impacted page edits: {len(draft_result.get('impacted_page_edits', []))}")

    # Step 5: Write outputs
    print("\n[Step 5] Writing outputs...")
    output_dir = PROJECT_ROOT / "output" / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Copy assets to output so images render in markdown viewers
    import shutil
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

    # Write full pipeline output
    pipeline_output = {
        "bundle": bundle,
        "research": research_result,
        "vision": vision_result,
        "draft": draft_result,
        "validation_warnings": validation_warnings,
        "timestamp": datetime.now().isoformat(),
    }
    (output_dir / "pipeline_output.json").write_text(json.dumps(pipeline_output, indent=2, default=str))
    print(f"    Full output: {output_dir / 'pipeline_output.json'}")

    # Step 6: Publish PR to GitHub (if token is set)
    from src.config import GITHUB_TOKEN
    pr_results = {}
    if GITHUB_TOKEN:
        print("\n[Step 6] Publishing to GitHub...")
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
    else:
        print("\n[Step 6] Skipping GitHub publish (GITHUB_TOKEN not set)")

    print(f"\n{'=' * 60}")
    print(f"Pipeline complete!")
    print(f"Output: {output_dir}")
    print(f"{'=' * 60}")

    return {
        "status": "success",
        "output_dir": str(output_dir),
        "validation_warnings": validation_warnings,
        "pr_results": pr_results,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", help="Path to input bundle directory")
    args = parser.parse_args()
    run_pipeline(args.bundle)
