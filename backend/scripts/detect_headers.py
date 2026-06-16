import argparse
import json
from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.parsing.excel_loader import load_sheets
from app.parsing.header_detector import detect_table_headers


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect deterministic table headers.")
    parser.add_argument("path", help="Path to an .xlsx workbook.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON output instead of a readable summary.",
    )
    args = parser.parse_args()

    results = {
        sheet_name: detect_table_headers(grid)
        for sheet_name, grid in load_sheets(args.path).items()
    }

    if args.json:
        print(json.dumps(results, indent=2))
        return

    for sheet_name, headers in results.items():
        print(f"Sheet: {sheet_name}")

        if not headers:
            print("  No table headers detected.")
            print()
            continue

        for header in headers:
            title = header["title"] or "Untitled table"
            title_coordinate = header["title_coordinate"] or "n/a"
            print(
                f"  - {title} ({title_coordinate}): "
                f"header row {header['row_index']} at {header['coordinate']}"
            )
            columns = ", ".join(
                f"{column['name']} [{column['coordinate']}]"
                for column in header["columns"]
            )
            print(f"    Columns: {columns}")

        print()


if __name__ == "__main__":
    main()
