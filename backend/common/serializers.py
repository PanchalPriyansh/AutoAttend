"""Shared conversion of stored BSON values into JSON-safe ones.

Extracted from academic/serializers.py so every feature package converts
ObjectIds and dates identically. The timezone reattachment below is the
reason this is shared rather than reimplemented: a copy that forgets it
emits timestamps that look correct and are silently wrong.
"""

from datetime import datetime, timezone

from bson import ObjectId


def as_utc(value):
    """Put a stored timestamp on the same footing as a freshly built one.

    pymongo hands back naive UTC datetimes, while anything the application
    has just constructed carries a timezone. Mixing the two is not a
    cosmetic problem: comparing them raises TypeError, and it is a real
    bug this project has already shipped once -- the in-memory test fakes
    store aware datetimes, so only a live database surfaces it.

    Every read path that compares or serializes a stored datetime goes
    through here, so the reattachment exists once rather than in each
    feature that remembers to do it.
    """
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def to_json_value(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        # Without reattaching the timezone, isoformat() would drop the
        # "+00:00" offset and imply a local time the value never had.
        return as_utc(value).isoformat()
    return value


def student_summary(student):
    """The narrow view of a student that list-shaped responses carry --
    enough to identify a person on screen, and nothing more.

    Shared because face enrolment and attendance both hand back rosters
    and must describe a student identically; a per-feature copy is how one
    of them starts leaking `password_hash` after a careless edit. Built
    from a literal allow-list, so a new field on `users` cannot appear in
    a response by simply existing.
    """
    return {
        "id": str(student["_id"]),
        "name": student.get("name"),
        "email": student.get("email"),
        "is_active": student.get("is_active"),
    }
