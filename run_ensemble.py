"""Command-line entry point for the basin workflow."""

from __future__ import annotations

import sys

from ensemble_merge import merge_stage
from ensemble_products import products_stage
from ensemble_runner import run_ensemble_stage
from final_delivery import build_final_delivery, extract_individual_layers

USAGE = """Usage:
  python run_ensemble.py all      <config.toml>
  python run_ensemble.py products <config.toml>
  python run_ensemble.py ensemble <config.toml> [member | start end]
  python run_ensemble.py merge    <config.toml> [member | start end]
  python run_ensemble.py final-delivery <config.toml> <final.tif> <final.gpkg> <output_dir>
  python run_ensemble.py extract-individual <probability_stack.tif> <output_dir>
"""


def _member_range(arguments: list[str]) -> tuple[int | None, int | None]:
    if not arguments:
        return None, None
    if len(arguments) > 2:
        raise ValueError("Provide one member number or a start and end number")
    values = [int(value) for value in arguments]
    if any(value < 1 for value in values):
        raise ValueError("Member numbers must be positive")
    start, end = (values[0], values[0]) if len(values) == 1 else values
    if end < start:
        raise ValueError("The end member must be greater than or equal to the start")
    return start, end


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if arguments in (["--help"], ["-h"]):
        print(USAGE)
        return 0
    if arguments and arguments[0] == "final-delivery":
        if len(arguments) != 5:
            print(USAGE)
            return 2
        build_final_delivery(*arguments[1:])
        return 0
    if arguments and arguments[0] == "extract-individual":
        if len(arguments) != 3:
            print(USAGE)
            return 2
        extract_individual_layers(*arguments[1:])
        return 0
    if len(arguments) < 2 or arguments[0] not in {"ensemble", "merge", "products", "all"}:
        print(USAGE)
        return 2

    command, config_path, *member_arguments = arguments
    if command in {"products", "all"} and member_arguments:
        print(USAGE)
        return 2

    if command == "ensemble":
        start, end = _member_range(member_arguments)
        return run_ensemble_stage(config_path, start, end)
    if command == "merge":
        start, end = _member_range(member_arguments)
        return merge_stage(config_path, start, end)
    if command == "products":
        return products_stage(config_path)

    for stage in (run_ensemble_stage, merge_stage, products_stage):
        result = stage(config_path)
        if result:
            return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
