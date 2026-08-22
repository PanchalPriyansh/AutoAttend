"""Business logic for capturing and saving attendance.

No Flask request/response objects appear in any signature here -- HTTP
concerns belong to routes/attendance.py, which maps the domain exceptions
from common/errors.py, attendance/errors.py, and recognition/errors.py
onto status codes. All computer vision is delegated to the recognition
package, the only place allowed to import the CV libraries, so this
module stays runnable and testable without them.

Two rules shape everything below:

  - **Recognition proposes; faculty decide.** `recognize` returns a
    partition of the roster and writes nothing. Attendance is stored only
    by `save_session`, from a list a human submitted. A face match is
    evidence that somebody was in the room, never proof, and the stored
    record is the faculty member's statement rather than the pipeline's.

  - **The capture is never persisted.** The image or video exists as
    bytes for the duration of `recognize`; nothing writes it to a
    document, a file, or a log line. A classroom frame holds many people
    who never agreed to being photographed, so the derived decision is
    kept and the picture is not.
"""

import logging
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError

from attendance.errors import ForbiddenError
from attendance.validators import require_records
from common.errors import ConflictError, NotFoundError, ValidationError
from database.schema import (
    ATTENDANCE_RECORDS,
    ATTENDANCE_SESSIONS,
    CLASS_ENROLLMENTS,
    CLASSES,
    COURSES,
    DEPARTMENTS,
    FACE_ENCODINGS,
    INSTITUTES,
    SEMESTERS,
    USERS,
)
from recognition import encoder, frames, matcher
from recognition.errors import RecognitionUnavailableError
from users.assignments import list_enrollments

logger = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc)


# --- Authorization ----------------------------------------------------


def require_owned_class(db, class_id, faculty_id):
    """Resolve a class and confirm it belongs to the acting faculty member.

    Every class-scoped operation in this feature goes through here.
    `role_required("faculty")` settles what the caller *is* from the token
    alone; only the document can settle whether this particular class is
    theirs, so the check cannot live in the decorator and must not be left
    to the React app.

    A class with no assigned faculty is refused for the same reason as one
    assigned elsewhere: nobody holds it, so nobody may record attendance
    against it until an admin assigns someone.
    """
    document = db[CLASSES].find_one({"_id": class_id})
    if document is None:
        raise NotFoundError("Class not found")

    if document.get("faculty_id") != faculty_id:
        raise ForbiddenError("You are not assigned to this class")

    return document


# --- Assigned classes -------------------------------------------------


def _hierarchy_context(db, classes):
    """Resolve each class's course/semester/department/institute names.

    Four batched `$in` lookups, one per level, rather than walking the
    chain per class: a faculty member holding eight classes would
    otherwise cost thirty-two queries to render one dropdown.

    A missing parent leaves the name as None instead of raising. The
    hierarchy blocks deletes while children exist, so this should be
    unreachable; one orphaned row should not take down the whole class
    list if it ever happens.
    """
    # (name in the result, collection to read, field pointing one level
    # further up). The chain starts at each class's course_id below.
    levels = [
        ("course", COURSES, "semester_id"),
        ("semester", SEMESTERS, "department_id"),
        ("department", DEPARTMENTS, "institute_id"),
        ("institute", INSTITUTES, None),
    ]

    # Maps each class to the id it currently points at, one level up per
    # iteration: first its course, then that course's semester, and so on.
    parent_ids = {
        document["_id"]: document.get("course_id") for document in classes
    }
    context = {document["_id"]: {} for document in classes}

    for name, collection, next_field in levels:
        wanted = [value for value in parent_ids.values() if value is not None]
        documents = (
            {
                document["_id"]: document
                for document in db[collection].find({"_id": {"$in": wanted}})
            }
            if wanted
            else {}
        )

        next_parent_ids = {}
        for class_id, parent_id in parent_ids.items():
            parent = documents.get(parent_id)
            context[class_id][name] = parent.get("name") if parent else None
            next_parent_ids[class_id] = (
                parent.get(next_field) if parent and next_field else None
            )

        parent_ids = next_parent_ids

    return context


def _roster_counts(db, class_ids):
    """Counted in Python from one indexed query rather than an aggregation,
    for the same reason list_enrollments joins that way: the query rides
    `class_id`, the result is small, and the code reads the way the rule
    does.
    """
    counts = {}
    for enrollment in db[CLASS_ENROLLMENTS].find(
        {"class_id": {"$in": class_ids}}, {"class_id": 1}
    ):
        class_id = enrollment["class_id"]
        counts[class_id] = counts.get(class_id, 0) + 1

    return counts


