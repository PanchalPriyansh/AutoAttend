"""Input validation rules specific to user accounts.

Pure functions -- no Flask objects, no database access. The generic
helpers (ObjectId and string parsing) come from common/validators.py;
only the rules that are about *users* live here.

The role enum and email pattern below intentionally mirror
USERS_VALIDATOR in database/schema.py. That `$jsonSchema` is the last
line of defense, not the first: without these checks a bad email reaches
MongoDB, fails the collection validator, and surfaces to the client as an
opaque 500 instead of a 400 naming the field.
"""

import re

from common.errors import ValidationError
from common.validators import parse_object_id, require_non_empty_string

# Mirrors the `role` enum in USERS_VALIDATOR (database/schema.py).
ROLES = ("admin", "faculty", "student")

# Mirrors the `email` pattern in USERS_VALIDATOR (database/schema.py).
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MIN_PASSWORD_LENGTH = 8


def require_role(value, field_name="role"):
    """Validate a role coming from a request body."""
    if value is None or value == "":
        raise ValidationError(f"{field_name} is required")
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    if value not in ROLES:
        raise ValidationError(f"{field_name} must be one of {', '.join(ROLES)}")

    return value


def parse_optional_role(value, field_name="role"):
    """Validate a role used as a list filter, where absence means "no filter".

    An unrecognised value is still a 400 rather than a silently empty
    result, so a typo in `?role=` is visible instead of looking like an
    empty directory.
    """
    if value is None or value == "":
        return None
    return require_role(value, field_name)


def require_email(body, field_name="email"):
    """Return the trimmed email, rejecting anything the schema would reject.

    Normalisation (lowercasing) is deliberately left to
    auth.service.normalize_email, which is also what the login path uses
    -- doing it in two places is how the two drift apart.
    """
    value = require_non_empty_string(body, field_name)
    if not EMAIL_PATTERN.match(value):
        raise ValidationError(f"{field_name} must be a valid email address")

    return value


def validate_password_length(password, field_name="password"):
    """Length is the only policy, and it is enforced identically by the API
    and by `flask create-admin` so the two cannot disagree.

    The value itself is never echoed back in the message.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(
            f"{field_name} must be at least {MIN_PASSWORD_LENGTH} characters"
        )


def require_password(body, field_name="password"):
    """Return the raw password from a request body.

    Deliberately not trimmed: leading and trailing spaces are legitimate
    characters in a credential, and silently altering one would let an
    account be created with a password its owner cannot reproduce.
    """
    value = body.get(field_name)
    if value is None:
        raise ValidationError(f"{field_name} is required")
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")

    validate_password_length(value, field_name)
    return value


def require_bool(body, field_name):
    """Require a real JSON boolean -- "true" and 1 are rejected.

    Accepting a truthy string would make `{"is_active": "false"}` mean
    *active*, which is the wrong answer to an account-disabling request.
    """
    value = body.get(field_name)
    if value is None:
        raise ValidationError(f"{field_name} is required")
    if not isinstance(value, bool):
        raise ValidationError(f"{field_name} must be true or false")

    return value


def parse_optional_object_id(value, field_name):
    """Parse an id where absence is legitimate (an optional query filter)."""
    if value is None or value == "":
        return None
    return parse_object_id(value, field_name)


def require_nullable_object_id(body, field_name):
    """Parse an id that may be explicitly `null` but may not be omitted.

    The distinction matters for unassignment: an explicit `null` clears
    the reference, while a missing key is a malformed request. Treating
    the two alike would let an empty body silently unassign a class.
    """
    if field_name not in body:
        raise ValidationError(f"{field_name} is required")

    value = body[field_name]
    if value is None:
        return None

    return parse_object_id(value, field_name)
