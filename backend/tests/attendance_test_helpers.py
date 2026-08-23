"""Shared, in-memory fakes for attendance-capture and attendance-history
tests (07-attendance-capture.md, 08-faculty-attendance-history.md).

`attendance/service.py` reads/writes eight collections (`users`,
`institutes`, `departments`, `semesters`, `courses`, `classes`,
`class_enrollments`, `face_encodings`) plus the two this feature owns
(`attendance_sessions`, `attendance_records`), and several of those reads
use `$in` with a projection (`_hierarchy_context`, `_roster_counts`,
`_roster_encodings`) that the existing `FakeHierarchyCollection`
(academic_test_helpers.py) does not support -- it only matches plain
equality and does not accept a `projection` argument at all. Rather than
extend that shared fixture (used by many other suites) or bolt a second
matcher onto it, `FakeCollection` below is a single fake, reused for every
collection this feature touches, built on `auth_test_helpers._matches`
(already extended with `$in`/`$ne`/`$regex`/`$or`/`$and` support) so it
mirrors real MongoDB query semantics for exactly the operators this
feature's service layer issues.

08-faculty-attendance-history.md added two more query shapes: `list_sessions`
sorts/pages a `find(...)` cursor (`.sort(...).skip(...).limit(...)`), and
`_session_counts` group-counts `attendance_records` with a single
`aggregate(...)` call. `FakeCursor.sort`/`.skip`/`.limit` and
`FakeCollection.aggregate` below support exactly those two shapes -- not a
general aggregation engine, mirroring how narrowly the rest of this file is
already scoped.

Not a test module itself (no `test_` prefix), so pytest does not collect
it. No real credentials, institutional data, or biometric data are used
anywhere -- every "encoding" is a short, obviously-synthetic vector built
by `synthetic_encoding()`, and `fake_closest_match` never touches
`face_recognition`/`numpy`/`dlib`; it is a monkeypatch replacement for
`recognition.encoder.closest_match` used throughout the attendance test
suite so no real biometric computation happens anywhere in these tests.
"""

from datetime import datetime, timezone

from academic_test_helpers import (
    make_class,
    make_class_enrollment,
    make_course,
    make_department,
    make_institute,
    make_semester,
)
from auth_test_helpers import FakeUsersCollection, _matches, make_user
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

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


# --- Result stand-ins ------------------------------------------------------


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


class _UpdateResult:
    def __init__(self, matched_count, modified_count):
        self.matched_count = matched_count
        self.modified_count = modified_count


class _DeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


def _apply_projection(document, projection):
    """A narrow stand-in for pymongo's inclusion projection -- only the
    `{"field": 1, ...}` form is used anywhere in attendance/service.py.
    """
    if not projection:
        return document

    included = [key for key, value in projection.items() if key != "_id" and value]
    result = {key: document[key] for key in included if key in document}
    if projection.get("_id", 1):
        result["_id"] = document.get("_id")

    return result


class FakeCursor:
    """Stands in for a pymongo `Cursor`. `sort`/`skip`/`limit` are
    chainable, mirroring the real cursor API, and only need to support the
    one shape `list_sessions` issues: a single-key sort followed by
    `skip`/`limit` paging.
    """

    def __init__(self, docs):
        self._docs = list(docs)

    def __iter__(self):
        return iter(self._docs)

    def __len__(self):
        return len(self._docs)

    def sort(self, keys):
        """`keys` is pymongo's list-of-`(field, direction)` form, e.g.
        `[("date", -1)]`. Applied least-significant-key-first with a
        stable sort so multiple keys compose correctly, even though
        `list_sessions` only ever passes one.
        """
        for field, direction in reversed(keys):
            self._docs.sort(key=lambda d: d.get(field), reverse=(direction == -1))
        return self

    def skip(self, count):
        self._docs = self._docs[count:]
        return self

    def limit(self, count):
        if count:
            self._docs = self._docs[:count]
        return self


