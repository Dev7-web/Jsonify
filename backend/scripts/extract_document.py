import argparse
import asyncio
import json
from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db import close_mongo_connection, connect_to_mongo
from app.pipeline.extract import ExtractError, extract_document_json


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract JSON from an approved Excel document using its matched schema."
    )
    parser.add_argument("document_id", help="Document _id to extract.")
    args = parser.parse_args()

    await connect_to_mongo()
    try:
        result = await extract_document_json(args.document_id)
    except ExtractError as error:
        raise SystemExit(str(error)) from error
    finally:
        close_mongo_connection()

    print(json.dumps(result["output_json"], indent=2, default=str))
    print()
    print(f"status: {result['status']}")
    print(f"schema_id: {result['schema_id']}")
    print(f"locators_created: {result['locators_created']}")


if __name__ == "__main__":
    asyncio.run(main())
