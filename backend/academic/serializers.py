"""Convert stored hierarchy documents into JSON-safe response dicts.

One place decides what leaves the backend. Serialization is driven by the
level descriptor rather than by whatever happens to be on the document,
so a field that is not declared in levels.py can never be returned to a
client by accident.

`_id` is exposed as `id`, ObjectId references become strings, and BSON
dates become ISO 8601 strings.
"""

from common.serializers import to_json_value


def serialize(level, document):
    result = {"id": str(document["_id"])}

    if level.parent is not None:
        result[level.parent.field] = to_json_value(document.get(level.parent.field))

    for field_name in level.fields + level.date_fields + level.read_only_fields:
        result[field_name] = to_json_value(document.get(field_name))

    result["created_at"] = to_json_value(document.get("created_at"))
    return result


def serialize_many(level, documents):
    return [serialize(level, document) for document in documents]
