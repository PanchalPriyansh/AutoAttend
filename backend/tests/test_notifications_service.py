"""Tests for backend/notifications/service.py.

Spec contract under test (.claude/specs/10-low-attendance-notifications.md,
"Rules for implementation" + "Definition of done", grouped by that
checklist's own headings):

Selection (`find_low_attendance`):
  - Below-threshold selection is per class, never an overall average
    (rule 3); membership is strict `<` (rule 8); the denominator is the
    student's own records in that class (rule 4, matching
    attendance/threshold.py::attendance_percentage exactly); only still
    -enrolled pairs qualify (rule 5); only active students with a
    non-empty email are notified (rule 6); a class under
    `MIN_RECORDED_LECTURES` is skipped even at 0% (rule 7).

Cooldown (`cooldown_skips`):
  - A `(student_id, class_id)` pair sent within `cooldown_days` is
    skipped (rule 15); `cooldown_days=0` disables the check entirely
    without even querying (rule 15); `sent_at` is compared as a real BSON
    date, and a **naive** stored value (exactly what pymongo hands back
    in production) must not raise when compared against the computed,
    timezone-aware cutoff (rule 17) -- see notifications_test_helpers.py's
    module docstring for why this is pinned explicitly.

Sending and recording (`notify_low_attendance`):
  - One email per student per run, listing every short class (rule 9);
    plain text only (rule 10); the body carries only that student's own
    data (rule 11); a leak test in the spirit of 09's (rule 23); send
    first, then record -- a transport failure writes nothing, logs, and
    does not stop the rest of the sweep (rules 13-14); a dry run opens no
    connection and writes nothing (rule 20); a student whose every short
    class is inside the cooldown gets no email at all (rule 16).

Service-level tests call `notifications/service.py` directly against the
`FakeCollection`-backed db from notifications_test_helpers.py -- no
Flask, no HTTP, no live MongoDB, and no SMTP/email import happens on this
path (only `notifications/mailer.py` imports those, and it is only
reached here through the injected `FakeTransport`, which never opens a
socket). No real student name, email address, or credential is used
anywhere in this file.
"""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from academic_test_helpers import make_class_enrollment
from attendance.threshold import attendance_percentage
from notifications.errors import MailerSendError
from notifications.service import (
    MIN_RECORDED_LECTURES,
    cooldown_skips,
    find_low_attendance,
    notify_low_attendance,
)
from notifications_test_helpers import (
    FakeTransport,
    TransportMustNotBeUsed,
    build_class_with_hierarchy,
    make_attendance_notification,
    make_fake_notifications_db,
    make_records,
    make_smtp_settings,
    make_student,
    make_sweep_settings,
    make_user,
    merge_hierarchies,
    statuses_for,
)
from database.schema import ATTENDANCE_NOTIFICATIONS

NOW = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
THRESHOLD = 75.0


def _enroll(student, klass):
    return make_class_enrollment(klass["_id"], student["_id"])


# --- find_low_attendance: selection --------------------------------------


