import argparse
import json
from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.llm.client import LLMCallError, LLMConfigurationError
from app.llm.header_prompt import HeaderStructureParseError, detect_header_structure
from app.parsing.flatten import flatten_excel


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect workbook header structure with LLM.")
    parser.add_argument("path", help="Path to an .xlsx workbook.")
    args = parser.parse_args()

    try:
        flattened_text = flatten_excel(args.path)
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(str(error)) from error

    try:
        header_structure = detect_header_structure(flattened_text)
    except LLMConfigurationError as error:
        raise SystemExit(str(error)) from error
    except LLMCallError as error:
        raise SystemExit(f"LLM call failed: {error}") from error
    except HeaderStructureParseError as error:
        raise SystemExit(f"LLM response could not be parsed: {error}") from error

    print(json.dumps(header_structure, indent=2))


if __name__ == "__main__":
    main()
