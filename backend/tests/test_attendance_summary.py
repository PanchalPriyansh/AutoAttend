"""Tests for backend/attendance/summary.py (the student-facing read side)
and the two shared helpers it is graded against
(attendance/serializers.py::attendance_percentage/_standing).

Spec contract under test (09-student-attendance-dashboard.md, "Backend" +
"Rules for implementation" + "Definition of done"):
  - `require_enrolled_class`: 404 for a missing class, 403 for a class
    that exists but this student is not enrolled in, the document
    returned for a class they are actually enrolled in.
  - `class_attendance_overview`: one `(class_document, context, counts)`
    triple per class this student is enrolled in, labelled with hierarchy
    names; `overall` whose counts equal the sum of the per-class counts;
    empty enrollment returns `([], zeroed counts)`; a class with no
    attendance taken yet is included with all-zero counts; the
    denominator is only the records this student has -- a student
    enrolled after some lectures were taken is not marked down for them;
    a record naming this student for a class they are not enrolled in is
    excluded from both the per-class entry and `overall`; another
    student's record in a shared class never affects this student's
    counts; entries are ordered by course then class name, matching
    `list_assigned_classes`.
  - `student_class_attendance`: `require_enrolled_class` gates it the same
    way; lectures are newest-first and `monthly` is oldest-first;
    present + absent == total holds in the header, in every monthly
    bucket, and against `len(lectures)`; an inclusive `date_from`/
    `date_to` narrows lectures and monthly together; a record whose
    session has since been deleted is skipped rather than raised.
  - `attendance_percentage`: rounds to one decimal and is `None`, never
    `0`, when there is nothing to divide by -- the one rule every count in
    this feature depends on.

Service-level tests call `attendance/summary.py` directly against the
`FakeCollection`-backed db from attendance_test_helpers.py -- no Flask, no
HTTP, no live MongoDB, and no CV library is imported anywhere on this
path. No real credentials, production data, or biometric data are used
anywhere in this file.
"""

from datetime import datetime, timezone

import pytest
from attendance_test_helpers import (
    make_attendance_record,
    make_attendance_session,
    make_class,
    make_class_enrollment,
    make_course,
    make_department,
    make_fake_attendance_db,
    make_institute,
    make_semester,
    make_user,
)
from bson import ObjectId

from attendance import summary
from attendance.errors import ForbiddenError
from attendance.serializers import attendance_percentage
from common.errors import NotFoundError


def _build_class_with_hierarchy(
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


def _merge_hierarchies(*hierarchies):
    merged = {"institutes": [], "departments": [], "semesters": [], "courses": []}
    for hierarchy in hierarchies:
        for key in merged:
            merged[key].extend(hierarchy[key])
    return merged


def _student():
    return make_user(email=f"student-{ObjectId()}@college.test", role="student")


# --- require_enrolled_class ---------------------------------------------------


class TestRequireEnrolledClass:
    def test_missing_class_raises_not_found(self):
        db = make_fake_attendance_db()

        with pytest.raises(NotFoundError):
            summary.require_enrolled_class(db, ObjectId(), ObjectId())

    def test_a_class_the_student_is_not_enrolled_in_raises_forbidden(self):
        klass, hierarchy = _build_class_with_hierarchy()
        db = make_fake_attendance_db(classes=[klass], **hierarchy)

        with pytest.raises(ForbiddenError):
            summary.require_enrolled_class(db, klass["_id"], ObjectId())

    def test_an_enrolled_class_returns_the_document(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment], **hierarchy,
        )

        document = summary.require_enrolled_class(db, klass["_id"], student["_id"])

        assert document["_id"] == klass["_id"]


# --- class_attendance_overview ------------------------------------------------


