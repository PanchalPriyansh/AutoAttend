"""Input parsing/validation helpers shared by every feature package.

Pure functions -- no Flask request/response objects, no database access.
Route handlers call these to turn raw JSON/query-string values into the
`ObjectId`, `str`, and `datetime` values the service layers expect, so a
malformed id surfaces as a 400 rather than a driver traceback or a 500.

Blank/whitespace-only strings are rejected here rather than being left to
each collection's `minLength: 1` `$jsonSchema` validator: the schema is
the last line of defense, not the first, and a validator rejection would
otherwise reach the client as an opaque 500.
"""

from datetime import date, datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId

from common.errors import ValidationError


def parse_object_id(value, field_name):
    """Parse an id from a request body or query string into an ObjectId."""
    if value is None or value == "":
        raise ValidationError(f"{field_name} is required")
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")

    try:
        return ObjectId(value)
    except (InvalidId, TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} is not a valid id") from exc


def require_non_empty_string(body, field_name):
    """Return `body[field_name]` trimmed, rejecting missing/blank values."""
    value = body.get(field_name)
    if value is None:
        raise ValidationError(f"{field_name} is required")
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")

    trimmed = value.strip()
    if not trimmed:
        raise ValidationError(f"{field_name} cannot be blank")

    return trimmed


def parse_date(body, field_name):
    """Parse an ISO 8601 date or datetime string into a timezone-aware
    `datetime`, so it is stored as a real BSON date.

    database/schema.py requires `bsonType: "date"` for these fields --
    storing the ISO string itself would fail the collection validator.
    """
    value = body.get(field_name)
    if value is None:
        raise ValidationError(f"{field_name} is required")
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be an ISO 8601 date string")

    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value.strip()), datetime.min.time())
        except ValueError as exc:
            raise ValidationError(f"{field_name} must be an ISO 8601 date string") from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def require_end_after_start(start_date, end_date):
    """A semester must end after it starts; equal dates are rejected too."""
    if end_date <= start_date:
        raise ValidationError("end_date must be after start_date")


def require_bounded_string(body, field_name, max_length):
    """A required non-empty string with a ceiling on its length.

    `require_non_empty_string` plus a maximum, for the case where the
    value is about to be fed to something whose cost scales with it --
    25-forgot-password submits a code to a password-hash comparison, and
    without a bound a caller can post a megabyte and make the server hash
    every byte of it. The cap belongs on the way in rather than at the
    comparison, so nothing expensive is reached at all.

    The limit is named in the message: unlike a credential, the length of
    a code is not a secret, and a caller who genuinely sent something too
    long deserves to know the rule.
    """
    value = require_non_empty_string(body, field_name)
    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} must be at most {max_length} characters"
        )

    return value
