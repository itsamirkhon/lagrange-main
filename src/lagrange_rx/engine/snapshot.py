"""Reproducibility: Snapshot = hash of input, scenarios, seed, rules, engine and solver versions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Sequence

import numpy as np
import scipy
from pydantic import BaseModel

from lagrange_rx import __version__
from lagrange_rx.engine.types import Scenario
from lagrange_rx.rules import Rules


def _plain(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return _plain(obj.model_dump(mode="json", by_alias=True))
    if is_dataclass(obj) and not isinstance(obj, type):
        return _plain(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, float):
        return repr(obj)
    return obj


def stable_hash(obj: Any) -> str:
    text = json.dumps(_plain(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    input_hash: str
    scenarios_hash: str
    config_hash: str
    seed: int
    rules_version: str
    rules_sha256: str
    engine_version: str
    solver_version: str


def solver_version() -> str:
    return f"scipy-{scipy.__version__}/highs numpy-{np.__version__}"


def make_snapshot(graph: BaseModel, scenarios: Sequence[Scenario], seed: int, rules: Rules, config: Any) -> Snapshot:
    parts = dict(
        input_hash=stable_hash(graph), scenarios_hash=stable_hash(list(scenarios)), config_hash=stable_hash(config),
        seed=seed, rules_version=rules.version, rules_sha256=rules.sha256, engine_version=__version__,
        solver_version=solver_version(),
    )
    return Snapshot(snapshot_id=stable_hash(parts)[:24], **parts)
