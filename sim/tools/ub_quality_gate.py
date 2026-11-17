"""Deterministic /ub-mode quality gate: Crapper, Dryer, and Mutator.

Usage:
    python -m sim.tools.ub_quality_gate [--crap-max 10] [--dryer-max 0.8] [--skip-mutator]
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import copy
import hashlib
import importlib.abc
import importlib.util
import json
import subprocess
import sys
import trace
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SIM_DIR = Path(__file__).resolve().parent.parent
GENERATOR_DIR = SIM_DIR / "generator"
UI_DIR = SIM_DIR / "ui"
DEFAULT_SOURCE_DIRS: tuple[Path, ...] = (GENERATOR_DIR, UI_DIR)
REPO_ROOT = SIM_DIR.parent

MUTATION_TARGETS: dict[str, list[str]] = {
    "generator/calibration.py": ["tests/test_truth_and_eval.py::test_calibration_invariants_and_validation_failures"],
    "generator/evaluate.py": ["tests/test_truth_and_eval.py::test_evaluate_plan_baseline_and_regret"],
    "generator/corruptions.py": ["tests/test_corruptions.py"],
    "ui/api.py": ["tests/test_ui.py"],
}


@dataclass(frozen=True)
class FunctionMetrics:
    """Crapper metrics for a single function or method."""

    file: str
    name: str
    lineno: int
    cc: int
    covered_lines: int
    total_lines: int
    coverage: float
    crap: float


@dataclass(frozen=True)
class DuplicatePair:
    """Dryer similarity finding between two functions."""

    file_a: str
    func_a: str
    file_b: str
    func_b: str
    similarity: float


@dataclass(frozen=True)
class MutantResult:
    """Mutator execution result for a single AST mutation."""

    file: str
    func: str
    lineno: int
    description: str
    killed: bool


def _iter_direct_ast_nodes(root: ast.AST) -> Iterable[ast.AST]:
    """Yield AST nodes inside a function body without descending into nested defs."""
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def compute_cyclomatic_complexity(func_node: ast.AST) -> int:
    """Compute McCabe cyclomatic complexity for a FunctionDef/AsyncFunctionDef."""
    cc = 1
    branch_types = (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)
    for node in _iter_direct_ast_nodes(func_node):
        if isinstance(node, branch_types):
            cc += 1
        elif isinstance(node, ast.BoolOp):
            cc += max(0, len(node.values) - 1)
        elif isinstance(node, ast.comprehension):
            cc += len(node.ifs)
        elif isinstance(node, ast.match_case):
            cc += 1
    return cc


def _collect_functions(tree: ast.AST, filepath: Path) -> list[tuple[str, ast.AST]]:
    """Collect qualified function names and AST nodes in a module."""
    results: list[tuple[str, ast.AST]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}{child.name}"
                results.append((qual, child))
                visit(child, f"{qual}.")

    visit(tree, "")
    return results


def _executable_lines_by_func(source: str, filepath: Path) -> dict[tuple[str, int], set[int]]:
    """Extract executable bytecode line numbers per function via compile().co_lines()."""
    code = compile(source, str(filepath), "exec")
    line_map: dict[tuple[str, int], set[int]] = {}

    def walk_code(co: object) -> None:
        for const in getattr(co, "co_consts", ()):
            if hasattr(const, "co_lines") and hasattr(const, "co_firstlineno"):
                lines = {
                    ln
                    for _, _, ln in const.co_lines()
                    if ln is not None and ln > const.co_firstlineno
                }
                line_map[(const.co_name, const.co_firstlineno)] = lines
                walk_code(const)

    walk_code(code)
    return line_map


def _run_pytest_with_trace(test_paths: list[str]) -> dict[str, set[int]]:
    """Run pytest under Python's built-in trace module and return executed lines per file."""
    import pytest

    tracer = trace.Trace(count=True, trace=False)
    args = ["-q", "--tb=short", *test_paths]
    exit_code = tracer.runfunc(pytest.main, args)
    if exit_code != 0:
        raise RuntimeError(f"pytest failed with exit code {exit_code} during coverage trace")

    executed: dict[str, set[int]] = {}
    for (filename, lineno), count in tracer.results().counts.items():
        if count > 0:
            resolved = str(Path(filename).resolve())
            executed.setdefault(resolved, set()).add(lineno)
    return executed


