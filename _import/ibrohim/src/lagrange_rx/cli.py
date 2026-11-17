"""CLI to run the demo solver independently of the web UI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .demo_data import demo_case
from .models import PharmaCase
from .solver import optimize


def main() -> None:
    parser = argparse.ArgumentParser(description="Run LAGRANGE Rx MILP")
    parser.add_argument("--case", type=Path, help="Optional JSON PharmaCase (otherwise synthetic demo)")
    parser.add_argument("--risk", type=float, default=0.35, help="Risk aversion in [0,1]")
    parser.add_argument("--output", type=Path, help="Save JSON result to file")
    args = parser.parse_args()
    case = PharmaCase.model_validate_json(args.case.read_text(encoding="utf-8")) if args.case else demo_case()
    response = optimize(case, args.risk)
    text = json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
