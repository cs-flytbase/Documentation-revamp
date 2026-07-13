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

1. Look at each image/GIF and describe WHAT it shows in detail.
2. Identify WHAT FEATURE or WORKFLOW the image demonstrates.
3. Extract KEYWORDS that describe the content (UI elements, actions, feature names).
4. Determine if it's a hero image (the main/first image for the page).
5. Read the PM document and identify which TOPIC in the document this image relates to.

Return a JSON array of objects, one per asset file. Each object:
{
  "file_name": "original filename",
  "description": "2-3 sentence description of what the image/GIF shows",
  "content_keywords": ["keyword1", "keyword2", "keyword3"],
  "feature_context": "One sentence explaining what feature or workflow this image demonstrates",
  "alt_text": "concise accessibility text",
  "is_hero": true/false
}

Rules:
- Be specific about UI elements visible (buttons, panels, maps, drones, modals, config screens).
- content_keywords should include: UI element names, feature names, action verbs, and any text visible in the image. These keywords will be used to match the image to the correct section in the document.
- feature_context should describe the PURPOSE of what's shown, not just what you see. Example: "Demonstrates the API key configuration workflow for SafeSky integration" NOT "Shows a modal with a text field".
- If it's a GIF, multiple key frames from the animation are provided in sequence. Describe the FULL workflow/action shown across all frames - what changes between frames, what the user is doing, and the end result.
- If it's a screenshot, describe the state of the UI and what's highlighted or annotated.
- is_hero is true ONLY for the first image if no [HERO GIF] marker exists in the PM doc.
- Consider the full set of images together - note how they relate to each other in sequence.
- Do NOT try to guess which section heading the image belongs under. Just describe what you see and let the drafting agent decide placement.
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

    def _extract_gif_frames(self, file_path: str) -> list[tuple[str, str]]:
        """Extract key frames from a GIF and return as base64-encoded PNGs.

        Samples frames evenly across the GIF duration so the LLM can
        understand the full workflow being demonstrated.

        Rules:
        - GIFs > 30 seconds are rejected (too long for meaningful frame sampling)
        - GIFs > 20 seconds get 10 frames
        - GIFs <= 20 seconds get 6 frames
        """
        from PIL import Image
        import io

        img = Image.open(file_path)
        total_frames = getattr(img, "n_frames", 1)

        if total_frames <= 1:
            return [self._encode_static(file_path)]

        # Estimate duration: sum frame durations (in ms)
        total_duration_ms = 0
        for i in range(total_frames):
            img.seek(i)
            total_duration_ms += img.info.get("duration", 100)
        duration_seconds = total_duration_ms / 1000

        # Reject GIFs longer than 30 seconds
        if duration_seconds > 30:
            print(f"  [Vision] WARNING: GIF {Path(file_path).name} is {duration_seconds:.1f}s — exceeds 30s limit. Using first frame only.")
            img.seek(0)
            frame = img.convert("RGBA")
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            data = base64.b64encode(buf.getvalue()).decode("utf-8")
            return [(data, "image/png")]

        # 10 frames for >20s, 6 frames for <=20s
        max_frames = 10 if duration_seconds > 20 else 6
        print(f"  [Vision] GIF duration: {duration_seconds:.1f}s → extracting {max_frames} frames")

        if total_frames <= max_frames:
            indices = list(range(total_frames))
        else:
            indices = [int(i * (total_frames - 1) / (max_frames - 1)) for i in range(max_frames)]

        frames = []
        for idx in indices:
            img.seek(idx)
            frame = img.convert("RGBA")
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            data = base64.b64encode(buf.getvalue()).decode("utf-8")
            frames.append((data, "image/png"))

        return frames

    def _encode_static(self, file_path: str) -> tuple[str, str]:
        """Read and base64-encode a static image file. Returns (base64_data, media_type)."""
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

    def _encode_image(self, file_path: str) -> tuple[str, str] | list[tuple[str, str]]:
        """Encode an image or GIF. For animated GIFs, returns multiple frames."""
        path = Path(file_path)
        if path.suffix.lower() == ".gif":
            frames = self._extract_gif_frames(file_path)
            if len(frames) > 1:
                return frames  # Multiple frames for animated GIF
            return frames[0]  # Single frame for static GIF
        return self._encode_static(file_path)

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

        # Encode all images — GIFs may produce multiple frames
        encoded_images = []
        gif_frame_counts = {}  # track which files are multi-frame GIFs
        for i, path in enumerate(file_paths):
            try:
                result = self._encode_image(path)
                if isinstance(result, list):
                    # Multi-frame GIF — store frames separately
                    gif_frame_counts[i] = len(result)
                    encoded_images.append(result[0])  # placeholder for indexing
                    print(f"  [Vision] GIF {filenames[i]}: extracted {len(result)} key frames")
                else:
                    encoded_images.append(result)
            except Exception as e:
                print(f"  [Vision] WARNING: Could not encode {Path(path).name}: {e}")
                encoded_images.append(None)

        # Filter out failed encodes but track indices
        valid_indices = [i for i, img in enumerate(encoded_images) if img is not None]
        valid_filenames = [filenames[i] for i in valid_indices]

        if not valid_indices:
            return [self._fallback_result(fn, markers, idx) for idx, fn in enumerate(filenames)]

        # Build flat image list — expand GIF frames inline
        valid_images = []
        for i in valid_indices:
            if i in gif_frame_counts:
                # Re-extract frames for this GIF
                frames = self._extract_gif_frames(file_paths[i])
                valid_images.extend(frames)
            else:
                valid_images.append(encoded_images[i])

        prompt_text = self._build_prompt(pm_doc, valid_filenames, markers)

        # Add GIF frame context to the prompt
        if gif_frame_counts:
            gif_notes = []
            for i in valid_indices:
                if i in gif_frame_counts:
                    gif_notes.append(
                        f"- {filenames[i]} is an animated GIF. {gif_frame_counts[i]} key frames "
                        f"are provided in sequence. Describe the FULL workflow shown across all frames."
                    )
            prompt_text += "\n\n## GIF Frame Notes\n" + "\n".join(gif_notes)

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
