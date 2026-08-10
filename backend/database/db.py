import logging

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from config import Config

logger = logging.getLogger(__name__)

# Short timeout so a down/unreachable MongoDB doesn't hang the health check.
MONGO_SERVER_SELECTION_TIMEOUT_MS = 2000

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = MongoClient(
            Config.MONGODB_URI,
            serverSelectionTimeoutMS=MONGO_SERVER_SELECTION_TIMEOUT_MS,
        )
    return _client


def get_db():
    if not Config.MONGODB_URI:
        raise RuntimeError("MONGODB_URI is not configured")

    client = _get_client()
    return client[Config.MONGODB_DB_NAME]


def get_db_status():
    if not Config.MONGODB_URI:
        return {"connected": False, "error": "MongoDB URI not configured"}

    try:
        client = _get_client()
        client.admin.command("ping")
        return {"connected": True}
    except Exception as exc:
        message = (
            "MongoDB connectivity check failed"
            if isinstance(exc, PyMongoError)
            else "Unexpected error checking MongoDB connectivity"
        )
        logger.exception(message)
        return {"connected": False, "error": "MongoDB unreachable"}
