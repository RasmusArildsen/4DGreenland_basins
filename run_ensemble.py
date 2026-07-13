from __future__ import annotations

import sys
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parent / "src"
if PROJECT_SRC.exists() and str(PROJECT_SRC) not in sys.path:
    sys.path.insert(0, str(PROJECT_SRC))

try:
    from greenland_basins.ensemble_merge import merge_stage
    from greenland_basins.ensemble_products import products_stage
    from greenland_basins.ensemble_runner import load_config, run_ensemble_stage
except ModuleNotFoundError:
    from ensemble_merge import merge_stage
    from ensemble_products import products_stage
    from ensemble_runner import load_config, run_ensemble_stage


VALID_COMMANDS = {
    "ensemble",
    "ensemble-single",
    "ensemble-range",
    "merge",
    "products",
    "all",
    "single",
    "range",
}


def _merge_strategy_from_config(cfg_path: str | Path) -> str:
    cfg = load_config(cfg_path)
    return str(cfg.get("postprocess", {}).get("merge_strategy", "final")).lower()


def _parse_positive_int(raw: str, *, name: str) -> int:
    try:
        value = int(raw)
    except Exception as exc:
        raise SystemExit(f"{name} must be an integer, got: {raw}") from exc
    if value < 1:
        raise SystemExit(f"{name} must be >= 1, got: {value}")
    return value


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv
    if len(argv) < 3 or argv[1] not in VALID_COMMANDS:
        print(
            "Usage:\n"
            "  python run_ensemble.py {ensemble|merge|products|all} <config.toml>\n"
            "  python run_ensemble.py ensemble-single <config.toml> <member_index>\n"
            "  python run_ensemble.py ensemble-range  <config.toml> <start_index> <end_index>\n"
            "  python run_ensemble.py single <config.toml> <member_index>\n"
            "  python run_ensemble.py range  <config.toml> <start_index> <end_index>"
        )
        return 2

    command, cfg_path = argv[1], argv[2]

    if command == "ensemble":
        if len(argv) != 3:
            return 2
        return run_ensemble_stage(cfg_path)

    if command == "ensemble-single":
        if len(argv) != 4:
            return 2
        member_index = _parse_positive_int(argv[3], name="member_index")
        return run_ensemble_stage(cfg_path, start=member_index, end=member_index)

    if command == "ensemble-range":
        if len(argv) != 5:
            return 2
        start = _parse_positive_int(argv[3], name="start_index")
        end = _parse_positive_int(argv[4], name="end_index")
        if end < start:
            raise SystemExit(f"end_index must be >= start_index (got {start}..{end})")
        return run_ensemble_stage(cfg_path, start=start, end=end)

    if command == "merge":
        if len(argv) != 3:
            return 2
        return merge_stage(cfg_path)

    if command == "single":
        if len(argv) != 4:
            return 2
        member_index = _parse_positive_int(argv[3], name="member_index")
        return merge_stage(cfg_path, start=member_index, end=member_index)

    if command == "range":
        if len(argv) != 5:
            return 2
        start = _parse_positive_int(argv[3], name="start_index")
        end = _parse_positive_int(argv[4], name="end_index")
        if end < start:
            raise SystemExit(f"end_index must be >= start_index (got {start}..{end})")
        return merge_stage(cfg_path, start=start, end=end)

    if command == "products":
        if len(argv) != 3:
            return 2
        return products_stage(cfg_path)

    if len(argv) != 3:
        return 2

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
