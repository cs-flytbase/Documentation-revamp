"""Ingestion pipeline — scrapes sites, chunks, embeds, upserts to Pinecone.

Usage:
    python -m src.ingest                    # Full run: both docs and releases
    python -m src.ingest --source docs      # Only docs.flytbase.com
    python -m src.ingest --source releases  # Only releases.flytbase.com
    python -m src.ingest --test             # Small test run (first 10 pages)
"""

import argparse
import json
import time
from pathlib import Path

from sentence_transformers import SentenceTransformer

from src.config import SETTINGS, PROJECT_ROOT
from src.tools.scraper import scrape_and_chunk
from src.tools.corpus_store import ensure_index, upsert_chunks, get_stats, get_existing_source_urls, delete_by_source_url


# -- URL lists (from sitemaps, hardcoded for reliability) --

DOCS_URLS = [
    "https://docs.flytbase.com",
    "https://docs.flytbase.com/introduction-to-flytbase/flytbase-advantage",
    "https://docs.flytbase.com/getting-started-with-your-flytbase-account/creating-a-flytbase-profile",
    "https://docs.flytbase.com/getting-started-with-your-flytbase-account/enterprise-single-sign-on-sso",
    "https://docs.flytbase.com/getting-started-with-your-flytbase-account/creating-an-organization",
    "https://docs.flytbase.com/getting-started-with-your-flytbase-account/joining-an-organization",
    "https://docs.flytbase.com/getting-started-with-your-flytbase-account/managing-your-organization",
    "https://docs.flytbase.com/getting-started-with-your-flytbase-account/roles-and-permissions",
    "https://docs.flytbase.com/getting-started-with-your-flytbase-account/site-management",
    "https://docs.flytbase.com/getting-started-with-your-flytbase-account/setup-private-cloud-storage-aws-s3",
    "https://docs.flytbase.com/navigating-flytbase/navigating-your-flytbase-platform",
    "https://docs.flytbase.com/maps-and-overlays/maps-on-flytbase",
    "https://docs.flytbase.com/maps-and-overlays/map-overlays",
    "https://docs.flytbase.com/device-management/dji-dock-3-+-flytbase-the-future-of-autonomous-ops-starts-here",
    "https://docs.flytbase.com/device-management/add-and-setup-your-device",
    "https://docs.flytbase.com/device-management/add-and-setup-your-device/register-your-dji-dock-3",
    "https://docs.flytbase.com/device-management/add-and-setup-your-device/register-your-dji-dock-1-and-2",
    "https://docs.flytbase.com/device-management/add-and-setup-your-device/register-your-hextronics-docks",
    "https://docs.flytbase.com/device-management/activating-device-subscription",
    "https://docs.flytbase.com/device-management/device-management",
    "https://docs.flytbase.com/device-management/device-management/dji-docks",
    "https://docs.flytbase.com/device-management/device-management/dji-docks/overview",
    "https://docs.flytbase.com/device-management/device-management/dji-docks/device-maintenance",
    "https://docs.flytbase.com/device-management/device-management/dji-docks/diagnostics",
    "https://docs.flytbase.com/device-management/device-management/dji-docks/device-logs",
    "https://docs.flytbase.com/device-management/device-management/dji-docks/firmware",
    "https://docs.flytbase.com/device-management/device-management/dji-docks/device-settings",
    "https://docs.flytbase.com/device-management/device-management/dji-docks/device-settings/drone-control",
    "https://docs.flytbase.com/device-management/device-management/dji-docks/device-settings/collision-avoidance-sensing-cas",
    "https://docs.flytbase.com/device-management/device-management/hextronics-docks",
    "https://docs.flytbase.com/device-management/device-management/hextronics-docks/first-flight-with-hextronics-atlas-hextronics-universal",
    "https://docs.flytbase.com/device-management/device-management/hextronics-docks/overview",
    "https://docs.flytbase.com/device-management/device-management/hextronics-docks/diagnostics",
    "https://docs.flytbase.com/device-management/device-management/hextronics-docks/settings-drone-and-dock",
    "https://docs.flytbase.com/device-management/device-management/hextronics-docks/settings-failsafes",
    "https://docs.flytbase.com/device-management/device-management/hextronics-docks/settings-precision-landing",
    "https://docs.flytbase.com/device-management/airsense",
    "https://docs.flytbase.com/pre-flight-modules/learn-more-about-failsafes",
    "https://docs.flytbase.com/pre-flight-modules/learn-more-about-failsafes/emergency-landing",
    "https://docs.flytbase.com/pre-flight-modules/learn-more-about-failsafes/alternate-landing-location",
    "https://docs.flytbase.com/pre-flight-modules/learn-more-about-failsafes/drc-visibility",
    "https://docs.flytbase.com/pre-flight-modules/platform-settings",
    "https://docs.flytbase.com/pre-flight-modules/platform-settings/preferences",
    "https://docs.flytbase.com/pre-flight-modules/platform-settings/flight-configuration",
    "https://docs.flytbase.com/pre-flight-modules/platform-settings/key-bindings",
    "https://docs.flytbase.com/pre-flight-modules/platform-settings/checklist",
    "https://docs.flytbase.com/pre-flight-modules/platform-settings/privacy",
    "https://docs.flytbase.com/pre-flight-modules/planning",
    "https://docs.flytbase.com/pre-flight-modules/planning/mission-planning",
    "https://docs.flytbase.com/pre-flight-modules/planning/mission-planning/path-mission",
    "https://docs.flytbase.com/pre-flight-modules/planning/mission-planning/grid-mission",
    "https://docs.flytbase.com/pre-flight-modules/planning/mission-planning/importing-a-mission-using-kml-file",
    "https://docs.flytbase.com/pre-flight-modules/planning/mission-planning/wpml-mission",
    "https://docs.flytbase.com/pre-flight-modules/planning/mission-planning/mission-breakpoint",
    "https://docs.flytbase.com/pre-flight-modules/planning/mission-management",
    "https://docs.flytbase.com/pre-flight-modules/planning/mission-scheduler",
    "https://docs.flytbase.com/pre-flight-modules/3d-mission-planning",
    "https://docs.flytbase.com/pre-flight-modules/3d-mission-planning/copy-paste-waypoints-across-missions",
    "https://docs.flytbase.com/pre-flight-modules/zones",
    "https://docs.flytbase.com/terrain-and-altitude-visualization/terrain-visualization",
    "https://docs.flytbase.com/terrain-and-altitude-visualization/terrain-visualization/terrain-visualization-for-missions",
    "https://docs.flytbase.com/terrain-and-altitude-visualization/terrain-visualization/terrain-visualization-for-go-to-location-gtl",
    "https://docs.flytbase.com/terrain-and-altitude-visualization/in-flight-altitude-visualization",
    "https://docs.flytbase.com/in-flight-modules/setting-up-your-first-flight",
    "https://docs.flytbase.com/in-flight-modules/flight-execution",
    "https://docs.flytbase.com/in-flight-modules/flight-execution/go-to-location-gtl",
    "https://docs.flytbase.com/in-flight-modules/annotations",
    "https://docs.flytbase.com/in-flight-modules/annotations/organizing-and-customizing-annotations-groups",
    "https://docs.flytbase.com/in-flight-modules/annotations/importing-annotations-from-kml-and-kmz",
    "https://docs.flytbase.com/in-flight-modules/annotations/customizing-annotations-fine-tune-annotations-for-operational-precision",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/multi-view-dashboard",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/multi-view-dashboard/real-time-fleet-monitoring",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/multi-view-dashboard/live-video-feed",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/multi-view-dashboard/interactive-3d-map-visualization",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/fleet-management",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/how-to-control-your-drone",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/how-to-control-your-drone/manual-drone-controls",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/how-to-control-your-drone/drone-access-control",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/payload-controls",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/payload-controls/manual-payload-controls",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/payload-controls/payload-access-control",
    "https://docs.flytbase.com/in-flight-modules/how-to-manage-your-flight-operations/payload-controls/nighttime-visibility-and-imaging-settings",
    "https://docs.flytbase.com/in-flight-modules/video-wall",
    "https://docs.flytbase.com/in-flight-modules/alerts-and-notifications",
    "https://docs.flytbase.com/in-flight-modules/speaker-and-spotlight",
    "https://docs.flytbase.com/in-flight-modules/live-mission-recorder",
    "https://docs.flytbase.com/in-flight-modules/guest-sharing-with-maps",
    "https://docs.flytbase.com/in-flight-modules/point-of-interest",
    "https://docs.flytbase.com/avss-parachute-integration",
    "https://docs.flytbase.com/post-flight-modules/reviewing-your-flight-logs",
    "https://docs.flytbase.com/post-flight-modules/gallery",
    "https://docs.flytbase.com/post-flight-modules/reports",
    "https://docs.flytbase.com/flinks-and-flows/flinks",
    "https://docs.flytbase.com/flinks-and-flows/flinks/flinks-alarms",
    "https://docs.flytbase.com/flinks-and-flows/flinks/flinks-live-streaming",
    "https://docs.flytbase.com/flinks-and-flows/flinks/flinks-live-streaming/introduction-to-secured-rtsp-streaming",
    "https://docs.flytbase.com/flinks-and-flows/flinks/flinks-live-streaming/introduction-to-secured-rtsp-streaming/security-and-network-considerations",
    "https://docs.flytbase.com/flinks-and-flows/flinks/flinks-live-streaming/introduction-to-secured-rtsp-streaming/setting-up-authenticated-rtsp-streaming",
    "https://docs.flytbase.com/flinks-and-flows/flinks/flinks-data-processing",
    "https://docs.flytbase.com/flinks-and-flows/flinks/flinks-data-processing/mapping-using-dronedeploy",
    "https://docs.flytbase.com/flinks-and-flows/flinks/flinks-data-processing/mapping-using-pix4d",
    "https://docs.flytbase.com/flinks-and-flows/flinks/flinks-data-processing/mapping-using-strayos",
    "https://docs.flytbase.com/flinks-and-flows/flows",
    "https://docs.flytbase.com/flinks-and-flows/flows/flows-alarms",
    "https://docs.flytbase.com/flinks-and-flows/flows/flows-operational-notifications",
    "https://docs.flytbase.com/discover-more/general-faqs",
    "https://docs.flytbase.com/discover-more/contact-technical-support",
]

