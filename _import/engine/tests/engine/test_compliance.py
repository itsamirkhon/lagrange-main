"""Behaviour tests of the independent legality checker (engine/compliance.py)."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lagrange_rx.engine import compliance as c
from lagrange_rx.rules import load_rules
from tests.engine import compliance_suite as suite

RULES = load_rules()
ALLOWED_PREFIXES = ("lagrange_rx.ontology", "lagrange_rx.rules", "lagrange_rx.engine.types")
STDLIB = set(__import__("sys").stdlib_module_names)


@pytest.mark.parametrize("case", suite.CASES, ids=lambda f: f.__name__)
def test_boundary_case(case):
    case(c, RULES)


def test_whole_suite_green_on_real_module():
    assert suite.run_all(c, RULES) == []


def imported_modules(path: str) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(Path(path).read_text())):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    return mods


def test_imports_are_stdlib_or_contract_modules_only():
    for mod in imported_modules(c.__file__):
        assert mod.split(".")[0] in STDLIB or mod in ALLOWED_PREFIXES, mod


def test_route_limits_reported_once_per_route_not_per_scenario():
    g, plan = suite.graph(), suite.make_plan()
    one, two = suite.scen("a"), suite.scen("b")
    outs = [suite.outcome(suite.PRICE_OK, s, 1000.0, sbr={"R1": 1000.0}) for s in (one, two)]
    n1 = [r for r in c.check_plan(g, plan, [one], outs[:1], RULES) if r.rule_id == suite.LIMITS]
    n2 = [r for r in c.check_plan(g, plan, [one, two], outs, RULES) if r.rule_id == suite.LIMITS]
    assert len(n1) == len(n2) > 0 and {r.scenario_id for r in n2} == {"*"}


def test_is_legal_semantics():
    ok = suite.run(c, RULES, suite.PRICE_OK)
    assert c.is_legal(ok) and not c.is_legal(suite.run(c, RULES, 1e9))
    assert c.is_legal([])


def test_outcomes_matched_by_scenario_id():
    g, plan = suite.graph(), suite.make_plan()
    a, b = suite.scen("a"), suite.scen("b")
    good = suite.outcome(suite.PRICE_OK, a, 1000.0, sbr={"R1": 1000.0})
    bad = suite.outcome(1e9, b, 1000.0, sbr={"R1": 1000.0})
    res = c.check_plan(g, plan, [a, b], [bad, good], RULES)
    assert {r.scenario_id for r in res if not r.passed} == {"b"}


def test_check_result_reports_observed_and_limit():
    res = suite.run(c, RULES, 160_000.0)
    r = next(r for r in res if r.rule_id == suite.MARKUP)
    assert not r.passed and r.observed == 160_000.0 and r.limit == pytest.approx(153_525.0)