class FakeCollection:
    """A single fake standing in for every collection
    `attendance/service.py` (and, through it, `users/assignments.py`)
    touches other than `users` -- `institutes`, `departments`,
    `semesters`, `courses`, `classes`, `class_enrollments`,
    `face_encodings`, `attendance_sessions`, and `attendance_records`.

    `unique_scope` mirrors a collection's compound unique index (e.g.
    `("class_id", "date")` for attendance_sessions) so a scoped duplicate
    raises `DuplicateKeyError` the same way the real index would, and
    `attendance/service.py` translates the race case into a 409 exactly
    as it does against real MongoDB.
    """

    def __init__(self, documents=None, unique_scope=()):
        self._docs = list(documents or [])
        self._unique_scope = unique_scope

    def find_one(self, query):
        for document in self._docs:
            if _matches(document, query):
                return document
        return None

    def find(self, query=None, projection=None):
        matches = (d for d in self._docs if _matches(d, query or {}))
        return FakeCursor(_apply_projection(d, projection) for d in matches)

    def count_documents(self, query, limit=None):
        count = sum(1 for d in self._docs if _matches(d, query))
        return min(count, limit) if limit else count

    def _raise_if_duplicate(self, candidate, exclude_id=None):
        if not self._unique_scope or not all(k in candidate for k in self._unique_scope):
            return
        scope = {k: candidate[k] for k in self._unique_scope}
        for document in self._docs:
            if exclude_id is not None and document.get("_id") == exclude_id:
                continue
            if _matches(document, scope):
                raise DuplicateKeyError(
                    "E11000 duplicate key error collection: fake scoped index"
                )

    def insert_one(self, document):
        self._raise_if_duplicate(document)
        stored = dict(document)
        stored.setdefault("_id", ObjectId())
        self._docs.append(stored)
        return _InsertOneResult(stored["_id"])

    def insert_many(self, documents):
        inserted_ids = []
        # Duplicate-checked one at a time and only appended once every
        # candidate has cleared the check, mirroring how a real unique
        # index would refuse the whole batch on the first collision rather
        # than leaving a partial insert behind.
        prepared = []
        for document in documents:
            self._raise_if_duplicate(document)
            stored = dict(document)
            stored.setdefault("_id", ObjectId())
            prepared.append(stored)
        for stored in prepared:
            self._docs.append(stored)
            inserted_ids.append(stored["_id"])
        return _InsertManyResult(inserted_ids)

    def update_one(self, query, update):
        for document in self._docs:
            if _matches(document, query):
                document.update((update or {}).get("$set", {}))
                return _UpdateResult(1, 1)
        return _UpdateResult(0, 0)

    def delete_one(self, query):
        for index, document in enumerate(self._docs):
            if _matches(document, query):
                del self._docs[index]
                return _DeleteResult(1)
        return _DeleteResult(0)

    def delete_many(self, query):
        remaining = []
        deleted = 0
        for document in self._docs:
            if _matches(document, query):
                deleted += 1
            else:
                remaining.append(document)
        self._docs = remaining
        return _DeleteResult(deleted)

    def aggregate(self, pipeline):
        """A narrow stand-in for pymongo's `aggregate` -- handles exactly
        the pipeline shape `attendance/service.py::_session_counts` issues
        over `attendance_records`: a `$match` on
        `session_id: {"$in": [...]}` followed by a `$group` by
        `session_id` computing `total` (a count) and `present` (a
        conditional sum on `status == "present"`).

        Not a general aggregation engine -- any other pipeline shape is a
        signal this fake needs to grow deliberately, not a bug to silently
        paper over, so it is not attempted here.
        """
        match_stage = pipeline[0]["$match"]
        matched = [d for d in self._docs if _matches(d, match_stage)]

        groups = {}
        for document in matched:
            bucket = groups.setdefault(document["session_id"], {"total": 0, "present": 0})
            bucket["total"] += 1
            if document.get("status") == "present":
                bucket["present"] += 1

        return [
            {"_id": session_id, "total": bucket["total"], "present": bucket["present"]}
            for session_id, bucket in groups.items()
        ]


def make_fake_attendance_db(
    *,
    users=None,
    institutes=None,
    departments=None,
    semesters=None,
    courses=None,
    classes=None,
    class_enrollments=None,
    face_encodings=None,
    attendance_sessions=None,
    attendance_records=None,
):
    """A fake `db` covering exactly the collections
    `backend/routes/attendance.py` / `attendance/service.py` touch,
    keyed by the real collection-name constants from `database/schema.py`.

    Unique scopes mirror `uniq_course_id_name` (classes),
    `uniq_class_id_student_id` (class_enrollments),
    `uniq_class_id_date` (attendance_sessions), and
    `uniq_session_id_student_id` (attendance_records).
    """
    return {
        USERS: FakeUsersCollection(users),
        INSTITUTES: FakeCollection(institutes),
        DEPARTMENTS: FakeCollection(departments),
        SEMESTERS: FakeCollection(semesters),
        COURSES: FakeCollection(courses),
        CLASSES: FakeCollection(classes, unique_scope=("course_id", "name")),
        CLASS_ENROLLMENTS: FakeCollection(
            class_enrollments, unique_scope=("class_id", "student_id")
        ),
        FACE_ENCODINGS: FakeCollection(face_encodings),
        ATTENDANCE_SESSIONS: FakeCollection(
            attendance_sessions, unique_scope=("class_id", "date")
        ),
        ATTENDANCE_RECORDS: FakeCollection(
            attendance_records, unique_scope=("session_id", "student_id")
        ),
    }


# --- Document builders -----------------------------------------------------