RELEASES_URLS = [
    "https://releases.flytbase.com/april-2026/verkos-detect-anything-agents-updates/baseline-images-for-accurate-state-change-detection",
    "https://releases.flytbase.com/april-2026/verkos-detect-anything-agents-updates/site-pointers-teach-verkos-detect-anything-agent-what-it-gets-wrong-at-your-site",
    "https://releases.flytbase.com/april-2026/verkos-detect-anything-agents-updates/smarter-detection-event-configuration-for-verkos-detect-anything-agent",
    "https://releases.flytbase.com/april-2026/fleet-view-for-one-to-many-operations-direct-launch-smart-visibility-and-performance-at-scale",
    "https://releases.flytbase.com/april-2026/see-your-mission-in-context-launch-flow-moves-to-the-side-panel",
    "https://releases.flytbase.com/april-2026/airdata-integration-automatic-flight-log-sync",
    "https://releases.flytbase.com/april-2026/smart-missions-automatic-detection-on-every-mission-run",
    "https://releases.flytbase.com/march-2026/3d-mission-support-across-scheduler-flows-and-fleet-view",
    "https://releases.flytbase.com/march-2026/grid-mission-enhancements",
    "https://releases.flytbase.com/march-2026/flink-store",
    "https://releases.flytbase.com/february-2026/coverage-intelligence-beta",
    "https://releases.flytbase.com/february-2026/flytbase-integrates-with-alarispro-flight-logging-and-management",
    "https://releases.flytbase.com/february-2026/flytbase-integrates-with-senhive-counter-drone-detection-and-response",
    "https://releases.flytbase.com/february-2026/verkos-ai-detect-anything-agents",
    "https://releases.flytbase.com/february-2026/copy-paste-waypoints-across-missions",
    "https://releases.flytbase.com/february-2026/ai-gimbal-track-during-manual-flight",
    "https://releases.flytbase.com/january-2026/grid-missions-in-3d-mission-planner",
    "https://releases.flytbase.com/january-2026/enhanced-manual-control",
    "https://releases.flytbase.com/december-2025/native-dji-ai-detection-and-gimbal-track-for-dock-3",
    "https://releases.flytbase.com/december-2025/dji-as1-and-al1-payload-support",
    "https://releases.flytbase.com/december-2025/flytbase-one-non-docked-operations",
    "https://releases.flytbase.com/december-2025/payload-and-manual-control-enhancements",
    "https://releases.flytbase.com/november-2025/ai-spot-check",
    "https://releases.flytbase.com/november-2025/flytbase-ai-r-automated-gimbal-tracking",
    "https://releases.flytbase.com/november-2025/enhanced-low-light-operations-night-mode-ni-r-and-smart-low-light",
    "https://releases.flytbase.com/november-2025/point-of-interest-poi",
    "https://releases.flytbase.com/october-2025/ai-r-model-switching-and-confidence-control",
    "https://releases.flytbase.com/september-2025/issues-reporting-and-support",
    "https://releases.flytbase.com/september-2025/flytbase-video-streaming-updated-behavior",
    "https://releases.flytbase.com/september-2025/webhooks-for-media-and-mission-events",
    "https://releases.flytbase.com/september-2025/enterprise-sso",
    "https://releases.flytbase.com/june-2025/enhancements-to-cockpit-view",
    "https://releases.flytbase.com/june-2025/improved-annotations-grouping-import-and-visual-control-at-scale",
    "https://releases.flytbase.com/june-2025/introducing-secure-guest-sharing",
    "https://releases.flytbase.com/june-2025/ai-r-thermal-detections-and-real-time-email-alerts",
    "https://releases.flytbase.com/june-2025/3d-mission-planning",
    "https://releases.flytbase.com/june-2025/support-for-sniffer4d-nano-2",
    "https://releases.flytbase.com/may-2025/improvements-to-go-to-location-gtl-task-altitude",
    "https://releases.flytbase.com/introducing-fleet-view-2.0",
    "https://releases.flytbase.com/april-2025/introducing-point-cloud-and-elevation-map-overlays",
    "https://releases.flytbase.com/april-2025/introducing-reports-streamlined-shift-and-incident-reporting",
    "https://releases.flytbase.com/april-2025/choose-between-gnss-and-rtk-precision-for-mission-specific-requirements",
    "https://releases.flytbase.com/introducing-emergency-landing",
    "https://releases.flytbase.com/introducing-drc-visibility-enhanced-awareness-for-reliable-manual-control",
    "https://releases.flytbase.com/march-2025/introducing-avss-parachute-integration",
    "https://releases.flytbase.com/march-2025/improvements-to-thrustmaster-joystick-mid-flight-speed-adjustments",
    "https://releases.flytbase.com/march-2025/introducing-terrain-and-in-flight-altitude-visualizations",
    "https://releases.flytbase.com/january-2025/improvements-to-drone-deploy-flink",
    "https://releases.flytbase.com/december-2024/introducing-thermal-alert-based-flows",
    "https://releases.flytbase.com/november-2024/introducing-microsoft-login-option",
    "https://releases.flytbase.com/november-2024/introducing-thermal-alerts",
    "https://releases.flytbase.com/october-2024/improvements-in-login-magic-links-and-otp-authentication",
    "https://releases.flytbase.com/october-2024/introducing-live-streaming-flink-integration-with-genetec-and-milestone",
    "https://releases.flytbase.com/october-2024/introducing-video-wall-comprehensive-real-time-fleet-monitoring",
    "https://releases.flytbase.com/october-2024/flight-logs-update-gutma-format-support",
    "https://releases.flytbase.com/september-2024/new-features-and-improvements-go-to-safe-altitude-gtsa-and-control-panel-update",
    "https://releases.flytbase.com/august-2024/feature-updates-zones-and-unit-conversion",
    "https://releases.flytbase.com/july-2024/new-features-pix4d-flinks-integration-vvm-statistics-and-updates-to-gallery",
    "https://releases.flytbase.com/july-2024/new-features-3d-maps-operational-notifications-and-breakpoints-for-grid-missions",
    "https://releases.flytbase.com/june-2024/new-features-and-improvements-map-overlays-4g-lte-support-for-dji-docks-and-camera-controls",
    "https://releases.flytbase.com/may-2024/new-features-and-improvements-gallery-panorama-images-and-flinks-authentication",
    "https://releases.flytbase.com/april-2024/introducing-flinks-and-workflow-automation",
    "https://releases.flytbase.com/march-2024/introducing-support-for-dji-dock-2",
    "https://releases.flytbase.com/march-2024/enhanced-grid-mission-planning",
    "https://releases.flytbase.com/march-2024/new-feature-alerts-and-notifications",
    "https://releases.flytbase.com/february-2024/introducing-live-mission-recording",
    "https://releases.flytbase.com/january-2024/introducing-fly-zones-dji-airsense-drag-to-move-gimbal-and-enhancements-to-map-annotations",
    "https://releases.flytbase.com/january-2024/new-features-collision-avoidance-sensing-cas-and-minor-improvements",
    "https://releases.flytbase.com/december-2023/introducing-speaker-and-spotlight-payload-integration",
    "https://releases.flytbase.com/december-2023/introducing-sites-improved-drone-telemetry-interface-and-platform-optimizations",
    "https://releases.flytbase.com/december-2023/new-feature-audio-chat",
    "https://releases.flytbase.com/november-2023/improved-drone-and-payload-controls-thrustmaster-joystick-integration-and-annotations",
    "https://releases.flytbase.com/november-2023/introducing-drone-and-payload-control-access-transfer",
    "https://releases.flytbase.com/october-2023/team-visibility-see-whos-active-in-your-organization",
    "https://releases.flytbase.com/october-2023/optimized-mission-planning-and-scheduler-empowering-operators-with-enhanced-control",
    "https://releases.flytbase.com/october-2023/new-features-and-improvements",
    "https://releases.flytbase.com/september-2023/new-features-for-enhanced-safety-automated-missions-and-flight-control",
]


