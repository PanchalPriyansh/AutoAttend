"""What a student can see of their own attendance.

Deliberately separate from service.py, which owns capturing and
correcting a register on behalf of a class. Three things differ here and
they all point the same way:

  - **The consumer is different.** A faculty member reads a class; a
    student reads themselves. Nothing in this module ever returns a
    roster, a class average, or another person's status, and none of the
    faculty serializers are reachable from it.

  - **The authorization rule is different.** service.py asks whether a
    class is *yours to hold*; this asks whether it is *yours to attend*.
    `require_enrolled_class` below is the enrollment counterpart of
    `require_owned_class`, and keeping them apart is what stops one being
    quietly substituted for the other.

  - **Nothing here goes near the capture machinery.** service.py imports
    the recognition package at module level; this module has no reason
    to, and no path through it can fail because `dlib` or OpenCV is
    missing.

Every figure is derived at read time. Nothing in this module writes, and
no count or percentage is ever stored -- a stored aggregate is a second
source of truth that goes stale the moment a faculty member corrects or
deletes a session behind it.

The student is always identified by the caller's own token. No function
here takes a student id from anywhere a request body or query string
could reach, which is what makes reading somebody else's attendance
structurally impossible rather than merely checked.
"""

import logging

from academic.context import class_hierarchy_context
from attendance.errors import ForbiddenError
from common.errors import NotFoundError
from common.serializers import as_utc
from database.schema import (
    ATTENDANCE_RECORDS,
    ATTENDANCE_SESSIONS,
    CLASS_ENROLLMENTS,
    CLASSES,
)
from users.assignments import list_student_classes

logger = logging.getLogger(__name__)

# The stored lecture date is a timestamp at UTC midnight; a trend bucket
# is the calendar month it falls in.
MONTH_FORMAT = "%Y-%m"


# --- Authorization ----------------------------------------------------


def require_enrolled_class(db, class_id, student_id):
    """Resolve a class and confirm this student is enrolled in it.

    The enrollment counterpart of service.py's `require_owned_class`, and
    it answers with the same honesty: a class that does not exist is a
    404, and one that exists but is not theirs is a 403.

    Returning an empty result for an unenrolled class would look safer
    and is not -- it silently answers a question the caller was not
    entitled to ask, and leaves a student who mistyped an id staring at
    "no attendance recorded" for a class that is full of it.
    """
    document = db[CLASSES].find_one({"_id": class_id})
    if document is None:
        raise NotFoundError("Class not found")

    enrolled = db[CLASS_ENROLLMENTS].count_documents(
        {"class_id": class_id, "student_id": student_id}, limit=1
    )
    if not enrolled:
        raise ForbiddenError("You are not enrolled in this class")

    return document


# --- Counting ---------------------------------------------------------
#
# Counted in Python from one indexed query, the way `_roster_counts` in
# service.py already counts a roster, because the bound is the same shape:
# one student's own rows. `_session_counts` groups in the database instead
# because its bound is every record of every session on a page, which is a
# different query and belongs where it already lives. A later feature
# needing these numbers for a whole class or institute wants that one, not
# this one.


def _empty_counts():
    return {"present": 0, "absent": 0, "total": 0}


def _count(counts, status):
    """Absent is everything that is not present.

    The `status` enum admits exactly two values and the collection
    validator enforces it, so this cannot silently drop a third -- and
    counting it this way is what guarantees present + absent == total in
    every response, at every scope.
    """
    counts["total"] += 1
    if status == "present":
        counts["present"] += 1
    else:
        counts["absent"] += 1


# --- Overview ---------------------------------------------------------


