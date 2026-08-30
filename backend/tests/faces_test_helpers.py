"""Shared, in-memory fakes for face-enrolment tests (06-face-enrollment.md).

Builds on the fakes that already exist rather than duplicating them:
`FakeUsersCollection` (auth_test_helpers.py) backs `users`,
`FakeHierarchyCollection` (academic_test_helpers.py) backs `classes` /
`class_enrollments`, and `FakeFaceEncodingsCollection` below is the new
fake backing `face_encodings`, following the same "fake the pymongo
driver at the point of use" pattern used throughout `backend/tests/`.
There is no mongomock and no real test database in this project, by
deliberate convention -- every collection is a hand-written fake.

`FakeFaceEncodingsCollection` supports exactly what
`recognition/service.py` calls: `count_documents`, `find` (including the
`$ne` / `$in` operators on `student_id`, an inclusion `projection`, and
`.sort("created_at", -1)`), `insert_one`, `delete_one`, `delete_many`. It
reuses the `_matches` query matcher from auth_test_helpers.py (extended
there with `$ne` support for this feature) rather than inventing a second
one.

Not a test module itself (no `test_` prefix), so pytest does not collect
it; it is imported directly by test_face_routes.py. No real credentials,
photographs, or biometric data are used anywhere -- `encoding` values
below are short, obviously-synthetic lists of floats, never a real
face_recognition descriptor derived from an actual photograph.
"""

import io
from datetime import datetime, timezone

from academic_test_helpers import FakeHierarchyCollection, make_class, make_class_enrollment
from auth_test_helpers import FakeUsersCollection, _matches, make_user
from bson import ObjectId

from database.schema import CLASS_ENROLLMENTS, CLASSES, FACE_ENCODINGS, USERS


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class _DeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


class FakeFaceEncodingsCursor:
    """Stands in for the pymongo `Cursor` returned by `find(...)`; only
    `.sort("created_at", -1)` followed by iteration/`list(...)` is used
    by `recognition/service.py.list_encodings`.
    """

    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, field_name, direction=1):
        self._docs.sort(key=lambda d: d.get(field_name), reverse=(direction < 0))
        return self

    def __iter__(self):
        return iter(self._docs)

    def __len__(self):
        return len(self._docs)


def _apply_projection(document, projection):
    """A narrow stand-in for pymongo's inclusion projection -- only the
    `{"field": 1, ...}` form is supported, which is all
    `recognition/service.py` uses.
    """
    if not projection:
        return document

    included = [key for key, value in projection.items() if key != "_id" and value]
    result = {key: document[key] for key in included if key in document}
    if projection.get("_id", 1):
        result["_id"] = document.get("_id")

    return result


class FakeFaceEncodingsCollection:
    """Stands in for `db[FACE_ENCODINGS]`."""

    def __init__(self, documents=None):
        self._docs = list(documents or [])

    def find(self, query=None, projection=None):
        matches = (d for d in self._docs if _matches(d, query or {}))
        return FakeFaceEncodingsCursor(_apply_projection(d, projection) for d in matches)

    def find_one(self, query):
        for document in self._docs:
            if _matches(document, query):
                return document
        return None

    def count_documents(self, query, limit=None):
        count = sum(1 for d in self._docs if _matches(d, query))
        return min(count, limit) if limit else count

    def insert_one(self, document):
        stored = dict(document)
        stored.setdefault("_id", ObjectId())
        self._docs.append(stored)
        return _InsertOneResult(stored["_id"])

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


def make_fake_faces_db(*, users=None, classes=None, class_enrollments=None, face_encodings=None):
    """A fake `db` covering exactly the collections
    `backend/routes/faces.py` touches (directly, or through
    `users/assignments.py`'s shared `require_user_with_role` /
    `list_enrollments`): `users`, `classes`, `class_enrollments`, and
    `face_encodings`.
    """
    return {
        USERS: FakeUsersCollection(users),
        CLASSES: FakeHierarchyCollection(classes, unique_scope=("course_id", "name")),
        CLASS_ENROLLMENTS: FakeHierarchyCollection(
            class_enrollments, unique_scope=("class_id", "student_id")
        ),
        FACE_ENCODINGS: FakeFaceEncodingsCollection(face_encodings),
    }


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
    """Builds a raw `face_encodings` document as it would be stored in
    MongoDB. `encoding` defaults to a short, obviously-synthetic list of
    128 floats -- never a real face_recognition descriptor derived from
    an actual photograph.
    """
    document = {
        "_id": ObjectId(),
        "student_id": student_id,
        "encoding": list(encoding) if encoding is not None else [0.001 * i for i in range(128)],
        "model": model,
        "source": source,
        "created_at": created_at or datetime.now(timezone.utc),
        "created_by": created_by or ObjectId(),
    }
    document.update(overrides)
    return document


def bulk_image_parts(files):
    """Build the value of a multi-file `images` form field
    (22-bulk-face-enrollment-import.md), from a list of
    `(filename, data, content_type)` triples.

    Werkzeug's test client turns a dict value that is a *list* into
    several form parts under the same field name (see
    `werkzeug.test._iter_data`), and a `(stream, filename, content_type)`
    tuple entry into one file part -- so `data={"images":
    bulk_image_parts(files)}` produces exactly what
    `request.files.getlist("images")` reads on the server side, mirroring
    how a browser submits `<input type="file" multiple>`.
    """
    return [(io.BytesIO(data), filename, content_type) for filename, data, content_type in files]


__all__ = [
    "make_fake_faces_db",
    "make_face_encoding",
    "make_user",
    "make_class",
    "make_class_enrollment",
    "bulk_image_parts",
]
