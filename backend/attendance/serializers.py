"""Convert attendance domain results into JSON-safe dicts.

One place decides what leaves the backend. Every function below builds
its result from a literal allow-list of field names, never by copying a
document and removing keys -- so a raw `attendance_records` row cannot
reach a response by accident, and neither can anything the recognition
pipeline held in memory on the way through.

Note what is structurally absent: no encoding vector, no distance to
anything but the matched student, and nothing derived from the captured
image or video beyond the counts the reviewer needs.
"""

from attendance.threshold import (
    attendance_percentage,
    lectures_to_reach,
    meets_threshold,
)
from common.serializers import as_utc, student_summary, to_json_value

# The stored `date` is a timestamp at UTC midnight, but it means a
# calendar day. Rendering the time with it invites a reader to wonder what
# happened at midnight, and invites a client to re-parse it into a
# different day in their own timezone.
DATE_FORMAT = "%Y-%m-%d"


def _serialize_date(value):
    return value.strftime(DATE_FORMAT) if value is not None else None


def _is_edited(session):
    """Whether this session's stored content changed after it was recorded.

    Derived rather than stored: a flag written alongside the change is a
    second source of truth that drifts the first time anything updates one
    and not the other.

    Read from the timestamps rather than from `updated_by` so a capture
    replaced through `POST /api/attendance` counts too. `updated_by` answers
    a narrower question -- who last corrected it through the history screen
    -- and stays null for a replace, which re-takes the lecture rather than
    correcting it.
    """
    created_at = session.get("created_at")
    updated_at = session.get("updated_at")
    if created_at is None or updated_at is None:
        return False

    return as_utc(updated_at) > as_utc(created_at)


def _session_header(session):
    """The fields every session response carries, list row or full detail.

    Shared so the two shapes cannot drift into describing the same session
    differently.
    """
    return {
        "id": str(session["_id"]),
        "class_id": to_json_value(session.get("class_id")),
        "date": _serialize_date(session.get("date")),
        "source": session.get("source"),
        "taken_by": to_json_value(session.get("taken_by")),
        "created_at": to_json_value(session.get("created_at")),
        "updated_at": to_json_value(session.get("updated_at")),
        # Explicitly null rather than absent on a session nobody has
        # corrected, so a client can render "never edited" without having to
        # tell a missing key from a missing person.
        "updated_by": to_json_value(session.get("updated_by")),
        "edited": _is_edited(session),
    }


def serialize_assigned_class(entry):
    """`entry` is one (class_document, context, student_count) triple from
    attendance.service.list_assigned_classes, where `context` holds the
    course/semester/department/institute names.

    The hierarchy is flattened to names on purpose: the faculty member is
    picking a class, not navigating five levels, and a name they can read
    is the whole reason those lookups happened.
    """
    document, context, student_count = entry

    return {
        "id": str(document["_id"]),
        "name": document.get("name"),
        "course": context.get("course"),
        "semester": context.get("semester"),
        "department": context.get("department"),
        "institute": context.get("institute"),
        "student_count": student_count,
    }


def serialize_assigned_classes(entries):
    return [serialize_assigned_class(entry) for entry in entries]


def serialize_proposal(proposal):
    """The recognition result, as three lists that partition the roster.

    `recognized` carries a distance and a confidence band because a
    reviewer deciding whether to trust a match needs to see how close it
    actually was. The distance is rounded: further precision is noise from
    a heuristic, and displaying it to four decimals would imply a
    certainty the number does not carry.
    """
    return {
        "class_id": str(proposal["class_id"]),
        "source": proposal["source"],
        "frames_analyzed": proposal["frames_analyzed"],
        "detected_faces": proposal["detected_faces"],
        "unknown_faces": proposal["unknown_faces"],
        "recognized": [
            {
                "student": student_summary(student),
                "distance": round(distance, 3),
                "confidence": confidence,
            }
            for student, distance, confidence in proposal["recognized"]
        ],
        "unrecognized": [
            {"student": student_summary(student), "sample_count": sample_count}
            for student, sample_count in proposal["unrecognized"]
        ],
        "not_enrolled": [
            {"student": student_summary(student), "sample_count": 0}
            for student in proposal["not_enrolled"]
        ],
    }


def serialize_session(session, records):
    """`records` is a list of (record_document, student_document) pairs.

    Present and absent counts are computed here rather than stored: a
    stored count is a second source of truth that drifts the moment a
    record changes.
    """
    serialized = [
        {
            "student": student_summary(student),
            "status": record.get("status"),
            "marked_by": record.get("marked_by"),
        }
        for record, student in records
    ]

    return {
        **_session_header(session),
        "present_count": sum(1 for row in serialized if row["status"] == "present"),
        "absent_count": sum(1 for row in serialized if row["status"] == "absent"),
        "total_count": len(serialized),
        "records": serialized,
    }


