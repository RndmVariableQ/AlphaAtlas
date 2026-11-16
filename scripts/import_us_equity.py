"""Import a folder of monthly CRSP/Compustat Parquet files for atlas run."""

import argparse
from pathlib import Path

from alpha_atlas.assets.us_equity import import_us_equity
from alpha_atlas.config import asset_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", type=Path, default=Path("atlas_data"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = root / asset_config(root, "us_equity")["data_dir"]
    try:
        result = import_us_equity(args.source, destination)
    except (ValueError, FileNotFoundError, FileExistsError) as error:
        parser.error(str(error))
    print(f"Imported {result['rows']:,} rows into {destination}")


if __name__ == "__main__":
    main()
