"""Run stages 1 → 2 → 3 in sequence with shared options.

    python -m pipeline.run_all                     # full pipeline
    python -m pipeline.run_all --from-stage 2      # skip the mirror
    python -m pipeline.run_all --smoke             # 2 partitions end to end
"""
from __future__ import annotations

import argparse
import sys

from pipeline import stage1_mirror, stage2_journals, stage3_keywords
from pipeline.lib.config import add_common_args


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    p.add_argument("--from-stage", type=int, choices=[1, 2, 3], default=1)
    p.add_argument("--smoke", action="store_true", help="Mirror and process only 2 works partitions")
    args = p.parse_args(argv)

    common: list[str] = ["--config", str(args.config)]
    for opt in ("storage_root", "snapshot_id", "run_id", "workers", "tmp_dir"):
        if (v := getattr(args, opt)) is not None:
            common += [f"--{opt.replace('_', '-')}", str(v)]

    if args.from_stage <= 1:
        s1 = stage1_mirror.main(common + (["--max-files", "2"] if args.smoke else []))
        if "--snapshot-id" not in common:
            common += ["--snapshot-id", s1["snapshot_id"]]
    if args.from_stage <= 2:
        stage2_journals.main(common + (["--max-partitions", "2"] if args.smoke else []))
    stage3_keywords.main(common)


if __name__ == "__main__":
    sys.exit(main())
