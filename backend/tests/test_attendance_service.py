"""Tests for backend/attendance/service.py.

Spec contract under test (07-attendance-capture.md, "Rules for
implementation" + "Definition of done"):
  - `require_owned_class`: 404 for a missing class, 403 for a class
    assigned to somebody else (including nobody), and the document
    returned for the true owner.
  - `list_assigned_classes`: scoped to the caller, labelled with
    hierarchy names, an accurate student count, and an empty list (not
    an error) for a faculty member with no classes.
  - `recognize`: authorization is checked before recognition
    availability; the roster partitions into `recognized` /
    `unrecognized` / `not_enrolled` covering every student exactly once;
    nothing is written to either collection; a zero-sample student always
    lands in `not_enrolled`; an unmatched face becomes an unknown face;
    two faces matching one student collapse to the better distance with
    the surplus becoming unknown; confidence bands reflect distance;
    encodings scoped to the class mean another class's students can never
    match; video returns `frames_analyzed > 1`, unions recognitions
    across frames, and respects the frame cap; no captured bytes reach a
    log line.
  - `save_session`: creates one session and one record per enrolled
    student with a real `datetime` `date` and `taken_by` from the caller
    argument; absences are stored explicitly; `class_id` is denormalised
    from the session onto every record; an incomplete/duplicate/stranger
    `records` payload is rejected and writes nothing; a second save
    without `replace` conflicts; `replace=True` validates before
    deleting the old records and leaves exactly one session; a race that
    surfaces as `DuplicateKeyError` is reported the same way as the
    ordinary conflict.
  - `get_session`: 404 for an untaken date, the saved session + records
    otherwise, ownership enforced.

Also covers 08-faculty-attendance-history.md's four additions to this
module ("APIs" + "Rules for implementation" + "Definition of done"):
  - `list_sessions`: scoped to one owned class, newest-first, `from`/`to`
    inclusive on the lecture date, `total` unaffected by `limit`/`skip`
    paging, an empty owned class returns `([], 0)` rather than an error,
    and counts come from exactly one grouped aggregation regardless of how
    many sessions are on the page.
  - `get_session_by_id`: 404 for an unknown id, 403 for a session under
    somebody else's class, the session plus every stored record (absences
    explicit) otherwise.
  - `update_session_records`: only `status`/`marked_by` change; `class_id`,
    `date`, `source`, `created_at`, `taken_by` are untouched;
    `updated_at`/`updated_by` are the only session fields touched, and
    `updated_by` is exactly the `faculty_id` argument; the payload is
    validated against the session's own stored students, not the class's
    current roster, so enrolling/unenrolling since the lecture does not
    change what a valid edit looks like; a malformed payload changes
    nothing.
  - `delete_session`: records removed before the session; unrelated
    sessions/records for the same class are untouched; after a delete the
    same class+date can be saved again without a stale conflict.

Service-level tests call `attendance/service.py` directly against the
`FakeCollection`-backed db from attendance_test_helpers.py -- no Flask,
no HTTP, no live MongoDB. `recognition.encoder.closest_match` is always
monkeypatched to `fake_closest_match`, so no real face_recognition/numpy
is ever imported and no real biometric computation happens anywhere in
this file.
"""

from datetime import datetime, timedelta, timezone

import pytest
from attendance_test_helpers import (
    FakeCollection,
    build_owned_class_with_roster,
    fake_closest_match,
    make_attendance_record,
    make_attendance_session,
    make_class,
    make_class_enrollment,
    make_course,
    make_department,
    make_face_encoding,
    make_fake_attendance_db,
    make_institute,
    make_semester,
    make_session_with_records,
    make_user,
    synthetic_encoding,
)
from bson import ObjectId

from attendance import service
from attendance.errors import ForbiddenError
from attendance.validators import parse_attendance_date
from common.errors import ConflictError, NotFoundError, ValidationError
from database.schema import ATTENDANCE_RECORDS, ATTENDANCE_SESSIONS
from recognition import frames as frames_module
from recognition import matcher as matcher_module
from recognition.errors import RecognitionUnavailableError


@pytest.fixture(autouse=True)
def _stub_recognition_defaults(monkeypatch):
    """Every test in this file at least imports recognition/encoder.py's
    constants; anything that actually calls into it gets deterministic,
    library-free stubs by default. Individual tests override
    `encode_faces`/`extract_frames`/availability as needed.
    """
    monkeypatch.setattr("recognition.encoder.is_available", lambda: True)
    monkeypatch.setattr("recognition.encoder.closest_match", fake_closest_match)
    monkeypatch.setattr("recognition.frames.is_available", lambda: True)


def _stub_encode_faces(monkeypatch, frames_faces):
    """`frames_faces` is a list of frames, each a list of synthetic
    encodings the fake detector should report for that frame, consumed in
    call order (`recognize()` calls `encode_faces` once per image it was
    handed -- once for a photo, once per extracted video frame).
    """
    iterator = iter(frames_faces)
    monkeypatch.setattr("recognition.encoder.encode_faces", lambda image_bytes: next(iterator))


def _iso_date(value):
    return parse_attendance_date({"date": value})


# --- require_owned_class -----------------------------------------------------


