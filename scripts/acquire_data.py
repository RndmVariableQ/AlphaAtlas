"""Explicit data acquisition entrypoint; never invoked by automated tests."""

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("asset", choices=["ashare", "futures"])
    parser.add_argument("--start", type=date.fromisoformat, default=date(2015, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 1))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--merge", action="store_true", help="Merge futures partitions after download"
    )
    parser.add_argument(
        "--merge-only", action="store_true", help="Merge existing futures partitions"
    )
    parser.add_argument(
        "--far-contracts",
        action="store_true",
        help="Futures only: export main + two later maturities to data/futures_curve",
    )
    args = parser.parse_args()
    if args.far_contracts and args.asset != "futures":
        parser.error("--far-contracts is only supported for futures")
    if (args.merge or args.merge_only) and args.asset != "futures":
        parser.error("--merge/--merge-only is only supported for futures")
    destination = ROOT / "data" / ("futures_curve" if args.far_contracts else "futures")
    if args.merge_only:
        from alpha_atlas.assets.futures import merge_futures

        result = merge_futures(destination)
        print(f"merged {result['rows']:,} rows: {result['path']}", flush=True)
        return
    if args.asset == "ashare":
        from alpha_atlas.assets.ashare_rq import fetch_ashare

        result = fetch_ashare(
            ROOT / "data/ashare",
            args.start,
            args.end,
            env_file=args.env_file,
            root=ROOT,
            workers=args.workers,
        )
    else:
        from alpha_atlas.assets.futures import fetch_futures

        result = fetch_futures(
            destination,
            args.start,
            args.end,
            env_file=args.env_file,
            workers=args.workers,
            with_far_contracts=args.far_contracts,
        )
    print(f"acquisition status={result['status']}, rows={result.get('rows', 0):,}", flush=True)
    if args.merge:
        from alpha_atlas.assets.futures import merge_futures

        merged = merge_futures(destination)
        print(f"merged {merged['rows']:,} rows: {merged['path']}", flush=True)


if __name__ == "__main__":
    main()
