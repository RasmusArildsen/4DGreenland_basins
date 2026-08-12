from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


TARGETS: dict[str, tuple[str, int]] = {
    "surf_100m": ("surface", 100),
    "bed_100m": ("bed", 100),
    "hybrid_100m": ("hybrid", 100),
    "surf_500m": ("surface", 500),
    "bed_500m": ("bed", 500),
    "hybrid_500m": ("hybrid", 500),
}


def _replace_key(text: str, key: str, value: str) -> str:
    pattern = rf'(?m)^{re.escape(key)}\s*=\s*.*$'
    replacement = f"{key} = {value}"
    if not re.search(pattern, text):
        raise KeyError(f"Could not find config key: {key}")
    return re.sub(pattern, replacement, text, count=1)


def write_target_config(
    *,
    template: Path,
    out_dir: Path,
    target_name: str,
    dem_mode: str,
    dem_res_m: int,
    n_members: int | None,
) -> Path:
    text = template.read_text(encoding="utf-8")
    text = _replace_key(text, "dem_mode", f'"{dem_mode}"')
    text = _replace_key(text, "dem_res_m", str(dem_res_m))
    if n_members is not None:
        text = _replace_key(text, "n_members", str(n_members))

    out_dir.mkdir(parents=True, exist_ok=True)
    target_config = out_dir / f"{target_name}.toml"
    target_config.write_text(text, encoding="utf-8")
    return target_config


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the six standard Greenland basin target configurations."
    )
    parser.add_argument(
        "--config",
        default="config.toml",
        help="Config template to copy and override per target.",
    )
    parser.add_argument(
        "--stage",
        choices=["ensemble", "merge", "products", "all"],
        default="all",
        help="Workflow stage to run for each target.",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        choices=sorted(TARGETS),
        default=list(TARGETS),
        help="Subset of targets to run.",
    )
    parser.add_argument(
        "--members",
        type=int,
        default=None,
        help="Override run.n_members in each generated target config.",
    )
    parser.add_argument(
        "--config-dir",
        default=".target_configs",
        help="Directory for generated per-target configs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without running them.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_dir = Path(__file__).resolve().parent
    template = Path(args.config)
    if not template.is_absolute():
        template = repo_dir / template
    if not template.exists():
        raise FileNotFoundError(f"Config template not found: {template}")

    config_dir = Path(args.config_dir)
    if not config_dir.is_absolute():
        config_dir = repo_dir / config_dir

    for target_name in args.targets:
        dem_mode, dem_res_m = TARGETS[target_name]
        target_config = write_target_config(
            template=template,
            out_dir=config_dir,
            target_name=target_name,
            dem_mode=dem_mode,
            dem_res_m=dem_res_m,
            n_members=args.members,
        )
        cmd = [sys.executable, str(repo_dir / "run_ensemble.py"), args.stage, str(target_config)]
        print(" ".join(cmd))
        if not args.dry_run:
            subprocess.run(cmd, cwd=repo_dir, check=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
