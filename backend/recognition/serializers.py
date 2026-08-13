"""Convert stored face-encoding documents into JSON-safe dicts.

One place decides what leaves the backend. Every function below builds
its result from a literal allow-list of field names, never by copying the
document and removing keys -- the `encoding` vector is therefore
structurally unable to reach a response, rather than being excluded by a
filter someone could later forget to update. It is the most sensitive
value the project stores and no client, admin included, has a reason to
receive it.
"""

from common.serializers import to_json_value


def serialize_encoding(document):
    return {
        "id": str(document["_id"]),
        "student_id": to_json_value(document.get("student_id")),
        "model": document.get("model"),
        "source": document.get("source"),
        "created_at": to_json_value(document.get("created_at")),
        "created_by": to_json_value(document.get("created_by")),
    }


def serialize_encodings(documents):
    return [serialize_encoding(document) for document in documents]


def _serialize_student(student):
    """The same narrow view a class roster uses -- enough to identify the
    student on screen, and nothing more.
    """
    return {
        "id": str(student["_id"]),
        "name": student.get("name"),
        "email": student.get("email"),
        "is_active": student.get("is_active"),
    }


def serialize_enrollment_status(entry):
    """`entry` is one (student, sample_count, last_enrolled_at) triple from
    recognition.service.class_enrollment_status.
    """
    student, sample_count, last_enrolled_at = entry

    return {
        "student": _serialize_student(student),
        "sample_count": sample_count,
        "last_enrolled_at": to_json_value(last_enrolled_at),
    }


def serialize_enrollment_statuses(entries):
    return [serialize_enrollment_status(entry) for entry in entries]
