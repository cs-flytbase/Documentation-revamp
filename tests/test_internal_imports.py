"""Every internal import must point at a module and a name that exist.

PR #34 deleted src/tools/scraper.py while src/agents/source.py still
imported it. CI stayed green (the tests stub the Source Agent) and every
Slack run died at its first stage (2026-10-22, run 39787718634). This check
needs no third-party packages, so it runs in CI as-is.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _module_file(dotted: str) -> Path | None:
    base = ROOT.joinpath(*dotted.split("."))
    for p in (base.with_suffix(".py"), base / "__init__.py"):
        if p.exists():
            return p
    return None


def _defined(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.If, ast.Try)):
            for sub in ast.walk(node):
                if isinstance(sub, (ast.FunctionDef, ast.ClassDef)):
                    names.add(sub.name)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for a in sub.names:
                        names.add((a.asname or a.name).split(".")[0])
                elif isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        if isinstance(t, ast.Name):
                            names.add(t.id)
    return names


def test_every_internal_import_resolves():
    broken = []
    for f in sorted((ROOT / "src").rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "src":
                target = _module_file(node.module)
                if target is None:
                    sub = [a.name for a in node.names if _module_file(f"{node.module}.{a.name}")]
                    if len(sub) != len(node.names):
                        broken.append(f"{f.relative_to(ROOT)}:{node.lineno} imports missing module {node.module}")
                    continue
                if target.name == "__init__.py":
                    continue
                have = _defined(target)
                for a in node.names:
                    if a.name != "*" and a.name not in have and not _module_file(f"{node.module}.{a.name}"):
                        broken.append(f"{f.relative_to(ROOT)}:{node.lineno} imports {a.name} from {node.module}, which does not define it")
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] == "src" and _module_file(a.name) is None:
                        broken.append(f"{f.relative_to(ROOT)}:{node.lineno} imports missing module {a.name}")
    assert not broken, "\n".join(broken)


def test_a_file_the_requester_excluded_is_never_downloaded_or_routed():
    from src.slack_run import build_material
    kv = {"content": "x", "assets": [{"url": "https://f/1", "name": "Screenshot-750.png"},
                                     {"url": "https://f/2", "name": "Screenshot-749.png"}],
          "intake": {"asset_audience": {"Screenshot-750.png": "none", "Screenshot-749.png": "both"}}}
    m = build_material(kv)
    assert [a["original"] for a in m["assets"]] == ["Screenshot-749.png"]
    assert "Screenshot-750.png" not in m["asset_audience"]
