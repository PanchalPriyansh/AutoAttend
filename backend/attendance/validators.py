"""Input validation rules specific to attendance capture.

Pure functions -- no Flask objects, no database access, no CV library.
Routes hand raw request values here and get back the `ObjectId`,
`datetime`, and enum values the service layer expects, so a malformed
payload is a 400 naming the field rather than a driver traceback or a
`$jsonSchema` rejection surfacing as an opaque 500.

The three enums mirror ATTENDANCE_SESSIONS_VALIDATOR and
ATTENDANCE_RECORDS_VALIDATOR in database/schema.py. That schema is the
last line of defense, not the first.
"""

from datetime import datetime, timezone

from common.errors import ValidationError
from common.validators import parse_date, parse_object_id

# Mirrors the `source` enum in ATTENDANCE_SESSIONS_VALIDATOR.
SESSION_SOURCES = ("photo", "video", "manual")

# Mirrors the `status` and `marked_by` enums in ATTENDANCE_RECORDS_VALIDATOR.
STATUSES = ("present", "absent")
MARKED_BY_VALUES = ("recognition", "faculty")

DEFAULT_MARKED_BY = "faculty"


def _require_enum(value, field_name, allowed):
    if value is None or value == "":
        raise ValidationError(f"{field_name} is required")
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    if value not in allowed:
        raise ValidationError(f"{field_name} must be one of: {', '.join(allowed)}")

    return value


def require_session_source(value):
    """How the session was produced. Provenance for later review, not a
    claim about how accurate the result is.
    """
    return _require_enum(value, "source", SESSION_SOURCES)


def require_status(value):
    return _require_enum(value, "status", STATUSES)


def require_marked_by(value):
    """Whether the pipeline proposed this status or a human set it.

    Absent means a human: a payload that does not say where a decision
    came from is safest read as somebody's own entry, since claiming
    recognition produced something it did not would corrupt the only
    signal available for judging how well matching actually performs.
    """
    if value is None or value == "":
        return DEFAULT_MARKED_BY

    return _require_enum(value, "marked_by", MARKED_BY_VALUES)


def parse_attendance_date(body, field_name="date"):
    """Parse a lecture date and normalise it to UTC midnight.

    Normalisation is what makes `uniq_class_id_date` mean "one session per
    lecture": two captures of the same class an hour apart would otherwise
    be two distinct `date` values and both would be stored.

    A future date is rejected. Attendance for a lecture that has not
    happened is a typo or a mis-set clock, never a use case, and it would
    quietly skew every average and risk score computed later.
    """
    parsed = parse_date(body, field_name)
    normalized = parsed.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if normalized > today:
        raise ValidationError(f"{field_name} cannot be in the future")

    return normalized


def require_replace_flag(value):
    """Whether an existing session for this class and date may be
    overwritten. Absent means no -- replacing attendance is destructive
    and must be asked for explicitly.
    """
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValidationError("replace must be true or false")

    return value


def require_records(records, roster_ids):
    """Validate a reviewed present/absent list against the class roster.

    The list must account for the roster exactly: every enrolled student
    once, nobody twice, and nobody who is not enrolled. A partial list
    would write a session that looks complete to every later feature while
    silently omitting people -- and since absences are stored explicitly,
    a missing row is indistinguishable from a student who was never
    considered.

    `roster_ids` is the enrolled students' ObjectIds in roster order.
    Returns the normalised records in that same order rather than payload
    order, so the stored rows do not depend on how the client happened to
    sort them.
    """
    if not isinstance(records, list):
        raise ValidationError("records must be a list")
    if not records:
        raise ValidationError("records is required")

    enrolled = set(roster_ids)
    by_student = {}
    for entry in records:
        if not isinstance(entry, dict):
            raise ValidationError("each record must be an object")

        student_id = parse_object_id(entry.get("student_id"), "student_id")
        if student_id in by_student:
            raise ValidationError("records contains the same student more than once")
        if student_id not in enrolled:
            raise ValidationError(
                "records contains a student who is not enrolled in this class"
            )

        by_student[student_id] = {
            "student_id": student_id,
            "status": require_status(entry.get("status")),
            "marked_by": require_marked_by(entry.get("marked_by")),
        }

    missing = len(enrolled) - len(by_student)
    if missing:
        raise ValidationError(
            f"records is missing {missing} enrolled student"
            f"{'' if missing == 1 else 's'}; every student on the roster needs "
            "a present or absent entry"
        )

    return [by_student[student_id] for student_id in roster_ids]