class TestClassAttendanceOverview:
    def test_returns_one_entry_per_enrolled_class_with_hierarchy_context(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        session = make_attendance_session(klass["_id"])
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present"),
            make_attendance_record(
                ObjectId(), klass["_id"], student["_id"], status="present",
            ),
            make_attendance_record(ObjectId(), klass["_id"], student["_id"], status="absent"),
        ]
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment],
            attendance_records=records, **hierarchy,
        )

        entries, overall = summary.class_attendance_overview(db, student["_id"])

        assert len(entries) == 1
        document, context, counts = entries[0]
        assert document["_id"] == klass["_id"]
        assert context["course"] == "Data Structures"
        assert context["semester"] == "Semester 3"
        assert context["department"] == "Computer Engineering"
        assert context["institute"] == "Test Institute"
        assert counts == {"present": 2, "absent": 1, "total": 3}

    def test_overall_equals_the_sum_of_the_per_class_counts(self):
        student = _student()
        class_a, hierarchy_a = _build_class_with_hierarchy(class_name="A", course_name="Course A")
        class_b, hierarchy_b = _build_class_with_hierarchy(class_name="B", course_name="Course B")
        enrollments = [
            make_class_enrollment(class_a["_id"], student["_id"]),
            make_class_enrollment(class_b["_id"], student["_id"]),
        ]
        records = [
            make_attendance_record(ObjectId(), class_a["_id"], student["_id"], status="present"),
            make_attendance_record(ObjectId(), class_a["_id"], student["_id"], status="present"),
            make_attendance_record(ObjectId(), class_b["_id"], student["_id"], status="absent"),
        ]
        db = make_fake_attendance_db(
            classes=[class_a, class_b], class_enrollments=enrollments,
            attendance_records=records, **_merge_hierarchies(hierarchy_a, hierarchy_b),
        )

        entries, overall = summary.class_attendance_overview(db, student["_id"])

        by_class = {document["_id"]: counts for document, _, counts in entries}
        expected_total = {
            key: sum(counts[key] for counts in by_class.values())
            for key in ("present", "absent", "total")
        }
        assert overall == expected_total
        assert overall == {"present": 2, "absent": 1, "total": 3}

    def test_no_enrollment_returns_empty_entries_and_a_zeroed_overall(self):
        db = make_fake_attendance_db()

        entries, overall = summary.class_attendance_overview(db, ObjectId())

        assert entries == []
        assert overall == {"present": 0, "absent": 0, "total": 0}

    def test_a_class_with_no_attendance_taken_yet_has_all_zero_counts(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment], **hierarchy,
        )

        entries, overall = summary.class_attendance_overview(db, student["_id"])

        _, _, counts = entries[0]
        assert counts == {"present": 0, "absent": 0, "total": 0}
        assert overall == {"present": 0, "absent": 0, "total": 0}

    def test_student_enrolled_after_some_lectures_is_not_marked_down_for_them(self):
        """Only three attendance_records happen to exist for this student in
        this class, regardless of how many sessions the class has held --
        the denominator must reflect that, never a session count.
        """
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        # No records reference the two lectures held before this student
        # joined -- there is no row for them, by construction.
        records = [
            make_attendance_record(ObjectId(), klass["_id"], student["_id"], status="present"),
        ]
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment],
            attendance_records=records, **hierarchy,
        )

        entries, overall = summary.class_attendance_overview(db, student["_id"])

        _, _, counts = entries[0]
        assert counts == {"present": 1, "absent": 0, "total": 1}

    def test_a_record_for_a_class_this_student_is_not_enrolled_in_is_excluded(self):
        """The trap case the spec calls out directly: a record that
        references this student's id but a class_id absent from their
        enrollments must not appear in `entries` and must not be summed
        into `overall`.
        """
        student = _student()
        enrolled_class, enrolled_hierarchy = _build_class_with_hierarchy(
            class_name="A", course_name="Course A",
        )
        unenrolled_class, unenrolled_hierarchy = _build_class_with_hierarchy(
            class_name="B", course_name="Course B",
        )
        enrollment = make_class_enrollment(enrolled_class["_id"], student["_id"])
        records = [
            make_attendance_record(ObjectId(), enrolled_class["_id"], student["_id"], status="present"),
            # This student has no enrollment for `unenrolled_class`.
            make_attendance_record(ObjectId(), unenrolled_class["_id"], student["_id"], status="absent"),
        ]
        db = make_fake_attendance_db(
            classes=[enrolled_class, unenrolled_class], class_enrollments=[enrollment],
            attendance_records=records,
            **_merge_hierarchies(enrolled_hierarchy, unenrolled_hierarchy),
        )

        entries, overall = summary.class_attendance_overview(db, student["_id"])

        assert [document["_id"] for document, _, _ in entries] == [enrolled_class["_id"]]
        assert overall == {"present": 1, "absent": 0, "total": 1}

    def test_a_classmates_record_in_a_shared_class_does_not_affect_this_students_counts(self):
        student = _student()
        classmate = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollments = [
            make_class_enrollment(klass["_id"], student["_id"]),
            make_class_enrollment(klass["_id"], classmate["_id"]),
        ]
        session = make_attendance_session(klass["_id"])
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present"),
            make_attendance_record(session["_id"], klass["_id"], classmate["_id"], status="absent"),
            make_attendance_record(session["_id"], klass["_id"], classmate["_id"], status="absent"),
        ]
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=enrollments,
            attendance_records=records, **hierarchy,
        )

        entries, _ = summary.class_attendance_overview(db, student["_id"])

        _, _, counts = entries[0]
        assert counts == {"present": 1, "absent": 0, "total": 1}

    def test_entries_are_ordered_by_course_then_class_name(self):
        student = _student()
        class_z, hierarchy_z = _build_class_with_hierarchy(class_name="A", course_name="Zoology")
        class_b, hierarchy_b = _build_class_with_hierarchy(class_name="A", course_name="Biology")
        enrollments = [
            make_class_enrollment(class_z["_id"], student["_id"]),
            make_class_enrollment(class_b["_id"], student["_id"]),
        ]
        db = make_fake_attendance_db(
            classes=[class_z, class_b], class_enrollments=enrollments,
            **_merge_hierarchies(hierarchy_z, hierarchy_b),
        )

        entries, _ = summary.class_attendance_overview(db, student["_id"])

        assert [context["course"] for _, context, _ in entries] == ["Biology", "Zoology"]


