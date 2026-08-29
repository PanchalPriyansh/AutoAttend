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

from academic.context import class_hierarchy_context
from attendance.errors import ForbiddenError
from attendance.validators import MAX_EXPORT_SESSIONS, require_records
from common.errors import ConflictError, NotFoundError, ValidationError
from database.schema import (
    ATTENDANCE_RECORDS,
    ATTENDANCE_SESSIONS,
    CLASS_ENROLLMENTS,
    CLASSES,
    FACE_ENCODINGS,
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


def _require_owned_session(db, session_id, faculty_id):
    """Resolve a saved session and confirm its class belongs to the caller.

    Every session-scoped operation goes through here, so a session id is
    never enough on its own to read, correct, or delete attendance.

    The two failures are kept apart on purpose. A session id that does not
    exist is a 404; one that exists under somebody else's class is a 403.
    Collapsing the second into the first to avoid confirming the session
    exists would be withholding a straight answer from an authenticated
    colleague about why they cannot touch it -- the same choice
    require_owned_class already makes for a class.
    """
    session = db[ATTENDANCE_SESSIONS].find_one({"_id": session_id})
    if session is None:
        raise NotFoundError("Attendance session not found")

    require_owned_class(db, session["class_id"], faculty_id)

    return session


# --- Assigned classes -------------------------------------------------


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

    context = class_hierarchy_context(db, classes)
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


# --- History ----------------------------------------------------------


def _session_counts(db, session_ids):
    """Present/total per session, from one grouped aggregation.

    Counting in the database rather than in Python is a deliberate
    departure from _roster_counts above. That one reads a single class's
    enrollments; this one would read every record of every session on the
    page -- a couple of hundred lectures times a full roster -- to produce
    one pair of numbers each. The rows are summed where they live and only
    the totals travel.
    """
    if not session_ids:
        return {}

    pipeline = [
        {"$match": {"session_id": {"$in": session_ids}}},
        {
            "$group": {
                "_id": "$session_id",
                "total": {"$sum": 1},
                "present": {
                    "$sum": {"$cond": [{"$eq": ["$status", "present"]}, 1, 0]}
                },
            }
        },
    ]

    return {
        row["_id"]: {"total": row["total"], "present": row["present"]}
        for row in db[ATTENDANCE_RECORDS].aggregate(pipeline)
    }


def list_sessions(db, class_id, faculty_id, *, date_from, date_to, limit, skip):
    """One page of the attendance recorded for a class, newest lecture first.

    Returns `(entries, total)`, where each entry is a (session, counts)
    pair and `total` counts everything matching the filter regardless of
    paging -- so the screen can say what it is showing a page of.

    The query is an equality on class_id plus a range and sort on date,
    which is the prefix shape of the existing uniq_class_id_date index; no
    additional index is needed to serve it.
    """
    require_owned_class(db, class_id, faculty_id)

    query = {"class_id": class_id}
    date_range = {}
    if date_from is not None:
        date_range["$gte"] = date_from
    if date_to is not None:
        # Inclusive: both bounds are already at UTC midnight, and a session's
        # stored date is too, so an equal value is a match rather than a
        # lecture that falls just outside the range the user asked for.
        date_range["$lte"] = date_to
    if date_range:
        query["date"] = date_range

    total = db[ATTENDANCE_SESSIONS].count_documents(query)
    sessions = list(
        db[ATTENDANCE_SESSIONS].find(query).sort([("date", -1)]).skip(skip).limit(limit)
    )

    counts = _session_counts(db, [session["_id"] for session in sessions])

    entries = [(session, counts.get(session["_id"], {})) for session in sessions]

    return entries, total


def get_session_by_id(db, session_id, faculty_id):
    """One saved session with its records, addressed by id."""
    session = _require_owned_session(db, session_id, faculty_id)

    return session, _load_session_records(db, session_id)


# --- Export -----------------------------------------------------------
#
# Two shapes over one range: the CSV's register (a row per student per
# lecture) and the PDF's report (a row per student, plus an index of the
# lectures the figures came from). Both are reads and neither writes so
# much as a record that it happened.
#
# They deliberately share `_export_sessions`. The owner check and the
# session bound are the two things that must be identical between them --
# a second copy is how one format ends up enforcing something the other
# does not -- while the fetch below it differs because the questions do.


def _export_sessions(db, class_id, faculty_id, *, date_from, date_to):
    """`(class_document, sessions)` -- the lectures one export covers,
    oldest first.

    Authorization, then the range, then the bound, in that order: nothing
    is counted for a class the caller does not hold, and nothing is
    fetched for a range too wide to answer.

    The class document comes back rather than being re-read by each
    caller: `require_owned_class` has already fetched it, and both
    exports need its name for the filename.

    Ascending, unlike `list_sessions`. A screen leads with the lecture you
    just took; a file is read from the top and a register runs forwards.
    """
    class_document = require_owned_class(db, class_id, faculty_id)

    query = {"class_id": class_id}
    date_range = {}
    if date_from is not None:
        date_range["$gte"] = date_from
    if date_to is not None:
        # Inclusive, matching list_sessions: both bounds and the stored
        # date are at UTC midnight, so an equal value is a match.
        date_range["$lte"] = date_to
    if date_range:
        query["date"] = date_range

    total = db[ATTENDANCE_SESSIONS].count_documents(query)
    if total > MAX_EXPORT_SESSIONS:
        raise ValidationError(
            f"That range covers {total} lectures, and an export can cover at "
            f"most {MAX_EXPORT_SESSIONS}. Narrow the date range and try again."
        )

    return class_document, list(
        db[ATTENDANCE_SESSIONS].find(query).sort([("date", 1)])
    )


def _students_by_id(db, student_ids):
    """One `$in` lookup for a batch of students, keyed by id."""
    if not student_ids:
        return {}

    return {
        student["_id"]: student
        for student in db[USERS].find({"_id": {"$in": list(student_ids)}})
    }


def _sort_key(student):
    return (student.get("name") or "").lower()


def export_records(db, class_id, faculty_id, *, date_from, date_to):
    """The register: `(class_document, context, triples)`.

    Each triple is `(session, record, student)`, ordered by lecture date
    ascending and then by student name -- the order the file is written
    in, decided here because the service is what knows both orderings.

    Two batched queries rather than `_load_session_records` per session:
    a term of lectures would otherwise be a hundred round trips to build
    one file.
    """
    class_document, sessions = _export_sessions(
        db, class_id, faculty_id, date_from=date_from, date_to=date_to
    )
    context = class_hierarchy_context(db, [class_document]).get(class_id, {})

    if not sessions:
        return class_document, context, []

    session_ids = [session["_id"] for session in sessions]
    records = list(
        db[ATTENDANCE_RECORDS].find({"session_id": {"$in": session_ids}})
    )
    students = _students_by_id(db, {record["student_id"] for record in records})

    by_session = {}
    for record in records:
        student = students.get(record["student_id"])
        if student is None:
            # Should be unreachable -- nothing in this project hard-deletes
            # a user. Skipped rather than raised, mirroring
            # _load_session_records: one bad row must not cost the whole
            # export.
            logger.warning(
                "Attendance record %s references a missing student", record["_id"]
            )
            continue
        by_session.setdefault(record["session_id"], []).append((record, student))

    triples = []
    for session in sessions:
        pairs = by_session.get(session["_id"], [])
        pairs.sort(key=lambda pair: _sort_key(pair[1]))
        triples.extend((session, record, student) for record, student in pairs)

    return class_document, context, triples


def _student_counts(db, session_ids):
    """Present/total per student across a batch of sessions, from one
    grouped aggregation.

    The per-student twin of `_session_counts`, and grouped in the database
    for the same reason: summing a term of rosters in Python would pull
    every record of every lecture across the wire to produce two numbers
    per person.
    """
    if not session_ids:
        return {}

    pipeline = [
        {"$match": {"session_id": {"$in": session_ids}}},
        {
            "$group": {
                "_id": "$student_id",
                "total": {"$sum": 1},
                "present": {
                    "$sum": {"$cond": [{"$eq": ["$status", "present"]}, 1, 0]}
                },
            }
        },
    ]

    return {
        row["_id"]: {"total": row["total"], "present": row["present"]}
        for row in db[ATTENDANCE_RECORDS].aggregate(pipeline)
    }


def export_summary(db, class_id, faculty_id, *, date_from, date_to):
    """The report: `(class_document, context, faculty, lectures, standings)`.

    `lectures` is `(session, counts)` per recorded lecture, oldest first.
    `standings` is `(student, present, total)` per student, by name.

    **Who appears is the union of the roster and everyone holding a record
    in range.** The roster alone would drop a student unenrolled mid-term
    whose lectures are still in the range and still in the CSV, and two
    files describing the same range must not disagree about who was in
    it. The records alone would drop an enrolled student with nothing
    recorded yet -- which is exactly the row a head of department is
    looking for.

    `total` is each student's own record count, never the lecture count.
    A student enrolled halfway through a term has fewer records, and
    dividing by the class's session count would mark them down for
    lectures held before they were on the roster.
    """
    class_document, sessions = _export_sessions(
        db, class_id, faculty_id, date_from=date_from, date_to=date_to
    )
    context = class_hierarchy_context(db, [class_document]).get(class_id, {})
    faculty = db[USERS].find_one({"_id": faculty_id})

    session_ids = [session["_id"] for session in sessions]
    counts = _session_counts(db, session_ids)
    lectures = [(session, counts.get(session["_id"], {})) for session in sessions]

    per_student = _student_counts(db, session_ids)

    roster = {
        student["_id"]: student
        for _, student in list_enrollments(db, class_id)
    }
    # Anyone with a record but no longer enrolled -- fetched separately
    # rather than assumed absent, so the two exports agree on who was in
    # the range.
    missing = set(per_student) - set(roster)
    students = {**roster, **_students_by_id(db, missing)}

    standings = [
        (
            student,
            per_student.get(student_id, {}).get("present", 0),
            per_student.get(student_id, {}).get("total", 0),
        )
        for student_id, student in students.items()
    ]
    standings.sort(key=lambda standing: _sort_key(standing[0]))

    return class_document, context, faculty, lectures, standings


def update_session_records(db, session_id, faculty_id, records):
    """Correct the present/absent list of a session already recorded.

    Only statuses change. `class_id`, `date`, `source`, `created_at`, and
    `taken_by` are all immutable here -- `taken_by` in particular still
    names whoever took the lecture, because a correction does not make the
    person making it the one who was in the room. `updated_at` and
    `updated_by` are the only session fields this touches.

    The list is validated against the students already in the session
    rather than against today's roster. A session records who was
    considered on the day; re-deriving that set from the current roster
    would quietly add a student enrolled since, or demand the removal of
    one unenrolled since, and either would rewrite history.
    """
    session = _require_owned_session(db, session_id, faculty_id)

    stored = _load_session_records(db, session_id)
    if not stored:
        raise ValidationError("This session has no attendance records to correct")

    normalized = require_records(
        records,
        [record["student_id"] for record, _ in stored],
        scope="this session",
    )

    now = _now()
    # Updated in place rather than deleted and reinserted: each record keeps
    # the created_at it was first written with, and a failure part-way
    # through cannot leave the session with no records at all. The roster
    # bounds the loop, so this is a handful of small writes.
    for record in normalized:
        db[ATTENDANCE_RECORDS].update_one(
            {"session_id": session_id, "student_id": record["student_id"]},
            {"$set": {"status": record["status"], "marked_by": record["marked_by"]}},
        )

    db[ATTENDANCE_SESSIONS].update_one(
        {"_id": session_id},
        {"$set": {"updated_at": now, "updated_by": faculty_id}},
    )

    logger.info(
        "Corrected attendance for class %s on %s (%d students) by %s",
        session["class_id"],
        session["date"].date().isoformat(),
        len(normalized),
        faculty_id,
    )

    session = {**session, "updated_at": now, "updated_by": faculty_id}

    return session, _load_session_records(db, session_id)


def delete_session(db, session_id, faculty_id):
    """Remove a session and the records belonging to it.

    Records first, then the session. A failure between the two leaves a
    session with no records -- visible and fixable -- rather than records
    pointing at a session that no longer exists, which nothing in 09-11
    would be able to read but everything would still count.

    Nothing is soft-deleted. Unlike a user, a session is referenced only by
    its own records, and one taken against the wrong class is not a
    historical fact worth keeping. That is why the UI confirms first.
    """
    session = _require_owned_session(db, session_id, faculty_id)

    deleted = db[ATTENDANCE_RECORDS].delete_many({"session_id": session_id})
    db[ATTENDANCE_SESSIONS].delete_one({"_id": session_id})

    logger.info(
        "Deleted attendance for class %s on %s (%d records) by %s",
        session["class_id"],
        session["date"].date().isoformat(),
        deleted.deleted_count,
        faculty_id,
    )

    return deleted.deleted_count
