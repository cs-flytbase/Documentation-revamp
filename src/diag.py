"""Make every pipeline failure locatable from its Actions log.

Several steps catch an exception, keep only str(e) and carry on, so a run can
end "success" with "Docs repo failed: list index out of range" and no hint of
which file or line raised it. Son of Anton's diagnose_failure tool reads these
runs, and it can only point at code the log points at.

report_exception() keeps the old error string for callers, and also prints the
full traceback plus a GitHub ::error annotation carrying the project file and
line. install_excepthook() does the same for anything that is never caught.
"""
import sys
import traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_annotated: set[str] = set()


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")


def project_frame(tb) -> tuple[str, int] | None:
    """The innermost frame inside this repo, as (repo-relative path, line).

    Library frames (requests, openai, ...) are where an error surfaces, not
    where our code went wrong, so they are skipped.
    """
    found = None
    for frame in traceback.extract_tb(tb):
        try:
            rel = Path(frame.filename).resolve().relative_to(_REPO_ROOT)
        except ValueError:
            continue
        found = (rel.as_posix(), frame.lineno)
    return found


def annotate(message: str, title: str = "Pipeline error", file: str | None = None, line: int | None = None) -> None:
    """Emit a GitHub Actions error annotation. It does not fail the job."""
    props = [f"title={_escape_property(title)}"]
    if file:
        props.insert(0, f"file={_escape_property(file)}")
        if line:
            props.insert(1, f"line={line}")
    print(f"::error {','.join(props)}::{_escape_data(message)}", flush=True)
    _annotated.add(message)


def report_exception(context: str, exc: BaseException) -> str:
    """Log a caught exception with its traceback and location.

    Returns "context: exc", the same string the callers already put in their
    error lists, so the user-facing message does not change.
    """
    message = f"{context}: {exc}"
    print(f"[diag] {message}", flush=True)
    print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), end="", flush=True)
    where = project_frame(exc.__traceback__)
    annotate(
        f"{type(exc).__name__}: {exc}",
        title=context,
        file=where[0] if where else None,
        line=where[1] if where else None,
    )
    _annotated.add(message)
    return message


def annotate_errors(errors) -> None:
    """Annotate pipeline errors that did not come from report_exception."""
    for err in errors or []:
        if err and err not in _annotated:
            annotate(str(err))


def install_excepthook() -> None:
    """Annotate an uncaught exception with its location before the run dies."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        where = project_frame(tb)
        annotate(
            f"{exc_type.__name__}: {exc}",
            title="Uncaught exception",
            file=where[0] if where else None,
            line=where[1] if where else None,
        )
        previous(exc_type, exc, tb)

    sys.excepthook = hook
