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

from common.serializers import student_summary, to_json_value

# The stored `date` is a timestamp at UTC midnight, but it means a
# calendar day. Rendering the time with it invites a reader to wonder what
# happened at midnight, and invites a client to re-parse it into a
# different day in their own timezone.
DATE_FORMAT = "%Y-%m-%d"


def _serialize_date(value):
    return value.strftime(DATE_FORMAT) if value is not None else None


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
        "id": str(session["_id"]),
        "class_id": to_json_value(session.get("class_id")),
        "date": _serialize_date(session.get("date")),
        "source": session.get("source"),
        "taken_by": to_json_value(session.get("taken_by")),
        "created_at": to_json_value(session.get("created_at")),
        "updated_at": to_json_value(session.get("updated_at")),
        "present_count": sum(1 for row in serialized if row["status"] == "present"),
        "absent_count": sum(1 for row in serialized if row["status"] == "absent"),
        "records": serialized,
    }