def list_assigned_classes(db, faculty_id):
    """Return (class, context, student_count) for every class this faculty
    member holds, sorted the way they are read: by course, then class.

    An empty list is an ordinary answer -- a faculty member with no
    assignments yet is a normal state, not an error.
    """
    classes = list(db[CLASSES].find({"faculty_id": faculty_id}))
    if not classes:
        return []

    context = _hierarchy_context(db, classes)
    counts = _roster_counts(db, [document["_id"] for document in classes])

    entries = [
        (document, context[document["_id"]], counts.get(document["_id"], 0))
        for document in classes
    ]
    entries.sort(
        key=lambda entry: (
            (entry[1].get("course") or "").lower(),
            (entry[0].get("name") or "").lower(),
        )
    )

    return entries


# --- Recognition ------------------------------------------------------


def _require_recognition_available():
    if not encoder.is_available():
        raise RecognitionUnavailableError(
            "Face recognition is unavailable on the server. The recognition "
            "library is not installed."
        )


def _require_video_support_available():
    if not frames.is_available():
        raise RecognitionUnavailableError(
            "Video capture is unavailable on the server. OpenCV is not "
            "installed. Capture a photo instead."
        )


def _roster_encodings(db, student_ids):
    """Load face encodings for this roster only.

    Scoped to the class rather than reading the whole collection for two
    reasons: a student from another class becomes structurally incapable
    of being marked present here, and the comparison cost stays
    proportional to one roster instead of the whole institute.
    """
    by_student = {}
    for document in db[FACE_ENCODINGS].find(
        {"student_id": {"$in": student_ids}}, {"student_id": 1, "encoding": 1}
    ):
        by_student.setdefault(document["student_id"], []).append(document["encoding"])

    return by_student


def _detect(image_frames):
    return [encoder.encode_faces(image_bytes) for image_bytes in image_frames]


def recognize(db, class_id, faculty_id, *, image_bytes=None, video_bytes=None,
              content_type=None):
    """Match the faces in a capture against this class's roster.

    Returns a proposal partitioning every enrolled student into exactly
    one of three groups, and writes nothing at all.

    Checks run authorization-first: a faculty member who does not hold
    this class gets the same 403 whether or not the server happens to have
    the recognition libraries installed. Only then does availability
    matter, and only then is the roster read -- so a rejected request
    never pays for image decoding.
    """
    require_owned_class(db, class_id, faculty_id)
    _require_recognition_available()

    is_video = video_bytes is not None
    if is_video:
        _require_video_support_available()

    roster = list_enrollments(db, class_id)
    students = [student for _, student in roster]
    encodings_by_student = _roster_encodings(
        db, [student["_id"] for student in students]
    )

    image_frames = (
        frames.extract_frames(video_bytes, content_type) if is_video else [image_bytes]
    )
    frames_encodings = _detect(image_frames)

    # Flattened to (student_id, encoding) pairs because several samples per
    # student is normal: each is an independent chance to match the same
    # person, and the matcher only needs to know which student a hit
    # belongs to.
    known = [
        (student_id, encoding)
        for student_id, encodings in encodings_by_student.items()
        for encoding in encodings
    ]
    best_by_student, detected_faces, unknown_faces = matcher.match_frames(
        frames_encodings, known
    )

    recognized = []
    unrecognized = []
    not_enrolled = []
    for student in students:
        samples = encodings_by_student.get(student["_id"], [])
        if not samples:
            # Kept apart from `unrecognized` deliberately. The camera did
            # not fail to find this student -- nobody ever registered their
            # face, so the pipeline could not have found them. Marking them
            # absent on that basis would record a data-entry gap as an
            # attendance fact, and the faculty member has to see which of
            # the two they are looking at.
            not_enrolled.append(student)
            continue

        distance = best_by_student.get(student["_id"])
        if distance is None:
            unrecognized.append((student, len(samples)))
            continue

        recognized.append((student, distance, matcher.confidence_for(distance)))

    # Logged without anything derived from the capture, so an operational
    # log never becomes a record of who was photographed.
    logger.info(
        "Ran attendance recognition for class %s: %d of %d students matched",
        class_id,
        len(recognized),
        len(students),
    )

    return {
        "class_id": class_id,
        "source": "video" if is_video else "photo",
        "frames_analyzed": len(image_frames),
        "detected_faces": detected_faces,
        "unknown_faces": unknown_faces,
        "recognized": recognized,
        "unrecognized": unrecognized,
        "not_enrolled": not_enrolled,
    }


