from __future__ import annotations

import sys
from pathlib import Path

from ensemble_merge import merge_stage
from ensemble_products import products_stage
from ensemble_runner import load_config, run_ensemble_stage


VALID_COMMANDS = {"ensemble", "merge", "products", "all"}


def _merge_strategy_from_config(cfg_path: str | Path) -> str:
    cfg = load_config(cfg_path)
    return str(cfg.get("postprocess", {}).get("merge_strategy", "final")).lower()


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in VALID_COMMANDS:
        print("Usage: python run_ensemble.py {ensemble|merge|products|all} <config.toml>")
        return 2

    command, cfg_path = argv[1], argv[2]

    if command == "ensemble":
        return run_ensemble_stage(cfg_path)

    if command == "merge":
        return merge_stage(cfg_path)

    if command == "products":
        return products_stage(cfg_path)

    # all
    rc = run_ensemble_stage(cfg_path)
    if rc != 0:
        return rc

    merge_strategy = _merge_strategy_from_config(cfg_path)
    if merge_strategy == "member":
        rc = merge_stage(cfg_path)
        if rc != 0:
            return rc

    return products_stage(cfg_path)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
