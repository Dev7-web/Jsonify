import asyncio
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db import get_db, connect_to_mongo, close_mongo_connection

async def main():
    await connect_to_mongo()
    db = get_db()
    
    doc = await db.documents.find_one({"_id": "bf199020-6e8e-4c39-9087-580645a6f22f"})
    if doc:
        print("KEYS IN DOCUMENT:")
        for k in doc.keys():
            print(f"- {k}")
        print("\nOUTPUT JSON:")
        import json
        print(json.dumps(doc.get("output_json"), indent=2))
    else:
        print("Document not found.")
        
    close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(main())