# --- student_class_attendance -------------------------------------------------


class TestStudentClassAttendance:
    def test_an_unenrolled_class_raises_forbidden(self):
        klass, hierarchy = _build_class_with_hierarchy()
        db = make_fake_attendance_db(classes=[klass], **hierarchy)

        with pytest.raises(ForbiddenError):
            summary.student_class_attendance(
                db, klass["_id"], ObjectId(), date_from=None, date_to=None,
            )

    def test_a_nonexistent_class_raises_not_found(self):
        db = make_fake_attendance_db()

        with pytest.raises(NotFoundError):
            summary.student_class_attendance(
                db, ObjectId(), ObjectId(), date_from=None, date_to=None,
            )

    def test_lectures_are_returned_newest_first(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        oldest = make_attendance_session(klass["_id"], date=datetime(2020, 1, 1, tzinfo=timezone.utc))
        middle = make_attendance_session(klass["_id"], date=datetime(2020, 1, 15, tzinfo=timezone.utc))
        newest = make_attendance_session(klass["_id"], date=datetime(2020, 1, 31, tzinfo=timezone.utc))
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present")
            for session in (oldest, middle, newest)
        ]
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment],
            attendance_sessions=[oldest, middle, newest], attendance_records=records,
            **hierarchy,
        )

        _, _, _, _, lectures = summary.student_class_attendance(
            db, klass["_id"], student["_id"], date_from=None, date_to=None,
        )

        assert [lecture["date"] for lecture in lectures] == [
            newest["date"], middle["date"], oldest["date"],
        ]

    def test_monthly_is_returned_oldest_first(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        march = make_attendance_session(klass["_id"], date=datetime(2020, 3, 1, tzinfo=timezone.utc))
        january = make_attendance_session(klass["_id"], date=datetime(2020, 1, 1, tzinfo=timezone.utc))
        february = make_attendance_session(klass["_id"], date=datetime(2020, 2, 1, tzinfo=timezone.utc))
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present")
            for session in (march, january, february)
        ]
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment],
            attendance_sessions=[march, january, february], attendance_records=records,
            **hierarchy,
        )

        _, _, _, monthly, _ = summary.student_class_attendance(
            db, klass["_id"], student["_id"], date_from=None, date_to=None,
        )

        assert [bucket["month"] for bucket in monthly] == ["2020-01", "2020-02", "2020-03"]

    def test_present_plus_absent_equals_total_in_counts_monthly_and_lecture_length(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        sessions = [
            make_attendance_session(klass["_id"], date=datetime(2020, 1, day, tzinfo=timezone.utc))
            for day in (1, 2, 3, 4)
        ]
        statuses = ["present", "absent", "present", "present"]
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status=status)
            for session, status in zip(sessions, statuses)
        ]
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment],
            attendance_sessions=sessions, attendance_records=records, **hierarchy,
        )

        _, _, counts, monthly, lectures = summary.student_class_attendance(
            db, klass["_id"], student["_id"], date_from=None, date_to=None,
        )

        assert counts["present"] + counts["absent"] == counts["total"] == 4
        assert len(lectures) == counts["total"]
        for bucket in monthly:
            assert bucket["present"] + bucket["absent"] == bucket["total"]
        assert sum(bucket["total"] for bucket in monthly) == counts["total"]

    def test_denominator_is_the_students_own_records_only(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        # A record exists only for the lecture held after this student
        # joined; nothing references the earlier one for them.
        after_joining = make_attendance_session(
            klass["_id"], date=datetime(2020, 2, 1, tzinfo=timezone.utc)
        )
        records = [
            make_attendance_record(after_joining["_id"], klass["_id"], student["_id"], status="present"),
        ]
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment],
            attendance_sessions=[after_joining], attendance_records=records, **hierarchy,
        )

        _, _, counts, _, lectures = summary.student_class_attendance(
            db, klass["_id"], student["_id"], date_from=None, date_to=None,
        )

        assert counts == {"present": 1, "absent": 0, "total": 1}
        assert len(lectures) == 1

    def test_date_range_narrows_lectures_and_monthly_to_the_same_set(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        january = make_attendance_session(klass["_id"], date=datetime(2020, 1, 1, tzinfo=timezone.utc))
        february = make_attendance_session(klass["_id"], date=datetime(2020, 2, 1, tzinfo=timezone.utc))
        march = make_attendance_session(klass["_id"], date=datetime(2020, 3, 1, tzinfo=timezone.utc))
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present")
            for session in (january, february, march)
        ]
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment],
            attendance_sessions=[january, february, march], attendance_records=records,
            **hierarchy,
        )

        _, _, counts, monthly, lectures = summary.student_class_attendance(
            db, klass["_id"], student["_id"],
            date_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
            date_to=datetime(2020, 2, 1, tzinfo=timezone.utc),
        )

        assert {lecture["date"] for lecture in lectures} == {january["date"], february["date"]}
        assert {bucket["month"] for bucket in monthly} == {"2020-01", "2020-02"}
        assert counts["total"] == 2

    def test_a_date_range_bound_is_inclusive(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        session = make_attendance_session(klass["_id"], date=datetime(2020, 1, 15, tzinfo=timezone.utc))
        record = make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present")
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment],
            attendance_sessions=[session], attendance_records=[record], **hierarchy,
        )

        _, _, counts, _, lectures = summary.student_class_attendance(
            db, klass["_id"], student["_id"],
            date_from=datetime(2020, 1, 15, tzinfo=timezone.utc),
            date_to=datetime(2020, 1, 15, tzinfo=timezone.utc),
        )

        assert counts["total"] == 1
        assert lectures[0]["date"] == session["date"]

    def test_a_record_whose_session_has_been_deleted_is_skipped_not_raised(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        # No matching attendance_sessions document for this record's
        # session_id -- as if the session had been deleted after the
        # record was written (should be unreachable in practice, since a
        # delete removes its records first, but must not crash here).
        orphaned_record = make_attendance_record(
            ObjectId(), klass["_id"], student["_id"], status="present",
        )
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment],
            attendance_records=[orphaned_record], **hierarchy,
        )

        _, _, counts, monthly, lectures = summary.student_class_attendance(
            db, klass["_id"], student["_id"], date_from=None, date_to=None,
        )

        assert counts == {"present": 0, "absent": 0, "total": 0}
        assert monthly == []
        assert lectures == []

    def test_returns_the_class_document_and_its_hierarchy_context(self):
        student = _student()
        klass, hierarchy = _build_class_with_hierarchy(course_name="Operating Systems")
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        db = make_fake_attendance_db(
            classes=[klass], class_enrollments=[enrollment], **hierarchy,
        )

        document, context, _, _, _ = summary.student_class_attendance(
            db, klass["_id"], student["_id"], date_from=None, date_to=None,
        )

        assert document["_id"] == klass["_id"]
        assert context["course"] == "Operating Systems"


# --- attendance_percentage (attendance/serializers.py) -------------------------
#
# Every count `summary.py` produces is only meaningful once this rule is
# applied to it, so it is worth its own direct coverage rather than only
# being exercised indirectly through a serialized response.


class TestAttendancePercentage:
    def test_zero_total_is_none_not_zero(self):
        assert attendance_percentage(0, 0) is None

    def test_full_attendance_is_100(self):
        assert attendance_percentage(4, 4) == 100.0

    def test_no_attendance_is_0_not_none_when_total_is_nonzero(self):
        assert attendance_percentage(0, 4) == 0.0

    def test_rounds_to_one_decimal_place(self):
        assert attendance_percentage(1, 3) == 33.3

    @pytest.mark.parametrize(
        "present, total, expected",
        [(1, 4, 25.0), (3, 4, 75.0), (2, 3, 66.7)],
    )
    def test_representative_fractions(self, present, total, expected):
        assert attendance_percentage(present, total) == expected
