"""Convert stored user and enrollment documents into JSON-safe dicts.

One place decides what leaves the backend. Every function below builds
its result from a literal allow-list of field names, never by copying the
document and removing keys -- `password_hash` is therefore structurally
unable to reach a response, rather than being excluded by a filter that
someone could later forget to update.
"""

from common.serializers import to_json_value


def serialize_user(document):
    return {
        "id": str(document["_id"]),
        "name": document.get("name"),
        "email": document.get("email"),
        "role": document.get("role"),
        "institute_id": to_json_value(document.get("institute_id")),
        "is_active": document.get("is_active"),
        "created_at": to_json_value(document.get("created_at")),
        "updated_at": to_json_value(document.get("updated_at")),
    }


def serialize_users(documents):
    return [serialize_user(document) for document in documents]


def _serialize_enrolled_student(student):
    """A narrower view than serialize_user: a class roster needs to
    identify and contact a student, not expose their whole account.

    Written out separately rather than filtering serialize_user's output
    so that a field added there does not silently widen this one.
    """
    return {
        "id": str(student["_id"]),
        "name": student.get("name"),
        "email": student.get("email"),
        "is_active": student.get("is_active"),
    }


def serialize_enrollment(enrollment, student):
    return {
        "id": str(enrollment["_id"]),
        "student": _serialize_enrolled_student(student),
        "enrolled_at": to_json_value(enrollment.get("enrolled_at")),
    }


def serialize_enrollments(pairs):
    """`pairs` is the (enrollment, student) sequence from
    users.assignments.list_enrollments.
    """
    return [serialize_enrollment(enrollment, student) for enrollment, student in pairs]