class TestRequireOwnedClass:
    def test_missing_class_raises_not_found(self):
        db = make_fake_attendance_db()

        with pytest.raises(NotFoundError):
            service.require_owned_class(db, ObjectId(), ObjectId())

    def test_class_assigned_to_someone_else_raises_forbidden(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=ObjectId())
        db = make_fake_attendance_db(classes=[klass])

        with pytest.raises(ForbiddenError):
            service.require_owned_class(db, klass["_id"], faculty_id)

    def test_unassigned_class_raises_forbidden_not_a_server_error(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=None)
        db = make_fake_attendance_db(classes=[klass])

        with pytest.raises(ForbiddenError):
            service.require_owned_class(db, klass["_id"], faculty_id)

    def test_owned_class_returns_the_document(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=faculty_id)
        db = make_fake_attendance_db(classes=[klass])

        result = service.require_owned_class(db, klass["_id"], faculty_id)

        assert result["_id"] == klass["_id"]


# --- list_assigned_classes ----------------------------------------------------


class TestListAssignedClasses:
    def test_returns_empty_list_for_a_faculty_member_with_no_classes(self):
        db = make_fake_attendance_db()

        assert service.list_assigned_classes(db, ObjectId()) == []

    def test_scoped_to_the_acting_faculty_member(self):
        faculty_id = ObjectId()
        mine = make_class(ObjectId(), faculty_id=faculty_id, name="Mine")
        theirs = make_class(ObjectId(), faculty_id=ObjectId(), name="Theirs")
        db = make_fake_attendance_db(classes=[mine, theirs])

        entries = service.list_assigned_classes(db, faculty_id)

        assert [entry[0]["_id"] for entry in entries] == [mine["_id"]]

    def test_includes_hierarchy_names(self):
        faculty_id = ObjectId()
        institute = make_institute(name="Example Institute")
        department = make_department(institute["_id"], name="Computer Engineering")
        semester = make_semester(department["_id"], name="Semester 3")
        course = make_course(semester["_id"], name="Data Structures")
        klass = make_class(course["_id"], faculty_id=faculty_id, name="CS-A")
        db = make_fake_attendance_db(
            institutes=[institute],
            departments=[department],
            semesters=[semester],
            courses=[course],
            classes=[klass],
        )

        entries = service.list_assigned_classes(db, faculty_id)

        _, context, _ = entries[0]
        assert context == {
            "course": "Data Structures",
            "semester": "Semester 3",
            "department": "Computer Engineering",
            "institute": "Example Institute",
        }

    def test_reports_an_accurate_student_count(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=3)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)

        entries = service.list_assigned_classes(db, faculty_id)

        _, _, student_count = entries[0]
        assert student_count == 3

    def test_a_class_with_no_enrolled_students_reports_zero_not_an_error(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=faculty_id)
        db = make_fake_attendance_db(classes=[klass])

        entries = service.list_assigned_classes(db, faculty_id)

        _, _, student_count = entries[0]
        assert student_count == 0


# --- recognize -----------------------------------------------------------------