def _resolve_source_dirs(generator_dir: Path | list[Path] | tuple[Path, ...] | None) -> list[Path]:
    """Normalize source_dir argument into an ordered list of existing directories."""
    if generator_dir is None:
        return [d for d in DEFAULT_SOURCE_DIRS if d.exists()]
    if isinstance(generator_dir, Path):
        return [generator_dir]
    return list(generator_dir)


def run_crapper(
    generator_dir: Path | list[Path] | tuple[Path, ...] | None = None,
    test_paths: list[str] | None = None,
    threshold: float = 10.0,
) -> list[FunctionMetrics]:
    """Compute CC, trace line coverage, and CRAP score for every function."""
    targets = test_paths or [str(REPO_ROOT / "tests")]
    executed_by_file = _run_pytest_with_trace(targets)
    metrics: list[FunctionMetrics] = []

    for src_dir in _resolve_source_dirs(generator_dir):
        for py_file in sorted(src_dir.glob("*.py")):
            resolved = str(py_file.resolve())
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=resolved)
            exec_map = _executable_lines_by_func(source, py_file)
            ran_lines = executed_by_file.get(resolved, set())
            rel_name = f"{src_dir.name}/{py_file.name}"

            for qual_name, func_node in _collect_functions(tree, py_file):
                cc = compute_cyclomatic_complexity(func_node)
                short_name = qual_name.rsplit(".", 1)[-1]
                func_lines = exec_map.get((short_name, func_node.lineno), set())
                total = len(func_lines)
                covered = len(func_lines & ran_lines)
                cov = 1.0 if total == 0 else covered / total
                crap = (cc ** 2) * ((1.0 - cov) ** 3) + cc
                metrics.append(
                    FunctionMetrics(
                        file=rel_name,
                        name=qual_name,
                        lineno=func_node.lineno,
                        cc=cc,
                        covered_lines=covered,
                        total_lines=total,
                        coverage=round(cov, 4),
                        crap=round(crap, 2),
                    )
                )
    return metrics


def _normalize_ast(node: ast.AST) -> ast.AST:
    """Strip variable/arg identifiers and locations to compare structural AST shape."""
    cloned = copy.deepcopy(node)
    for item in ast.walk(cloned):
        if isinstance(item, ast.Name):
            item.id = "VAR"
        elif isinstance(item, ast.arg):
            item.arg = "ARG"
        elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            item.name = "FUNC"
        for attr in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
            if hasattr(item, attr):
                delattr(item, attr)
    return cloned


def _walk_subtrees(cur: ast.AST, counts: Counter[str], min_nodes: int) -> tuple[int, str]:
    """Recursively hash AST subtrees of size >= min_nodes."""
    child_parts: list[str] = []
    size = 1
    for child in ast.iter_child_nodes(cur):
        c_size, c_hash = _walk_subtrees(child, counts, min_nodes)
        size += c_size
        child_parts.append(c_hash)
    payload = f"{type(cur).__name__}({','.join(child_parts)})"
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()
    if size >= min_nodes:
        counts[digest] += 1
    return size, digest


def _subtree_hashes(node: ast.AST, min_nodes: int = 4) -> Counter[str]:
    """Return multiset of structural hashes for AST subtrees with >= min_nodes."""
    counts: Counter[str] = Counter()
    _walk_subtrees(node, counts, min_nodes)
    return counts


def _strip_docstring(func_node: ast.AST) -> ast.AST:
    """Return a shallow-copied function node without its leading docstring."""
    cloned = copy.deepcopy(func_node)
    body = getattr(cloned, "body", [])
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        cloned.body = body[1:]
    return cloned


def _jaccard_counter(a: Counter[str], b: Counter[str]) -> float:
    """Weighted Jaccard similarity between two subtree multisets."""
    if not a and not b:
        return 0.0
    keys = set(a) | set(b)
    inter = sum(min(a[k], b[k]) for k in keys)
    union = sum(max(a[k], b[k]) for k in keys)
    return 0.0 if union == 0 else inter / union