def class_attendance_overview(db, student_id):
    """Every class this student is enrolled in, with their standing in it.

    Returns `(entries, overall)`, where each entry is a
    `(class_document, context, counts)` triple and `context` holds the
    course/semester/department/institute names.

    Four queries regardless of how many classes the student takes: their
    enrollments, those classes, all of their attendance records in one
    read riding `idx_student_id_class_id`, and the batched hierarchy walk.

    A class with no attendance taken yet is included with zero counts. It
    is a real part of the student's standing -- leaving it out would let
    a class quietly disappear from the screen for the whole period before
    its first lecture is recorded.
    """
    classes = list_student_classes(db, student_id)
    if not classes:
        return [], _empty_counts()

    by_class = {document["_id"]: _empty_counts() for document in classes}

    for record in db[ATTENDANCE_RECORDS].find(
        {"student_id": student_id}, {"class_id": 1, "status": 1}
    ):
        counts = by_class.get(record.get("class_id"))
        if counts is None:
            # A lecture in a class this student has since been unenrolled
            # from. Excluded from the per-class rows and from the overall
            # total alike, so the two always reconcile: a figure in the
            # roll-up that no row on screen accounts for is worse than a
            # figure that is missing.
            continue
        _count(counts, record.get("status"))

    context = class_hierarchy_context(db, classes)

    entries = [
        (document, context[document["_id"]], by_class[document["_id"]])
        for document in classes
    ]
    # Sorted the way they are read, matching list_assigned_classes. Putting
    # the weakest class first is a presentation decision and belongs to the
    # screen, not to the query.
    entries.sort(
        key=lambda entry: (
            (entry[1].get("course") or "").lower(),
            (entry[0].get("name") or "").lower(),
        )
    )

    overall = _empty_counts()
    for _, _, counts in entries:
        for key in overall:
            overall[key] += counts[key]

    return entries, overall


# --- One class --------------------------------------------------------


def _lecture_dates(db, records):
    """Map each record's session to its lecture date.

    `attendance_records` deliberately does not carry the date -- it is a
    property of the lecture, and denormalising it would leave two copies
    to disagree the first time a session is edited. So every date-shaped
    answer on this path (the filter, the trend, the list) depends on this
    one `$in` lookup over `_id`.
    """
    if not records:
        return {}

    session_ids = [record["session_id"] for record in records]

    return {
        session["_id"]: session.get("date")
        for session in db[ATTENDANCE_SESSIONS].find(
            {"_id": {"$in": session_ids}}, {"date": 1}
        )
    }


def student_class_attendance(db, class_id, student_id, *, date_from, date_to):
    """This student's own record of every lecture they were counted in,
    for one class.

    Returns `(class_document, context, counts, monthly, lectures)`.
    `lectures` is newest-first because a log is read that way; `monthly`
    is oldest-first because a trend is read left to right. Both describe
    exactly the same set of lectures, so a date filter narrows them
    together and the totals on screen always account for what is shown.

    The denominator is what this student has rows for, never how many
    sessions the class has held. Someone enrolled in week six has no rows
    for weeks one to five and is not marked down for them.
    """
    document = require_enrolled_class(db, class_id, student_id)

    records = list(
        db[ATTENDANCE_RECORDS].find(
            {"student_id": student_id, "class_id": class_id},
            {"session_id": 1, "status": 1},
        )
    )
    dates_by_session = _lecture_dates(db, records)

    lectures = []
    for record in records:
        date = dates_by_session.get(record.get("session_id"))
        if date is None:
            # Should be unreachable: deleting a session deletes its records
            # first. Skipped rather than raised so one orphaned row cannot
            # take down a student's whole dashboard.
            logger.warning(
                "Attendance record %s references a missing session", record["_id"]
            )
            continue

        # Reattached before it is compared to anything. The bounds arrive
        # timezone-aware from the validators while pymongo hands back naive
        # UTC, and comparing the two raises rather than misbehaving quietly.
        date = as_utc(date)
        if date_from is not None and date < date_from:
            continue
        if date_to is not None and date > date_to:
            continue

        lectures.append({"date": date, "status": record.get("status")})

    lectures.sort(key=lambda lecture: lecture["date"], reverse=True)

    counts = _empty_counts()
    buckets = {}
    for lecture in lectures:
        _count(counts, lecture["status"])
        month = lecture["date"].strftime(MONTH_FORMAT)
        _count(buckets.setdefault(month, _empty_counts()), lecture["status"])

    # Sorted by the "YYYY-MM" key itself, which is chronological because the
    # format is zero-padded and fixed-width.
    monthly = [{"month": month, **buckets[month]} for month in sorted(buckets)]

    context = class_hierarchy_context(db, [document])[document["_id"]]

    return document, context, counts, monthly, lectures
