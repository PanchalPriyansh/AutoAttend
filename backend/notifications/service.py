"""Finding students below the attendance bar, and mailing them.

The whole feature in one module: one aggregation to find candidates, a
few batched reads to narrow them to people who should actually be
written to, one read to apply the cooldown, then send and record.

**This is a threshold comparison.** `percentage < threshold`, where the
percentage is the same figure the student's own dashboard already shows
them. There is no model here, nothing is trained, and nothing is
predicted -- see CLAUDE.md, which rules the academic-risk model out of
scope permanently. A student appears in this sweep because of what was
recorded about them, never because of what was inferred.

No Flask object appears in any signature, matching every other service
module in the project, and the transport is passed in rather than built
here: a dry run is simply a call that is given no transport at all.

Nothing in this module imports `smtplib`, `email`, or anything from
`recognition/` -- the sweep runs on a machine where neither CV library is
installed.
"""

import logging
from datetime import datetime, timedelta, timezone

from academic.context import class_hierarchy_context
from attendance.serializers import attendance_percentage
from database.schema import (
    ATTENDANCE_NOTIFICATIONS,
    ATTENDANCE_RECORDS,
    CLASS_ENROLLMENTS,
    CLASSES,
    USERS,
)
from notifications.errors import MailerSendError
from notifications.mailer import build_message, is_sendable_address
from notifications.messages import build_body, build_subject

logger = logging.getLogger(__name__)

# How many of a student's own lectures must be on record before a class
# is worth writing to them about.
#
# Without a floor, the first lecture of a new class mails everybody who
# missed it at 0%, which is both useless and the fastest way to teach a
# cohort to filter this address into a folder they never read. Five is a
# judgement, not a derived value: enough that the percentage means
# something, few enough that a warning still arrives with time to act on
# it.
#
# Deliberately a constant rather than a fourth environment variable. It
# is a property of what makes a percentage meaningful, not something a
# deployment should be tuning per institute.
MIN_RECORDED_LECTURES = 5


# --- Selection --------------------------------------------------------


def _low_attendance_pipeline(threshold):
    """Group one student's records in one class, and keep the pairs that
    fall below the bar.

    The percentage is rounded *inside* the pipeline, before the
    comparison, so the bar is applied to the number the student is shown
    rather than to an unrounded one behind it. Filtering on the raw value
    would let 74.96% through and then display it as "75.0%, below the
    required 75%", which reads as a bug and would be reported as one.

    The `total` floor sits before the percentage is computed: a pair that
    cannot qualify should not be projected in the first place.

    `$round` needs MongoDB 4.2 or newer.
    """
    return [
        {
            "$group": {
                "_id": {"student_id": "$student_id", "class_id": "$class_id"},
                "total": {"$sum": 1},
                # Absent is everything that is not present, matching
                # attendance/summary.py::_count. The status enum admits
                # exactly two values and the collection validator
                # enforces it, so this cannot silently drop a third.
                "present": {
                    "$sum": {"$cond": [{"$eq": ["$status", "present"]}, 1, 0]}
                },
            }
        },
        {"$match": {"total": {"$gte": MIN_RECORDED_LECTURES}}},
        {
            "$addFields": {
                "percentage": {
                    "$round": [
                        {"$multiply": [{"$divide": ["$present", "$total"]}, 100]},
                        1,
                    ]
                }
            }
        },
        # Strictly below. A student who has exactly met the bar has met
        # it, and mailing them would say otherwise.
        {"$match": {"percentage": {"$lt": threshold}}},
    ]


def _eligible_students(db, student_ids):
    """The subset of candidates who should actually be written to.

    Filtered in the query rather than afterwards, so a deactivated
    account or a faculty member who somehow has attendance records cannot
    reach the mail path at all. The address check is applied here too:
    an address that cannot safely become a header is not a person this
    sweep can reach, so it is a selection rule, not a send-time failure.
    """
    students = {}

    for student in db[USERS].find(
        {"_id": {"$in": student_ids}, "role": "student", "is_active": True},
        {"name": 1, "email": 1},
    ):
        if not is_sendable_address(student.get("email")):
            # No exception and no counter: a student with no usable
            # address is an admin data problem, and one that this command
            # is in no position to fix or report to anyone useful.
            logger.warning(
                "Student %s has no usable email address; skipped", student["_id"]
            )
            continue
        students[student["_id"]] = student

    return students


