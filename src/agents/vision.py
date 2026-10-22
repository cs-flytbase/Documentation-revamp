"""Vision agent — describes images and GIFs from the input bundle.

Batch agent. Takes the full PM doc plus all asset file paths at once,
extracts screenshot/GIF markers from the PM doc, and returns structured
descriptions that the drafting agent can place precisely.
"""

import base64
import os
import subprocess
import time
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


VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".m4v")
BATCH_SIZE = 6      # assets per model call; one bad answer costs a batch, not the run
MAX_ATTEMPTS = 3    # per model, with exponential backoff


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
            max_tokens=4096,
        )
        self._sleep = time.sleep

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

    def _extract_video_frames(self, file_path: str) -> list[tuple[str, str]]:
        """Sample frames from a video with ffmpeg, as base64 PNGs.

        One frame every 2 seconds, between 6 and 12 frames, scaled to at most
        1280 px wide so on-screen text stays legible. Raises if ffmpeg is
        missing or the file cannot be read, so the caller reports it.
        """
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file_path],
            capture_output=True, text=True, timeout=60,
        )
        duration = float((probe.stdout or "0").strip() or 0)
        if duration <= 0:
            raise ValueError(f"could not read the duration of {Path(file_path).name}")
        count = max(6, min(12, int(duration // 2) or 1))
        frames = []
        for i in range(count):
            t = duration * (i + 0.5) / count
            shot = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", file_path, "-frames:v", "1",
                 "-vf", "scale='min(1280,iw)':-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                capture_output=True, timeout=60,
            )
            if shot.returncode == 0 and shot.stdout:
                frames.append((base64.b64encode(shot.stdout).decode("utf-8"), "image/png"))
        if not frames:
            raise ValueError(f"no frames could be extracted from {Path(file_path).name}")
        print(f"  [Vision] Video {Path(file_path).name}: {duration:.1f}s -> {len(frames)} frames")
        return frames

    def _encode_image(self, file_path: str) -> tuple[str, str] | list[tuple[str, str]]:
        """Encode an image, GIF or video. GIFs and videos return several frames."""
        path = Path(file_path)
        if path.suffix.lower() in VIDEO_SUFFIXES:
            frames = self._extract_video_frames(file_path)
            return frames if len(frames) > 1 else frames[0]
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

    def _call_vision_batch(self, prompt_text: str, encoded_images: list[tuple[str, str]], model: str | None = None) -> str:
        """Send the images plus the text prompt in one call."""
        model = model or self.model
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
                model=model,
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
                model=model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=self.system_prompt,
                messages=[{"role": "user", "content": content_parts}],
            )
            return response.content[0].text

    def describe_all_assets(self, file_paths: list[str], pm_doc: str = "") -> list[dict]:
        """Describe every asset, with the full PM doc as context. One dict per file, in order.

        Never fails and never returns a placeholder (P0, 2026-10-22). In the
        Cockpit 3.0 run all 13 descriptions came back as "Image asset: <file>":
        one call carried every image, its answer did not parse, and the
        placeholder became the published alt text. Now:
          - assets go in batches of BATCH_SIZE, so one bad answer costs a batch;
          - each batch is retried with backoff, then tried on VISION_FALLBACK_MODEL;
          - an asset that still has no description gets alt text built from its
            file name and the material around it, marked "_fallback" so the PR
            says so.
        """
        if not file_paths:
            return []
        filenames = [Path(p).name for p in file_paths]
        markers = _extract_markers(pm_doc) if pm_doc else []
        print(f"  [Vision] Processing {len(file_paths)} assets in batches of {BATCH_SIZE}")
        described: dict[str, dict] = {}
        for start in range(0, len(file_paths), BATCH_SIZE):
            described.update(self._describe_batch(file_paths[start:start + BATCH_SIZE], pm_doc, markers))
        out = []
        for i, name in enumerate(filenames):
            item = described.get(name)
            out.append(item if item else self._fallback_result(name, markers, i, pm_doc))
        return out

    def _describe_batch(self, paths: list[str], pm_doc: str, markers: list[dict]) -> dict[str, dict]:
        """{file name: description} for the files the model described."""
        names, images, notes = [], [], []
        for path in paths:
            name = Path(path).name
            try:
                enc = self._encode_image(path)
            except Exception as e:  # noqa: BLE001 - an unreadable file gets a fallback, not a crash
                print(f"  [Vision] WARNING: could not read {name}: {e}")
                continue
            names.append(name)
            if isinstance(enc, list):
                images.extend(enc)
                notes.append(f"- {name}: {len(enc)} key frames in sequence. Describe the FULL workflow across them.")
            else:
                images.append(enc)
        if not names:
            return {}
        prompt = self._build_prompt(pm_doc, names, markers)
        if notes:
            prompt += "\n\n## Animation and Video Frame Notes\n" + "\n".join(notes)

        fallback = os.environ.get("VISION_FALLBACK_MODEL", "").strip()
        models = [self.model] + ([fallback] if fallback and fallback != self.model else [])
        best: dict[str, dict] = {}
        for model in models:
            for attempt in range(MAX_ATTEMPTS):
                try:
                    raw = self._call_vision_batch(prompt, images, model=model)
                    got = self._parse_batch(raw, names)
                    if len(got) > len(best):
                        best = got
                    if len(best) == len(names):
                        return best
                    print(f"  [Vision] {model}: described {len(got)}/{len(names)} (attempt {attempt + 1})")
                except Exception as e:  # noqa: BLE001 - retried, then a fallback model, then context alt text
                    print(f"  [Vision] {model} failed (attempt {attempt + 1}): {e}")
                if attempt + 1 < MAX_ATTEMPTS:
                    self._sleep(2 ** attempt)
        return best

    @staticmethod
    def _parse_batch(raw: str, names: list[str]) -> dict[str, dict]:
        """Parse the model's JSON array; match objects by file name, then by position."""
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("["), text.rfind("]")
            if start == -1 or end <= start:
                return {}
            try:
                parsed = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return {}
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            return {}
        out: dict[str, dict] = {}
        loose = []
        for obj in parsed:
            if not isinstance(obj, dict) or not str(obj.get("description", "")).strip():
                continue
            if obj.get("file_name") in names and obj["file_name"] not in out:
                out[obj["file_name"]] = obj
            else:
                loose.append(obj)
        # Objects without a usable file name fill the remaining files in order,
        # only when the counts line up exactly (otherwise they could be swapped).
        rest = [n for n in names if n not in out]
        if loose and len(loose) == len(rest):
            for name, obj in zip(rest, loose):
                out[name] = {**obj, "file_name": name}
        return out

    def _fallback_result(self, filename: str, markers: list[dict], index: int, pm_doc: str = "") -> dict:
        """A usable description when the model could not describe the file.

        Built from the file name and the material that mentions it; never a
        placeholder like "Image asset: x.png". Marked "_fallback" so the PR
        asks a person to check the alt text.
        """
        heading = step_number = None
        is_hero = False
        if index < len(markers):
            heading = markers[index].get("heading")
            step_number = markers[index].get("step_number")
            is_hero = markers[index].get("marker_type") == "HERO GIF"
        text = context_alt(filename, pm_doc, heading)
        return {
            "file_name": filename,
            "description": text,
            "section_heading": heading or "",
            "step_number": step_number,
            "alt_text": text,
            "is_hero": is_hero,
            "_fallback": True,
        }


def _humanize(filename: str) -> str:
    stem = Path(filename).stem
    stem = re.sub(r"[-_]+", " ", stem)
    stem = re.sub(r"\b\d{1,2}\b", " ", stem)          # step numbers: "zone overlays 01 panel"
    stem = re.sub(r"\b(final|copy|v\d+|img|image|screenshot)\b", " ", stem, flags=re.I)
    stem = " ".join(stem.split())
    return stem[:1].upper() + stem[1:] if stem else "Feature screenshot"


def context_alt(filename: str, pm_doc: str = "", heading: str | None = None) -> str:
    """Alt text from the file name, plus the material's own words when it names the file."""
    base = _humanize(filename)
    for line in (pm_doc or "").splitlines():
        if filename in line or Path(filename).stem in line:
            words = re.sub(r"[`*_#>\[\]()]|!\[.*?\]", " ", line.replace(filename, ""))
            words = " ".join(words.split()).strip(" :-.")
            if len(words) >= 12:
                return f"{base}: {words[:120]}"
            break
    return f"{base} ({heading})" if heading else base
