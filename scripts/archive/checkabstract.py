from pymongo import MongoClient
from pymongo import TEXT
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

from dotenv import load_dotenv
import os

from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env")

MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = "crs"
COLLECTION_NAME = "crossref"

client = MongoClient(MONGO_URI)

db = client["crs"]
collection = db["crossref"]

# Find one record with an abstract
doc = collection.find_one(
    {"abstract": {"$exists": True}},
    {"DOI": 1, "title": 1, "abstract": 1}
)

print(doc)