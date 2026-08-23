"""Shared, in-memory fakes for low-attendance notification tests
(.claude/specs/10-low-attendance-notifications.md).

`notifications/service.py` reads six collections -- `attendance_records`
(through one aggregation), `users`, `class_enrollments`, `classes`, and
the four hierarchy levels `academic/context.py::class_hierarchy_context`
resolves (`courses`, `semesters`, `departments`, `institutes`) -- plus
`attendance_notifications`, which it both reads (the cooldown check) and
writes (`insert_many`) after a successful send.

`FakeCollection` below is a single fake reused for every one of those
collections, following the same "one fake per feature, not per
collection" convention `attendance_test_helpers.py` already established
for 07/08. Its `aggregate` handles exactly the one pipeline shape
`notifications/service.py::_low_attendance_pipeline` issues over
`attendance_records`: group by `(student_id, class_id)`, drop groups
under the recorded-lecture floor, compute a rounded percentage, and keep
only the groups strictly below the threshold. Any other pipeline shape
is a signal this fake needs to grow deliberately, not a bug to silently
paper over.

`find`'s query matching normalises a timezone-aware value to naive UTC
before any `$gte`/`$lte`/`$gt`/`$lt` comparison, mirroring what the BSON
wire format actually does: pymongo always hands back a naive datetime,
regardless of whether an aware or naive value was written. Skipping that
normalisation is exactly the bug this project has already shipped once
(see `common/serializers.py::as_utc` and the docstring on
`notifications/service.py::cooldown_skips`) -- a fake that compared an
aware cutoff against a naive stored `sent_at` directly would raise
`TypeError` where real MongoDB never does, and would make a correct
`cooldown_skips` implementation look broken.
`TestCooldownSkips::test_a_naive_stored_sent_at_does_not_raise_and_the_cooldown_still_applies`
in test_notifications_service.py pins exactly this.

Not a test module itself (no `test_` prefix), so pytest does not collect
it. No real email address, credential, or biometric data appears
anywhere -- every address fixture uses an `example.test`-style domain and
every password is an obviously-fake placeholder string.
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
from attendance_test_helpers import make_attendance_record
from auth_test_helpers import make_user
from bson import ObjectId

from database.schema import (
    ATTENDANCE_NOTIFICATIONS,
    ATTENDANCE_RECORDS,
    CLASS_ENROLLMENTS,
    CLASSES,
    COURSES,
    DEPARTMENTS,
    INSTITUTES,
    SEMESTERS,
    USERS,
)
from notifications.errors import MailerSendError
from notifications.settings import SmtpSettings, SweepSettings

# --- Result stand-ins -------------------------------------------------


class _InsertManyResult:
    def __init__(self, inserted_ids):
        self.inserted_ids = inserted_ids


# --- Query matching -----------------------------------------------------


def _to_comparable(value):
    """Mirrors what the BSON wire format does to a datetime: an aware
    value is converted to naive UTC, matching what pymongo hands back for
    every stored date regardless of how it was written. Comparing two
    naive values this way never raises, which is the entire point.
    """
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _field_matches(value, condition):
    if isinstance(condition, dict):
        if "$in" in condition:
            return value in condition["$in"]

        left = _to_comparable(value)
        if "$gte" in condition:
            if left is None or not left >= _to_comparable(condition["$gte"]):
                return False
        if "$lte" in condition:
            if left is None or not left <= _to_comparable(condition["$lte"]):
                return False
        if "$gt" in condition:
            if left is None or not left > _to_comparable(condition["$gt"]):
                return False
        if "$lt" in condition:
            if left is None or not left < _to_comparable(condition["$lt"]):
                return False
        return True

    return value == condition


def _matches(document, query):
    return all(
        _field_matches(document.get(key), condition)
        for key, condition in (query or {}).items()
    )


def _apply_projection(document, projection):
    """A narrow stand-in for pymongo's inclusion projection -- only the
    `{"field": 1, ...}` form is used anywhere in notifications/service.py.
    """
    if not projection:
        return document

    included = [key for key, value in projection.items() if key != "_id" and value]
    result = {key: document[key] for key in included if key in document}
    if projection.get("_id", 1):
        result["_id"] = document.get("_id")

    return result


class FakeCollection:
    """A single fake standing in for every collection
    `notifications/service.py` (and, through it,
    `academic/context.py::class_hierarchy_context`) touches: `users`,
    `class_enrollments`, `classes`, `courses`, `semesters`, `departments`,
    `institutes`, `attendance_records`, and `attendance_notifications`.

    No unique-index emulation -- nothing in this feature relies on one
    (`attendance_notifications`'s index is deliberately non-unique, see
    database/schema.py), so `_raise_if_duplicate`-style bookkeeping,
    present in attendance_test_helpers.FakeCollection, is unneeded here.
    """

    def __init__(self, documents=None):
        self._docs = list(documents or [])

    def find(self, query=None, projection=None):
        matches = (d for d in self._docs if _matches(d, query or {}))
        return [_apply_projection(d, projection) for d in matches]

    def find_one(self, query):
        for document in self._docs:
            if _matches(document, query):
                return document
        return None

    def insert_one(self, document):
        stored = dict(document)
        stored.setdefault("_id", ObjectId())
        self._docs.append(stored)
        return stored

    def insert_many(self, documents):
        inserted_ids = []
        for document in documents:
            stored = dict(document)
            stored.setdefault("_id", ObjectId())
            self._docs.append(stored)
            inserted_ids.append(stored["_id"])
        return _InsertManyResult(inserted_ids)

    def aggregate(self, pipeline):
        """A narrow stand-in for pymongo's `aggregate` -- handles exactly
        the pipeline shape `notifications/service.py::_low_attendance_pipeline`
        issues over `attendance_records`: `$group` by
        `{student_id, class_id}` computing `total`/`present`, `$match` on
        the recorded-lecture floor, `$addFields` a rounded `percentage`,
        then `$match` strictly below the threshold.

        Not a general aggregation engine -- any other pipeline shape is a
        signal this fake needs to grow deliberately, not a bug to
        silently paper over, so it is not attempted here.
        """
        total_floor = pipeline[1]["$match"]["total"]["$gte"]
        threshold = pipeline[3]["$match"]["percentage"]["$lt"]

        groups = {}
        for document in self._docs:
            key = (document["student_id"], document["class_id"])
            bucket = groups.setdefault(key, {"total": 0, "present": 0})
            bucket["total"] += 1
            if document.get("status") == "present":
                bucket["present"] += 1

        rows = []
        for (student_id, class_id), bucket in groups.items():
            if bucket["total"] < total_floor:
                continue
            percentage = round(bucket["present"] / bucket["total"] * 100, 1)
            if percentage < threshold:
                rows.append(
                    {
                        "_id": {"student_id": student_id, "class_id": class_id},
                        "total": bucket["total"],
                        "present": bucket["present"],
                    }
                )
        return rows

    def __len__(self):
        return len(self._docs)


def make_fake_notifications_db(
    *,
    users=None,
    institutes=None,
    departments=None,
    semesters=None,
    courses=None,
    classes=None,
    class_enrollments=None,
    attendance_records=None,
    attendance_notifications=None,
):
    """A fake `db` covering exactly the collections
    `notifications/service.py` touches, keyed by the real
    collection-name constants from `database/schema.py`.
    """
    return {
        USERS: FakeCollection(users),
        INSTITUTES: FakeCollection(institutes),
        DEPARTMENTS: FakeCollection(departments),
        SEMESTERS: FakeCollection(semesters),
        COURSES: FakeCollection(courses),
        CLASSES: FakeCollection(classes),
        CLASS_ENROLLMENTS: FakeCollection(class_enrollments),
        ATTENDANCE_RECORDS: FakeCollection(attendance_records),
        ATTENDANCE_NOTIFICATIONS: FakeCollection(attendance_notifications),
    }


# --- Document builders --------------------------------------------------


def make_student(*, name="Test Student", email=None, is_active=True, **overrides):
    """`email=None` (the default) generates a fresh, obviously-fake
    address. `email=""` is passed through as-is rather than replaced --
    callers rely on that to build the "student with no usable email"
    case (`is_sendable_address("")` is `False`), so a falsy-but-explicit
    empty string must not be silently upgraded to a real-looking one.
    """
    if email is None:
        email = f"student-{ObjectId()}@example.test"
    return make_user(name=name, email=email, role="student", is_active=is_active, **overrides)


def build_class_with_hierarchy(
    *,
    class_name="A",
    course_name="Data Structures",
    semester_name="Semester 3",
    department_name="Computer Engineering",
    institute_name="Test Institute",
):
    institute = make_institute(name=institute_name)
    department = make_department(institute["_id"], name=department_name)
    semester = make_semester(department["_id"], name=semester_name)
    course = make_course(semester["_id"], name=course_name)
    klass = make_class(course["_id"], name=class_name)
    hierarchy = {
        "institutes": [institute],
        "departments": [department],
        "semesters": [semester],
        "courses": [course],
    }
    return klass, hierarchy


def merge_hierarchies(*hierarchies):
    merged = {"institutes": [], "departments": [], "semesters": [], "courses": []}
    for hierarchy in hierarchies:
        for key in merged:
            merged[key].extend(hierarchy[key])
    return merged


def statuses_for(present, total):
    """`present` "present" rows followed by the remaining "absent" rows --
    the order does not matter to the aggregation, only the counts do.
    """
    return ["present"] * present + ["absent"] * (total - present)


def make_records(class_id, student_id, statuses):
    """One `attendance_records` row per element of `statuses`, each with
    its own fabricated `session_id`. The aggregation groups by
    `(student_id, class_id)` alone, never by session, so a shared session
    is not needed to build a representative history.
    """
    return [
        make_attendance_record(ObjectId(), class_id, student_id, status=status)
        for status in statuses
    ]


def make_attendance_notification(
    student_id,
    class_id,
    *,
    email=None,
    threshold=75.0,
    percentage=50.0,
    present_count=2,
    total_count=4,
    sent_at=None,
    **overrides,
):
    document = {
        "_id": ObjectId(),
        "student_id": student_id,
        "class_id": class_id,
        "email": email or f"student-{ObjectId()}@example.test",
        "threshold": threshold,
        "percentage": percentage,
        "present_count": present_count,
        "total_count": total_count,
        "sent_at": sent_at if sent_at is not None else datetime.now(timezone.utc),
    }
    document.update(overrides)
    return document


# --- Settings builders ---------------------------------------------------


def make_sweep_settings(**overrides):
    defaults = {"threshold": 75.0, "cooldown_days": 7}
    defaults.update(overrides)
    return SweepSettings(**defaults)


def make_smtp_settings(**overrides):
    defaults = {
        "host": "smtp.example.test",
        "port": 587,
        "username": "bot@example.test",
        "password": "test-only-fake-password",
        "sender": "noreply@example.test",
        "sender_name": "AutoAttend",
        "use_tls": True,
    }
    defaults.update(overrides)
    return SmtpSettings(**defaults)


# --- Transport fakes -------------------------------------------------------


class FakeTransport:
    """Records every `EmailMessage` handed to `.send`, standing in for
    `notifications/mailer.py::SmtpTransport`. No socket is ever opened by
    any test that uses this.

    `fail_for` is a set of recipient addresses whose `.send()` call
    raises `MailerSendError`, for exercising the "send first, then
    record; one student's failure must not stop the others" rule without
    needing a real SMTP failure.
    """

    def __init__(self, fail_for=()):
        self.sent = []
        self._fail_for = set(fail_for)

    def send(self, message):
        recipient = message["To"]
        if recipient in self._fail_for:
            raise MailerSendError("Simulated transport failure")
        self.sent.append(message)


class TransportMustNotBeUsed:
    """A transport whose `.send` fails the test loudly if it is ever
    called -- used to assert a dry run opens no connection and sends
    nothing, rather than merely checking the database afterwards.
    """

    def send(self, message):
        raise AssertionError("Transport.send must not be called during a dry run")


__all__ = [
    "FakeCollection",
    "make_fake_notifications_db",
    "make_student",
    "build_class_with_hierarchy",
    "merge_hierarchies",
    "statuses_for",
    "make_records",
    "make_attendance_notification",
    "make_sweep_settings",
    "make_smtp_settings",
    "FakeTransport",
    "TransportMustNotBeUsed",
    "make_class_enrollment",
    "make_user",
]
