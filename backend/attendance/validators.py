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

# Paging bounds for the session history list. A default keeps a client that
# asks for nothing from pulling a whole semester; the maximum keeps one that
# asks for everything from doing it anyway.
DEFAULT_SESSION_LIMIT = 50
MAX_SESSION_LIMIT = 200


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


def _to_utc_midnight(value):
    """Collapse a parsed datetime to the calendar day it names.

    Every date this feature stores or compares is a lecture day, never a
    moment, so the time is dropped rather than carried around and
    accidentally compared.
    """
    return value.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def parse_attendance_date(body, field_name="date"):
    """Parse a lecture date and normalise it to UTC midnight.

    Normalisation is what makes `uniq_class_id_date` mean "one session per
    lecture": two captures of the same class an hour apart would otherwise
    be two distinct `date` values and both would be stored.

    A future date is rejected. Attendance for a lecture that has not
    happened is a typo or a mis-set clock, never a use case, and it would
    quietly skew every average and risk score computed later.
    """
    normalized = _to_utc_midnight(parse_date(body, field_name))

    today = _to_utc_midnight(datetime.now(timezone.utc))
    if normalized > today:
        raise ValidationError(f"{field_name} cannot be in the future")

    return normalized


def parse_optional_date(args, field_name):
    """A history filter bound: the date if given, None if not.

    Unlike a lecture date, a future bound is accepted. `to=2030-01-01` is a
    harmless way of saying "up to whenever" -- it selects nothing that does
    not exist, whereas recording attendance on a future date would invent a
    lecture.
    """
    if args.get(field_name) in (None, ""):
        return None

    return _to_utc_midnight(parse_date(args, field_name))


def _parse_bounded_int(args, field_name, *, default, minimum, maximum=None):
    """Read a paging parameter from query-string strings.

    A value above `maximum` is clamped rather than refused: asking for more
    history than the server will send is a reasonable thing for a client to
    do, and answering with the most it can is more useful than a 400. A
    value below `minimum`, or one that is not a number at all, is a
    malformed request and says so.
    """
    raw = args.get(field_name)
    if raw in (None, ""):
        return default

    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a whole number") from exc

    if value < minimum:
        raise ValidationError(f"{field_name} must be {minimum} or greater")

    return min(value, maximum) if maximum is not None else value


def parse_date_range(args):
    """Validate an inclusive `from`/`to` filter on the lecture date.

    Returns `{"date_from": …, "date_to": …}`, either of which may be None
    when the caller did not narrow that end.

    Two callers, one rule. The faculty session history and the student's
    own attendance both let a date range be applied to the same stored
    `date` field, and a second copy of "to cannot be earlier than from"
    is how the two screens start disagreeing about what an empty result
    means.
    """
    date_from = parse_optional_date(args, "from")
    date_to = parse_optional_date(args, "to")

    if date_from is not None and date_to is not None and date_to < date_from:
        raise ValidationError("to cannot be earlier than from")

    return {"date_from": date_from, "date_to": date_to}


def parse_session_filters(args):
    """Validate the query string of the session history list.

    Returns the four values the service needs: the inclusive date range
    plus the paging bounds.
    """
    return {
        # Evaluated first, so a malformed date is still reported before a
        # malformed limit -- the order the single-function version raised in.
        **parse_date_range(args),
        "limit": _parse_bounded_int(
            args, "limit", default=DEFAULT_SESSION_LIMIT, minimum=1,
            maximum=MAX_SESSION_LIMIT,
        ),
        "skip": _parse_bounded_int(args, "skip", default=0, minimum=0),
    }


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


def require_records(records, expected_ids, *, scope="this class"):
    """Validate a present/absent list against the students it must cover.

    The list must account for those students exactly: every one of them
    once, nobody twice, and nobody else. A partial list would write a
    session that looks complete to every later feature while silently
    omitting people -- and since absences are stored explicitly, a missing
    row is indistinguishable from a student who was never considered.

    `expected_ids` is those students' ObjectIds, in the order the stored
    rows should end up in. Returns the normalised records in that same
    order rather than payload order, so what is stored does not depend on
    how the client happened to sort it.

    Two callers, one rule. Capturing a session validates against the class
    roster; correcting a saved one validates against the students already
    in that session, which is not the same set once somebody has been
    enrolled or unenrolled since. `scope` only names which of the two is
    being enforced, so the error message tells the caller what it actually
    compared against.
    """
    if not isinstance(records, list):
        raise ValidationError("records must be a list")
    if not records:
        raise ValidationError("records is required")

    expected = set(expected_ids)
    by_student = {}
    for entry in records:
        if not isinstance(entry, dict):
            raise ValidationError("each record must be an object")

        student_id = parse_object_id(entry.get("student_id"), "student_id")
        if student_id in by_student:
            raise ValidationError("records contains the same student more than once")
        if student_id not in expected:
            raise ValidationError(
                f"records contains a student who is not part of {scope}"
            )

        by_student[student_id] = {
            "student_id": student_id,
            "status": require_status(entry.get("status")),
            "marked_by": require_marked_by(entry.get("marked_by")),
        }

    missing = len(expected) - len(by_student)
    if missing:
        raise ValidationError(
            f"records is missing {missing} student"
            f"{'' if missing == 1 else 's'} from {scope}; every one of them "
            "needs a present or absent entry"
        )

    return [by_student[student_id] for student_id in expected_ids]