class TestRecognize:
    def test_ownership_is_checked_before_recognition_availability(self, monkeypatch):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=ObjectId())
        db = make_fake_attendance_db(classes=[klass])
        monkeypatch.setattr("recognition.encoder.is_available", lambda: False)

        with pytest.raises(ForbiddenError):
            service.recognize(db, klass["_id"], faculty_id, image_bytes=b"synthetic-image-bytes")

    def test_raises_recognition_unavailable_when_the_library_is_missing(self, monkeypatch):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=faculty_id)
        db = make_fake_attendance_db(classes=[klass])
        monkeypatch.setattr("recognition.encoder.is_available", lambda: False)

        with pytest.raises(RecognitionUnavailableError):
            service.recognize(db, klass["_id"], faculty_id, image_bytes=b"synthetic-image-bytes")

    def test_a_video_request_reports_unavailable_when_only_opencv_is_missing(self, monkeypatch):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=faculty_id)
        db = make_fake_attendance_db(classes=[klass])
        monkeypatch.setattr("recognition.frames.is_available", lambda: False)

        with pytest.raises(RecognitionUnavailableError):
            service.recognize(
                db, klass["_id"], faculty_id, video_bytes=b"synthetic-video-bytes",
                content_type="video/mp4",
            )

    def test_a_photo_still_works_when_only_opencv_is_missing(self, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        monkeypatch.setattr("recognition.frames.is_available", lambda: False)
        _stub_encode_faces(monkeypatch, [[]])

        proposal = service.recognize(db, klass["_id"], faculty_id, image_bytes=b"synthetic-image-bytes")

        assert proposal["source"] == "photo"

    def test_partitions_the_roster_into_recognized_unrecognized_and_not_enrolled(self, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=3)
        recognized_student, unrecognized_student, bare_student = students
        encodings = [
            make_face_encoding(recognized_student["_id"], encoding=synthetic_encoding(0.0)),
            make_face_encoding(unrecognized_student["_id"], encoding=synthetic_encoding(50.0)),
            # bare_student is intentionally given zero samples.
        ]
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments, face_encodings=encodings,
        )
        _stub_encode_faces(monkeypatch, [[synthetic_encoding(0.02)]])

        proposal = service.recognize(db, klass["_id"], faculty_id, image_bytes=b"synthetic-image-bytes")

        recognized_ids = {student["_id"] for student, _, _ in proposal["recognized"]}
        unrecognized_ids = {student["_id"] for student, _ in proposal["unrecognized"]}
        not_enrolled_ids = {student["_id"] for student in proposal["not_enrolled"]}

        assert recognized_ids == {recognized_student["_id"]}
        assert unrecognized_ids == {unrecognized_student["_id"]}
        assert not_enrolled_ids == {bare_student["_id"]}
        assert recognized_ids | unrecognized_ids | not_enrolled_ids == {s["_id"] for s in students}
        assert len(recognized_ids) + len(unrecognized_ids) + len(not_enrolled_ids) == 3

    def test_writes_nothing_to_either_collection(self, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        encodings = [make_face_encoding(students[0]["_id"], encoding=synthetic_encoding(0.0))]
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments, face_encodings=encodings,
        )
        _stub_encode_faces(monkeypatch, [[synthetic_encoding(0.0)]])

        service.recognize(db, klass["_id"], faculty_id, image_bytes=b"synthetic-image-bytes")

        assert list(db[ATTENDANCE_SESSIONS].find({})) == []
        assert list(db[ATTENDANCE_RECORDS].find({})) == []

    def test_a_student_with_zero_samples_is_always_not_enrolled_never_unrecognized(self, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        _stub_encode_faces(monkeypatch, [[]])

        proposal = service.recognize(db, klass["_id"], faculty_id, image_bytes=b"synthetic-image-bytes")

        assert proposal["unrecognized"] == []
        assert [s["_id"] for s in proposal["not_enrolled"]] == [students[0]["_id"]]

    def test_a_face_matching_nobody_increments_unknown_faces_and_matches_no_student(self, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        encodings = [make_face_encoding(students[0]["_id"], encoding=synthetic_encoding(0.0))]
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments, face_encodings=encodings,
        )
        _stub_encode_faces(monkeypatch, [[synthetic_encoding(999.0)]])

        proposal = service.recognize(db, klass["_id"], faculty_id, image_bytes=b"synthetic-image-bytes")

        assert proposal["unknown_faces"] == 1
        assert proposal["recognized"] == []

    def test_two_faces_matching_one_student_mark_present_once_with_the_better_distance(
        self, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        encodings = [make_face_encoding(students[0]["_id"], encoding=synthetic_encoding(0.0))]
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments, face_encodings=encodings,
        )
        _stub_encode_faces(monkeypatch, [[synthetic_encoding(0.30), synthetic_encoding(0.05)]])

        proposal = service.recognize(db, klass["_id"], faculty_id, image_bytes=b"synthetic-image-bytes")

        assert len(proposal["recognized"]) == 1
        student, distance, _ = proposal["recognized"][0]
        assert student["_id"] == students[0]["_id"]
        assert distance == pytest.approx(0.05)
        assert proposal["unknown_faces"] == 1

    def test_confidence_bands_reflect_distance(self, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        high_student, low_student = students
        encodings = [
            make_face_encoding(high_student["_id"], encoding=synthetic_encoding(0.0)),
            make_face_encoding(low_student["_id"], encoding=synthetic_encoding(10.0)),
        ]
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments, face_encodings=encodings,
        )
        high_conf_face = synthetic_encoding(0.0)
        low_conf_face = synthetic_encoding(10.0 + matcher_module.LOW_CONFIDENCE_DISTANCE + 0.01)
        _stub_encode_faces(monkeypatch, [[high_conf_face, low_conf_face]])

        proposal = service.recognize(db, klass["_id"], faculty_id, image_bytes=b"synthetic-image-bytes")

        confidences = {
            student["_id"]: confidence for student, _, confidence in proposal["recognized"]
        }
        assert confidences[high_student["_id"]] == "high"
        assert confidences[low_student["_id"]] == "low"

    def test_encodings_from_another_class_cannot_produce_a_match(self, monkeypatch):
        faculty_id = ObjectId()
        class_a, students_a, enrollments_a = build_owned_class_with_roster(faculty_id, student_count=1)
        class_b, students_b, enrollments_b = build_owned_class_with_roster(faculty_id, student_count=1)
        encodings = [
            make_face_encoding(students_a[0]["_id"], encoding=synthetic_encoding(100.0)),
            # student_b numerically coincides with the detected face below,
            # but student_b is not on class_a's roster.
            make_face_encoding(students_b[0]["_id"], encoding=synthetic_encoding(0.0)),
        ]
        db = make_fake_attendance_db(
            classes=[class_a, class_b],
            users=students_a + students_b,
            class_enrollments=enrollments_a + enrollments_b,
            face_encodings=encodings,
        )
        _stub_encode_faces(monkeypatch, [[synthetic_encoding(0.0)]])

        proposal = service.recognize(db, class_a["_id"], faculty_id, image_bytes=b"synthetic-image-bytes")

        recognized_ids = {student["_id"] for student, _, _ in proposal["recognized"]}
        assert recognized_ids == set()
        assert proposal["unknown_faces"] == 1

    def test_video_returns_multiple_frames_analyzed_and_unions_recognitions(self, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        encodings = [make_face_encoding(students[0]["_id"], encoding=synthetic_encoding(0.0))]
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments, face_encodings=encodings,
        )
        monkeypatch.setattr(
            "recognition.frames.extract_frames",
            lambda video_bytes, content_type=None: [b"f1", b"f2", b"f3"],
        )
        # The student only appears in the second of three frames.
        _stub_encode_faces(monkeypatch, [[], [synthetic_encoding(0.0)], []])

        proposal = service.recognize(
            db, klass["_id"], faculty_id, video_bytes=b"synthetic-video-bytes",
            content_type="video/mp4",
        )

        assert proposal["source"] == "video"
        assert proposal["frames_analyzed"] == 3
        assert [s["_id"] for s, _, _ in proposal["recognized"]] == [students[0]["_id"]]

    def test_video_frame_cap_is_respected(self, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        capped_frames = [f"frame-{i}".encode() for i in range(frames_module.MAX_FRAMES)]
        monkeypatch.setattr(
            "recognition.frames.extract_frames",
            lambda video_bytes, content_type=None: capped_frames,
        )
        _stub_encode_faces(monkeypatch, [[] for _ in capped_frames])

        proposal = service.recognize(
            db, klass["_id"], faculty_id, video_bytes=b"synthetic-video-bytes",
            content_type="video/mp4",
        )

        assert proposal["frames_analyzed"] == frames_module.MAX_FRAMES

    def test_no_media_bytes_appear_in_the_recognition_log_line(self, monkeypatch, caplog):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        marker = "UNIQUE_MEDIA_MARKER_DO_NOT_LOG_5f8a"
        _stub_encode_faces(monkeypatch, [[]])

        with caplog.at_level("INFO"):
            service.recognize(db, klass["_id"], faculty_id, image_bytes=marker.encode())

        for record in caplog.records:
            assert marker not in record.getMessage()


# --- get_session -----------------------------------------------------------------


class TestGetSession:
    def test_404_when_no_session_exists_for_the_date(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=faculty_id)
        db = make_fake_attendance_db(classes=[klass])

        with pytest.raises(NotFoundError):
            service.get_session(db, klass["_id"], _iso_date("2020-01-01"), faculty_id)

    def test_ownership_is_enforced(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=ObjectId())
        db = make_fake_attendance_db(classes=[klass])

        with pytest.raises(ForbiddenError):
            service.get_session(db, klass["_id"], _iso_date("2020-01-01"), faculty_id)

    def test_returns_the_saved_session_with_its_records(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        date = _iso_date("2020-01-01")
        session_doc = make_attendance_session(klass["_id"], date=date, taken_by=faculty_id)
        records = [
            make_attendance_record(session_doc["_id"], klass["_id"], students[0]["_id"], status="present"),
            make_attendance_record(session_doc["_id"], klass["_id"], students[1]["_id"], status="absent"),
        ]
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session_doc], attendance_records=records,
        )

        session, pairs = service.get_session(db, klass["_id"], date, faculty_id)

        assert session["_id"] == session_doc["_id"]
        assert {student["_id"] for _, student in pairs} == {s["_id"] for s in students}


# --- save_session -----------------------------------------------------------------


class TestSaveSession:
    def test_creates_one_session_and_one_record_per_enrolled_student(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        payload = [
            {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
            {"student_id": str(students[1]["_id"]), "status": "absent", "marked_by": "recognition"},
        ]

        session, pairs, created = service.save_session(
            db, klass["_id"], date, faculty_id, source="photo", records=payload, replace=False,
        )

        assert created is True
        assert len(list(db[ATTENDANCE_SESSIONS].find({}))) == 1
        assert len(list(db[ATTENDANCE_RECORDS].find({}))) == 2
        assert len(pairs) == 2

    def test_date_is_stored_as_a_real_datetime_at_utc_midnight(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]

        session, _, _ = service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=False,
        )

        stored = db[ATTENDANCE_SESSIONS].find_one({"_id": session["_id"]})
        assert isinstance(stored["date"], datetime)
        assert not isinstance(stored["date"], str)
        assert stored["date"] == date
        assert stored["date"].hour == 0
        assert stored["date"].minute == 0

    def test_taken_by_matches_the_acting_faculty_member(self):
        faculty_id = ObjectId()
        spoofed_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]

        session, _, _ = service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=False,
        )

        assert session["taken_by"] == faculty_id
        assert session["taken_by"] != spoofed_id

    def test_absent_students_are_stored_explicitly(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        payload = [{"student_id": str(students[0]["_id"]), "status": "absent"}]

        service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=False,
        )

        stored = list(db[ATTENDANCE_RECORDS].find({}))
        assert len(stored) == 1
        assert stored[0]["status"] == "absent"

    def test_class_id_on_every_record_matches_the_parent_sessions_class_id(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        payload = [
            {"student_id": str(students[0]["_id"]), "status": "present"},
            {"student_id": str(students[1]["_id"]), "status": "absent"},
        ]

        session, _, _ = service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=False,
        )

        stored = list(db[ATTENDANCE_RECORDS].find({}))
        assert all(record["class_id"] == session["class_id"] == klass["_id"] for record in stored)

    def test_records_missing_a_roster_student_is_rejected_and_writes_nothing(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        incomplete_payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]

        with pytest.raises(ValidationError):
            service.save_session(
                db, klass["_id"], date, faculty_id, source="manual",
                records=incomplete_payload, replace=False,
            )

        assert list(db[ATTENDANCE_SESSIONS].find({})) == []
        assert list(db[ATTENDANCE_RECORDS].find({})) == []

    def test_records_with_a_duplicate_student_is_rejected_and_writes_nothing(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        duplicate_payload = [
            {"student_id": str(students[0]["_id"]), "status": "present"},
            {"student_id": str(students[0]["_id"]), "status": "absent"},
        ]

        with pytest.raises(ValidationError):
            service.save_session(
                db, klass["_id"], date, faculty_id, source="manual",
                records=duplicate_payload, replace=False,
            )

        assert list(db[ATTENDANCE_SESSIONS].find({})) == []

    def test_records_naming_a_non_enrolled_student_is_rejected_and_writes_nothing(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        stranger_payload = [
            {"student_id": str(students[0]["_id"]), "status": "present"},
            {"student_id": str(ObjectId()), "status": "present"},
        ]

        with pytest.raises(ValidationError):
            service.save_session(
                db, klass["_id"], date, faculty_id, source="manual",
                records=stranger_payload, replace=False,
            )

        assert list(db[ATTENDANCE_SESSIONS].find({})) == []

    def test_a_class_with_no_enrolled_students_is_rejected(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=faculty_id)
        db = make_fake_attendance_db(classes=[klass])
        date = _iso_date("2020-01-01")

        with pytest.raises(ValidationError):
            service.save_session(
                db, klass["_id"], date, faculty_id, source="manual", records=[], replace=False,
            )

    def test_a_second_save_without_replace_is_a_conflict(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]
        service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=False,
        )

        with pytest.raises(ConflictError):
            service.save_session(
                db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=False,
            )

    def test_replace_true_leaves_exactly_one_session_and_replaces_the_records(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        first_payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]
        second_payload = [{"student_id": str(students[0]["_id"]), "status": "absent"}]

        first_session, _, first_created = service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=first_payload, replace=False,
        )
        second_session, pairs, second_created = service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=second_payload, replace=True,
        )

        assert first_created is True
        assert second_created is False
        assert second_session["_id"] == first_session["_id"]
        assert len(list(db[ATTENDANCE_SESSIONS].find({}))) == 1
        assert len(list(db[ATTENDANCE_RECORDS].find({}))) == 1
        assert pairs[0][0]["status"] == "absent"

    def test_replace_bumps_updated_at(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]
        first_session, _, _ = service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=False,
        )

        second_session, _, _ = service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=True,
        )

        assert second_session["updated_at"] >= first_session["updated_at"]

    def test_replace_does_not_delete_existing_records_when_the_new_payload_is_invalid(self):
        """Because replace is delete-then-insert rather than a transaction,
        the old records must survive a payload that fails validation."""
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        valid_payload = [
            {"student_id": str(students[0]["_id"]), "status": "present"},
            {"student_id": str(students[1]["_id"]), "status": "present"},
        ]
        service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=valid_payload, replace=False,
        )
        broken_payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]

        with pytest.raises(ValidationError):
            service.save_session(
                db, klass["_id"], date, faculty_id, source="manual",
                records=broken_payload, replace=True,
            )

        assert len(list(db[ATTENDANCE_RECORDS].find({}))) == 2

    def test_ownership_is_enforced(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(ObjectId(), student_count=1)
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)
        date = _iso_date("2020-01-01")
        payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]

        with pytest.raises(ForbiddenError):
            service.save_session(
                db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=False,
            )

    def test_a_race_producing_a_duplicate_key_error_is_reported_as_a_conflict(self):
        """The unique index is the backstop for two concurrent saves; the
        loser must see the same ConflictError the ordinary pre-check
        path produces, not an unhandled driver exception.
        """
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        date = _iso_date("2020-01-01")
        payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]
        db = make_fake_attendance_db(classes=[klass], users=students, class_enrollments=enrollments)

        class _RacingSessions(FakeCollection):
            """Simulates a concurrent request winning the race: find_one
            reports no existing session (as the pre-check would have seen
            a moment earlier), but the unique index still refuses the
            insert.
            """

            def find_one(self, query):
                return None

        racing_sessions = _RacingSessions(unique_scope=("class_id", "date"))
        racing_sessions._docs.append(
            make_attendance_session(klass["_id"], date=date, taken_by=faculty_id)
        )
        db[ATTENDANCE_SESSIONS] = racing_sessions

        with pytest.raises(ConflictError):
            service.save_session(
                db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=False,
            )