def serialize_session_summary(entry):
    """One row of the session history list: a session and its counts, with
    no `records` array.

    `entry` is a (session_document, counts) pair from
    attendance.service.list_sessions, where `counts` holds `present` and
    `total` from a single grouped aggregation. The roster itself is left
    out deliberately -- a semester of full rosters is exactly the payload
    this list must not carry, and the detail endpoint is one click away.
    """
    session, counts = entry
    total = counts.get("total", 0)
    present = counts.get("present", 0)

    return {
        **_session_header(session),
        "present_count": present,
        "absent_count": total - present,
        "total_count": total,
    }


def serialize_session_summaries(entries):
    return [serialize_session_summary(entry) for entry in entries]


# --- Student-facing shapes --------------------------------------------
#
# Kept apart from everything above, and built from their own allow-lists
# rather than by trimming the faculty ones. The faculty shapes carry the
# class -- `student_summary`, a records array, `marked_by`, `taken_by` --
# and a student response that started as one of those and had fields
# removed is one careless edit away from carrying them again.


def _standing(counts):
    """The four numbers that describe attendance at any scope: one class,
    everything, or a single month.
    """
    present = counts.get("present", 0)
    absent = counts.get("absent", 0)
    total = counts.get("total", 0)

    return {
        "present_count": present,
        "absent_count": absent,
        "total_count": total,
        "percentage": attendance_percentage(present, total),
    }


def _class_identity(document, context):
    """A class as the student reads it: its own name plus the hierarchy it
    sits in. No faculty member, no roster, no enrollment count.
    """
    return {
        "id": str(document["_id"]),
        "name": document.get("name"),
        "course": context.get("course"),
        "semester": context.get("semester"),
        "department": context.get("department"),
        "institute": context.get("institute"),
    }


def _comparison(counts, threshold):
    """How one class stands against the bar.

    Deliberately separate from `_standing`, and applied at one scope only.
    `_standing` is shared by the per-class, overall, and monthly figures;
    the bar belongs to none of the other two. An average hides the class a
    student is actually short in -- 85% overall can contain a class at 55%
    -- and a month is not a window anybody is assessed on. Applying it per
    class is also what the sweep does, so the screen and the email cannot
    reach different verdicts about the same student.

    Both fields are None together: None is not False, and a class with
    nothing recorded has not failed to meet the requirement.
    """
    present = counts.get("present", 0)
    total = counts.get("total", 0)

    return {
        "meets_threshold": meets_threshold(
            attendance_percentage(present, total), threshold
        ),
        "lectures_to_reach": lectures_to_reach(present, total, threshold),
    }


def serialize_student_overview(entries, overall, threshold):
    """`entries` is a list of (class_document, context, counts) triples
    from attendance.summary.class_attendance_overview.

    `threshold` is the configured bar, or None when it is unusable -- in
    which case every class reports None for both comparison fields and the
    screen falls back to showing attendance alone.
    """
    return {
        "threshold": threshold,
        "classes": [
            {
                **_class_identity(document, context),
                **_standing(counts),
                **_comparison(counts, threshold),
            }
            for document, context, counts in entries
        ],
        "overall": _standing(overall),
    }


def serialize_student_class_attendance(
    document, context, counts, monthly, lectures, threshold
):
    """One class in detail, for the student it describes.

    `monthly` stays oldest-first and `lectures` newest-first, exactly as
    attendance.summary produced them: a trend is read left to right and a
    log is read newest first. The two orderings in one response are
    deliberate.

    A lecture is a date and a status. Not `marked_by`, not `source`, not a
    session id -- provenance is an audit signal for judging how well
    recognition performs, and the stored status is the faculty member's
    statement either way, so showing a student "a camera decided this"
    would misrepresent both what happened and who is answerable for it.

    The bar applies to the class and not to any bucket inside it: a month
    is not a window a student is assessed on, so `monthly` carries the
    same four figures it always did and no comparison.
    """
    return {
        "class": _class_identity(document, context),
        "threshold": threshold,
        **_standing(counts),
        **_comparison(counts, threshold),
        "monthly": [
            {"month": bucket["month"], **_standing(bucket)} for bucket in monthly
        ],
        "sessions": [
            {"date": _serialize_date(lecture["date"]), "status": lecture["status"]}
            for lecture in lectures
        ],
    }
