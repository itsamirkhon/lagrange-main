"""Product/simulation boundary and single-source invariants (docs/MONOREPO.md, rule 3)."""
import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKIP = {".venv", "__pycache__", ".git", "node_modules", ".claude"}


def py_files(sub: str):
    for p in (ROOT / sub).rglob("*.py"):
        if not SKIP & set(p.parts):
            yield p


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def test_product_never_imports_sim():
    bad = [str(p.relative_to(ROOT)) for p in py_files("src") if "sim" in imported_roots(p)]
    assert not bad, bad


def test_sim_never_imports_product():
    bad = [str(p.relative_to(ROOT)) for p in py_files("sim") if "lagrange_rx" in imported_roots(p)]
    assert not bad, bad


def test_product_never_reads_truth_or_manifest_paths():
    pat = re.compile(r"sim/truth|truth/|manifest\.json|\"sim\"|'sim'")
    bad = []
    for p in py_files("src"):
        if pat.search(p.read_text(encoding="utf-8")):
            bad.append(str(p.relative_to(ROOT)))
    assert not bad, bad


def test_single_rules_catalog():
    found = [p for p in ROOT.rglob("uz_pharma_rules*.y*ml") if not SKIP & set(p.parts)]
    assert [p.relative_to(ROOT).as_posix() for p in found] == ["rules/uz_pharma_rules.yaml"]


def test_no_duplicate_domain_models():
    names = {"Product", "Route", "SupplyOption", "Supplier", "CostItem", "ClientGraph"}
    defs: dict[str, list[str]] = {}
    for p in py_files("src"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.ClassDef) and n.name in names:
                defs.setdefault(n.name, []).append(str(p.relative_to(ROOT)))
    dup = {k: v for k, v in defs.items() if len(v) > 1}
    assert not dup, dup