# --- Sessions ---------------------------------------------------------


def _load_session_records(db, session_id, students_by_id=None):
    """Pair each stored record with its student, sorted by name.

    `students_by_id` lets a caller that already has the roster in hand
    skip the second query; without it the students are fetched with one
    `$in` lookup.
    """
    records = list(db[ATTENDANCE_RECORDS].find({"session_id": session_id}))
    if not records:
        return []

    if students_by_id is None:
        student_ids = [record["student_id"] for record in records]
        students_by_id = {
            student["_id"]: student
            for student in db[USERS].find({"_id": {"$in": student_ids}})
        }

    pairs = []
    for record in records:
        student = students_by_id.get(record["student_id"])
        if student is None:
            # Should be unreachable: nothing in the project hard-deletes a
            # user. Skipped rather than raised so one bad row cannot take
            # down a whole session.
            logger.warning(
                "Attendance record %s references a missing student", record["_id"]
            )
            continue
        pairs.append((record, student))

    pairs.sort(key=lambda pair: (pair[1].get("name") or "").lower())
    return pairs


def get_session(db, class_id, date, faculty_id):
    """The saved session for one class on one date, with its records."""
    require_owned_class(db, class_id, faculty_id)

    session = db[ATTENDANCE_SESSIONS].find_one({"class_id": class_id, "date": date})
    if session is None:
        raise NotFoundError("Attendance has not been taken for this class on this date")

    return session, _load_session_records(db, session["_id"])


def save_session(db, class_id, date, faculty_id, *, source, records, replace):
    """Store a reviewed present/absent list.

    Returns `(session, record_pairs, created)`; `created` distinguishes a
    new session from a replaced one so the route can answer 201 or 200.

    Everything is validated before anything is written. That ordering is
    what makes `replace` safe: a payload that turns out to be malformed
    must not have already deleted the attendance it was going to replace.
    """
    require_owned_class(db, class_id, faculty_id)

    roster = list_enrollments(db, class_id)
    if not roster:
        raise ValidationError(
            "This class has no enrolled students, so there is no attendance to record"
        )

    students_by_id = {student["_id"]: student for _, student in roster}
    normalized = require_records(records, [student["_id"] for _, student in roster])

    now = _now()
    existing = db[ATTENDANCE_SESSIONS].find_one({"class_id": class_id, "date": date})
    if existing is not None and not replace:
        raise ConflictError(
            "Attendance has already been recorded for this class on this date"
        )

    if existing is None:
        session = {
            "class_id": class_id,
            # A real datetime at UTC midnight, not an ISO string: the
            # attendance_sessions validator declares date as bsonType
            # "date", and uniq_class_id_date only means one-session-per-
            # lecture because attendance/validators.py normalised it.
            "date": date,
            "source": source,
            "taken_by": faculty_id,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = db[ATTENDANCE_SESSIONS].insert_one(session)
        except DuplicateKeyError as exc:
            # Two saves for the same lecture raced. The unique index is
            # what actually decided it; the loser gets the same 409 the
            # find_one above would have produced a moment earlier.
            raise ConflictError(
                "Attendance has already been recorded for this class on this date"
            ) from exc

        session["_id"] = result.inserted_id
        created = True
    else:
        session = {
            **existing,
            "source": source,
            "taken_by": faculty_id,
            "updated_at": now,
        }
        db[ATTENDANCE_SESSIONS].update_one(
            {"_id": existing["_id"]},
            {"$set": {"source": source, "taken_by": faculty_id, "updated_at": now}},
        )
        db[ATTENDANCE_RECORDS].delete_many({"session_id": existing["_id"]})
        created = False

    documents = [
        {
            "session_id": session["_id"],
            # Copied from the session rather than taken from the request,
            # so a record can never claim a different class than its parent.
            "class_id": class_id,
            "student_id": record["student_id"],
            "status": record["status"],
            "marked_by": record["marked_by"],
            "created_at": now,
        }
        for record in normalized
    ]
    db[ATTENDANCE_RECORDS].insert_many(documents)

    logger.info(
        "%s attendance for class %s on %s (%d students)",
        "Recorded" if created else "Replaced",
        class_id,
        date.date().isoformat(),
        len(documents),
    )

    pairs = _load_session_records(db, session["_id"], students_by_id)

    return session, pairs, created