# --- _session_counts (08-faculty-attendance-history.md) ----------------------


class TestSessionCounts:
    def test_returns_empty_dict_for_no_session_ids(self):
        db = make_fake_attendance_db()

        assert service._session_counts(db, []) == {}

    def test_totals_and_present_counts_are_grouped_per_session(self):
        klass = make_class(ObjectId(), faculty_id=ObjectId())
        students = [
            make_user(email=f"s{i}-{ObjectId()}@college.test", role="student") for i in range(3)
        ]
        session_a, records_a = make_session_with_records(
            klass["_id"], students, statuses=["present", "present", "absent"],
        )
        session_b, records_b = make_session_with_records(
            klass["_id"], students[:2], statuses=["absent", "absent"],
        )
        db = make_fake_attendance_db(
            classes=[klass], users=students,
            attendance_sessions=[session_a, session_b],
            attendance_records=records_a + records_b,
        )

        counts = service._session_counts(db, [session_a["_id"], session_b["_id"]])

        assert counts[session_a["_id"]] == {"total": 3, "present": 2}
        assert counts[session_b["_id"]] == {"total": 2, "present": 0}

    def test_a_session_id_with_no_records_is_absent_from_the_result(self):
        db = make_fake_attendance_db()
        lonely_id = ObjectId()

        counts = service._session_counts(db, [lonely_id])

        assert lonely_id not in counts