def _enrolled_pairs(db, student_ids, class_ids):
    """The (student, class) pairs that are still enrolled.

    Attendance records outlive an enrollment -- unenrolling a student
    does not delete their history -- so a past class would otherwise
    keep generating warnings forever. One `$in` read on both sides rather
    than a query per pair.
    """
    return {
        (enrollment["student_id"], enrollment["class_id"])
        for enrollment in db[CLASS_ENROLLMENTS].find(
            {"student_id": {"$in": student_ids}, "class_id": {"$in": class_ids}},
            {"student_id": 1, "class_id": 1},
        )
    }


def find_low_attendance(db, threshold):
    """Every (student, class) pair that should be warned about, before
    the cooldown is applied.

    Returns a list of plain dicts -- no Mongo documents escape this
    function, which is what stops a stray field reaching a message body
    later on.

    Eight queries regardless of how large the institute is: the
    aggregation, students, enrollments, classes, and the four batched
    hierarchy lookups. Nothing here runs per student.
    """
    rows = list(db[ATTENDANCE_RECORDS].aggregate(_low_attendance_pipeline(threshold)))
    if not rows:
        return []

    student_ids = list({row["_id"]["student_id"] for row in rows})
    class_ids = list({row["_id"]["class_id"] for row in rows})

    students = _eligible_students(db, student_ids)
    enrolled = _enrolled_pairs(db, student_ids, class_ids)
    classes = {
        document["_id"]: document
        for document in db[CLASSES].find({"_id": {"$in": class_ids}})
    }
    context = class_hierarchy_context(db, list(classes.values()))

    entries = []
    for row in rows:
        student_id = row["_id"]["student_id"]
        class_id = row["_id"]["class_id"]

        student = students.get(student_id)
        if student is None:
            continue
        if (student_id, class_id) not in enrolled:
            continue

        class_document = classes.get(class_id)
        if class_document is None:
            # Should be unreachable: the hierarchy blocks deleting a class
            # while children exist. Skipped rather than raised so one
            # orphaned row cannot stop the whole sweep.
            logger.warning(
                "Attendance records reference a missing class %s; skipped", class_id
            )
            continue

        present = row["present"]
        total = row["total"]

        entries.append(
            {
                "student_id": student_id,
                "class_id": class_id,
                "student_name": student.get("name"),
                "email": student["email"].strip(),
                "course": context[class_id].get("course"),
                "class_name": class_document.get("name"),
                # Recomputed through the same helper the student
                # dashboard uses rather than carried over from the
                # pipeline, so the figure emailed and the figure on
                # screen are one number and cannot drift apart.
                "percentage": attendance_percentage(present, total),
                "present_count": present,
                "total_count": total,
            }
        )

    # Sorted so a run is reproducible and a dry-run listing reads in a
    # stable order. Presentation, not a query concern -- but a sweep whose
    # output reshuffles between runs is much harder to trust.
    entries.sort(
        key=lambda entry: (
            (entry["student_name"] or "").lower(),
            (entry["course"] or "").lower(),
            (entry["class_name"] or "").lower(),
        )
    )

    return entries


# --- Cooldown ---------------------------------------------------------


def cooldown_skips(db, entries, cooldown_days, now):
    """The (student, class) pairs that were mailed too recently.

    The cutoff comparison is done **in the query**, not in Python. That
    is the deliberate fix for a bug this project has already shipped
    once: pymongo hands back naive datetimes while anything freshly built
    carries a timezone, and comparing the two raises TypeError rather
    than misbehaving quietly (see common/serializers.py::as_utc). Letting
    MongoDB compare two BSON dates removes the mismatch entirely instead
    of guarding against it -- and the in-memory test fakes, which store
    aware datetimes, would not have caught it either way.

    A cooldown of 0 disables the check and mails everyone still below the
    bar, so no query is issued at all.
    """
    if cooldown_days <= 0 or not entries:
        return set()

    cutoff = now - timedelta(days=cooldown_days)
    student_ids = list({entry["student_id"] for entry in entries})

    return {
        (document["student_id"], document["class_id"])
        for document in db[ATTENDANCE_NOTIFICATIONS].find(
            {"student_id": {"$in": student_ids}, "sent_at": {"$gte": cutoff}},
            {"student_id": 1, "class_id": 1},
        )
    }


