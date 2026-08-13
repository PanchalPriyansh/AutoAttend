"""Shared, in-memory fakes for admin-user-management tests.

Builds on the fakes that already exist rather than duplicating them:
`FakeUsersCollection` (extended in auth_test_helpers.py for this feature)
backs `users`, and `FakeHierarchyCollection` from academic_test_helpers.py
backs `institutes`/`classes`/`class_enrollments`, following the same
"fake the pymongo driver at the point of use" pattern used throughout
`backend/tests/`.

Not a test module itself (no `test_` prefix), so pytest does not collect
it; it is imported directly by `test_user_routes.py`. No real credentials,
institutional data, or biometric data are used anywhere -- every value is
an obviously-fake, hardcoded test value.
"""

from academic_test_helpers import (
    FakeHierarchyCollection,
    make_class,
    make_class_enrollment,
    make_institute,
)
from auth_test_helpers import FakeUsersCollection, make_user
from database.schema import CLASS_ENROLLMENTS, CLASSES, INSTITUTES, USERS


def make_fake_users_db(*, users=None, institutes=None, classes=None, class_enrollments=None):
    """A fake `db` covering exactly the collections
    `backend/routes/users.py` touches: `users` (via `users/service.py`),
    `institutes` (referenced, never written, by `users/service.py`), and
    `classes` / `class_enrollments` (via `users/assignments.py`).

    `class_enrollments` is scoped-unique on `(class_id, student_id)`,
    mirroring the real `uniq_class_id_student_id` index, so a duplicate
    enrollment raises `DuplicateKeyError` the same way the real index
    would, letting `users/assignments.enroll_student` translate it into a
    409 exactly as it does against real MongoDB.
    """
    return {
        USERS: FakeUsersCollection(users),
        INSTITUTES: FakeHierarchyCollection(institutes, unique_scope=("code",)),
        CLASSES: FakeHierarchyCollection(classes, unique_scope=("course_id", "name")),
        CLASS_ENROLLMENTS: FakeHierarchyCollection(
            class_enrollments, unique_scope=("class_id", "student_id")
        ),
    }


__all__ = [
    "make_fake_users_db",
    "make_user",
    "make_institute",
    "make_class",
    "make_class_enrollment",
]
