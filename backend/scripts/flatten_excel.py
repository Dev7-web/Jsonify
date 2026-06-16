import argparse
from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.parsing.flatten import flatten_excel, flatten_excel_sections


def main() -> None:
    parser = argparse.ArgumentParser(description="Flatten an .xlsx workbook for the LLM.")
    parser.add_argument("path", help="Path to an .xlsx workbook.")
    parser.add_argument(
        "--sheet",
        help="Print only one sheet section by exact sheet name.",
    )
    args = parser.parse_args()

    if args.sheet:
        sections = flatten_excel_sections(args.path)
        for section in sections:
            if section["name"] == args.sheet:
                print(section["text"])
                return

        raise SystemExit(f"Sheet not found: {args.sheet}")

    print(flatten_excel(args.path))


if __name__ == "__main__":
    main()
