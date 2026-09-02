"""Source agent — scrapes URLs, reads raw text, and synthesizes a PM document.

Takes any mix of URLs and raw text, scrapes web pages, and uses an LLM
to produce a clean, structured PM document that the rest of the pipeline
consumes.

Usage:
    from src.agents.source import SourceAgent
    agent = SourceAgent()
    result = agent.build_pm_doc(content="https://example.com\nSome raw text...", comments="Focus on BVLOS")
"""

import re
from urllib.parse import urlparse

from src.agents.base import BaseAgent
from src.tools.scraper import fetch_page, extract_content

SYSTEM_PROMPT = """You are the Source Agent for the FlytBase documentation pipeline.

Your job: take raw source material (scraped web pages, pasted text, or a mix)
and produce a clean, structured PM document that downstream agents can use
to generate release notes and documentation pages.

The PM document you produce must have these sections:
- # Feature Title
- ## Overview (what the feature does, 2-3 sentences)
- ## The Problem (why this feature matters, what pain it solves)
- ## How It Works (detailed breakdown of functionality, sub-sections with ### as needed)
- ## Key Specifications (table format if possible: Component | Details)
- ## Availability (how to access, who to contact, pricing if mentioned)
- ## Quotes (any executive quotes, attributed with name and title)

Rules:
- Use ONLY facts from the provided source material. Do NOT invent details.
- If the source material mentions numbers, measurements, or constraints, include them exactly.
- If multiple sources cover the same topic, merge and deduplicate the information.
- Use the operator's comments/instructions to guide emphasis and framing.
- Write in a neutral, factual tone. No marketing language.
- If information for a section is not available, include the heading with "Not specified in source material."
- Return ONLY the markdown PM document. No JSON wrapping, no commentary.
"""


class SourceAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="source",
            system_prompt=SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=8192,
        )

    def _parse_content(self, content: str) -> tuple[list[str], list[str]]:
        """Split content into URLs and raw text blocks.

        Lines that look like URLs are collected separately.
        Everything else is treated as raw text.
        """
        urls = []
        text_lines = []

        for line in content.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # Check if line is a URL
            if re.match(r'^https?://', line):
                urls.append(line)
            elif re.match(r'^www\.', line):
                urls.append(f"https://{line}")
            else:
                text_lines.append(line)

        return urls, text_lines

    # Video pages are embeds, not source material. Scraping a watch page pulls
    # in descriptions, comments and recommended-video titles, which pollutes the
    # PM doc and has caused the drafting model to refuse to generate at all.
    VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "loom.com", "wistia.com")

    def _scrape_urls(self, urls: list[str]) -> list[dict]:
        """Scrape URLs and return extracted content sections."""
        all_sections = []
        for i, url in enumerate(urls):
            if any(h in url.lower() for h in self.VIDEO_HOSTS):
                print(f"  [Source] Skipping video URL (embed, not source material): {url}")
                continue
            print(f"  [Source] Scraping [{i+1}/{len(urls)}]: {url}")
            html = fetch_page(url)
            if not html:
                print(f"  [Source] Failed to fetch: {url}")
                continue
            sections = extract_content(html)
            domain = urlparse(url).netloc
            for section in sections:
                if len(section["text"]) < 20:
                    continue
                all_sections.append({
                    "source": domain,
                    "heading": section["heading"],
                    "text": section["text"],
                })
            print(f"  [Source] Extracted {len(sections)} sections from {domain}")
        return all_sections

    def _scrape_reference(self, reference_link: str) -> str:
        """Scrape a reference link and return its content as a style exemplar."""
        if not reference_link:
            return ""
        print(f"  [Source] Scraping reference: {reference_link}")
        html = fetch_page(reference_link)
        if not html:
            return ""
        sections = extract_content(html)
        return "\n\n".join(
            f"{'#' * s['level']} {s['heading']}\n{s['text']}" if s['heading']
            else s['text']
            for s in sections
            if len(s['text']) >= 20
        )

    def build_pm_doc(
        self,
        content: str,
        comments: str = "",
        reference_link: str = "",
    ) -> dict:
        """Build a PM document from raw content.

        Args:
            content: Mixed URLs and raw text. URLs are scraped, text is kept as-is.
            comments: Operator instructions for emphasis/framing.
            reference_link: Optional URL to an existing release note for style reference.

        Returns:
            dict with keys: pm_doc (str), reference_exemplar (str)
        """
        print(f"  [Source] Parsing content ({len(content)} chars)...")

        # Parse content into URLs and text
        urls, text_lines = self._parse_content(content)
        print(f"  [Source] Found {len(urls)} URLs and {len(text_lines)} text lines")

        # Scrape URLs
        scraped_sections = []
        if urls:
            scraped_sections = self._scrape_urls(urls)

        # Scrape reference
        reference_exemplar = ""
        if reference_link:
            reference_exemplar = self._scrape_reference(reference_link)

        # Build the prompt
        source_material = ""

        if scraped_sections:
            source_material += "## SCRAPED WEB CONTENT\n\n"
            for s in scraped_sections:
                source_material += f"[Source: {s['source']}]\n"
                if s['heading']:
                    source_material += f"### {s['heading']}\n"
                source_material += f"{s['text']}\n\n"

        if text_lines:
            raw_text = "\n".join(text_lines)
            source_material += f"## RAW TEXT PROVIDED BY OPERATOR\n\n{raw_text}\n\n"

        if not source_material.strip():
            return {
                "pm_doc": "",
                "reference_exemplar": reference_exemplar,
                "error": "No content provided — no URLs to scrape and no raw text.",
            }

        prompt = f"""Synthesize the following source material into a structured PM document.

--- SOURCE MATERIAL ---
{source_material}
--- END SOURCE MATERIAL ---
"""

        if comments:
            prompt += f"""
--- OPERATOR INSTRUCTIONS ---
{comments}
--- END INSTRUCTIONS ---
"""

        if reference_exemplar:
            prompt += f"""
--- STYLE REFERENCE (write in a similar tone and structure) ---
{reference_exemplar[:3000]}
--- END REFERENCE ---
"""

        prompt += "\nProduce the PM document now."

        print(f"  [Source] Calling LLM to synthesize PM doc...")
        pm_doc = self.run(prompt)
        print(f"  [Source] PM doc generated ({len(pm_doc)} chars)")

        return {
            "pm_doc": pm_doc,
            "reference_exemplar": reference_exemplar,
        }