class TestFindLowAttendanceSelection:
    def test_a_student_below_threshold_in_one_class_is_selected_for_that_class_only(self):
        student = make_student()
        short_class, short_hierarchy = build_class_with_hierarchy(
            class_name="A", course_name="Short Course"
        )
        clear_class, clear_hierarchy = build_class_with_hierarchy(
            class_name="B", course_name="Clear Course"
        )
        records = make_records(short_class["_id"], student["_id"], statuses_for(3, 8))  # 37.5%
        records += make_records(clear_class["_id"], student["_id"], statuses_for(9, 10))  # 90%
        db = make_fake_notifications_db(
            users=[student],
            classes=[short_class, clear_class],
            class_enrollments=[_enroll(student, short_class), _enroll(student, clear_class)],
            attendance_records=records,
            **merge_hierarchies(short_hierarchy, clear_hierarchy),
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert len(entries) == 1
        assert entries[0]["class_id"] == short_class["_id"]
        assert entries[0]["percentage"] == 37.5

    def test_a_student_exactly_at_the_threshold_is_not_selected(self):
        student = make_student()
        klass, hierarchy = build_class_with_hierarchy()
        records = make_records(klass["_id"], student["_id"], statuses_for(6, 8))  # exactly 75%
        db = make_fake_notifications_db(
            users=[student], classes=[klass], class_enrollments=[_enroll(student, klass)],
            attendance_records=records, **hierarchy,
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert entries == []

    def test_a_student_whose_overall_average_clears_the_bar_is_still_selected_for_the_one_short_class(self):
        student = make_student()
        class_a, hierarchy_a = build_class_with_hierarchy(class_name="A", course_name="Course A")
        class_b, hierarchy_b = build_class_with_hierarchy(class_name="B", course_name="Course B")
        class_c, hierarchy_c = build_class_with_hierarchy(class_name="C", course_name="Course C")
        # Overall: (10 + 10 + 3) / 30 = 76.7% -- clears a 75% bar as an
        # average -- but class_c alone is 30%, well below it.
        records = (
            make_records(class_a["_id"], student["_id"], statuses_for(10, 10))
            + make_records(class_b["_id"], student["_id"], statuses_for(10, 10))
            + make_records(class_c["_id"], student["_id"], statuses_for(3, 10))
        )
        db = make_fake_notifications_db(
            users=[student],
            classes=[class_a, class_b, class_c],
            class_enrollments=[
                _enroll(student, class_a), _enroll(student, class_b), _enroll(student, class_c),
            ],
            attendance_records=records,
            **merge_hierarchies(hierarchy_a, hierarchy_b, hierarchy_c),
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert [entry["class_id"] for entry in entries] == [class_c["_id"]]

    def test_an_unenrolled_pair_is_never_selected_even_when_attendance_records_exist(self):
        student = make_student()
        klass, hierarchy = build_class_with_hierarchy()
        records = make_records(klass["_id"], student["_id"], statuses_for(1, 10))  # 10%
        db = make_fake_notifications_db(
            users=[student], classes=[klass],
            class_enrollments=[],  # deliberately no enrollment row
            attendance_records=records, **hierarchy,
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert entries == []

    def test_a_deactivated_student_is_skipped(self):
        student = make_student(is_active=False)
        klass, hierarchy = build_class_with_hierarchy()
        records = make_records(klass["_id"], student["_id"], statuses_for(1, 10))
        db = make_fake_notifications_db(
            users=[student], classes=[klass], class_enrollments=[_enroll(student, klass)],
            attendance_records=records, **hierarchy,
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert entries == []

    def test_a_non_student_user_is_skipped(self):
        faculty = make_user(
            name="Faculty Member", email="faculty@example.test", role="faculty",
        )
        klass, hierarchy = build_class_with_hierarchy()
        records = make_records(klass["_id"], faculty["_id"], statuses_for(1, 10))
        db = make_fake_notifications_db(
            users=[faculty], classes=[klass], class_enrollments=[_enroll(faculty, klass)],
            attendance_records=records, **hierarchy,
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert entries == []

    def test_a_student_with_no_email_is_skipped(self):
        student = make_student(email="")
        klass, hierarchy = build_class_with_hierarchy()
        records = make_records(klass["_id"], student["_id"], statuses_for(1, 10))
        db = make_fake_notifications_db(
            users=[student], classes=[klass], class_enrollments=[_enroll(student, klass)],
            attendance_records=records, **hierarchy,
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert entries == []

    def test_a_class_under_the_minimum_recorded_lectures_is_skipped_even_at_zero_percent(self):
        assert MIN_RECORDED_LECTURES == 5  # documented constant this test pins
        student = make_student()
        klass, hierarchy = build_class_with_hierarchy()
        records = make_records(klass["_id"], student["_id"], statuses_for(0, 3))  # 0%, only 3 rows
        db = make_fake_notifications_db(
            users=[student], classes=[klass], class_enrollments=[_enroll(student, klass)],
            attendance_records=records, **hierarchy,
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert entries == []

    def test_a_class_at_exactly_the_minimum_recorded_lectures_is_included(self):
        student = make_student()
        klass, hierarchy = build_class_with_hierarchy()
        records = make_records(
            klass["_id"], student["_id"], statuses_for(1, MIN_RECORDED_LECTURES)
        )
        db = make_fake_notifications_db(
            users=[student], classes=[klass], class_enrollments=[_enroll(student, klass)],
            attendance_records=records, **hierarchy,
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert len(entries) == 1
        assert entries[0]["total_count"] == MIN_RECORDED_LECTURES

    def test_the_percentage_matches_attendance_percentage_the_student_dashboard_uses(self):
        student = make_student()
        klass, hierarchy = build_class_with_hierarchy()
        records = make_records(klass["_id"], student["_id"], statuses_for(2, 8))
        db = make_fake_notifications_db(
            users=[student], classes=[klass], class_enrollments=[_enroll(student, klass)],
            attendance_records=records, **hierarchy,
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert entries[0]["percentage"] == attendance_percentage(2, 8)

    def test_a_record_referencing_a_class_that_no_longer_exists_is_skipped_not_raised(self):
        student = make_student()
        missing_class_id = ObjectId()
        records = make_records(missing_class_id, student["_id"], statuses_for(1, 10))
        db = make_fake_notifications_db(
            users=[student],
            classes=[],  # the class document itself is absent
            class_enrollments=[make_class_enrollment(missing_class_id, student["_id"])],
            attendance_records=records,
        )

        entries = find_low_attendance(db, THRESHOLD)

        assert entries == []

    def test_entries_carry_the_hierarchy_context_and_no_unexpected_fields(self):
        student = make_student(name="Alice Test")
        klass, hierarchy = build_class_with_hierarchy(
            class_name="A", course_name="Data Structures",
        )
        records = make_records(klass["_id"], student["_id"], statuses_for(1, 10))
        db = make_fake_notifications_db(
            users=[student], classes=[klass], class_enrollments=[_enroll(student, klass)],
            attendance_records=records, **hierarchy,
        )

        entries = find_low_attendance(db, THRESHOLD)

        entry = entries[0]
        assert entry["student_name"] == "Alice Test"
        assert entry["course"] == "Data Structures"
        assert entry["class_name"] == "A"
        assert entry["present_count"] == 1
        assert entry["total_count"] == 10


# --- cooldown_skips ---------------------------------------------------------


class TestCooldownSkips:
    def test_a_pair_notified_within_the_cooldown_window_is_skipped(self):
        student_id, class_id = ObjectId(), ObjectId()
        notification = make_attendance_notification(
            student_id, class_id, sent_at=NOW - timedelta(days=2),
        )
        db = make_fake_notifications_db(attendance_notifications=[notification])
        entries = [{"student_id": student_id, "class_id": class_id}]

        skips = cooldown_skips(db, entries, cooldown_days=7, now=NOW)

        assert (student_id, class_id) in skips

    def test_a_pair_notified_before_the_cooldown_window_is_not_skipped(self):
        student_id, class_id = ObjectId(), ObjectId()
        notification = make_attendance_notification(
            student_id, class_id, sent_at=NOW - timedelta(days=10),
        )
        db = make_fake_notifications_db(attendance_notifications=[notification])
        entries = [{"student_id": student_id, "class_id": class_id}]

        skips = cooldown_skips(db, entries, cooldown_days=7, now=NOW)

        assert skips == set()

    def test_a_cooldown_of_zero_disables_the_check_and_issues_no_query(self):
        student_id, class_id = ObjectId(), ObjectId()
        notification = make_attendance_notification(
            student_id, class_id, sent_at=NOW - timedelta(minutes=1),
        )
        db = make_fake_notifications_db(attendance_notifications=[notification])
        entries = [{"student_id": student_id, "class_id": class_id}]

        skips = cooldown_skips(db, entries, cooldown_days=0, now=NOW)

        assert skips == set()

    def test_no_entries_returns_an_empty_set(self):
        db = make_fake_notifications_db()

        skips = cooldown_skips(db, [], cooldown_days=7, now=NOW)

        assert skips == set()

    def test_a_naive_stored_sent_at_does_not_raise_and_the_cooldown_still_applies(self):
        """The regression this project has already shipped once: pymongo
        hands back a naive datetime for every stored date regardless of
        how it was written, while `now` here is timezone-aware. Comparing
        the two must never raise TypeError -- a fake that raises here has
        a bug in the fake, not in the product (see this module's and
        notifications_test_helpers.py's docstrings).
        """
        student_id, class_id = ObjectId(), ObjectId()
        naive_recent_sent_at = (NOW - timedelta(days=1)).replace(tzinfo=None)
        assert naive_recent_sent_at.tzinfo is None  # the exact shape pymongo returns
        notification = make_attendance_notification(
            student_id, class_id, sent_at=naive_recent_sent_at,
        )
        db = make_fake_notifications_db(attendance_notifications=[notification])
        entries = [{"student_id": student_id, "class_id": class_id}]

        skips = cooldown_skips(db, entries, cooldown_days=7, now=NOW)

        assert (student_id, class_id) in skips

    def test_a_naive_stored_sent_at_older_than_the_cooldown_is_not_skipped(self):
        student_id, class_id = ObjectId(), ObjectId()
        naive_old_sent_at = (NOW - timedelta(days=30)).replace(tzinfo=None)
        notification = make_attendance_notification(
            student_id, class_id, sent_at=naive_old_sent_at,
        )
        db = make_fake_notifications_db(attendance_notifications=[notification])
        entries = [{"student_id": student_id, "class_id": class_id}]

        skips = cooldown_skips(db, entries, cooldown_days=7, now=NOW)

        assert skips == set()


# --- notify_low_attendance ----------------------------------------------


def _build_student_short_in(*class_specs, student=None):
    """`class_specs` is a list of `(present, total)` pairs. Returns
    `(student, db_kwargs)` where `db_kwargs` covers one distinct class per
    spec, all belonging to that one student.
    """
    student = student or make_student()
    classes, hierarchies, enrollments, records = [], [], [], []
    for index, (present, total) in enumerate(class_specs):
        klass, hierarchy = build_class_with_hierarchy(
            class_name=chr(ord("A") + index), course_name=f"Course {index}",
        )
        classes.append(klass)
        hierarchies.append(hierarchy)
        enrollments.append(_enroll(student, klass))
        records += make_records(klass["_id"], student["_id"], statuses_for(present, total))

    return student, {
        "classes": classes,
        "class_enrollments": enrollments,
        "attendance_records": records,
        **merge_hierarchies(*hierarchies),
    }


class TestNotifyLowAttendanceGroupingAndContent:
    def test_a_student_short_in_three_classes_receives_exactly_one_email_listing_all_three(self):
        student, db_kwargs = _build_student_short_in((1, 10), (2, 10), (3, 10))
        db = make_fake_notifications_db(users=[student], **db_kwargs)
        transport = FakeTransport()

        result = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD, cooldown_days=7),
            make_smtp_settings(), transport, now=NOW,
        )

        assert len(transport.sent) == 1
        assert result["students_notified"] == 1
        assert result["classes_notified"] == 3
        message = transport.sent[0]
        body = message.get_content()
        assert "Course 0" in body
        assert "Course 1" in body
        assert "Course 2" in body

    def test_message_is_plain_text_with_no_html_part_and_no_attachment(self):
        student, db_kwargs = _build_student_short_in((1, 10))
        db = make_fake_notifications_db(users=[student], **db_kwargs)
        transport = FakeTransport()

        notify_low_attendance(
            db, make_sweep_settings(), make_smtp_settings(), transport, now=NOW,
        )

        message = transport.sent[0]
        assert message.get_content_type() == "text/plain"
        assert message.is_multipart() is False
        assert list(message.iter_attachments()) == []

    def test_body_contains_the_students_name_each_classs_figures_and_the_threshold(self):
        student = make_student(name="Alice Test")
        student, db_kwargs = _build_student_short_in((3, 8), student=student)
        db = make_fake_notifications_db(users=[student], **db_kwargs)
        transport = FakeTransport()

        notify_low_attendance(
            db, make_sweep_settings(threshold=75.0), make_smtp_settings(), transport, now=NOW,
        )

        body = transport.sent[0].get_content()
        assert "Alice Test" in body
        assert "37.5%" in body
        assert "3 of 8" in body
        assert "75%" in body

    def test_the_recipient_is_the_students_own_email_address(self):
        student = make_student(email="alice@example.test")
        student, db_kwargs = _build_student_short_in((1, 10), student=student)
        db = make_fake_notifications_db(users=[student], **db_kwargs)
        transport = FakeTransport()

        notify_low_attendance(
            db, make_sweep_settings(), make_smtp_settings(), transport, now=NOW,
        )

        assert transport.sent[0]["To"] == "alice@example.test"


class TestNotifyLowAttendanceCrossStudentLeakage:
    def test_two_below_threshold_students_messages_never_mention_each_other(self):
        alice = make_student(name="Alice Test", email="alice@example.test")
        bob = make_student(name="Bob Test", email="bob@example.test")
        alice, alice_kwargs = _build_student_short_in((1, 8), student=alice)  # 12.5%
        bob, bob_kwargs = _build_student_short_in((3, 8), student=bob)  # 37.5%
        # _build_student_short_in reuses course names/letters per index, so
        # give each student's class a distinguishable identity.
        alice_kwargs["classes"][0]["name"] = "Alice-Only-Class"
        bob_kwargs["classes"][0]["name"] = "Bob-Only-Class"
        db = make_fake_notifications_db(
            users=[alice, bob],
            classes=alice_kwargs["classes"] + bob_kwargs["classes"],
            class_enrollments=alice_kwargs["class_enrollments"] + bob_kwargs["class_enrollments"],
            attendance_records=alice_kwargs["attendance_records"] + bob_kwargs["attendance_records"],
            **merge_hierarchies(
                {k: v for k, v in alice_kwargs.items() if k in ("institutes", "departments", "semesters", "courses")},
                {k: v for k, v in bob_kwargs.items() if k in ("institutes", "departments", "semesters", "courses")},
            ),
        )
        transport = FakeTransport()

        result = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD), make_smtp_settings(), transport, now=NOW,
        )

        assert result["students_notified"] == 2
        assert len(transport.sent) == 2

        by_recipient = {message["To"]: message for message in transport.sent}
        alice_body = by_recipient["alice@example.test"].get_content()
        bob_body = by_recipient["bob@example.test"].get_content()

        assert "Bob Test" not in alice_body
        assert "bob@example.test" not in alice_body
        assert "Bob-Only-Class" not in alice_body
        assert "37.5%" not in alice_body
        assert "3 of 8" not in alice_body

        assert "Alice Test" not in bob_body
        assert "alice@example.test" not in bob_body
        assert "Alice-Only-Class" not in bob_body
        assert "12.5%" not in bob_body
        assert "1 of 8" not in bob_body


class TestNotifyLowAttendanceCooldownAndRecording:
    def test_a_successful_send_writes_one_document_per_class_sharing_one_sent_at(self):
        student, db_kwargs = _build_student_short_in((1, 10), (2, 10))
        db = make_fake_notifications_db(users=[student], **db_kwargs)
        transport = FakeTransport()

        notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD), make_smtp_settings(), transport, now=NOW,
        )

        written = db[ATTENDANCE_NOTIFICATIONS].find({"student_id": student["_id"]})
        assert len(written) == 2
        assert {document["class_id"] for document in written} == {
            klass["_id"] for klass in db_kwargs["classes"]
        }
        assert {document["sent_at"] for document in written} == {NOW}
        for document in written:
            assert document["threshold"] == THRESHOLD
            assert document["email"] == student["email"]

    def test_running_the_command_twice_in_a_row_sends_zero_on_the_second_run(self):
        student, db_kwargs = _build_student_short_in((1, 10))
        db = make_fake_notifications_db(users=[student], **db_kwargs)

        first = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD, cooldown_days=7),
            make_smtp_settings(), FakeTransport(), now=NOW,
        )
        second_transport = FakeTransport()
        second = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD, cooldown_days=7),
            make_smtp_settings(), second_transport, now=NOW,
        )

        assert first["students_notified"] == 1
        assert second["students_notified"] == 0
        assert second["skipped_cooldown"] == 1
        assert second_transport.sent == []

    def test_a_pair_whose_last_sent_at_is_older_than_the_cooldown_is_mailed_again(self):
        student, db_kwargs = _build_student_short_in((1, 10))
        klass = db_kwargs["classes"][0]
        old_notification = make_attendance_notification(
            student["_id"], klass["_id"], sent_at=NOW - timedelta(days=30),
        )
        db = make_fake_notifications_db(
            users=[student], attendance_notifications=[old_notification], **db_kwargs
        )
        transport = FakeTransport()

        result = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD, cooldown_days=7),
            make_smtp_settings(), transport, now=NOW,
        )

        assert result["students_notified"] == 1
        assert len(transport.sent) == 1
        assert len(db[ATTENDANCE_NOTIFICATIONS].find({"student_id": student["_id"]})) == 2

    def test_a_transport_failure_for_one_student_writes_nothing_for_them_and_does_not_stop_the_others(self):
        failing = make_student(name="Failing Student", email="failing@example.test")
        surviving = make_student(name="Surviving Student", email="surviving@example.test")
        failing, failing_kwargs = _build_student_short_in((1, 10), student=failing)
        surviving, surviving_kwargs = _build_student_short_in((2, 10), student=surviving)
        db = make_fake_notifications_db(
            users=[failing, surviving],
            classes=failing_kwargs["classes"] + surviving_kwargs["classes"],
            class_enrollments=(
                failing_kwargs["class_enrollments"] + surviving_kwargs["class_enrollments"]
            ),
            attendance_records=(
                failing_kwargs["attendance_records"] + surviving_kwargs["attendance_records"]
            ),
            **merge_hierarchies(
                {k: v for k, v in failing_kwargs.items() if k in ("institutes", "departments", "semesters", "courses")},
                {k: v for k, v in surviving_kwargs.items() if k in ("institutes", "departments", "semesters", "courses")},
            ),
        )
        transport = FakeTransport(fail_for={"failing@example.test"})

        result = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD), make_smtp_settings(), transport, now=NOW,
        )

        assert result["failed"] == 1
        assert result["students_notified"] == 1

        assert db[ATTENDANCE_NOTIFICATIONS].find({"student_id": failing["_id"]}) == []
        assert len(db[ATTENDANCE_NOTIFICATIONS].find({"student_id": surviving["_id"]})) == 1

    def test_a_student_whose_every_short_class_is_inside_the_cooldown_receives_no_email(self):
        student, db_kwargs = _build_student_short_in((1, 10), (2, 10))
        notifications = [
            make_attendance_notification(
                student["_id"], klass["_id"], sent_at=NOW - timedelta(days=1),
            )
            for klass in db_kwargs["classes"]
        ]
        db = make_fake_notifications_db(
            users=[student], attendance_notifications=notifications, **db_kwargs
        )
        transport = FakeTransport()

        result = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD, cooldown_days=7),
            make_smtp_settings(), transport, now=NOW,
        )

        assert result["students_notified"] == 0
        assert result["skipped_cooldown"] == 2
        assert transport.sent == []

    def test_a_student_with_one_cooled_down_class_and_one_due_class_is_mailed_about_only_the_due_one(self):
        student, db_kwargs = _build_student_short_in((1, 10), (2, 10))
        cooled_class, due_class = db_kwargs["classes"]
        db_kwargs["classes"][0]["name"] = "Cooled-Class"
        db_kwargs["classes"][1]["name"] = "Due-Class"
        notification = make_attendance_notification(
            student["_id"], cooled_class["_id"], sent_at=NOW - timedelta(days=1),
        )
        db = make_fake_notifications_db(
            users=[student], attendance_notifications=[notification], **db_kwargs
        )
        transport = FakeTransport()

        result = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD, cooldown_days=7),
            make_smtp_settings(), transport, now=NOW,
        )

        assert result["students_notified"] == 1
        assert result["skipped_cooldown"] == 1
        body = transport.sent[0].get_content()
        assert "Due-Class" in body
        assert "Cooled-Class" not in body


