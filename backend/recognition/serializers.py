"""Convert stored face-encoding documents into JSON-safe dicts.

One place decides what leaves the backend. Every function below builds
its result from a literal allow-list of field names, never by copying the
document and removing keys -- the `encoding` vector is therefore
structurally unable to reach a response, rather than being excluded by a
filter someone could later forget to update. It is the most sensitive
value the project stores and no client, admin included, has a reason to
receive it.
"""

from common.serializers import student_summary, to_json_value


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


# A file name is echoed back so the admin can find the photo it refers
# to, and it is the one string in this response the client chose. It is
# JSON-escaped like any other value, so the cap is not an injection
# defence -- it is what stops a pathological name from dominating a
# report of ten rows.
MAX_REPORTED_FILENAME = 120


def serialize_import_result(result):
    """One row of a bulk-import report.

    `result["student"]` is a raw student document or None, and goes
    through the same `student_summary` allow-list every other roster
    response uses -- so a bulk import cannot describe a student in a
    shape the single-upload path would not. As everywhere else in this
    module the dict is built from literal keys, which is why no encoding
    can reach it: `register_encoding` returns the stored document, and
    nothing here reads it.
    """
    student = result.get("student")
    filename = result.get("filename") or ""

    return {
        "filename": filename[:MAX_REPORTED_FILENAME],
        "status": result.get("status"),
        "student": student_summary(student) if student else None,
        "message": result.get("message"),
    }


def serialize_import_results(results):
    return [serialize_import_result(result) for result in results]


def serialize_import_summary(summary):
    """The report's totals, named one by one.

    Written as literal keys rather than passed through, so the four
    statuses the API documents are the four a client can ever receive.
    """
    return {
        "submitted": summary.get("submitted", 0),
        "registered": summary.get("registered", 0),
        "no_match": summary.get("no_match", 0),
        "ambiguous": summary.get("ambiguous", 0),
        "rejected": summary.get("rejected", 0),
    }


def serialize_enrollment_status(entry):
    """`entry` is one (student, sample_count, last_enrolled_at) triple from
    recognition.service.class_enrollment_status.
    """
    student, sample_count, last_enrolled_at = entry

    return {
        "student": student_summary(student),
        "sample_count": sample_count,
        "last_enrolled_at": to_json_value(last_enrolled_at),
    }


def serialize_enrollment_statuses(entries):
    return [serialize_enrollment_status(entry) for entry in entries]
