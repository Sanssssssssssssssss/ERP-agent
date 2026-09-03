"""Normalize ERP-Bench verifier details into Harbor 0.22's scalar reward contract."""

from __future__ import annotations

import argparse
import json
import math
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def adapt_erp_bench_reward(source: Path, output_dir: Path) -> dict[str, Any]:
    details = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(details, Mapping):
        raise ValueError("ERP-Bench reward details must be a JSON object")
    score = details.get("overall_score")
    if (
        isinstance(score, bool)
        or not isinstance(score, (int, float))
        or not math.isfinite(score)
    ):
        raise ValueError("ERP-Bench overall_score must be a finite number")

    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "reward.json").exists():
        raise FileExistsError(
            "output directory contains reward.json, which would shadow reward.txt"
        )

    details_path = output_dir / "verifier_details.json"
    reward_path = output_dir / "reward.txt"
    shutil.copyfile(source, details_path)
    reward_path.write_text(f"{score:g}\n", encoding="utf-8")
    return {
        "reward": score,
        "reward_path": str(reward_path.resolve()),
        "details_path": str(details_path.resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    print(json.dumps(adapt_erp_bench_reward(args.source, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
