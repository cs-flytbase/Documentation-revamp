"""Web scraper for docs.flytbase.com and releases.flytbase.com.

Fetches each page, extracts main content, splits into heading-based chunks.
Each chunk gets tagged with its IA node based on URL path matching.
"""

import hashlib
import re
import time

import requests
import yaml
from bs4 import BeautifulSoup
from pathlib import Path

from src.config import PROJECT_ROOT


def load_ia_structure() -> dict:
    """Load IA YAML and build a path-to-node lookup."""
    ia_path = PROJECT_ROOT / "config" / "ia_structure.yaml"
    with open(ia_path) as f:
        data = yaml.safe_load(f)

    lookup = {}

    def _walk(nodes, parent_label=""):
        for node in nodes:
            full_label = f"{parent_label} > {node['label']}" if parent_label else node["label"]
            lookup[node["path"]] = {
                "id": node["id"],
                "label": full_label,
            }
            if "children" in node:
                _walk(node["children"], full_label)

    _walk(data["ia_nodes"])
    return lookup


def match_ia_node(url: str, ia_lookup: dict) -> dict:
    """Find the best matching IA node for a URL.

    Tries longest path match first (most specific).
    """
    from urllib.parse import urlparse

    path = urlparse(url).path.rstrip("/")

    # Try exact match first, then progressively shorter paths
    best_match = None
    best_len = 0
    for ia_path, node_info in ia_lookup.items():
        if path == ia_path or path.startswith(ia_path + "/"):
            if len(ia_path) > best_len:
                best_match = node_info
                best_len = len(ia_path)

    if best_match:
        return best_match

    return {"id": "uncategorized", "label": "Uncategorized"}


def fetch_page(url: str, retries: int = 3, delay: float = 1.0) -> str | None:
    """Fetch a page and return the HTML. Retries on failure."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, timeout=15, headers={
                "User-Agent": "FlytBase-DocBot/1.0"
            })
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
            else:
                print(f"  Failed to fetch {url}: {e}")
                return None


def extract_content(html: str) -> list[dict]:
    """Extract content from HTML, split by headings.

    Returns a list of sections:
        [{"heading": "...", "level": 2, "text": "..."}]
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove nav, footer, sidebar, scripts
    for tag in soup.find_all(["nav", "footer", "script", "style", "aside"]):
        tag.decompose()

    # Try to find main content area
    main = soup.find("main") or soup.find("article") or soup.find("div", class_=re.compile(r"content|page|body", re.I))
    if not main:
        main = soup.body or soup

    sections = []
    current_heading = ""
    current_level = 0
    current_text = []

    for element in main.descendants:
        if element.name in ("h1", "h2", "h3", "h4"):
            # Save previous section
            if current_text:
                text = "\n".join(current_text).strip()
                if text:
                    sections.append({
                        "heading": current_heading,
                        "level": current_level,
                        "text": text,
                    })
            current_heading = element.get_text(strip=True)
            current_level = int(element.name[1])
            current_text = []
        elif element.name in ("p", "li", "td", "th", "pre", "code", "blockquote"):
            text = element.get_text(strip=True)
            if text and not any(element.find_parent(t) for t in ["p", "li", "td"]):
                current_text.append(text)

    # Don't forget the last section
    if current_text:
        text = "\n".join(current_text).strip()
        if text:
            sections.append({
                "heading": current_heading,
                "level": current_level,
                "text": text,
            })

    return sections


def make_chunk_id(url: str, heading: str) -> str:
    """Generate a deterministic chunk ID for dedup."""
    raw = f"{url}|{heading}"
    return hashlib.md5(raw.encode()).hexdigest()


def extract_feature_tags(text: str) -> list[str]:
    """Extract feature-related keywords from chunk text."""
    # Common FlytBase feature keywords
    keywords = [
        "mission", "dock", "drone", "flight", "payload", "gimbal",
        "annotation", "overlay", "map", "zone", "failsafe", "flink",
        "flow", "alarm", "rtsp", "streaming", "gallery", "report",
        "scheduler", "terrain", "altitude", "airsense", "cas",
        "verkos", "detection", "ai", "spot check", "video wall",
        "fleet", "speaker", "spotlight", "parachute", "sso",
        "3d", "grid", "path", "kml", "wpml", "breakpoint",
        "dronedeploy", "pix4d", "strayos", "hextronics",
    ]
    text_lower = text.lower()
    return [kw for kw in keywords if kw in text_lower]


def scrape_and_chunk(urls: list[str], source_type: str) -> list[dict]:
    """Scrape a list of URLs and return chunked data ready for embedding.

    Returns list of dicts:
        {
            "chunk_id": str,
            "content": str,
            "ia_node": str,
            "ia_label": str,
            "source_type": str,
            "source_url": str,
            "feature_tags": list[str],
            "heading": str,
        }
    """
    ia_lookup = load_ia_structure()
    all_chunks = []

    for i, url in enumerate(urls):
        print(f"  [{i+1}/{len(urls)}] Scraping {url}")
        html = fetch_page(url)
        if not html:
            continue

        sections = extract_content(html)
        ia_node = match_ia_node(url, ia_lookup)

        for section in sections:
            content = section["text"]
            if len(content) < 20:  # Skip trivially short sections
                continue

            heading = section["heading"] or "Introduction"
            chunk_id = make_chunk_id(url, heading)

            all_chunks.append({
                "chunk_id": chunk_id,
                "content": f"{heading}\n\n{content}" if heading else content,
                "ia_node": ia_node["id"],
                "ia_label": ia_node["label"],
                "source_type": source_type,
                "source_url": url,
                "feature_tags": extract_feature_tags(content),
                "heading": heading,
            })

    return all_chunks
