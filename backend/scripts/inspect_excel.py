import argparse
from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.parsing.excel_loader import load_sheets


def main() -> None:
    parser = argparse.ArgumentParser(description="Print normalized workbook sheet rows.")
    parser.add_argument("path", help="Path to an .xlsx workbook.")
    parser.add_argument("--rows", default=5, type=int, help="Rows to print per sheet.")
    args = parser.parse_args()

    sheets = load_sheets(args.path)

    for sheet_name, grid in sheets.items():
        print(f"Sheet: {sheet_name}")
        for index, row in enumerate(grid[: args.rows], start=1):
            print(f"{index}: {row}")
        print()


if __name__ == "__main__":
    main()