def make_face_encoding(
    student_id,
    *,
    encoding=None,
    model="hog",
    source="upload",
    created_by=None,
    created_at=None,
    **overrides,
):
    """Builds a raw `face_encodings` document. `encoding` defaults to a
    short, obviously-synthetic vector from `synthetic_encoding()` -- never
    a real face_recognition descriptor derived from an actual photograph.
    """
    document = {
        "_id": ObjectId(),
        "student_id": student_id,
        "encoding": encoding if encoding is not None else synthetic_encoding(0.0),
        "model": model,
        "source": source,
        "created_at": created_at or datetime.now(timezone.utc),
        "created_by": created_by or ObjectId(),
    }
    document.update(overrides)
    return document


def make_attendance_session(
    class_id,
    *,
    date=None,
    source="photo",
    taken_by=None,
    created_at=None,
    updated_at=None,
    **overrides,
):
    now = created_at or datetime.now(timezone.utc)
    document = {
        "_id": ObjectId(),
        "class_id": class_id,
        "date": date or now.replace(hour=0, minute=0, second=0, microsecond=0),
        "source": source,
        "taken_by": taken_by or ObjectId(),
        "created_at": now,
        "updated_at": updated_at or now,
    }
    document.update(overrides)
    return document


def make_attendance_record(
    session_id,
    class_id,
    student_id,
    *,
    status="present",
    marked_by="recognition",
    created_at=None,
    **overrides,
):
    document = {
        "_id": ObjectId(),
        "session_id": session_id,
        "class_id": class_id,
        "student_id": student_id,
        "status": status,
        "marked_by": marked_by,
        "created_at": created_at or datetime.now(timezone.utc),
    }
    document.update(overrides)
    return document


def make_session_with_records(
    class_id,
    students,
    *,
    statuses=None,
    marked_by=None,
    taken_by=None,
    date=None,
    source="photo",
    created_at=None,
    updated_at=None,
    **session_overrides,
):
    """A saved session plus one record per student in `students`, wired
    together the way `save_session` would have left them (same
    `session_id`, `class_id` denormalised onto every record).

    `statuses`/`marked_by` default every student to `"present"` /
    `"recognition"` and can be overridden per test with a same-length
    list, in `students` order. Returns `(session, records)`.
    """
    session = make_attendance_session(
        class_id, date=date, source=source, taken_by=taken_by,
        created_at=created_at, updated_at=updated_at, **session_overrides,
    )
    statuses = statuses if statuses is not None else ["present"] * len(students)
    marked_by_values = marked_by if marked_by is not None else ["recognition"] * len(students)
    records = [
        make_attendance_record(
            session["_id"], class_id, student["_id"], status=status, marked_by=mb,
        )
        for student, status, mb in zip(students, statuses, marked_by_values)
    ]
    return session, records


def build_owned_class_with_roster(faculty_id, *, student_count=2, course_id=None):
    """A class assigned to `faculty_id` with `student_count` enrolled
    students and no face samples yet -- the common starting point most
    attendance tests build on, adding `face_encodings` themselves.

    Returns `(klass, students, enrollments)`.
    """
    course_id = course_id or ObjectId()
    klass = make_class(course_id, faculty_id=faculty_id)
    students = [
        make_user(email=f"roster{i}-{ObjectId()}@college.test", role="student")
        for i in range(student_count)
    ]
    enrollments = [make_class_enrollment(klass["_id"], student["_id"]) for student in students]
    return klass, students, enrollments


# --- Synthetic recognition helpers ------------------------------------------
#
# `recognition.encoder.closest_match` is the only function anywhere in the
# project that calls face_recognition/numpy. Every test in this suite
# monkeypatches it to `fake_closest_match` below (or an equivalent), so the
# CV/ML libraries are never imported and no real biometric computation ever
# happens. `synthetic_encoding` produces a 128-length vector purely to keep
# the shape realistic (mirroring FACE_ENCODINGS_VALIDATOR); only its first
# element is compared, and it is never derived from anything resembling a
# real face descriptor.


def synthetic_encoding(value):
    return [round(value + 0.0001 * i, 6) for i in range(128)]


def fake_distance(candidate, known):
    return round(abs(candidate[0] - known[0]), 6)


def fake_closest_match(candidate, known_encodings):
    """A drop-in replacement for `recognition.encoder.closest_match` that
    never touches face_recognition/numpy. Nearest by the first element of
    each synthetic vector, mirroring the real function's `(index,
    distance)` contract.
    """
    if not known_encodings:
        return None

    best_index = min(
        range(len(known_encodings)),
        key=lambda i: fake_distance(candidate, known_encodings[i]),
    )
    return best_index, fake_distance(candidate, known_encodings[best_index])


__all__ = [
    "FakeCollection",
    "make_fake_attendance_db",
    "make_face_encoding",
    "make_attendance_session",
    "make_attendance_record",
    "make_session_with_records",
    "build_owned_class_with_roster",
    "synthetic_encoding",
    "fake_distance",
    "fake_closest_match",
    "make_user",
    "make_institute",
    "make_department",
    "make_semester",
    "make_course",
    "make_class",
    "make_class_enrollment",
]
