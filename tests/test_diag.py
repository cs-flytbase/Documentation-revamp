"""Caught pipeline errors must still say where they happened."""
import re
import sys

from src import diag


def _raise_index_error():
    return [][0]


def test_report_exception_keeps_the_callers_message(capsys):
    try:
        _raise_index_error()
    except IndexError as e:
        message = diag.report_exception("Docs repo failed", e)
    assert message == "Docs repo failed: list index out of range"


def test_report_exception_logs_traceback_and_located_annotation(capsys):
    try:
        _raise_index_error()
    except IndexError as e:
        diag.report_exception("Docs repo failed", e)
    out = capsys.readouterr().out
    assert "Traceback (most recent call last):" in out
    annotation = re.search(r"^::error (.*?)::(.*)$", out, re.M)
    assert annotation, out
    props, body = annotation.groups()
    assert "file=tests/test_diag.py" in props
    assert re.search(r"line=\d+", props)
    assert "title=Docs repo failed" in props
    assert body == "IndexError: list index out of range"


def test_annotation_escapes_newlines_and_property_separators(capsys):
    diag.annotate("line one\nline two", title="a: b, c")
    out = capsys.readouterr().out.strip()
    assert out == "::error title=a%3A b%2C c::line one%0Aline two"


def test_annotate_errors_skips_ones_already_reported(capsys):
    try:
        _raise_index_error()
    except IndexError as e:
        reported = diag.report_exception("Releases repo failed", e)
    capsys.readouterr()
    diag.annotate_errors([reported, "pipeline status: failed (no reason reported)"])
    out = capsys.readouterr().out
    assert out.count("::error") == 1
    assert "pipeline status: failed" in out


def test_project_frame_ignores_library_frames():
    try:
        import json
        json.loads("{not json")
    except ValueError as e:
        where = diag.project_frame(e.__traceback__)
    assert where[0] == "tests/test_diag.py"


def test_excepthook_annotates_then_defers(capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: seen.append(a))
    diag.install_excepthook()
    try:
        _raise_index_error()
    except IndexError as e:
        sys.excepthook(type(e), e, e.__traceback__)
    out = capsys.readouterr().out
    assert "title=Uncaught exception" in out and "file=tests/test_diag.py" in out
    assert len(seen) == 1
