import asyncio
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db import get_db, connect_to_mongo, close_mongo_connection

async def main():
    await connect_to_mongo()
    db = get_db()
    
    docs = await db.documents.find().to_list(10)
    for doc in docs:
        print(doc["_id"])
        
    close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(main())
