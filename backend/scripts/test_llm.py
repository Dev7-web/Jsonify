from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.llm.client import LLMConfigurationError, call_llm


def main() -> None:
    try:
        response = call_llm(
            "You are a health-check responder. Reply with exactly READY.",
            "Reply with the word READY.",
        )
    except LLMConfigurationError as error:
        raise SystemExit(str(error)) from error

    print(response)


if __name__ == "__main__":
    main()
