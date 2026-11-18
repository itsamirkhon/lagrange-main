"""LAGRANGE Rx simulation package."""

from __future__ import annotations

from typing import Any

__all__ = [
    "evaluate_plan_on_truth",
    "get_baseline_plan",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from sim.generator import evaluate

        return getattr(evaluate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
