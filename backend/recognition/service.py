"""Business logic for registering, listing, and deleting a student's face
encodings.

No Flask request/response objects appear in any signature here -- HTTP
concerns belong to routes/faces.py, which maps the domain exceptions from
common/errors.py and recognition/errors.py onto status codes. All CV work
is delegated to recognition/encoder.py, the only module allowed to import
the recognition library, so this module stays runnable and testable
without it.

The source image is never persisted. It exists as bytes for the duration
of register_encoding and nothing writes it anywhere -- not to the
document, not to a temp file, not to a log line.
"""

import logging
from datetime import datetime, timezone

from common.errors import ConflictError, NotFoundError
from database.schema import FACE_ENCODINGS, USERS
from recognition import encoder
from recognition.errors import RecognitionUnavailableError
from recognition.validators import require_single_face
from users.assignments import list_enrollments, require_user_with_role

logger = logging.getLogger(__name__)

# Several samples per student (different angles and lighting) match more
# reliably than one, but the count is bounded: an unbounded roster of
# vectors would slow every duplicate check and every future attendance
# pass without improving accuracy much beyond a handful.
MAX_SAMPLES_PER_STUDENT = 5


def _now():
    return datetime.now(timezone.utc)


def _require_student(db, student_id, *, require_active=True):
    return require_user_with_role(
        db, student_id, "student", "student_id", require_active=require_active
    )


def _require_sample_capacity(db, student_id):
    existing = db[FACE_ENCODINGS].count_documents({"student_id": student_id})
    if existing >= MAX_SAMPLES_PER_STUDENT:
        raise ConflictError(
            f"This student already has the maximum of {MAX_SAMPLES_PER_STUDENT} "
            "face samples. Delete one before adding another."
        )


def _require_recognition_available():
    if not encoder.is_available():
        raise RecognitionUnavailableError(
            "Face recognition is unavailable on the server. The recognition "
            "library is not installed."
        )


def _require_no_other_student_matches(db, student_id, candidate):
    """Reject a face that already belongs to somebody else.

    Without this, photographing the wrong student silently creates two
    identities for one face, and every later attendance pass has to guess
    between them. The admin is told to resolve it rather than the system
    picking a winner or overwriting an existing sample.

    The scan covers every other student's encodings. That is linear in the
    number of enrolled samples, which is bounded by (students x
    MAX_SAMPLES_PER_STUDENT) and is small at a college's scale; revisit
    with an approximate-nearest-neighbour index only if that stops holding.
    """
    # Projected to the two fields actually used: model/source/created_at/
    # created_by would otherwise be pulled across the wire on every single
    # registration for no reason.
    others = list(
        db[FACE_ENCODINGS].find(
            {"student_id": {"$ne": student_id}}, {"student_id": 1, "encoding": 1}
        )
    )
    match = encoder.closest_match(candidate, [item["encoding"] for item in others])
    if match is None:
        return

    index, distance = match
    if distance > encoder.MATCH_TOLERANCE:
        return

    owner = db[USERS].find_one({"_id": others[index]["student_id"]})
    owner_name = (owner or {}).get("name") or "another student"
    raise ConflictError(
        f"This face closely matches an existing sample for {owner_name}. "
        "Check that the correct student is being enrolled."
    )


def register_encoding(db, student_id, *, image_bytes, source, created_by):
    """Derive one face encoding from `image_bytes` and store it.

    Checks run cheapest-first and most-decisive-first. Availability leads:
    without the library no enrolment can succeed, so there is no point
    querying MongoDB, and a server missing the dependency reports that
    plainly instead of a database error masking it. Resolving the student
    and counting their samples are then single indexed queries, so an
    over-cap or wrong-role request never pays for image decoding at all.
    """
    _require_recognition_available()
    _require_student(db, student_id)
    _require_sample_capacity(db, student_id)

    encoding = require_single_face(encoder.encode_faces(image_bytes))
    _require_no_other_student_matches(db, student_id, encoding)

    document = {
        "student_id": student_id,
        "encoding": encoding,
        "model": encoder.DETECTOR_MODEL,
        "source": source,
        # A real datetime, not an ISO string: the face_encodings validator
        # declares created_at as bsonType "date".
        "created_at": _now(),
        "created_by": created_by,
    }

    result = db[FACE_ENCODINGS].insert_one(document)
    document["_id"] = result.inserted_id

    # Logged without the encoding and without anything derived from the
    # image, so an operational log never becomes a biometric store.
    logger.info("Registered a face sample for student %s", student_id)

    return document


def list_encodings(db, student_id):
    """Newest first -- the most recently captured sample is the one an
    admin is most likely to be reviewing or removing.
    """
    _require_student(db, student_id, require_active=False)

    return list(
        db[FACE_ENCODINGS].find({"student_id": student_id}).sort("created_at", -1)
    )


def delete_encoding(db, student_id, encoding_id):
    """Deleting is scoped by both ids so an encoding id belonging to a
    different student cannot be removed through this student's URL.
    """
    _require_student(db, student_id, require_active=False)

    result = db[FACE_ENCODINGS].delete_one(
        {"_id": encoding_id, "student_id": student_id}
    )
    if result.deleted_count == 0:
        raise NotFoundError("Face encoding not found")


def delete_student_encodings(db, student_id):
    """Erase every sample for one student, returning how many were removed.

    Allowed for a deactivated student on purpose: erasure must stay
    possible after an account is disabled.
    """
    _require_student(db, student_id, require_active=False)

    result = db[FACE_ENCODINGS].delete_many({"student_id": student_id})

    return result.deleted_count


def class_enrollment_status(db, class_id):
    """Return (student, sample_count, last_enrolled_at) for every student on
    a class roster, including those with no samples at all.

    Students with zero samples are the entire point of the endpoint: an
    unenrolled face is silently absent from every future attendance pass,
    so the gap has to be visible before attendance is taken.

    Grouped in Python from one indexed query rather than an aggregation
    pipeline, for the same reason list_enrollments joins that way: a
    roster is small, both queries are index-backed, and the code reads the
    way the rule does.
    """
    pairs = list_enrollments(db, class_id)
    if not pairs:
        return []

    students = [student for _, student in pairs]
    student_ids = [student["_id"] for student in students]

    counts = {}
    latest = {}
    for document in db[FACE_ENCODINGS].find({"student_id": {"$in": student_ids}}):
        owner = document["student_id"]
        counts[owner] = counts.get(owner, 0) + 1
        created_at = document.get("created_at")
        if created_at is not None and (
            latest.get(owner) is None or created_at > latest[owner]
        ):
            latest[owner] = created_at

    return [
        (student, counts.get(student["_id"], 0), latest.get(student["_id"]))
        for student in students
    ]