class TestNotifyLowAttendanceDryRun:
    def test_dry_run_never_invokes_the_transport_and_writes_nothing(self):
        student, db_kwargs = _build_student_short_in((1, 10), (2, 10))
        db = make_fake_notifications_db(users=[student], **db_kwargs)

        result = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD), transport=TransportMustNotBeUsed(),
            dry_run=True, now=NOW,
        )

        assert len(db[ATTENDANCE_NOTIFICATIONS]) == 0
        assert result["students_notified"] == 1
        assert result["classes_notified"] == 2
        assert len(result["recipients"]) == 1
        assert result["recipients"][0]["email"] == student["email"]
        assert len(result["recipients"][0]["classes"]) == 2

    def test_dry_run_requires_neither_transport_nor_smtp_settings(self):
        student, db_kwargs = _build_student_short_in((1, 10))
        db = make_fake_notifications_db(users=[student], **db_kwargs)

        result = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD), dry_run=True, now=NOW,
        )

        assert result["students_notified"] == 1


class TestNotifyLowAttendanceEmptyAndErrorCases:
    def test_no_candidates_returns_a_zeroed_result_and_touches_nothing(self):
        db = make_fake_notifications_db()
        transport = FakeTransport()

        result = notify_low_attendance(
            db, make_sweep_settings(), make_smtp_settings(), transport, now=NOW,
        )

        assert result["candidates"] == 0
        assert result["students_notified"] == 0
        assert transport.sent == []

    def test_sending_without_a_transport_raises_mailer_send_error(self):
        student, db_kwargs = _build_student_short_in((1, 10))
        db = make_fake_notifications_db(users=[student], **db_kwargs)

        with pytest.raises(MailerSendError):
            notify_low_attendance(
                db, make_sweep_settings(threshold=THRESHOLD), make_smtp_settings(),
                transport=None, dry_run=False, now=NOW,
            )


