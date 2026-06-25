"""Vision agent — describes images and GIFs from the input bundle.

Batch agent. Takes the full PM doc plus all asset file paths at once,
extracts screenshot/GIF markers from the PM doc, and returns structured
descriptions that the drafting agent can place precisely.
"""

import base64
import json
import mimetypes
import re
from pathlib import Path

from src.agents.base import BaseAgent

SYSTEM_PROMPT = """You are the Vision Agent for the FlytBase documentation pipeline.

You receive the FULL PM document and ALL asset images/GIFs at once. Your job is to:

1. Parse the PM document for [SCREENSHOT], [GIF], and [HERO GIF] markers.
2. Match each marker to the nearest H2 (##) or H3 (###) heading above it in the PM doc.
3. If a marker falls inside a numbered step (e.g., "3. Click on..."), note the step number.
4. Look at each image provided and describe what it shows.
5. Correlate images to markers by filename hints, order, or visual content.

Return a JSON array of objects, one per asset file. Each object:
{
  "file_name": "original filename",
  "description": "2-3 sentence description of what the image/GIF shows",
  "section_heading": "the EXACT H2 or H3 heading text this image belongs under (from the PM doc)",
  "step_number": <integer step number if under a numbered step, otherwise null>,
  "alt_text": "concise accessibility text",
  "is_hero": true/false
}

Rules:
- Be specific about UI elements visible (buttons, panels, maps, drones, modals).
- If it's a GIF, describe the workflow/action being demonstrated and note sequencing relative to other GIFs.
- If it's a screenshot, describe the state of the UI and what's highlighted or annotated.
- section_heading must be copied EXACTLY from the PM doc — do not paraphrase or invent headings.
- is_hero is true ONLY for images marked with [HERO GIF] or the first [SCREENSHOT] if no hero marker exists.
- Consider the full set of images together — note how they relate to each other in sequence.
- Return ONLY valid JSON. No markdown wrapping, no commentary outside the JSON array.
"""


def _extract_markers(pm_doc: str) -> list[dict]:
    """Extract all [SCREENSHOT], [GIF], [HERO GIF] markers with their context.

    Returns a list of dicts with keys:
        marker_type, heading, step_number, line_index
    """
    lines = pm_doc.split("\n")
    markers = []
    current_heading = None
    current_step = None

    step_pattern = re.compile(r"^\s*(\d+)\.\s+")
    heading_pattern = re.compile(r"^(#{2,3})\s+(.+)")
    marker_pattern = re.compile(
        r"\[(SCREENSHOT|GIF|HERO\s*GIF)\]", re.IGNORECASE
    )

    for i, line in enumerate(lines):
        # Track headings
        heading_match = heading_pattern.match(line)
        if heading_match:
            current_heading = heading_match.group(2).strip()
            current_step = None  # reset step on new heading
            continue

        # Track numbered steps
        step_match = step_pattern.match(line)
        if step_match:
            current_step = int(step_match.group(1))

        # Check for markers
        for m in marker_pattern.finditer(line):
            marker_type = m.group(1).strip().upper()
            markers.append({
                "marker_type": marker_type,
                "heading": current_heading,
                "step_number": current_step,
                "line_index": i,
            })

    return markers


class VisionAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="vision",
            system_prompt=SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=2048,
        )

    def _encode_image(self, file_path: str) -> tuple[str, str]:
        """Read and base64-encode an image file. Returns (base64_data, media_type)."""
        path = Path(file_path)
        media_type, _ = mimetypes.guess_type(str(path))
        if not media_type:
            ext = path.suffix.lower()
            media_types = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }
            media_type = media_types.get(ext, "image/png")

        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        return data, media_type

    def _build_prompt(self, pm_doc: str, filenames: list[str], markers: list[dict]) -> str:
        """Build the text prompt with PM doc context and marker analysis."""
        marker_summary = ""
        if markers:
            marker_lines = []
            for i, m in enumerate(markers, 1):
                step_info = f", step {m['step_number']}" if m["step_number"] else ""
                marker_lines.append(
                    f"  {i}. [{m['marker_type']}] under heading: \"{m['heading']}\"{step_info}"
                )
            marker_summary = "Markers found in PM doc:\n" + "\n".join(marker_lines)
        else:
            marker_summary = "No explicit markers found in PM doc. Infer placement from headings and content."

        filenames_str = "\n".join(f"  {i+1}. {fn}" for i, fn in enumerate(filenames))

        return f"""## Full PM Document

{pm_doc}

---

## Asset Files (in order)

{filenames_str}

---

## Marker Analysis

{marker_summary}

---

Examine all {len(filenames)} images provided above. For each asset file, return a JSON object as specified.
Consider the images as a set — reason about their sequence and how they relate to each other.
Return a single JSON array with {len(filenames)} objects, in the same order as the asset files listed."""

    def _call_vision_batch(self, prompt_text: str, encoded_images: list[tuple[str, str]]) -> str:
        """Send all images plus text prompt in a single LLM call."""
        if self.provider in ("openai", "groq"):
            content_parts = []
            for base64_data, media_type in encoded_images:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{base64_data}",
                    },
                })
            content_parts.append({"type": "text", "text": prompt_text})

            response = self.client.chat.completions.create(
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": content_parts},
                ],
            )
            return response.choices[0].message.content
        else:
            # Anthropic
            content_parts = []
            for base64_data, media_type in encoded_images:
                content_parts.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64_data,
                    },
                })
            content_parts.append({"type": "text", "text": prompt_text})

            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=self.system_prompt,
                messages=[{"role": "user", "content": content_parts}],
            )
            return response.content[0].text

    def describe_all_assets(self, file_paths: list[str], pm_doc: str = "") -> list[dict]:
        """Describe all assets in a single batch call with full PM doc context.

        Args:
            file_paths: List of absolute paths to image/GIF files.
            pm_doc: The FULL PM document text (not truncated).

        Returns:
            List of description dicts, one per asset file.
        """
        if not file_paths:
            return []

        filenames = [Path(p).name for p in file_paths]
        markers = _extract_markers(pm_doc) if pm_doc else []

        print(f"  [Vision] Processing {len(file_paths)} assets in single batch call")
        print(f"  [Vision] PM doc length: {len(pm_doc)} chars, {len(markers)} markers found")

        # Encode all images
        encoded_images = []
        for path in file_paths:
            try:
                encoded_images.append(self._encode_image(path))
            except Exception as e:
                print(f"  [Vision] WARNING: Could not encode {Path(path).name}: {e}")
                encoded_images.append(None)

        # Filter out failed encodes but track indices
        valid_indices = [i for i, img in enumerate(encoded_images) if img is not None]
        valid_images = [encoded_images[i] for i in valid_indices]
        valid_filenames = [filenames[i] for i in valid_indices]

        if not valid_images:
            return [self._fallback_result(fn, markers, idx) for idx, fn in enumerate(filenames)]

        prompt_text = self._build_prompt(pm_doc, valid_filenames, markers)
        raw_response = self._call_vision_batch(prompt_text, valid_images)

        # Parse response
        results = self._parse_response(raw_response, valid_filenames, markers)

        # Re-insert fallbacks for any images that failed to encode
        if len(valid_indices) < len(filenames):
            full_results = []
            valid_iter = iter(results)
            for i, fn in enumerate(filenames):
                if i in valid_indices:
                    full_results.append(next(valid_iter))
                else:
                    full_results.append(self._fallback_result(fn, markers, i))
            return full_results

        return results

    def _parse_response(self, raw: str, filenames: list[str], markers: list[dict]) -> list[dict]:
        """Parse the LLM JSON response, with fallback handling."""
        text = raw.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                # Ensure we have the right count
                while len(parsed) < len(filenames):
                    idx = len(parsed)
                    parsed.append(self._fallback_result(filenames[idx], markers, idx))
                return parsed[:len(filenames)]
            elif isinstance(parsed, dict):
                # Single object returned — wrap it
                return [parsed] + [
                    self._fallback_result(fn, markers, i + 1)
                    for i, fn in enumerate(filenames[1:])
                ]
        except json.JSONDecodeError:
            pass

        # Complete fallback
        return [self._fallback_result(fn, markers, i) for i, fn in enumerate(filenames)]

    def _fallback_result(self, filename: str, markers: list[dict], index: int) -> dict:
        """Generate a fallback result when parsing fails."""
        heading = None
        step_number = None
        is_hero = False

        if index < len(markers):
            heading = markers[index].get("heading")
            step_number = markers[index].get("step_number")
            is_hero = markers[index].get("marker_type") == "HERO GIF"

        return {
            "file_name": filename,
            "description": f"Image asset: {filename}",
            "section_heading": heading or "Unknown",
            "step_number": step_number,
            "alt_text": filename,
            "is_hero": is_hero,
        }
