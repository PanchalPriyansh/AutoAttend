"""Convert stored hierarchy documents into JSON-safe response dicts.

One place decides what leaves the backend. Serialization is driven by the
level descriptor rather than by whatever happens to be on the document,
so a field that is not declared in levels.py can never be returned to a
client by accident.

`_id` is exposed as `id`, ObjectId references become strings, and BSON
dates become ISO 8601 strings.
"""

from datetime import datetime, timezone

from bson import ObjectId


def _to_json_value(value):
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


def serialize(level, document):
    result = {"id": str(document["_id"])}

    if level.parent is not None:
        result[level.parent.field] = _to_json_value(document.get(level.parent.field))

    for field_name in level.fields + level.date_fields + level.read_only_fields:
        result[field_name] = _to_json_value(document.get(field_name))

    result["created_at"] = _to_json_value(document.get("created_at"))
    return result


def serialize_many(level, documents):
    return [serialize(level, document) for document in documents]