def run_dryer(
    generator_dir: Path | list[Path] | tuple[Path, ...] | None = None,
    threshold: float = 0.8,
    min_statements: int = 4,
) -> list[DuplicatePair]:
    """Compare non-trivial functions across source dirs and return pairs >= threshold."""
    entries: list[tuple[str, str, Counter[str]]] = []
    for src_dir in _resolve_source_dirs(generator_dir):
        for py_file in sorted(src_dir.glob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            rel_name = f"{src_dir.name}/{py_file.name}"
            for qual_name, func_node in _collect_functions(tree, py_file):
                stripped = _strip_docstring(func_node)
                if len(getattr(stripped, "body", [])) < min_statements:
                    continue
                subtrees = _subtree_hashes(_normalize_ast(stripped))
                if sum(subtrees.values()) < 6:
                    continue
                entries.append((rel_name, qual_name, subtrees))

    duplicates: list[DuplicatePair] = []
    for idx, (file_a, func_a, sub_a) in enumerate(entries):
        for file_b, func_b, sub_b in entries[idx + 1 :]:
            sim = _jaccard_counter(sub_a, sub_b)
            if sim >= threshold:
                duplicates.append(
                    DuplicatePair(
                        file_a=file_a,
                        func_a=func_a,
                        file_b=file_b,
                        func_b=func_b,
                        similarity=round(sim, 3),
                    )
                )
    return duplicates


_BINOP_SWAPS: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.FloorDiv: ast.Mult,
}

_CMP_SWAPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.GtE,
    ast.GtE: ast.Lt,
    ast.Gt: ast.LtE,
    ast.LtE: ast.Gt,
}


def _describe_site(node: ast.AST) -> str | None:
    """Return mutation label if AST node is a mutable operator, else None."""
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOP_SWAPS:
        return f"{type(node.op).__name__}->{_BINOP_SWAPS[type(node.op)].__name__}"
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP_SWAPS:
        op_t = type(node.ops[0])
        return f"{op_t.__name__}->{_CMP_SWAPS[op_t].__name__}"
    if isinstance(node, ast.BoolOp):
        target = "Or" if isinstance(node.op, ast.And) else "And"
        return f"{type(node.op).__name__}->{target}"
    return None


def _apply_mutation(node: ast.AST) -> None:
    """Mutate an AST operator node in place."""
    if isinstance(node, ast.BinOp):
        node.op = _BINOP_SWAPS[type(node.op)]()
    elif isinstance(node, ast.Compare):
        node.ops[0] = _CMP_SWAPS[type(node.ops[0])]()
    elif isinstance(node, ast.BoolOp):
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()


def _collect_mutation_sites(tree: ast.AST) -> list[tuple[str, int, int, str]]:
    """Collect (func_name, node_index, lineno, description) mutation sites."""
    sites: list[tuple[str, int, int, str]] = []
    nodes_by_idx = list(ast.walk(tree))
    idx_lookup = {id(n): i for i, n in enumerate(nodes_by_idx)}

    for func_name, func_node in _collect_functions(tree, Path("<mem>")):
        for node in _iter_direct_ast_nodes(func_node):
            desc = _describe_site(node)
            if desc is not None:
                lineno = getattr(node, "lineno", func_node.lineno)
                sites.append((func_name, idx_lookup[id(node)], lineno, desc))
    return sites


def _test_mutated_module(module_name: str, mutated_source: str, test_files: list[str]) -> bool:
    """Run targeted pytest in a clean subprocess with in-memory module override."""
    bootstrap = (
        "import importlib.abc, importlib.util, sys, pytest\n"
        "sys.path.insert(0, '.')\n"
        "TARGET = sys.argv[1]\n"
        "TESTS = sys.argv[2:]\n"
        "SRC = sys.stdin.read()\n"
        "class _MutLoader(importlib.abc.Loader):\n"
        "    def create_module(self, spec): return None\n"
        "    def exec_module(self, mod): exec(compile(SRC, mod.__file__ or '<mut>', 'exec'), mod.__dict__)\n"
        "class _MutFinder(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, fullname, path, target=None):\n"
        "        if fullname == TARGET:\n"
        "            sys.meta_path.remove(self)\n"
        "            spec = importlib.util.find_spec(fullname, path)\n"
        "            spec.loader = _MutLoader()\n"
        "            return spec\n"
        "sys.meta_path.insert(0, _MutFinder())\n"
        "raise SystemExit(pytest.main(['-x', '-q', '--tb=no', '--no-header', *TESTS]))\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", bootstrap, module_name, *test_files],
            cwd=str(REPO_ROOT),
            input=mutated_source,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return proc.returncode != 0
    except subprocess.TimeoutExpired:
        return True


