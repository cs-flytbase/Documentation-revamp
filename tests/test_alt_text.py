"""Alt text is one short sentence; the fact-checker does not check it (2026-10-22)."""
import sys
import types

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from src.agents.verification import strip_alt_text  # noqa: E402
from src.doc_template import _alt as doc_alt  # noqa: E402
from src.release_template import _alt as rel_alt  # noqa: E402

LONG = ("The settings panel shows Dock 3, AGL mode, a 90 m above-ground setting, 13.8 m/s flight speed, "
        "RTK positioning and the Dock 3 Terrain overlay labeled on the map. The grid is drawn over the site.")


def test_long_alt_text_becomes_one_short_sentence():
    for alt in (doc_alt(LONG, "x.png"), rel_alt(LONG, "x.png")):
        assert len(alt.split()) <= 15
        assert "grid is drawn" not in alt and "13.8" not in alt and "90 m" not in alt
        assert alt == "The settings panel shows Dock 3"
    assert doc_alt("The grid area selected on the 3D map.", "x.png") == "The grid area selected on the 3D map"
    assert doc_alt("", "zone-overlays-01.png") == "zone overlays 01"


def test_verification_input_has_no_alt_text():
    page = "Text 90 m.\n\n![The settings panel shows 13.8 m/s](assets/a.png)\n\n![x [y]](b.gif)"
    out = strip_alt_text(page)
    assert "13.8" not in out and out.count("![](") == 2 and "Text 90 m." in out


def test_verify_strips_alt_before_asking_the_model():
    from src.agents.verification import VerificationAgent
    seen = {}
    agent = VerificationAgent.__new__(VerificationAgent)
    agent._format_corpus_chunks = lambda c: ""
    agent.run = lambda prompt, **k: seen.setdefault("p", prompt) and '{"verified": true, "issues": [], "score": 10}'
    agent.verify("R ![Dock 3 at 90 m](assets/a.png)", "D ![13.8 m/s](assets/b.png)", "pm", [])
    assert "90 m" not in seen["p"] and "13.8" not in seen["p"]
