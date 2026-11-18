"""Command-line entry point to generate ground truth and L0-L4 worlds."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

from sim.generator.calibration import (
    build_calibration_report,
    save_calibration_report,
    validate_calibration,
)
from sim.generator.config_loader import load_levels_config, load_world_config
from sim.generator.doc_builders import build_world
from sim.generator.truth_gen import generate_truth

_DEFAULT_SIM_DIR = Path(__file__).resolve().parent.parent


def _reset_dir(path: Path) -> None:
    """Remove an existing directory cleanly and recreate it."""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def run_generation(sim_dir: Path | str = _DEFAULT_SIM_DIR, seed: int | None = None) -> dict[str, Any]:
    """Generate truth tables, validate calibration, and build L0-L4 worlds idempotently."""
    base_dir = Path(sim_dir)
    world_cfg = load_world_config(base_dir / "config" / "world.yaml")
    levels_cfg = load_levels_config(base_dir / "config" / "levels.yaml")
    effective_seed = int(world_cfg["seed"] if seed is None else seed)

    truth_dir = base_dir / "truth"
    worlds_dir = base_dir / "worlds"
    _reset_dir(truth_dir)
    _reset_dir(worlds_dir)

    truth_tables = generate_truth(world_cfg, effective_seed, truth_dir, base_dir / "inputs")
    report = build_calibration_report(truth_tables, world_cfg)
    validate_calibration(report, world_cfg)
    save_calibration_report(report, truth_dir / "calibration_report.json")

    manifests: dict[str, dict[str, Any]] = {}
    for level_name, level_cfg in levels_cfg.items():
        manifests[level_name] = build_world(
            level_name=level_name,
            level_cfg=level_cfg,
            world_cfg=world_cfg,
            truth_tables=truth_tables,
            out_world_dir=worlds_dir / level_name,
            seed=effective_seed,
        )
    return {"seed": effective_seed, "report": report, "manifests": manifests}


def _format_summary(result: dict[str, Any], sim_dir: Path) -> str:
    """Build concise CLI summary string."""
    rep = result["report"]
    lines = [
        f"LAGRANGE Rx simulation generated in {sim_dir} (seed={result['seed']})",
        (
            f"  Truth: annual_sales={rep['annual_sales_packs']} packs, "
            f"net_margin={rep['net_margin_ex_vat']:.4%}, "
            f"pre_reform_margin={rep['pre_reform_net_margin_ex_vat']:.4%}"
        ),
    ]
    for level_name, man in result["manifests"].items():
        docs = man.get("documents", [])
        n_corrs = sum(len(d.get("corruptions", [])) for d in docs)
        lines.append(f"  {level_name}: {len(docs)} documents, {n_corrs} corruptions")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments, execute simulation generation, and print summary."""
    parser = argparse.ArgumentParser(description="Generate LAGRANGE Rx truth and L0-L4 worlds.")
    parser.add_argument("--seed", type=int, default=None, help="Random seed (defaults to world.yaml seed).")
    parser.add_argument(
        "--sim-dir",
        type=Path,
        default=_DEFAULT_SIM_DIR,
        help="Path to sim/ directory (defaults to package sim/ directory).",
    )
    args = parser.parse_args(argv)
    result = run_generation(sim_dir=args.sim_dir, seed=args.seed)
    print(_format_summary(result, Path(args.sim_dir)))
    return 0
