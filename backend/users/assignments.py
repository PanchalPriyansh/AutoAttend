"""Linking people to classes: faculty assignment and student enrollment.

Separate from service.py because these operations span collections --
they read `users` but write `classes` and `class_enrollments`. No Flask
objects appear in any signature.

Together these fill in the "-> Student" tier that the academic hierarchy
feature deliberately left empty: `classes.faculty_id` and the
`class_enrollments` collection have no other writer in the project.
"""

import logging
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from common.errors import DuplicateError, NotFoundError, ValidationError
from database.schema import CLASS_ENROLLMENTS, CLASSES, USERS

logger = logging.getLogger(__name__)


def _require_class(db, class_id):
    if db[CLASSES].count_documents({"_id": class_id}, limit=1) == 0:
        raise NotFoundError("Class not found")


def require_user_with_role(db, user_id, role, field_name, *, require_active=True):
    """Resolve a referenced user, rejecting the wrong role or a disabled
    account before the reference is stored.

    A wrong-role id is a 400 rather than a 404: the account genuinely
    exists, so the problem is the input, not a missing resource. The same
    reasoning covers a deactivated account -- it exists but is not in a
    referenceable state.

    `require_active=False` is for operations that read or remove data
    already attached to a user rather than creating a new reference to
    them: refusing to delete a deactivated student's face encodings would
    make their biometric data impossible to erase, which is the opposite
    of what deactivation should allow.

    Public because face enrolment resolves students the same way; it is
    shared rather than reimplemented so a wrong-role id cannot mean a 400
    in one feature and a 404 in another.
    """
    user = db[USERS].find_one({"_id": user_id})
    if user is None:
        raise NotFoundError(f"{role.capitalize()} not found")

    if user.get("role") != role:
        raise ValidationError(f"{field_name} must reference a {role} user")
    if require_active and not user.get("is_active", False):
        raise ValidationError(f"{field_name} must reference an active {role} user")

    return user


def assign_faculty(db, class_id, faculty_id):
    """Set or clear a class's assigned faculty member.

    `faculty_id` of None unassigns. Assigning replaces any previous
    holder -- `classes.faculty_id` is a single field, so a class has at
    most one faculty member, while a faculty member may hold many classes.
    """
    _require_class(db, class_id)

    if faculty_id is not None:
        require_user_with_role(db, faculty_id, "faculty", "faculty_id")

    # No `updated_at` here: the `classes` validator in database/schema.py
    # does not declare one, and undeclared fields do not belong in
    # hierarchy documents.
    return db[CLASSES].find_one_and_update(
        {"_id": class_id},
        {"$set": {"faculty_id": faculty_id}},
        return_document=ReturnDocument.AFTER,
    )


def list_enrollments(db, class_id):
    """Return (enrollment, student) pairs for one class, sorted by name.

    Two queries rather than an aggregation `$lookup`: both are index-backed
    (`class_id` leads uniq_class_id_student_id, and the `$in` rides `_id`),
    so the cost is constant in the size of the roster, and the join stays
    plain Python that reads the same way the rule does.

    Deactivated students are deliberately not filtered out -- deactivating
    an account does not unenroll it, and a roster that silently dropped
    them would misrepresent the class.
    """
    _require_class(db, class_id)

    enrollments = list(db[CLASS_ENROLLMENTS].find({"class_id": class_id}))
    if not enrollments:
        return []

    student_ids = [enrollment["student_id"] for enrollment in enrollments]
    students = {
        student["_id"]: student
        for student in db[USERS].find({"_id": {"$in": student_ids}})
    }

    pairs = []
    for enrollment in enrollments:
        student = students.get(enrollment["student_id"])
        if student is None:
            # Should be unreachable: nothing in the project hard-deletes a
            # user. Skipped rather than raised so one bad row cannot take
            # down the whole roster.
            logger.warning(
                "Enrollment %s references a missing student", enrollment["_id"]
            )
            continue
        pairs.append((enrollment, student))

    pairs.sort(key=lambda pair: (pair[1].get("name") or "").lower())
    return pairs


def enroll_student(db, class_id, student_id):
    _require_class(db, class_id)
    student = require_user_with_role(db, student_id, "student", "student_id")

    enrollment = {
        "class_id": class_id,
        "student_id": student_id,
        # A real datetime, not an ISO string: the class_enrollments
        # validator declares enrolled_at as bsonType "date".
        "enrolled_at": datetime.now(timezone.utc),
    }

    try:
        result = db[CLASS_ENROLLMENTS].insert_one(enrollment)
    except DuplicateKeyError as exc:
        raise DuplicateError("This student is already enrolled in this class") from exc

    enrollment["_id"] = result.inserted_id
    return enrollment, student


def unenroll_student(db, class_id, student_id):
    """Removes the enrollment record only.

    The class check is skipped deliberately: a non-existent class matches
    no enrollment, so it already produces the same "Enrollment not found"
    404 that repeating a successful unenrollment does.
    """
    result = db[CLASS_ENROLLMENTS].delete_one(
        {"class_id": class_id, "student_id": student_id}
    )
    if result.deleted_count == 0:
        raise NotFoundError("Enrollment not found")
