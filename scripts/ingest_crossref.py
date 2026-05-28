import tarfile
import gzip
import orjson
from pymongo import MongoClient, UpdateOne

# =====================================================
# CONFIG
# =====================================================

CROSSREF_TAR = "/home/rwang/crossref/scripts/March_2026_Public_Data_File_from_Crossref.tar"
DATACITE_TAR = "https://datafiles.datacite.org/datafiles/public-2025/download?token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJmcmVzaCI6ZmFsc2UsImlhdCI6MTc3OTg5NTQzOSwianRpIjoiNTcxYThjM2MtNjVlMS00OTBlLWJmMjgtMTQ1NDhlNWUzNjQ1IiwidHlwZSI6ImFjY2VzcyIsInN1YiI6MTQzNCwibmJmIjoxNzc5ODk1NDM5LCJjc3JmIjoiMWI3OWIxMDYtZDkyOS00NTgwLWI0OWItMjUwNzliZGViNzE1IiwiZXhwIjoxNzc5OTgxODM5fQ.AbmvhRk1uyxrCW8rVNEIerWfdBIihuwsM2WNMRc_bHI"
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "crossref_datacite"
COLLECTION_NAME = "data"
BATCH_SIZE = 1000

# =====================================================
# CONNECT TO MONGO
# =====================================================

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
collection = db[COLLECTION_NAME]
print("Connected to MongoDB")

# =====================================================
# INDEXES
# =====================================================

collection.create_index("sources")
collection.create_index("year")
print("Indexes created")

# =====================================================
# HELPERS
# =====================================================

def extract_crossref_year(record):
    try:
        return record["published"]["date-parts"][0][0]
    except Exception:
        return None


def normalize_crossref(record):
    doi = record.get("DOI")
    if not doi:
        return None

    return {
        "id": doi,
        "source": "crossref",
        # Written only on first insert — preserved if DataCite ingests same DOI later
        "insert": {
            "doi": doi,
            "title": record.get("title"),
            "authors": record.get("author"),
            "publisher": record.get("publisher"),
            "year": extract_crossref_year(record),
        },
        # Always written — Crossref-specific fields, no collision with DataCite
        "update": {
            "journal": record.get("container-title"),
            "references": record.get("reference"),
            "type": record.get("type"),
        },
    }


def normalize_datacite(record):
    attributes = record.get("attributes", {})
    doi = attributes.get("doi")
    if not doi:
        return None

    return {
        "id": doi,
        "source": "datacite",
        # Written only on first insert — preserved if Crossref already ingested this DOI
        "insert": {
            "doi": doi,
            "title": attributes.get("titles"),
            "authors": attributes.get("creators"),
            "publisher": attributes.get("publisher"),
            "year": attributes.get("publicationYear"),
        },
        # Always written — DataCite-specific fields, no collision with Crossref
        "update": {
            "resource_type": attributes.get("types", {}).get("resourceTypeGeneral"),
            "subjects": attributes.get("subjects"),
        },
    }


# =====================================================
# BULK INGEST FUNCTION
# =====================================================

def flush(ops, source_name, total):
    collection.bulk_write(ops, ordered=False)
    total += len(ops)
    print(f"{source_name}: {total:,} processed")
    return total


def ingest_tar_dataset(tar_path, source_name, normalizer):
    print(f"\n{'='*30}")
    print(f"INGESTING {source_name.upper()}")
    print(f"{'='*30}")

    operations = []
    total_processed = 0
    total_errors = 0

    with tarfile.open(tar_path) as tar:
        for member in tar.getmembers():
            if not member.name.endswith(".jsonl.gz"):
                continue

            print(f"\nProcessing: {member.name}")
            gz_stream = tar.extractfile(member)
            if gz_stream is None:
                continue

            with gzip.open(gz_stream, "rb") as f:
                for line in f:
                    try:
                        record = orjson.loads(line)
                        doc = normalizer(record)
                        if doc is None:
                            continue

                        operations.append(
                            UpdateOne(
                                {"_id": doc["id"]},
                                {
                                    "$setOnInsert": doc["insert"],
                                    "$set": doc["update"],
                                    "$addToSet": {"sources": doc["source"]},
                                },
                                upsert=True,
                            )
                        )

                        if len(operations) >= BATCH_SIZE:
                            total_processed = flush(operations, source_name, total_processed)
                            operations = []

                    except Exception as e:
                        total_errors += 1
                        if total_errors <= 10:
                            print(f"ERROR: {e}")

    if operations:
        total_processed = flush(operations, source_name, total_processed)

    print(f"\nDONE: {source_name}")
    print(f"Total processed: {total_processed:,}")
    print(f"Total errors:    {total_errors:,}")


# =====================================================
# RUN INGESTION
# =====================================================

ingest_tar_dataset(CROSSREF_TAR, "crossref", normalize_crossref)
ingest_tar_dataset(DATACITE_TAR, "datacite", normalize_datacite)

print("\n===================================")
print("ALL INGESTION COMPLETE")
print("===================================")