# --- Sweep ------------------------------------------------------------


def _group_by_student(entries):
    """One student, one message -- never one message per class.

    A student short in three classes hears from us once. Three separate
    emails about the same problem on the same morning is how a warning
    becomes something people filter out.
    """
    grouped = {}
    for entry in entries:
        grouped.setdefault(entry["student_id"], []).append(entry)

    return grouped


def _record(entry, threshold, sent_at):
    return {
        "student_id": entry["student_id"],
        "class_id": entry["class_id"],
        # Captured at send time rather than read back off `users` later:
        # an address that is corrected tomorrow should not rewrite the
        # history of what was sent to the old one.
        "email": entry["email"],
        "threshold": float(threshold),
        "percentage": float(entry["percentage"]),
        "present_count": int(entry["present_count"]),
        "total_count": int(entry["total_count"]),
        "sent_at": sent_at,
    }


def _empty_result():
    return {
        "candidates": 0,
        "skipped_cooldown": 0,
        "students_notified": 0,
        "classes_notified": 0,
        "failed": 0,
        "recipients": [],
    }


def notify_low_attendance(db, sweep_settings, smtp_settings=None, transport=None,
                          *, dry_run=False, now=None):
    """Find, filter, send, and record. Returns a summary of counts.

    On a dry run neither `smtp_settings` nor `transport` is required or
    used: the caller never builds them, so the "a dry run opens no
    connection and writes nothing" rule holds because there is nothing
    present that could do either.

    `now` is injectable so the cooldown can be tested at a chosen instant
    rather than by waiting.
    """
    # Checked before any query runs. A caller that means to send but has
    # not supplied a way to send should hear about it immediately, rather
    # than after a full sweep of the database -- or, worse, not at all on
    # the run where nobody happened to be below the threshold.
    if not dry_run and (transport is None or smtp_settings is None):
        raise MailerSendError("A mail transport is required to send notifications")

    now = now or datetime.now(timezone.utc)
    result = _empty_result()

    entries = find_low_attendance(db, sweep_settings.threshold)
    result["candidates"] = len(entries)
    if not entries:
        return result

    skips = cooldown_skips(db, entries, sweep_settings.cooldown_days, now)
    due = []
    for entry in entries:
        if (entry["student_id"], entry["class_id"]) in skips:
            result["skipped_cooldown"] += 1
        else:
            due.append(entry)

    if not due:
        return result

    grouped = _group_by_student(due)

    if dry_run:
        # The only path on which an address is put into the result at
        # all. A real run reports counts, so there is nothing for the
        # command to print that it should not.
        result["recipients"] = [
            {
                "name": student_entries[0]["student_name"],
                "email": student_entries[0]["email"],
                "classes": [
                    {
                        "course": entry["course"],
                        "class_name": entry["class_name"],
                        "percentage": entry["percentage"],
                        "present_count": entry["present_count"],
                        "total_count": entry["total_count"],
                    }
                    for entry in student_entries
                ],
            }
            for student_entries in grouped.values()
        ]
        result["students_notified"] = len(grouped)
        result["classes_notified"] = len(due)
        return result

    for student_id, student_entries in grouped.items():
        message = build_message(
            sender=smtp_settings.sender,
            sender_name=smtp_settings.sender_name,
            recipient=student_entries[0]["email"],
            subject=build_subject(len(student_entries)),
            body=build_body(
                student_entries[0]["student_name"],
                student_entries,
                sweep_settings.threshold,
            ),
        )

        try:
            transport.send(message)
        except MailerSendError:
            # Nothing is recorded for a failed send, so the next run
            # retries this student. One unreachable address must not stop
            # everybody else being warned.
            #
            # The student id is logged; the address is not, since this
            # line may end up somewhere a recipient list should not.
            logger.exception("Could not notify student %s", student_id)
            result["failed"] += 1
            continue

        # Send first, then record -- deliberately in this order. A crash
        # between the two means somebody is mailed twice, which is an
        # annoyance. The other order means a row saying "warned" for mail
        # that never arrived, and nothing would ever notice.
        db[ATTENDANCE_NOTIFICATIONS].insert_many(
            [
                _record(entry, sweep_settings.threshold, now)
                for entry in student_entries
            ]
        )

        result["students_notified"] += 1
        result["classes_notified"] += len(student_entries)

    return result
