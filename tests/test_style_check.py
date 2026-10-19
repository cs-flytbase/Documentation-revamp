"""Mechanical style rules are enforced by code, however many there are."""
from src.style_check import autofix, errors, lint, load_rules
from src.style_guide import MAX_RULES, STYLE_RULES, feedback_block, pending_feedback


def _rules(issues):
    return {i.rule for i in issues}


def test_typography_and_product_names_are_fixed_automatically():
    text = "Flytbase now supports dock 2 — it’s “fast”… see Safe Sky."
    fixed, fixes = autofix(text)
    assert fixed == 'FlytBase now supports Dock 2, it\'s "fast"... see SafeSky.'
    assert fixes


def test_fixes_never_touch_code_urls_or_link_targets():
    text = ("See [the docs](https://docs.flytbase.com/a—b) and `flytbase --x` and "
            "https://github.com/flytbase/x ![m](assets/flytbase-map.png) {% embed url=\"https://youtu.be/a—b\" %}")
    fixed, _ = autofix(text)
    assert "https://docs.flytbase.com/a—b" in fixed
    assert "`flytbase --x`" in fixed
    assert "https://github.com/flytbase/x" in fixed
    assert "assets/flytbase-map.png" in fixed
    assert 'url="https://youtu.be/a—b"' in fixed


def test_director_banned_language_is_an_error():
    issues = lint("Imagine you are flying. This game-changer is seamless and takes the headache away.")
    msgs = " ".join(i.message for i in errors(issues))
    for phrase in ("Imagine you", "game-changer", "seamless", "headache"):
        assert phrase in msgs


def test_we_mfa_raw_html_slash_headings():
    text = "## Setup / Usage\n\nWe added MFA. Use <strong>this</strong>.\n"
    rules = _rules(errors(lint(text)))
    assert {"we", "forbidden_claim", "raw_html", "slash_heading"} <= rules


def test_description_length_is_checked():
    long_desc = "---\ndescription: >-\n  " + "x" * 160 + "\n---\n\n# T\n"
    assert any(i.rule == "description" for i in lint(long_desc))
    assert not any(i.rule == "description" for i in lint("---\ndescription: Short.\n---\n\n# T\n"))


def test_long_sentences_are_warnings_not_errors():
    issues = lint(" ".join(["word"] * 40) + ".")
    assert [i.rule for i in issues] == ["long_sentence"]
    assert not errors(issues)


def test_clean_release_text_passes():
    text = ("---\ndescription: >-\n  See zones on the live video.\n---\n\n# Zone Overlays\n\n"
            "Zone overlays draw no-fly zones on the live video. Turn on **Zone overlays** in **Video overlays**.\n")
    assert not errors(lint(text, "release"))


def test_rules_are_data_and_load():
    rules = load_rules()
    assert len(rules["banned_phrases"]) >= 20
    assert any(t["right"] == "FlytBase" for t in rules["terminology"])


def test_style_guide_is_capped():
    assert len(STYLE_RULES) <= MAX_RULES


def test_only_feedback_newer_than_the_migration_reaches_the_prompt(tmp_path):
    (tmp_path / "voice_corrections.md").write_text(
        "# Voice\n"
        "- [2026-08-19] [voice] Old rule already migrated.\n"
        "- [voice] Undated old rule.\n"
        + "".join(f"- [2026-10-{d:02d}] [voice] New rule {d}.\n" for d in range(19, 31))
        + "- [2026-11-02] [voice] Newest rule.\n"
    )
    items = pending_feedback(tmp_path)
    assert items[0] == "[voice] Newest rule."
    assert len(items) == 12  # capped
    assert not any("Old rule" in i or "Undated" in i for i in items)
    assert "RECENT REVIEWER FEEDBACK" in feedback_block(tmp_path)


def test_no_pending_feedback_means_no_block(tmp_path):
    assert feedback_block(tmp_path) == ""