def _build_mutant_job(
    filename: str,
    module_name: str,
    tree: ast.AST,
    site: tuple[str, int, int, str],
    test_files: list[str],
) -> tuple[str, str, int, str, str, str, list[str]]:
    """Create mutated source payload for one mutation site."""
    func_name, node_idx, lineno, desc = site
    mutated_tree = copy.deepcopy(tree)
    target_node = list(ast.walk(mutated_tree))[node_idx]
    _apply_mutation(target_node)
    ast.fix_missing_locations(mutated_tree)
    return (filename, func_name, lineno, desc, module_name, ast.unparse(mutated_tree), test_files)


def run_mutator(
    generator_dir: Path | None = None,
    targets: dict[str, list[str]] | None = None,
    max_mutants_per_file: int | None = None,
    max_workers: int = 8,
) -> list[MutantResult]:
    """Run AST mutation testing on pure-logic modules."""
    target_map = targets or MUTATION_TARGETS
    base_dir = SIM_DIR if generator_dir is None else generator_dir
    jobs: list[tuple[str, str, int, str, str, str, list[str]]] = []

    for filename, test_files in sorted(target_map.items()):
        py_file = base_dir / filename
        if not py_file.exists():
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        sites = _collect_mutation_sites(tree)[:max_mutants_per_file]
        module_name = ".".join(py_file.resolve().relative_to(REPO_ROOT.resolve()).with_suffix("").parts)
        for site in sites:
            jobs.append(_build_mutant_job(filename, module_name, tree, site, test_files))

    def _run_job(job: tuple[str, str, int, str, str, str, list[str]]) -> MutantResult:
        fname, func_name, lineno, desc, mod_name, mut_src, tests = job
        killed = _test_mutated_module(mod_name, mut_src, tests)
        return MutantResult(file=fname, func=func_name, lineno=lineno, description=desc, killed=killed)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        return list(pool.map(_run_job, jobs))


def _print_mutator_section(mutants: list[MutantResult]) -> int:
    """Print Mutator summary and return survivor count."""
    if not mutants:
        print("[Mutator] skipped or no targets found")
        return 0
    survivors = [m for m in mutants if not m.killed]
    killed_count = len(mutants) - len(survivors)
    print(f"[Mutator] {killed_count}/{len(mutants)} mutants killed; survivors: {len(survivors)}")
    for surv in survivors:
        print(f"  SURVIVOR {surv.file}:{surv.func} (L{surv.lineno}) {surv.description}")
    return len(survivors)


def _print_report(
    crap_metrics: list[FunctionMetrics],
    crap_max: float,
    dryer_dups: list[DuplicatePair],
    dryer_max: float,
    mutants: list[MutantResult],
) -> bool:
    """Print summary report and return True if all quality gates pass."""
    crap_fails = [m for m in crap_metrics if m.crap >= crap_max]
    print(f"[Crapper] {len(crap_metrics)} functions analyzed; >= {crap_max}: {len(crap_fails)}")
    for item in sorted(crap_fails, key=lambda x: -x.crap):
        print(f"  FAIL {item.file}:{item.name} (L{item.lineno}) CC={item.cc} cov={item.coverage:.2f} CRAP={item.crap}")

    print(f"[Dryer] duplicate pairs >= {dryer_max}: {len(dryer_dups)}")
    for dup in dryer_dups:
        print(f"  FAIL {dup.file_a}:{dup.func_a} <-> {dup.file_b}:{dup.func_b} sim={dup.similarity}")

    survivor_count = _print_mutator_section(mutants)
    return not crap_fails and not dryer_dups and survivor_count == 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for /ub-mode quality gate."""
    parser = argparse.ArgumentParser(description="Run LAGRANGE Rx /ub-mode quality gates.")
    parser.add_argument("--crap-max", type=float, default=10.0)
    parser.add_argument("--dryer-max", type=float, default=0.8)
    parser.add_argument("--skip-mutator", action="store_true")
    parser.add_argument("--max-mutants", type=int, default=None)
    parser.add_argument("--tests", nargs="*", default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)

    crap_metrics = run_crapper(test_paths=args.tests, threshold=args.crap_max)
    dryer_dups = run_dryer(threshold=args.dryer_max)
    mutants = [] if args.skip_mutator else run_mutator(max_mutants_per_file=args.max_mutants)

    passed = _print_report(crap_metrics, args.crap_max, dryer_dups, args.dryer_max, mutants)
    if args.json_out is not None:
        payload = {
            "passed": passed,
            "crap": [m.__dict__ for m in crap_metrics],
            "dryer": [d.__dict__ for d in dryer_dups],
            "mutator": [m.__dict__ for m in mutants],
        }
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
