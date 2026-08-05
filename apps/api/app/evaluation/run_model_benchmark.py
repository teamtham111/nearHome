"""CLI entry point for the bounded fair-price benchmark."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluation.benchmark import run_benchmark
from app.evaluation.data import DEFAULT_DATASET


def main() -> None:
    parser = argparse.ArgumentParser(description="Run NearHome fair-price model evaluation")
    parser.add_argument("--mode", choices=("smoke", "full"), default="full")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    output = run_benchmark(args.mode, args.dataset, args.output, args.seed)
    print(f"OUTPUT DIRECTORY: {output}")


if __name__ == "__main__":
    main()
