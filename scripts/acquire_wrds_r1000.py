"""Thin WRDS CLI: provider code lives in alpha_atlas.assets.wrds."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--destination", type=Path, default=Path("data/us_equities"))
    parser.add_argument("--max-permnos", type=int, default=0)
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args()

    if args.env_file:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            parser.error(f"env file not found: {args.env_file}")
        load_dotenv(args.env_file, override=False)

    # Import after loading environment variables. Never print credentials.
    import wrds
    from alpha_atlas.assets.wrds import acquire

    db = wrds.Connection()
    try:
        manifest = acquire(
            db=db,
            destination=args.destination,
            start=args.start,
            end=args.end,
            max_permnos=args.max_permnos,
        )
        print(json.dumps({
            "destination": str(args.destination),
            "rows": manifest["rows"],
            "months": len(manifest["partitions"]),
            "status": manifest["status"],
            "purpose": manifest["purpose"],
        }, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
