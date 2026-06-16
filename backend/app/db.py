import os

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING

from .config import get_required_env


_client: AsyncIOMotorClient | None = None


def get_database_name() -> str:
    return os.getenv("MONGODB_DATABASE", "document_json_extractor")


def get_client() -> AsyncIOMotorClient:
    global _client

    if _client is None:
        _client = AsyncIOMotorClient(
            get_required_env("MONGODB_URI"),
            serverSelectionTimeoutMS=5000,
        )

    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_client()[get_database_name()]


async def ensure_indexes(db: AsyncIOMotorDatabase | None = None) -> None:
    database = db if db is not None else get_db()
    await database.schemas.create_index(
        [("fingerprint", ASCENDING), ("version", ASCENDING)],
        name="schemas_fingerprint_version_idx",
        unique=True,
    )


async def connect_to_mongo() -> None:
    try:
        client = get_client()
        await client.admin.command("ping")
        await ensure_indexes(client[get_database_name()])
    except Exception as error:
        raise RuntimeError(
            "Could not connect to MongoDB. "
            "Check MONGODB_URI in backend/.env and that the database is reachable."
        ) from error


def close_mongo_connection() -> None:
    global _client

    if _client is not None:
        _client.close()
        _client = None