# --- list_sessions (08-faculty-attendance-history.md) ------------------------


class TestListSessions:
    def test_ownership_is_enforced_for_someone_elses_class(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=ObjectId())
        db = make_fake_attendance_db(classes=[klass])

        with pytest.raises(ForbiddenError):
            service.list_sessions(
                db, klass["_id"], faculty_id, date_from=None, date_to=None, limit=50, skip=0,
            )

    def test_nonexistent_class_raises_not_found(self):
        db = make_fake_attendance_db()

        with pytest.raises(NotFoundError):
            service.list_sessions(
                db, ObjectId(), ObjectId(), date_from=None, date_to=None, limit=50, skip=0,
            )

    def test_an_owned_class_with_no_sessions_returns_empty_list_and_zero_total(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=faculty_id)
        db = make_fake_attendance_db(classes=[klass])

        entries, total = service.list_sessions(
            db, klass["_id"], faculty_id, date_from=None, date_to=None, limit=50, skip=0,
        )

        assert entries == []
        assert total == 0

    def test_sessions_are_returned_newest_first(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        oldest, _ = make_session_with_records(klass["_id"], students, date=_iso_date("2020-01-01"))
        middle, _ = make_session_with_records(klass["_id"], students, date=_iso_date("2020-01-15"))
        newest, _ = make_session_with_records(klass["_id"], students, date=_iso_date("2020-01-31"))
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[middle, oldest, newest],
        )

        entries, total = service.list_sessions(
            db, klass["_id"], faculty_id, date_from=None, date_to=None, limit=50, skip=0,
        )

        assert [entry[0]["_id"] for entry in entries] == [newest["_id"], middle["_id"], oldest["_id"]]
        assert total == 3

    def test_scoped_to_the_requested_class_only(self):
        faculty_id = ObjectId()
        klass_a, students_a, _ = build_owned_class_with_roster(faculty_id, student_count=1)
        klass_b, students_b, _ = build_owned_class_with_roster(faculty_id, student_count=1)
        session_a, _ = make_session_with_records(klass_a["_id"], students_a)
        session_b, _ = make_session_with_records(klass_b["_id"], students_b)
        db = make_fake_attendance_db(
            classes=[klass_a, klass_b], users=students_a + students_b,
            attendance_sessions=[session_a, session_b],
        )

        entries, total = service.list_sessions(
            db, klass_a["_id"], faculty_id, date_from=None, date_to=None, limit=50, skip=0,
        )

        assert [entry[0]["_id"] for entry in entries] == [session_a["_id"]]
        assert total == 1

    def test_from_and_to_filter_inclusively(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        jan1, _ = make_session_with_records(klass["_id"], students, date=_iso_date("2020-01-01"))
        jan15, _ = make_session_with_records(klass["_id"], students, date=_iso_date("2020-01-15"))
        jan31, _ = make_session_with_records(klass["_id"], students, date=_iso_date("2020-01-31"))
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[jan1, jan15, jan31],
        )

        entries, total = service.list_sessions(
            db, klass["_id"], faculty_id,
            date_from=_iso_date("2020-01-01"), date_to=_iso_date("2020-01-15"),
            limit=50, skip=0,
        )

        assert {entry[0]["_id"] for entry in entries} == {jan1["_id"], jan15["_id"]}
        assert total == 2

    def test_limit_and_skip_page_without_duplicating_or_dropping_and_total_stays_unpaged(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        sessions = [
            make_session_with_records(klass["_id"], students, date=_iso_date(f"2020-01-{day:02d}"))[0]
            for day in (1, 10, 20)
        ]
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=sessions,
        )

        page1, total1 = service.list_sessions(
            db, klass["_id"], faculty_id, date_from=None, date_to=None, limit=1, skip=0,
        )
        page2, total2 = service.list_sessions(
            db, klass["_id"], faculty_id, date_from=None, date_to=None, limit=1, skip=1,
        )
        page3, total3 = service.list_sessions(
            db, klass["_id"], faculty_id, date_from=None, date_to=None, limit=1, skip=2,
        )

        newest_first = [s["_id"] for s in sorted(sessions, key=lambda s: s["date"], reverse=True)]
        seen_ids = [entry[0]["_id"] for page in (page1, page2, page3) for entry in page]
        assert seen_ids == newest_first
        assert total1 == total2 == total3 == 3

    def test_counts_come_from_exactly_one_aggregation_call_regardless_of_page_size(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        sessions, records = [], []
        for day in range(1, 6):
            session, session_records = make_session_with_records(
                klass["_id"], students, date=_iso_date(f"2020-01-0{day}"),
            )
            sessions.append(session)
            records.extend(session_records)

        class _CountingRecords(FakeCollection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.aggregate_calls = 0

            def aggregate(self, pipeline):
                self.aggregate_calls += 1
                return super().aggregate(pipeline)

        counting_records = _CountingRecords(records)
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=sessions,
        )
        db[ATTENDANCE_RECORDS] = counting_records

        entries, _ = service.list_sessions(
            db, klass["_id"], faculty_id, date_from=None, date_to=None, limit=50, skip=0,
        )

        assert len(entries) == 5
        assert counting_records.aggregate_calls == 1

    def test_a_session_with_no_records_yet_reports_empty_counts_not_an_error(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=faculty_id)
        session = make_attendance_session(klass["_id"], date=_iso_date("2020-01-01"), taken_by=faculty_id)
        db = make_fake_attendance_db(classes=[klass], attendance_sessions=[session])

        entries, total = service.list_sessions(
            db, klass["_id"], faculty_id, date_from=None, date_to=None, limit=50, skip=0,
        )

        assert entries[0][1] == {}
        assert total == 1


# --- get_session_by_id (08-faculty-attendance-history.md) --------------------


class TestGetSessionById:
    def test_nonexistent_session_raises_not_found(self):
        db = make_fake_attendance_db()

        with pytest.raises(NotFoundError):
            service.get_session_by_id(db, ObjectId(), ObjectId())

    def test_a_session_under_someone_elses_class_raises_forbidden(self):
        faculty_id = ObjectId()
        klass = make_class(ObjectId(), faculty_id=ObjectId())
        session = make_attendance_session(klass["_id"], taken_by=ObjectId())
        db = make_fake_attendance_db(classes=[klass], attendance_sessions=[session])

        with pytest.raises(ForbiddenError):
            service.get_session_by_id(db, session["_id"], faculty_id)

    def test_returns_the_session_with_every_stored_record_absences_explicit(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        session, records = make_session_with_records(
            klass["_id"], students, statuses=["present", "absent"],
        )
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )

        result_session, pairs = service.get_session_by_id(db, session["_id"], faculty_id)

        assert result_session["_id"] == session["_id"]
        statuses = {student["_id"]: record["status"] for record, student in pairs}
        assert statuses == {students[0]["_id"]: "present", students[1]["_id"]: "absent"}


# --- update_session_records (08-faculty-attendance-history.md) ---------------


class TestUpdateSessionRecords:
    def test_nonexistent_session_raises_not_found(self):
        db = make_fake_attendance_db()

        with pytest.raises(NotFoundError):
            service.update_session_records(db, ObjectId(), ObjectId(), [])

    def test_a_session_under_someone_elses_class_raises_forbidden(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(ObjectId(), student_count=1)
        session, records = make_session_with_records(klass["_id"], students)
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        payload = [
            {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"}
        ]

        with pytest.raises(ForbiddenError):
            service.update_session_records(db, session["_id"], faculty_id, payload)

    def test_flips_a_students_status(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        payload = [{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}]

        _, pairs = service.update_session_records(db, session["_id"], faculty_id, payload)

        assert pairs[0][0]["status"] == "absent"
        assert db[ATTENDANCE_RECORDS].find_one({"student_id": students[0]["_id"]})["status"] == "absent"

    def test_class_id_date_source_created_at_and_taken_by_are_unchanged(self):
        faculty_id = ObjectId()
        original_taken_by = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(
            klass["_id"], students, source="photo", taken_by=original_taken_by,
            date=_iso_date("2020-01-01"),
        )
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        payload = [{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}]

        updated_session, _ = service.update_session_records(db, session["_id"], faculty_id, payload)

        assert updated_session["class_id"] == klass["_id"]
        assert updated_session["date"] == session["date"]
        assert updated_session["source"] == "photo"
        assert updated_session["created_at"] == session["created_at"]
        assert updated_session["taken_by"] == original_taken_by

    def test_updated_at_and_updated_by_are_written_and_updated_by_matches_the_caller(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students)
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        payload = [{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}]

        service.update_session_records(db, session["_id"], faculty_id, payload)

        stored_session = db[ATTENDANCE_SESSIONS].find_one({"_id": session["_id"]})
        assert stored_session["updated_by"] == faculty_id
        assert stored_session["updated_at"] >= session["updated_at"]

    def test_updated_by_comes_from_the_argument_never_from_the_payload(self):
        faculty_id = ObjectId()
        spoofed_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students)
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        payload = [
            {
                "student_id": str(students[0]["_id"]), "status": "absent",
                "marked_by": "faculty", "updated_by": str(spoofed_id),
            }
        ]

        updated_session, _ = service.update_session_records(db, session["_id"], faculty_id, payload)

        assert updated_session["updated_by"] == faculty_id
        assert updated_session["updated_by"] != spoofed_id

    def test_an_unchanged_records_marked_by_is_preserved_including_recognition(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        session, records = make_session_with_records(
            klass["_id"], students, statuses=["present", "present"],
            marked_by=["recognition", "recognition"],
        )
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        payload = [
            # Untouched: resubmitted exactly as stored.
            {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
            # Changed: status flipped, so the client sends "faculty".
            {"student_id": str(students[1]["_id"]), "status": "absent", "marked_by": "faculty"},
        ]

        service.update_session_records(db, session["_id"], faculty_id, payload)

        stored = {r["student_id"]: r for r in db[ATTENDANCE_RECORDS].find({})}
        assert stored[students[0]["_id"]]["marked_by"] == "recognition"
        assert stored[students[0]["_id"]]["status"] == "present"
        assert stored[students[1]["_id"]]["marked_by"] == "faculty"
        assert stored[students[1]["_id"]]["status"] == "absent"

    def test_missing_a_session_student_returns_400_and_writes_nothing(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present", "present"])
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        incomplete_payload = [
            {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
        ]

        with pytest.raises(ValidationError):
            service.update_session_records(db, session["_id"], faculty_id, incomplete_payload)

        stored = list(db[ATTENDANCE_RECORDS].find({}))
        assert all(r["status"] == "present" for r in stored)
        assert len(stored) == 2

    def test_a_duplicate_student_in_the_payload_returns_400_and_writes_nothing(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        duplicate_payload = [
            {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
            {"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"},
        ]

        with pytest.raises(ValidationError):
            service.update_session_records(db, session["_id"], faculty_id, duplicate_payload)

        assert db[ATTENDANCE_RECORDS].find_one({"student_id": students[0]["_id"]})["status"] == "present"

    def test_a_student_not_in_the_session_returns_400_and_writes_nothing(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        stranger_payload = [
            {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
            {"student_id": str(ObjectId()), "status": "present", "marked_by": "faculty"},
        ]

        with pytest.raises(ValidationError):
            service.update_session_records(db, session["_id"], faculty_id, stranger_payload)

        assert len(list(db[ATTENDANCE_RECORDS].find({}))) == 1

    def test_an_invalid_status_returns_400_and_writes_nothing(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        payload = [{"student_id": str(students[0]["_id"]), "status": "late", "marked_by": "faculty"}]

        with pytest.raises(ValidationError):
            service.update_session_records(db, session["_id"], faculty_id, payload)

        assert db[ATTENDANCE_RECORDS].find_one({"student_id": students[0]["_id"]})["status"] == "present"

    def test_an_invalid_marked_by_returns_400_and_writes_nothing(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        payload = [{"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "robot"}]

        with pytest.raises(ValidationError):
            service.update_session_records(db, session["_id"], faculty_id, payload)

        assert db[ATTENDANCE_RECORDS].find_one({"student_id": students[0]["_id"]})["marked_by"] == "recognition"

    def test_a_new_enrollment_since_the_lecture_does_not_break_an_otherwise_valid_edit(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        new_student = make_user(email=f"new-{ObjectId()}@college.test", role="student")
        new_enrollment = make_class_enrollment(klass["_id"], new_student["_id"])
        db = make_fake_attendance_db(
            classes=[klass], users=students + [new_student],
            class_enrollments=enrollments + [new_enrollment],
            attendance_sessions=[session], attendance_records=records,
        )
        payload = [{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}]

        _, pairs = service.update_session_records(db, session["_id"], faculty_id, payload)

        assert {student["_id"] for _, student in pairs} == {students[0]["_id"]}

    def test_a_new_enrollment_since_the_lecture_is_rejected_if_included_in_the_edit(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        new_student = make_user(email=f"new-{ObjectId()}@college.test", role="student")
        new_enrollment = make_class_enrollment(klass["_id"], new_student["_id"])
        db = make_fake_attendance_db(
            classes=[klass], users=students + [new_student],
            class_enrollments=enrollments + [new_enrollment],
            attendance_sessions=[session], attendance_records=records,
        )
        payload = [
            {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
            {"student_id": str(new_student["_id"]), "status": "present", "marked_by": "faculty"},
        ]

        with pytest.raises(ValidationError):
            service.update_session_records(db, session["_id"], faculty_id, payload)

    def test_a_student_unenrolled_since_the_lecture_must_still_be_covered_by_the_edit(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        session, records = make_session_with_records(
            klass["_id"], students, statuses=["present", "present"],
        )
        # students[1] has since been unenrolled, but was part of the
        # session, so a valid edit must still cover them.
        db = make_fake_attendance_db(
            classes=[klass], users=students,
            class_enrollments=[enrollments[0]],
            attendance_sessions=[session], attendance_records=records,
        )
        payload_missing_unenrolled = [
            {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
        ]

        with pytest.raises(ValidationError):
            service.update_session_records(db, session["_id"], faculty_id, payload_missing_unenrolled)

        payload_including_unenrolled = [
            {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
            {"student_id": str(students[1]["_id"]), "status": "absent", "marked_by": "faculty"},
        ]

        _, pairs = service.update_session_records(
            db, session["_id"], faculty_id, payload_including_unenrolled,
        )

        assert {student["_id"] for _, student in pairs} == {students[0]["_id"], students[1]["_id"]}


# --- delete_session (08-faculty-attendance-history.md) -----------------------


class TestDeleteSession:
    def test_nonexistent_session_raises_not_found(self):
        db = make_fake_attendance_db()

        with pytest.raises(NotFoundError):
            service.delete_session(db, ObjectId(), ObjectId())

    def test_a_session_under_someone_elses_class_raises_forbidden(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(ObjectId(), student_count=1)
        session, records = make_session_with_records(klass["_id"], students)
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )

        with pytest.raises(ForbiddenError):
            service.delete_session(db, session["_id"], faculty_id)

    def test_removes_the_session_and_all_of_its_records(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        session, records = make_session_with_records(klass["_id"], students)
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )

        deleted_count = service.delete_session(db, session["_id"], faculty_id)

        assert deleted_count == 2
        assert db[ATTENDANCE_SESSIONS].find_one({"_id": session["_id"]}) is None
        assert list(db[ATTENDANCE_RECORDS].find({"session_id": session["_id"]})) == []

    def test_deleting_one_session_leaves_other_sessions_of_the_same_class_untouched(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session_a, records_a = make_session_with_records(
            klass["_id"], students, date=_iso_date("2020-01-01"),
        )
        session_b, records_b = make_session_with_records(
            klass["_id"], students, date=_iso_date("2020-01-02"),
        )
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session_a, session_b], attendance_records=records_a + records_b,
        )

        service.delete_session(db, session_a["_id"], faculty_id)

        assert db[ATTENDANCE_SESSIONS].find_one({"_id": session_b["_id"]}) is not None
        assert len(list(db[ATTENDANCE_RECORDS].find({"session_id": session_b["_id"]}))) == 1

    def test_after_a_delete_the_same_class_and_date_can_be_recorded_again(self):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        date = _iso_date("2020-01-01")
        session, records = make_session_with_records(klass["_id"], students, date=date)
        db = make_fake_attendance_db(
            classes=[klass], users=students, class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )

        service.delete_session(db, session["_id"], faculty_id)

        payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]
        new_session, _, created = service.save_session(
            db, klass["_id"], date, faculty_id, source="manual", records=payload, replace=False,
        )

        assert created is True
        assert new_session["_id"] != session["_id"]