_embed_model = None

def get_embed_model() -> SentenceTransformer:
    """Lazy-load the embedding model (downloads on first use)."""
    global _embed_model
    if _embed_model is None:
        model_name = SETTINGS["pinecone"]["embedding_model"]
        print(f"  Loading embedding model: {model_name}")
        _embed_model = SentenceTransformer(model_name)
    return _embed_model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed texts locally using sentence-transformers."""
    model = get_embed_model()
    embeddings = model.encode(texts, show_progress_bar=True, normalize_embeddings=True)
    return embeddings.tolist()


def extract_source_date(url: str) -> str:
    """Extract date from release URL like /april-2026/... -> 2026-04."""
    import re
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    match = re.search(r"/(\w+)-(\d{4})/", url)
    if match:
        month_name, year = match.group(1).lower(), match.group(2)
        month_num = months.get(month_name, "00")
        return f"{year}-{month_num}"
    return ""


def run_ingestion(source: str = "all", test_mode: bool = False):
    """Main ingestion entry point."""
    print("=" * 60)
    print("FlytBase Documentation Corpus Ingestion")
    print("=" * 60)

    # Ensure index exists
    ensure_index()

    all_chunks = []

    if source in ("all", "docs"):
        urls = DOCS_URLS[:10] if test_mode else DOCS_URLS
        print(f"\n--- Scraping docs.flytbase.com ({len(urls)} pages) ---")
        doc_chunks = scrape_and_chunk(urls, source_type="doc")
        print(f"  Extracted {len(doc_chunks)} chunks from docs")
        all_chunks.extend(doc_chunks)

    if source in ("all", "releases"):
        urls = RELEASES_URLS[:10] if test_mode else RELEASES_URLS
        print(f"\n--- Scraping releases.flytbase.com ({len(urls)} pages) ---")
        release_chunks = scrape_and_chunk(urls, source_type="release")
        print(f"  Extracted {len(release_chunks)} chunks from releases")
        all_chunks.extend(release_chunks)

    if not all_chunks:
        print("\nNo chunks extracted. Nothing to embed.")
        return

    # Save raw chunks for inspection before embedding
    raw_path = PROJECT_ROOT / "output" / "raw_chunks.json"
    with open(raw_path, "w") as f:
        json.dump(all_chunks, f, indent=2)
    print(f"\nSaved {len(all_chunks)} raw chunks to {raw_path}")

    # Embed
    print(f"\n--- Embedding {len(all_chunks)} chunks ---")
    texts = [chunk["content"] for chunk in all_chunks]
    embeddings = embed_texts(texts)
    print(f"  Got {len(embeddings)} embeddings")

    # Prepare for upsert
    vectors = []
    for chunk, embedding in zip(all_chunks, embeddings):
        vectors.append({
            "id": chunk["chunk_id"],
            "values": embedding,
            "metadata": {
                "content": chunk["content"][:8000],  # Pinecone metadata limit
                "ia_node": chunk["ia_node"],
                "ia_label": chunk["ia_label"],
                "source_type": chunk["source_type"],
                "source_url": chunk["source_url"],
                "source_date": extract_source_date(chunk["source_url"]),
                "feature_tags": chunk["feature_tags"],
                "heading": chunk["heading"],
            },
        })

    # Upsert to Pinecone
    print(f"\n--- Upserting {len(vectors)} vectors to Pinecone ---")
    count = upsert_chunks(vectors)
    print(f"  Upserted {count} vectors")

    # Final stats
    time.sleep(2)  # Give Pinecone a moment to index
    stats = get_stats()
    print(f"\n--- Index stats ---")
    print(f"  Total vectors: {stats.total_vector_count}")
    print(f"  Dimension: {stats.dimension}")
    print("\nDone!")


def discover_sitemap_urls(base_url: str) -> list[str]:
    """Discover all page URLs from a GitBook-powered site's sitemap.

    Falls back to the hardcoded URL list if sitemap parsing fails.
    """
    import requests
    from bs4 import BeautifulSoup

    sitemap_url = f"{base_url.rstrip('/')}/sitemap.xml"
    try:
        resp = requests.get(sitemap_url, timeout=15, headers={"User-Agent": "FlytBase-Ingestion/1.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml-xml")
        urls = [loc.text.strip() for loc in soup.find_all("loc") if loc.text.strip()]
        if urls:
            print(f"  Discovered {len(urls)} URLs from sitemap: {sitemap_url}")
            return urls
    except Exception as e:
        print(f"  Could not fetch sitemap {sitemap_url}: {e}")

    return []


def run_incremental_refresh(source: str = "all"):
    """Incremental corpus refresh — only scrape and embed NEW or CHANGED pages.

    Compares current sitemap URLs against what's already in Pinecone.
    Only processes pages that are new (not in Pinecone) or have been updated.

    Usage:
        python -m src.ingest --refresh
        python -m src.ingest --refresh --source docs
        python -m src.ingest --refresh --source releases
    """
    print("=" * 60)
    print("FlytBase Corpus Refresh (Incremental)")
    print("=" * 60)

    ensure_index()

    # Step 1: Get URLs already in Pinecone
    print("\n--- Checking existing corpus ---")
    existing_urls = get_existing_source_urls()
    print(f"  Found {len(existing_urls)} URLs already in Pinecone")

    # Step 2: Discover current URLs from sitemaps + hardcoded lists
    new_urls_to_scrape = []

    if source in ("all", "docs"):
        sitemap_urls = discover_sitemap_urls("https://docs.flytbase.com")
        # Merge sitemap with hardcoded list (sitemap may have new pages)
        all_doc_urls = list(set(DOCS_URLS + sitemap_urls))
        new_docs = [u for u in all_doc_urls if u not in existing_urls]
        print(f"\n--- docs.flytbase.com ---")
        print(f"  Total known URLs: {len(all_doc_urls)}")
        print(f"  Already in corpus: {len(all_doc_urls) - len(new_docs)}")
        print(f"  New URLs to scrape: {len(new_docs)}")
        for u in new_docs:
            new_urls_to_scrape.append((u, "doc"))

    if source in ("all", "releases"):
        sitemap_urls = discover_sitemap_urls("https://releases.flytbase.com")
        all_release_urls = list(set(RELEASES_URLS + sitemap_urls))
        new_releases = [u for u in all_release_urls if u not in existing_urls]
        print(f"\n--- releases.flytbase.com ---")
        print(f"  Total known URLs: {len(all_release_urls)}")
        print(f"  Already in corpus: {len(all_release_urls) - len(new_releases)}")
        print(f"  New URLs to scrape: {len(new_releases)}")
        for u in new_releases:
            new_urls_to_scrape.append((u, "release"))

    if not new_urls_to_scrape:
        print("\n✅ Corpus is up to date. No new pages to ingest.")
        stats = get_stats()
        print(f"  Total vectors: {stats.total_vector_count}")
        return

    # Step 3: Scrape only the new URLs
    print(f"\n--- Scraping {len(new_urls_to_scrape)} new pages ---")
    all_chunks = []

    # Group by source_type for batch scraping
    doc_urls = [u for u, t in new_urls_to_scrape if t == "doc"]
    release_urls = [u for u, t in new_urls_to_scrape if t == "release"]

    if doc_urls:
        chunks = scrape_and_chunk(doc_urls, source_type="doc")
        print(f"  Extracted {len(chunks)} chunks from {len(doc_urls)} new doc pages")
        all_chunks.extend(chunks)

    if release_urls:
        chunks = scrape_and_chunk(release_urls, source_type="release")
        print(f"  Extracted {len(chunks)} chunks from {len(release_urls)} new release pages")
        all_chunks.extend(chunks)

    if not all_chunks:
        print("\nNo content extracted from new pages.")
        return

    # Step 4: Embed new chunks
    print(f"\n--- Embedding {len(all_chunks)} new chunks ---")
    texts = [chunk["content"] for chunk in all_chunks]
    embeddings = embed_texts(texts)
    print(f"  Got {len(embeddings)} embeddings")

    # Step 5: Prepare and upsert
    vectors = []
    for chunk, embedding in zip(all_chunks, embeddings):
        vectors.append({
            "id": chunk["chunk_id"],
            "values": embedding,
            "metadata": {
                "content": chunk["content"][:8000],
                "ia_node": chunk["ia_node"],
                "ia_label": chunk["ia_label"],
                "source_type": chunk["source_type"],
                "source_url": chunk["source_url"],
                "source_date": extract_source_date(chunk["source_url"]),
                "feature_tags": chunk["feature_tags"],
                "heading": chunk["heading"],
            },
        })

    print(f"\n--- Upserting {len(vectors)} new vectors to Pinecone ---")
    count = upsert_chunks(vectors)
    print(f"  Upserted {count} vectors")

    # Final stats
    time.sleep(2)
    stats = get_stats()
    print(f"\n--- Index stats ---")
    print(f"  Total vectors: {stats.total_vector_count}")
    print(f"  Dimension: {stats.dimension}")
    print(f"\n✅ Incremental refresh complete! Added {count} new vectors.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest FlytBase docs into Pinecone")
    parser.add_argument("--source", choices=["all", "docs", "releases"], default="all")
    parser.add_argument("--test", action="store_true", help="Test mode: only first 10 pages")
    parser.add_argument("--refresh", action="store_true",
                        help="Incremental refresh: only scrape new/changed pages")
    args = parser.parse_args()
    if args.refresh:
        run_incremental_refresh(source=args.source)
    else:
        run_ingestion(source=args.source, test_mode=args.test)
