"""Shared conversion of stored BSON values into JSON-safe ones.

Extracted from academic/serializers.py so every feature package converts
ObjectIds and dates identically. The timezone reattachment below is the
reason this is shared rather than reimplemented: a copy that forgets it
emits timestamps that look correct and are silently wrong.
"""

from datetime import datetime, timezone

from bson import ObjectId


def to_json_value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        # pymongo returns naive UTC datetimes; without reattaching the
        # timezone, isoformat() would drop the "+00:00" offset and imply
        # a local time the value never had.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value
