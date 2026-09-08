"""The pipeline workflow embeds Python inside a shell script inside YAML.

That nesting has broken the pipeline twice: once when a `\\n` escape was
flattened, and once when an unescaped double quote inside `python3 -c "..."`
closed the shell string early and bash tried to parse `(no reason reported)`.
The second one shipped to main and broke every run until someone tried to use
it.

Nothing caught either, because nothing ever parsed the embedded code. These
tests do.
"""
import re
import textwrap
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/slack-pipeline.yml"
SRC = WORKFLOW.read_text()


def _python_blocks():
    blocks = re.findall(r"python3 <<'PYEOF'\n(.*?)\n\s*PYEOF\n", SRC, re.S)
    assert blocks, "no heredoc python blocks found - did the invocation style change?"
    return blocks


def test_embedded_python_actually_compiles():
    """The check that would have caught the outage."""
    for i, block in enumerate(_python_blocks(), 1):
        compile(textwrap.dedent(block), f"<workflow block {i}>", "exec")


def test_python_is_never_interpolated_into():
    """A GitHub expression inside the Python source is how a quote from Slack
    becomes a syntax error, or worse. Values arrive through the environment."""
    for i, block in enumerate(_python_blocks(), 1):
        assert "${{" not in block, (
            f"block {i} interpolates a GitHub expression into Python source; "
            "pass it through env: and read os.environ instead"
        )


def test_no_fragile_python_dash_c():
    """`python3 -c "..."` requires every inner double quote to be escaped by
    hand. That is what broke. Heredocs need no escaping at all."""
    assert 'python3 -c "' not in SRC


def test_payload_values_reach_python_through_the_environment():
    for name in ("PL_DOC_SCOPE", "PL_MODE", "PL_TARGET_SECTION"):
        assert name in SRC, f"{name} is not passed to the pipeline step"


def test_doc_scope_still_reaches_run_pipeline():
    """doc_scope crosses five hops; this is the one inside the workflow."""
    assert "PL_DOC_SCOPE" in SRC
    assert "doc_scope=doc_scope" in SRC
