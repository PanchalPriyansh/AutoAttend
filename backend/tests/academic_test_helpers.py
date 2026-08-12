"""Shared, in-memory fakes for academic-hierarchy tests.

Provides minimal stand-ins for the `institutes`, `departments`,
`semesters`, `courses`, `classes`, and `class_enrollments` MongoDB
collections so `academic/service.py` and `routes/academic.py` can be
exercised without a live MongoDB connection, following the same "fake
the pymongo driver at the point of use" pattern already used by
auth_test_helpers.py / test_database.py / test_health.py.

`FakeHierarchyCollection` supports exactly what academic/service.py
calls: `find(...).sort(...)`, `find_one`, `count_documents`,
`insert_one`, `find_one_and_update`, `delete_one`. Its `unique_scope`
mirrors the compound unique index declared for the collection in
database/schema.py (e.g. `("institute_id", "code")` for departments),
so a scoped duplicate raises `DuplicateKeyError` the same way the real
index would, and `academic/service.py` translates it into a 409.

Not a test module itself (no `test_` prefix), so pytest does not
collect it; it is imported directly by test modules that need it. No
real credentials or institutional data are used anywhere -- every
value is an obviously-fake, hardcoded test value.
"""

from datetime import datetime, timezone

from auth_test_helpers import FakeUsersCollection
from bson import ObjectId
from database.schema import (
    CLASS_ENROLLMENTS,
    CLASSES,
    COURSES,
    DEPARTMENTS,
    INSTITUTES,
    SEMESTERS,
    USERS,
)
from pymongo.errors import DuplicateKeyError


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _DeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


def _matches(document, query):
    return all(document.get(key) == value for key, value in query.items())


class FakeCursor:
    """Stands in for a pymongo `Cursor`; only `.sort(...)` followed by
    iteration/`list(...)` is used by `academic/service.py`.
    """

    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, field_name, direction=1):
        self._docs.sort(key=lambda d: d.get(field_name), reverse=(direction < 0))
        return self

    def __iter__(self):
        return iter(self._docs)


class FakeHierarchyCollection:
    """Stands in for `db[INSTITUTES]` / `db[DEPARTMENTS]` / ... ; supports
    only what `academic/service.py` calls.
    """

    def __init__(self, documents=None, unique_scope=()):
        self._docs = list(documents or [])
        self._unique_scope = unique_scope

    def find(self, query=None):
        return FakeCursor(d for d in self._docs if _matches(d, query or {}))

    def find_one(self, query):
        for document in self._docs:
            if _matches(document, query):
                return document
        return None

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

    def find_one_and_update(self, query, update, return_document=None):
        for document in self._docs:
            if not _matches(document, query):
                continue
            set_fields = update.get("$set", {})
            merged = dict(document)
            merged.update(set_fields)
            self._raise_if_duplicate(merged, exclude_id=document.get("_id"))
            document.update(set_fields)
            return dict(document)
        return None

    def delete_one(self, query):
        for index, document in enumerate(self._docs):
            if _matches(document, query):
                del self._docs[index]
                return _DeleteResult(1)
        return _DeleteResult(0)


def make_fake_academic_db(
    users=None,
    institutes=None,
    departments=None,
    semesters=None,
    courses=None,
    classes=None,
    class_enrollments=None,
):
    """A combined fake `db` covering `users` plus all five hierarchy
    collections, so a single monkeypatched `get_db()` can back both
    `/api/auth/login` (used to obtain real JWT + CSRF cookies) and the
    `/api/*` hierarchy endpoints under test in the same request cycle.

    Unique scopes mirror the indexes declared in database/schema.py:
    `uniq_code`, `uniq_institute_id_code`, `uniq_department_id_name`,
    `uniq_semester_id_code`, `uniq_course_id_name`.
    """
    return {
        USERS: FakeUsersCollection(users),
        INSTITUTES: FakeHierarchyCollection(institutes, unique_scope=("code",)),
        DEPARTMENTS: FakeHierarchyCollection(
            departments, unique_scope=("institute_id", "code")
        ),
        SEMESTERS: FakeHierarchyCollection(
            semesters, unique_scope=("department_id", "name")
        ),
        COURSES: FakeHierarchyCollection(courses, unique_scope=("semester_id", "code")),
        CLASSES: FakeHierarchyCollection(classes, unique_scope=("course_id", "name")),
        CLASS_ENROLLMENTS: FakeHierarchyCollection(class_enrollments),
    }


def make_institute(*, name="Test Institute", code="TI", **overrides):
    document = {
        "_id": ObjectId(),
        "name": name,
        "code": code,
        "created_at": datetime.now(timezone.utc),
    }
    document.update(overrides)
    return document


def make_department(institute_id, *, name="Test Department", code="TD", **overrides):
    document = {
        "_id": ObjectId(),
        "institute_id": institute_id,
        "name": name,
        "code": code,
        "created_at": datetime.now(timezone.utc),
    }
    document.update(overrides)
    return document


def make_semester(department_id, *, name="Fall Semester", start_date=None, end_date=None, **overrides):
    now = datetime.now(timezone.utc)
    document = {
        "_id": ObjectId(),
        "department_id": department_id,
        "name": name,
        "start_date": start_date or now,
        "end_date": end_date or now,
        "created_at": now,
    }
    document.update(overrides)
    return document


def make_course(semester_id, *, name="Test Course", code="TC101", **overrides):
    document = {
        "_id": ObjectId(),
        "semester_id": semester_id,
        "name": name,
        "code": code,
        "created_at": datetime.now(timezone.utc),
    }
    document.update(overrides)
    return document


def make_class(course_id, *, name="Section A", faculty_id=None, **overrides):
    document = {
        "_id": ObjectId(),
        "course_id": course_id,
        "name": name,
        "faculty_id": faculty_id,
        "created_at": datetime.now(timezone.utc),
    }
    document.update(overrides)
    return document


def make_class_enrollment(class_id, student_id, **overrides):
    document = {
        "_id": ObjectId(),
        "class_id": class_id,
        "student_id": student_id,
        "enrolled_at": datetime.now(timezone.utc),
    }
    document.update(overrides)
    return document


def build_chain_documents():
    """One institute -> department -> semester -> course -> class, linked
    top-down. Returns `(docs, ids)` where `docs` maps level name to the raw
    document and `ids` maps level name to its `_id` as a string, ready to
    drop into URL paths/query strings/JSON bodies.
    """
    institute = make_institute()
    department = make_department(institute["_id"])
    semester = make_semester(department["_id"])
    course = make_course(semester["_id"])
    klass = make_class(course["_id"])

    docs = {
        "institute": institute,
        "department": department,
        "semester": semester,
        "course": course,
        "class": klass,
    }
    ids = {name: str(document["_id"]) for name, document in docs.items()}
    return docs, ids


def build_chain_db(users=None):
    """A fake db pre-seeded with a full, linked five-level chain plus
    whatever users are passed in (for login).
    """
    docs, ids = build_chain_documents()
    fake_db = make_fake_academic_db(
        users=users,
        institutes=[docs["institute"]],
        departments=[docs["department"]],
        semesters=[docs["semester"]],
        courses=[docs["course"]],
        classes=[docs["class"]],
    )
    return fake_db, ids, docs
