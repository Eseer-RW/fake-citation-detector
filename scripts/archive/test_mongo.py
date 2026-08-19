from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

print("Starting script...")

MONGO_URI = (
    "mongodb://rwang:4d2ceLf8vnra4kasdfgtyu"
    "@jupiter2:27017/crs"
    "?authSource=crs"
)

try:

    print("Creating client...")

    client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000
    )

    print("Attempting server connection...")

    client.server_info()

    print("Connected successfully!")

    db = client["crs"]

    collection = db["crossref"]

    print("Fetching one document...")

    doc = collection.find_one()

    print("DOCUMENT:")
    print(doc)

except ServerSelectionTimeoutError as e:

    print("SERVER TIMEOUT")
    print(e)

except Exception as e:

    print("ERROR")
    print(e)