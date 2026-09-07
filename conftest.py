"""Put the repository root on sys.path for tests.

Without this, `from src.doc_scope import ...` only resolves when pytest happens
to be run from the root by an interpreter that already has it on the path -
which is why the suite passed locally and failed in CI.
"""
import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
