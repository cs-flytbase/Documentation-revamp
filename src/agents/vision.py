"""Vision agent — describes images and GIFs from the input bundle.

One-shot agent. Takes a list of asset file paths, returns descriptions
of each asset: what it shows, what feature it relates to, where it might
belong in the final doc.
"""

import base64
import mimetypes
from pathlib import Path

from src.agents.base import BaseAgent

SYSTEM_PROMPT = """You are the Vision Agent for the FlytBase documentation pipeline.

Your job: given an image or GIF from a product release, describe what it shows
so the drafting agent knows how to use it in the documentation.

For each asset, return a JSON object:
{
  "file_name": "the original file name",
  "description": "2-3 sentence description of what the image/GIF shows",
  "feature_context": "what FlytBase feature or UI area this relates to",
  "suggested_placement": "where in the doc this asset should appear (e.g., 'after the configuration steps', 'in the overview section')",
  "alt_text": "concise alt text for accessibility"
}

Rules:
- Be specific about UI elements visible in the image (buttons, panels, maps, drones).
- If it's a GIF, describe the workflow/action being demonstrated.
- If it's a screenshot, describe the state of the UI and what's highlighted.
- Return ONLY valid JSON. No markdown wrapping.
"""


class VisionAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            name="vision",
            system_prompt=SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=1024,
        )

    def _encode_image(self, file_path: str) -> tuple[str, str]:
        """Read and base64-encode an image file. Returns (base64_data, media_type)."""
        path = Path(file_path)
        media_type, _ = mimetypes.guess_type(str(path))
        if not media_type:
            # Default based on extension
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

    def describe_asset(self, file_path: str, pm_context: str = "") -> dict:
        """Describe a single image/GIF asset."""
        import json

        base64_data, media_type = self._encode_image(file_path)
        file_name = Path(file_path).name

        prompt_text = f"File name: {file_name}\n\nContext from PM doc: {pm_context[:500] if pm_context else 'No additional context provided.'}\n\nDescribe this asset as specified in your instructions."

        raw_response = self.run_with_vision(prompt_text, base64_data, media_type)
        text = raw_response.strip()
        try:
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0]
            return json.loads(text)
        except json.JSONDecodeError:
            return {
                "file_name": file_name,
                "description": text,
                "feature_context": "unknown",
                "suggested_placement": "unknown",
                "alt_text": file_name,
            }

    def describe_all_assets(self, file_paths: list[str], pm_context: str = "") -> list[dict]:
        """Describe all assets in the input bundle."""
        results = []
        for path in file_paths:
            print(f"  [Vision] Describing {Path(path).name}")
            result = self.describe_asset(path, pm_context)
            results.append(result)
        return results