class TestTheTransportGuardRunsBeforeAnyQuery:
    """Added after the quality review of spec 10.

    The guard used to sit just before the send loop, which meant a caller
    who forgot a transport paid for a whole sweep first -- and, on a run
    where nobody happened to be below the threshold, never found out at
    all. Both are the kind of misconfiguration this project prefers to
    surface immediately (see notifications/settings.py, which validates
    everything before any I/O).
    """

    class _UnusableDb:
        """Fails the test if the sweep reaches the database at all."""

        def __getitem__(self, name):
            raise AssertionError(
                f"no collection ({name}) should be read before the transport guard"
            )

    def test_a_missing_transport_is_caught_before_the_database_is_touched(self):
        with pytest.raises(MailerSendError):
            notify_low_attendance(
                self._UnusableDb(), make_sweep_settings(), make_smtp_settings(),
                transport=None, now=NOW,
            )

    def test_missing_smtp_settings_are_caught_before_the_database_is_touched(self):
        with pytest.raises(MailerSendError):
            notify_low_attendance(
                self._UnusableDb(), make_sweep_settings(), None,
                transport=FakeTransport(), now=NOW,
            )

    def test_an_empty_database_still_raises_rather_than_returning_quietly(self):
        """The gap the old placement left: with nobody below the
        threshold there were no `due` entries, so the guard was skipped
        and a caller with no transport got a clean zeroed result back.
        """
        db = make_fake_notifications_db()

        with pytest.raises(MailerSendError):
            notify_low_attendance(
                db, make_sweep_settings(), make_smtp_settings(),
                transport=None, now=NOW,
            )

    def test_a_dry_run_needs_neither_and_still_reaches_the_database(self):
        student, db_kwargs = _build_student_short_in((1, 10))
        db = make_fake_notifications_db(users=[student], **db_kwargs)

        result = notify_low_attendance(
            db, make_sweep_settings(threshold=THRESHOLD), dry_run=True, now=NOW,
        )

        assert result["students_notified"] == 1
