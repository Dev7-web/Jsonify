"""Bootstrap a local MongoDB for development.

Run from the backend/ directory:
    python scripts/setup_local_mongo.py
"""

import asyncio
from pathlib import Path
import sys
from uuid import uuid4


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db import close_mongo_connection, connect_to_mongo, ensure_indexes, get_db
from app.models import Document, DocumentSchema


async def create_collection_if_missing(name: str) -> None:
    db = get_db()
    existing_collections = await db.list_collection_names()

    if name not in existing_collections:
        await db.create_collection(name)


async def verify_insert_and_read() -> None:
    db = get_db()
    run_id = str(uuid4())

    document = Document(
        filename=f"local-setup-{run_id}.xlsx",
        file_type="xlsx",
        stored_path=f"uploads/local-setup-{run_id}.xlsx",
    )
    schema = DocumentSchema(
        name="Local Setup Schema",
        file_type="xlsx",
        fingerprint=f"local-setup-{run_id}",
        header_structure={"sheets": []},
    )

    document_payload = document.model_dump(by_alias=True)
    schema_payload = schema.model_dump(by_alias=True)

    try:
        await db.documents.insert_one(document_payload)
        await db.schemas.insert_one(schema_payload)

        stored_document = await db.documents.find_one({"_id": document_payload["_id"]})
        stored_schema = await db.schemas.find_one({"_id": schema_payload["_id"]})

        if stored_document is None or stored_schema is None:
            raise RuntimeError("MongoDB insert/read verification failed: record not found.")

        if stored_document["filename"] != document.filename:
            raise RuntimeError("MongoDB insert/read verification failed: document filename mismatch.")

        if stored_schema["fingerprint"] != schema.fingerprint:
            raise RuntimeError("MongoDB insert/read verification failed: schema fingerprint mismatch.")
    finally:
        await db.documents.delete_one({"_id": document_payload["_id"]})
        await db.schemas.delete_one({"_id": schema_payload["_id"]})


async def main() -> None:
    await connect_to_mongo()

    db = get_db()
    await create_collection_if_missing("documents")
    await create_collection_if_missing("schemas")
    await verify_insert_and_read()

    collections = sorted(await db.list_collection_names())
    schema_indexes = sorted((await db.schemas.index_information()).keys())

    print(f"database: {db.name}")
    print(f"collections: {', '.join(collections)}")
    print(f"schemas indexes: {', '.join(schema_indexes)}")
    print("insert/read: ok")

    close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())
