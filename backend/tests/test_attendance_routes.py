"""Tests for backend/routes/attendance.py (the `attendance_bp` blueprint).

Spec contract under test (07-attendance-capture.md, "APIs" + "Database
changes" + "Rules for implementation" + "Definition of done"):
  - Four endpoints under `/api`, every one **faculty**-only, and every
    one naming a class additionally requires `classes.faculty_id` to
    equal the caller: `GET /api/classes/assigned`,
    `POST /api/attendance/recognize`, `GET /api/attendance/session`,
    `POST /api/attendance`.
  - `POST /api/attendance/recognize` writes nothing, ever, and requires
    exactly one of an `image`/`video` multipart part.
  - `POST /api/attendance` creates a session (`201`) or replaces one
    (`200`), refuses a duplicate without `replace` (`409`), rejects a
    `records` payload that does not match the roster exactly, and takes
    `taken_by` from the JWT identity, never the body.
  - Status codes: `200`/`201`/`400`/`401`/`403`/`404`/`409`/`413`/`503`,
    all on the `{"error": ...}` JSON contract.
  - CSRF still applies to the multipart recognise route.
  - The recognition library or OpenCV being unavailable is a clean `503`,
    never a crash, and does not take down unrelated routes.
  - No response anywhere contains an `encoding` vector.

Also covers 08-faculty-attendance-history.md's four additions to this
blueprint ("APIs" + "Rules for implementation" + "Definition of done"):
  - `GET /api/attendance/sessions` -- newest-first, `records`-free rows,
    `total`/`limit`/`skip` in the envelope, `from`/`to`/`limit`/`skip`
    validated to `400`, and an owned class with no sessions answers
    `200` with `[]`/`total: 0` rather than `404`.
  - `GET /api/attendance/sessions/<id>` -- the full session + records,
    `404` for an unknown id, `403` for someone else's session.
  - `PUT /api/attendance/sessions/<id>` -- flips statuses only;
    `class_id`/`date`/`source`/`created_at`/`taken_by` never change;
    `updated_by` is the caller, never the body; a changed row is stored
    `marked_by: "faculty"` and an unchanged row keeps its stored value;
    a malformed payload is `400` and changes nothing.
  - `DELETE /api/attendance/sessions/<id>` -- `{"deleted": true}`, `200`,
    cascades to the session's records, and the same class+date can be
    saved again afterwards without a `409`.
  - Every one of the four is faculty-owner-only (`401`/`403`/`404`,
    never `409`, never `503`), and `PUT`/`DELETE` enforce CSRF the same
    way `POST /api/attendance` already does.

Also covers 09-student-attendance-dashboard.md's two additions to this
blueprint ("APIs" + "Rules for implementation" + "Definition of done"):
  - `GET /api/attendance/me` -- one entry per enrolled class labelled with
    its hierarchy names, plus an `overall` roll-up whose counts equal the
    sum of the per-class counts; empty enrollment is `200` with an empty
    `classes` list and a zeroed `overall`; an unattended class reads
    `total_count: 0` / `percentage: null`, never `0`; a stray record for a
    class the student is not enrolled in, or another student's record in a
    shared class, never affects a response.
  - `GET /api/attendance/me/sessions?class_id=...` -- the student's own
    record newest-first with a `monthly` trend oldest-first,
    `present_count + absent_count == total_count` everywhere it is
    reported, an inclusive `from`/`to` filter narrowing `sessions` and
    `monthly` together, `403` for an unenrolled class, `404` for a
    nonexistent one, and `400` for a missing/malformed `class_id` or a
    malformed/backwards date range.
  - Both are `student`-only (`role_required`) and identify the caller
    solely from the JWT: a `student_id` sent in the query string, path, or
    a JSON body is never read, so it cannot change either response, and no
    response on either path carries another student's id/name/email/
    status, a roster, a class average, `marked_by`, `taken_by`,
    `updated_by`, `source`, or a session id.

Also covers 11-student-attendance-threshold.md's fields on those same two
endpoints ("APIs" + "Backend" + "Definition of done"):
  - `GET /api/attendance/me` gains a top-level `threshold` and each
    `classes[]` entry gains `meets_threshold` + `lectures_to_reach`;
    `GET /api/attendance/me/sessions` gains the same three fields for the
    one class it describes.
  - `overall` and every `monthly` bucket carry none of the three fields --
    the bar is applied per class only, never to an average or a month.
  - A class with `percentage: null` reports `meets_threshold: null` and
    `lectures_to_reach: null`, never `false`/`0`; a class exactly at the
    bar reports `meets_threshold: true`.
  - With `Config.LOW_ATTENDANCE_THRESHOLD` misconfigured, both endpoints
    still answer `200` with the same attendance figures as before,
    `threshold: null`, and `meets_threshold`/`lectures_to_reach` `null` on
    every class -- a bad environment variable degrades the dashboard, it
    does not break it.
  - No status code, role, parameter, or error contract changes; the
    `student_id`-is-never-read coverage above is unmodified by this
    feature and is not repeated here.

`routes.auth.get_db` and `routes.attendance.get_db` are monkeypatched to
the same in-memory fake db (attendance_test_helpers.py) so a real
`/api/auth/login` call mints genuine JWT + CSRF cookies while the
attendance endpoints read/write fake collections -- no live MongoDB is
required. `recognition.encoder`/`recognition.frames` are monkeypatched
throughout, so nothing here depends on face_recognition/dlib/OpenCV being
installed, and no real image/video/biometric data is used anywhere --
every "capture" is a short block of synthetic bytes.
"""

import io
from datetime import datetime, timedelta, timezone

import pytest
from attendance_test_helpers import (
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

from config import Config
from database.schema import ATTENDANCE_RECORDS, ATTENDANCE_SESSIONS
from recognition.errors import ImageDecodeError, VideoDecodeError
from recognition.validators import MAX_IMAGE_BYTES, MAX_REQUEST_BYTES, MAX_VIDEO_BYTES

FAKE_PASSWORD = "a-fake-test-password-1"

# Plain synthetic bytes -- never a real photograph or recording, and
# decoding is always monkeypatched, so the content itself is irrelevant.
FAKE_IMAGE_BYTES = b"synthetic-classroom-image-bytes-not-a-real-photograph"
FAKE_VIDEO_BYTES = b"synthetic-classroom-video-bytes-not-a-real-recording"

VALID_ENCODING = synthetic_encoding(0.0)


# --- db/login plumbing -------------------------------------------------------


def _patch_db(monkeypatch, fake_db):
    monkeypatch.setattr("routes.auth.get_db", lambda: fake_db)
    monkeypatch.setattr("routes.attendance.get_db", lambda: fake_db)
    return fake_db


def _login(client, email, password=FAKE_PASSWORD):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, f"login setup failed: {response.get_json()}"
    return response


def _csrf_headers(client):
    cookie = client.get_cookie("csrf_access_token", path="/")
    assert cookie is not None, "csrf_access_token cookie was not set after login"
    return {"X-CSRF-TOKEN": cookie.value}


def _login_as(monkeypatch, client, role, *, email=None, extra_users=None, **collections):
    """A logged-in user of `role` with a randomly generated id -- for
    tests where the acting user does *not* need to be the pre-known owner
    of a class (role checks, "somebody else's class", unauthenticated).
    """
    email = email or f"{role}-{ObjectId()}@college.test"
    user = make_user(email=email, password=FAKE_PASSWORD, role=role)
    fake_db = make_fake_attendance_db(users=[user] + list(extra_users or []), **collections)
    _patch_db(monkeypatch, fake_db)
    _login(client, email, FAKE_PASSWORD)
    return fake_db, _csrf_headers(client)


def _login_as_owner(
    monkeypatch, client, klass, *, students=None, enrollments=None, extra_classes=None, **collections
):
    """Logs in as the faculty member `klass["faculty_id"]` already names --
    for the happy-path / roster-shaped tests that need real ownership.

    `klass` (and its roster) must be built first, e.g. via
    `build_owned_class_with_roster(ObjectId(), ...)`, so its `faculty_id`
    is known before the login user is constructed. `extra_classes` adds
    further, unrelated classes to the fake db (e.g. another faculty
    member's) without colliding with the `classes` collection this helper
    already builds from `klass`.
    """
    email = f"owner-{klass['faculty_id']}@college.test"
    faculty = make_user(email=email, password=FAKE_PASSWORD, role="faculty")
    faculty["_id"] = klass["faculty_id"]
    fake_db = make_fake_attendance_db(
        users=[faculty] + list(students or []),
        classes=[klass] + list(extra_classes or []),
        class_enrollments=list(enrollments or []),
        **collections,
    )
    _patch_db(monkeypatch, fake_db)
    _login(client, email, FAKE_PASSWORD)
    return fake_db, _csrf_headers(client)


def _acting_user_id(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    return response.get_json()["id"]


# --- recognition stubbing -----------------------------------------------------


def _stub_recognition(
    monkeypatch,
    *,
    recognition_available=True,
    video_available=True,
    encode_faces=None,
    closest_match=None,
    extract_frames=None,
):
    """Replaces the CV/video libraries entirely so no test depends on
    face_recognition/dlib/numpy/OpenCV being installed.
    """
    monkeypatch.setattr("recognition.encoder.is_available", lambda: recognition_available)
    monkeypatch.setattr(
        "recognition.encoder.encode_faces",
        encode_faces or (lambda image_bytes: [list(VALID_ENCODING)]),
    )
    monkeypatch.setattr(
        "recognition.encoder.closest_match", closest_match or fake_closest_match
    )
    monkeypatch.setattr("recognition.frames.is_available", lambda: video_available)
    if extract_frames is not None:
        monkeypatch.setattr("recognition.frames.extract_frames", extract_frames)


# --- request builders ----------------------------------------------------------


def _image_part(data=FAKE_IMAGE_BYTES, filename="classroom.jpg", content_type="image/jpeg"):
    return (io.BytesIO(data), filename, content_type)


def _video_part(data=FAKE_VIDEO_BYTES, filename="classroom.mp4", content_type="video/mp4"):
    return (io.BytesIO(data), filename, content_type)


def _recognize(
    client,
    class_id,
    headers,
    *,
    image=True,
    video=False,
    image_bytes=FAKE_IMAGE_BYTES,
    video_bytes=FAKE_VIDEO_BYTES,
    image_content_type="image/jpeg",
    video_content_type="video/mp4",
    omit_class_id=False,
    extra_fields=None,
):
    form = {}
    if not omit_class_id:
        form["class_id"] = str(class_id)
    if image:
        form["image"] = _image_part(image_bytes, content_type=image_content_type)
    if video:
        form["video"] = _video_part(video_bytes, content_type=video_content_type)
    if extra_fields:
        form.update(extra_fields)
    return client.post("/api/attendance/recognize", data=form, headers=headers)


def _get_session(client, class_id, date, headers=None):
    return client.get(
        f"/api/attendance/session?class_id={class_id}&date={date}", headers=headers or {}
    )


def _save(client, headers, *, class_id, date, source="photo", records=None, replace=None, extra=None):
    body = {"class_id": str(class_id), "date": date, "source": source}
    if records is not None:
        body["records"] = records
    if replace is not None:
        body["replace"] = replace
    if extra:
        body.update(extra)
    return client.post("/api/attendance", json=body, headers=headers)


def _assert_no_encoding_leak(payload):
    if isinstance(payload, dict):
        assert "encoding" not in payload, f"encoding leaked in {payload}"
        for value in payload.values():
            _assert_no_encoding_leak(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_encoding_leak(item)


def _all_four_endpoint_requests(client, headers):
    """One representative request per endpoint, used to prove role
    enforcement / authentication is checked before anything else. Dummy
    ids are fine: role_required short-circuits before any id is resolved.
    """
    class_id = str(ObjectId())
    date = "2020-01-01"
    return [
        client.get("/api/classes/assigned", headers=headers),
        client.post(
            "/api/attendance/recognize",
            data={"class_id": class_id, "image": _image_part()},
            headers=headers,
        ),
        client.get(f"/api/attendance/session?class_id={class_id}&date={date}", headers=headers),
        client.post(
            "/api/attendance",
            json={"class_id": class_id, "date": date, "source": "manual", "records": []},
            headers=headers,
        ),
    ]


# --- history request builders (08-faculty-attendance-history.md) --------------


def _list_sessions(client, class_id, headers=None, *, query=""):
    suffix = f"&{query}" if query else ""
    return client.get(
        f"/api/attendance/sessions?class_id={class_id}{suffix}", headers=headers or {}
    )


def _get_session_by_id(client, session_id, headers=None):
    return client.get(f"/api/attendance/sessions/{session_id}", headers=headers or {})


def _update_session(client, session_id, headers=None, *, records=None):
    body = {"records": records if records is not None else []}
    return client.put(
        f"/api/attendance/sessions/{session_id}", json=body, headers=headers or {}
    )


def _delete_session_request(client, session_id, headers=None):
    return client.delete(f"/api/attendance/sessions/{session_id}", headers=headers or {})


def _all_history_endpoint_requests(client, headers):
    """One representative request per 08-faculty-attendance-history.md
    endpoint -- the plural/id-addressed history routes, distinct from the
    singular capture-flow routes `_all_four_endpoint_requests` covers.
    Dummy ids are fine: role_required short-circuits first.
    """
    class_id = str(ObjectId())
    session_id = str(ObjectId())
    return [
        _list_sessions(client, class_id, headers),
        _get_session_by_id(client, session_id, headers),
        _update_session(client, session_id, headers, records=[]),
        _delete_session_request(client, session_id, headers),
    ]


# --- GET /api/classes/assigned -------------------------------------------------


class TestListAssignedClassesRoute:
    def test_returns_only_the_callers_classes_with_the_documented_shape(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(monkeypatch, client, klass, students=students, enrollments=enrollments)

            response = client.get("/api/classes/assigned", headers=headers)

        assert response.status_code == 200
        body = response.get_json()
        assert "classes" in body
        entry = body["classes"][0]
        assert set(entry.keys()) == {
            "id", "name", "course", "semester", "department", "institute", "student_count",
        }
        assert entry["id"] == str(klass["_id"])
        assert entry["student_count"] == 2

    def test_returns_an_empty_list_not_an_error_for_a_faculty_member_with_no_classes(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = client.get("/api/classes/assigned", headers=headers)

        assert response.status_code == 200
        assert response.get_json()["classes"] == []

    def test_does_not_include_another_faculty_members_classes(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        other_class = make_class(ObjectId(), faculty_id=ObjectId(), name="Not Mine")
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                extra_classes=[other_class],
            )

            response = client.get("/api/classes/assigned", headers=headers)

        ids = {entry["id"] for entry in response.get_json()["classes"]}
        assert ids == {str(klass["_id"])}


# --- POST /api/attendance/recognize --------------------------------------------


class TestRecognizeRoute:
    def test_photo_returns_a_proposal_partitioning_the_whole_roster(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        encodings = [make_face_encoding(students[0]["_id"], encoding=synthetic_encoding(0.0))]
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                face_encodings=encodings,
            )
            _stub_recognition(monkeypatch, encode_faces=lambda image_bytes: [synthetic_encoding(0.0)])

            response = _recognize(client, klass["_id"], headers)

        assert response.status_code == 200
        body = response.get_json()
        assert body["class_id"] == str(klass["_id"])
        assert body["source"] == "photo"
        assert body["frames_analyzed"] == 1
        all_ids = (
            {e["student"]["id"] for e in body["recognized"]}
            | {e["student"]["id"] for e in body["unrecognized"]}
            | {e["student"]["id"] for e in body["not_enrolled"]}
        )
        assert all_ids == {str(s["_id"]) for s in students}

    def test_writes_nothing_to_either_collection(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            fake_db, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch)

            _recognize(client, klass["_id"], headers)

        assert list(fake_db[ATTENDANCE_SESSIONS].find({})) == []
        assert list(fake_db[ATTENDANCE_RECORDS].find({})) == []

    def test_video_returns_frames_analyzed_greater_than_one(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(
                monkeypatch,
                extract_frames=lambda video_bytes, content_type=None: [b"f1", b"f2"],
                encode_faces=lambda image_bytes: [],
            )

            response = _recognize(client, klass["_id"], headers, image=False, video=True)

        assert response.status_code == 200
        body = response.get_json()
        assert body["source"] == "video"
        assert body["frames_analyzed"] == 2

    def test_no_response_anywhere_contains_an_encoding_vector(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        encodings = [make_face_encoding(students[0]["_id"], encoding=synthetic_encoding(0.0))]
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                face_encodings=encodings,
            )
            _stub_recognition(monkeypatch, encode_faces=lambda image_bytes: [synthetic_encoding(0.0)])

            response = _recognize(client, klass["_id"], headers)

        _assert_no_encoding_leak(response.get_json())

    def test_neither_image_nor_video_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch)

            response = _recognize(client, klass["_id"], headers, image=False, video=False)

        assert response.status_code == 400

    def test_both_image_and_video_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch)

            response = _recognize(client, klass["_id"], headers, image=True, video=True)

        assert response.status_code == 400

    def test_missing_class_id_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch)

            response = _recognize(client, klass["_id"], headers, omit_class_id=True)

        assert response.status_code == 400

    def test_malformed_class_id_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch)

            response = _recognize(client, "not-a-valid-id", headers)

        assert response.status_code == 400

    def test_nonexistent_class_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")
            _stub_recognition(monkeypatch)

            response = _recognize(client, ObjectId(), headers)

        assert response.status_code == 404

    def test_non_media_content_type_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch)

            response = _recognize(
                client, klass["_id"], headers,
                image_content_type="text/plain", image_bytes=b"not an image",
            )

        assert response.status_code == 400

    def test_corrupt_image_returns_400_not_500(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            def _raise(image_bytes):
                raise ImageDecodeError("The uploaded file could not be read as an image")

            _stub_recognition(monkeypatch, encode_faces=_raise)

            response = _recognize(client, klass["_id"], headers)

        assert response.status_code == 400

    def test_corrupt_video_returns_400_not_500(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            def _raise(video_bytes, content_type=None):
                raise VideoDecodeError("The uploaded file could not be read as a video")

            _stub_recognition(monkeypatch, extract_frames=_raise)

            response = _recognize(client, klass["_id"], headers, image=False, video=True)

        assert response.status_code == 400

    def test_image_over_the_per_file_limit_but_under_the_request_limit_returns_400(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        oversized = b"x" * (MAX_IMAGE_BYTES + 1024)
        assert len(oversized) < MAX_REQUEST_BYTES
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch)

            response = _recognize(client, klass["_id"], headers, image_bytes=oversized)

        assert response.status_code == 400

    def test_video_over_the_per_file_limit_but_under_the_request_limit_returns_400(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        oversized = b"x" * (MAX_VIDEO_BYTES + 1024)
        assert len(oversized) < MAX_REQUEST_BYTES
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch)

            response = _recognize(client, klass["_id"], headers, image=False, video=True, video_bytes=oversized)

        assert response.status_code == 400

    def test_upload_over_the_request_size_limit_returns_413_as_json(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        too_large = b"x" * (MAX_REQUEST_BYTES + 1024 * 200)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch)

            response = _recognize(client, klass["_id"], headers, video=True, image=False, video_bytes=too_large)

        assert response.status_code == 413
        body_text = response.get_data(as_text=True)
        assert "<html" not in body_text.lower()
        assert "error" in response.get_json()

    def test_recognition_unavailable_returns_503_and_health_still_responds(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch, recognition_available=False)

            response = _recognize(client, klass["_id"], headers)

        assert response.status_code == 503
        assert "error" in response.get_json()

        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})
        with app_instance.test_client() as client:
            health = client.get("/api/health")

        assert health.status_code == 200

    def test_video_unavailable_returns_503_while_photo_still_works(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch, video_available=False, encode_faces=lambda image_bytes: [])

            video_response = _recognize(client, klass["_id"], headers, image=False, video=True)
            photo_response = _recognize(client, klass["_id"], headers, image=True, video=False)

        assert video_response.status_code == 503
        assert photo_response.status_code == 200


# --- GET /api/attendance/session -----------------------------------------------


class TestGetSessionRoute:
    def test_returns_the_saved_session_with_records(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            fake_db, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            saved = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[{"student_id": str(students[0]["_id"]), "status": "present"}],
            )
            assert saved.status_code == 201

            response = _get_session(client, klass["_id"], "2020-01-01", headers)

        assert response.status_code == 200
        body = response.get_json()
        assert body["class_id"] == str(klass["_id"])
        assert body["present_count"] == 1
        assert body["absent_count"] == 0
        assert len(body["records"]) == 1

    def test_404_when_no_session_exists_for_the_date(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _get_session(client, klass["_id"], "2020-01-01", headers)

        assert response.status_code == 404

    def test_malformed_date_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _get_session(client, klass["_id"], "not-a-date", headers)

        assert response.status_code == 400

    def test_malformed_class_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = _get_session(client, "not-a-valid-id", "2020-01-01", headers)

        assert response.status_code == 400


# --- POST /api/attendance -------------------------------------------------------


class TestSaveSessionRoute:
    def test_creates_a_new_session_and_returns_201(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[
                    {"student_id": str(students[0]["_id"]), "status": "present"},
                    {"student_id": str(students[1]["_id"]), "status": "absent"},
                ],
            )

        assert response.status_code == 201
        body = response.get_json()
        assert body["present_count"] == 1
        assert body["absent_count"] == 1

    def test_taken_by_is_the_caller_not_a_value_smuggled_through_the_body(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        spoofed_id = str(ObjectId())
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            real_id = _acting_user_id(client)

            response = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[{"student_id": str(students[0]["_id"]), "status": "present"}],
                extra={"taken_by": spoofed_id},
            )

        assert response.status_code == 201
        assert response.get_json()["taken_by"] == real_id
        assert response.get_json()["taken_by"] != spoofed_id

    def test_absent_students_are_returned_explicitly(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[{"student_id": str(students[0]["_id"]), "status": "absent"}],
            )

        assert response.status_code == 201
        assert response.get_json()["records"][0]["status"] == "absent"

    def test_records_missing_a_roster_student_returns_400_and_writes_nothing(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        with app_instance.test_client() as client:
            fake_db, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[{"student_id": str(students[0]["_id"]), "status": "present"}],
            )

        assert response.status_code == 400
        assert list(fake_db[ATTENDANCE_SESSIONS].find({})) == []

    def test_records_with_a_duplicate_student_returns_400_and_writes_nothing(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            fake_db, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[
                    {"student_id": str(students[0]["_id"]), "status": "present"},
                    {"student_id": str(students[0]["_id"]), "status": "absent"},
                ],
            )

        assert response.status_code == 400
        assert list(fake_db[ATTENDANCE_SESSIONS].find({})) == []

    def test_records_naming_a_non_enrolled_student_returns_400_and_writes_nothing(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            fake_db, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[
                    {"student_id": str(students[0]["_id"]), "status": "present"},
                    {"student_id": str(ObjectId()), "status": "present"},
                ],
            )

        assert response.status_code == 400
        assert list(fake_db[ATTENDANCE_SESSIONS].find({})) == []

    def test_an_invalid_status_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[{"student_id": str(students[0]["_id"]), "status": "late"}],
            )

        assert response.status_code == 400

    def test_an_invalid_marked_by_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[
                    {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "robot"}
                ],
            )

        assert response.status_code == 400

    def test_a_future_date_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _save(
                client, headers, class_id=klass["_id"], date=tomorrow, source="manual",
                records=[{"student_id": str(students[0]["_id"]), "status": "present"}],
            )

        assert response.status_code == 400

    def test_a_malformed_date_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _save(
                client, headers, class_id=klass["_id"], date="not-a-date", source="manual",
                records=[{"student_id": str(students[0]["_id"]), "status": "present"}],
            )

        assert response.status_code == 400

    def test_a_malformed_class_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = client.post(
                "/api/attendance",
                json={
                    "class_id": "not-a-valid-id",
                    "date": "2020-01-01",
                    "source": "manual",
                    "records": [],
                },
                headers=headers,
            )

        assert response.status_code == 400

    def test_a_second_save_without_replace_returns_409(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            payload = [{"student_id": str(students[0]["_id"]), "status": "present"}]
            first = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual", records=payload,
            )
            assert first.status_code == 201

            second = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual", records=payload,
            )

        assert second.status_code == 409

    def test_replace_true_returns_200_and_replaces_the_records(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            fake_db, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[{"student_id": str(students[0]["_id"]), "status": "present"}],
            )

            response = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[{"student_id": str(students[0]["_id"]), "status": "absent"}],
                replace=True,
            )

        assert response.status_code == 200
        assert response.get_json()["records"][0]["status"] == "absent"
        assert len(list(fake_db[ATTENDANCE_SESSIONS].find({}))) == 1


# --- GET /api/attendance/sessions (08-faculty-attendance-history.md) ----------


class TestListSessionsRoute:
    def test_returns_sessions_newest_first_with_the_documented_row_shape(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        oldest, oldest_records = make_session_with_records(
            klass["_id"], students, statuses=["present", "absent"], date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        newest, newest_records = make_session_with_records(
            klass["_id"], students, statuses=["present", "present"], date=datetime(2020, 1, 15, tzinfo=timezone.utc),
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[oldest, newest],
                attendance_records=oldest_records + newest_records,
            )

            response = _list_sessions(client, klass["_id"], headers)

        assert response.status_code == 200
        body = response.get_json()
        assert body["total"] == 2
        assert body["limit"] == 50
        assert body["skip"] == 0
        assert [s["id"] for s in body["sessions"]] == [str(newest["_id"]), str(oldest["_id"])]
        entry = body["sessions"][0]
        assert set(entry.keys()) == {
            "id", "class_id", "date", "source", "taken_by", "created_at",
            "updated_at", "updated_by", "edited", "present_count",
            "absent_count", "total_count",
        }
        assert "records" not in entry
        assert entry["present_count"] == 2
        assert entry["absent_count"] == 0
        assert entry["total_count"] == 2

    def test_an_owned_class_with_no_sessions_returns_200_empty_list_not_404(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _list_sessions(client, klass["_id"], headers)

        assert response.status_code == 200
        body = response.get_json()
        assert body["sessions"] == []
        assert body["total"] == 0

    def test_from_and_to_filter_inclusively(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        jan1, jan1_records = make_session_with_records(
            klass["_id"], students, date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        jan15, jan15_records = make_session_with_records(
            klass["_id"], students, date=datetime(2020, 1, 15, tzinfo=timezone.utc),
        )
        jan31, jan31_records = make_session_with_records(
            klass["_id"], students, date=datetime(2020, 1, 31, tzinfo=timezone.utc),
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[jan1, jan15, jan31],
                attendance_records=jan1_records + jan15_records + jan31_records,
            )

            response = _list_sessions(
                client, klass["_id"], headers, query="from=2020-01-01&to=2020-01-15",
            )

        assert response.status_code == 200
        body = response.get_json()
        assert {s["id"] for s in body["sessions"]} == {str(jan1["_id"]), str(jan15["_id"])}
        assert body["total"] == 2

    def test_a_to_earlier_than_from_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _list_sessions(
                client, klass["_id"], headers, query="from=2020-02-01&to=2020-01-01",
            )

        assert response.status_code == 400

    def test_a_malformed_from_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _list_sessions(client, klass["_id"], headers, query="from=not-a-date")

        assert response.status_code == 400

    def test_a_negative_skip_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _list_sessions(client, klass["_id"], headers, query="skip=-1")

        assert response.status_code == 400

    def test_a_non_integer_limit_returns_400(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _list_sessions(client, klass["_id"], headers, query="limit=not-a-number")

        assert response.status_code == 400

    def test_limit_above_the_maximum_is_clamped_to_200_not_rejected(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )

            response = _list_sessions(client, klass["_id"], headers, query="limit=5000")

        assert response.status_code == 200
        assert response.get_json()["limit"] == 200

    def test_skip_pages_without_duplicating_or_dropping_and_total_is_unpaged(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        sessions_and_records = [
            make_session_with_records(
                klass["_id"], students, date=datetime(2020, 1, day, tzinfo=timezone.utc),
            )
            for day in (1, 10, 20)
        ]
        sessions = [pair[0] for pair in sessions_and_records]
        records = [record for pair in sessions_and_records for record in pair[1]]
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=sessions, attendance_records=records,
            )

            page1 = _list_sessions(client, klass["_id"], headers, query="limit=1&skip=0").get_json()
            page2 = _list_sessions(client, klass["_id"], headers, query="limit=1&skip=1").get_json()
            page3 = _list_sessions(client, klass["_id"], headers, query="limit=1&skip=2").get_json()

        newest_first = [str(s["_id"]) for s in sorted(sessions, key=lambda s: s["date"], reverse=True)]
        seen = [s["id"] for page in (page1, page2, page3) for s in page["sessions"]]
        assert seen == newest_first
        assert page1["total"] == page2["total"] == page3["total"] == 3

    def test_missing_class_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = client.get("/api/attendance/sessions", headers=headers)

        assert response.status_code == 400

    def test_malformed_class_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = _list_sessions(client, "not-a-valid-id", headers)

        assert response.status_code == 400

    def test_nonexistent_class_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = _list_sessions(client, ObjectId(), headers)

        assert response.status_code == 404

    def test_a_class_assigned_to_someone_else_returns_403(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), faculty_id=ObjectId())
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty", classes=[klass])

            response = _list_sessions(client, klass["_id"], headers)

        assert response.status_code == 403


# --- GET /api/attendance/sessions/<id> (08-faculty-attendance-history.md) -----


class TestGetSessionByIdRoute:
    def test_returns_the_full_session_with_records_and_absences_explicit(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        session, records = make_session_with_records(
            klass["_id"], students, statuses=["present", "absent"],
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _get_session_by_id(client, session["_id"], headers)

        assert response.status_code == 200
        body = response.get_json()
        assert body["id"] == str(session["_id"])
        assert len(body["records"]) == 2
        statuses = {r["status"] for r in body["records"]}
        assert statuses == {"present", "absent"}
        assert body["edited"] is False
        assert body["updated_by"] is None

    def test_nonexistent_session_id_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = _get_session_by_id(client, ObjectId(), headers)

        assert response.status_code == 404

    def test_a_session_under_someone_elses_class_returns_403(self, app_instance, monkeypatch):
        klass, students, enrollments = build_owned_class_with_roster(ObjectId(), student_count=1)
        session, records = make_session_with_records(klass["_id"], students)
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "faculty",
                extra_users=students, classes=[klass], class_enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _get_session_by_id(client, session["_id"], headers)

        assert response.status_code == 403

    def test_malformed_session_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = _get_session_by_id(client, "not-a-valid-id", headers)

        assert response.status_code == 400


# --- PUT /api/attendance/sessions/<id> (08-faculty-attendance-history.md) -----


class TestUpdateSessionRoute:
    def test_flips_a_students_status_and_returns_200(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _update_session(
                client, session["_id"], headers,
                records=[{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}],
            )

        assert response.status_code == 200
        body = response.get_json()
        assert body["records"][0]["status"] == "absent"
        assert body["absent_count"] == 1
        assert body["present_count"] == 0

    def test_class_id_date_source_created_at_and_taken_by_are_unchanged(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        original_taken_by = faculty_id
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(
            klass["_id"], students, source="photo", taken_by=original_taken_by,
            date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _update_session(
                client, session["_id"], headers,
                records=[{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}],
            )

        body = response.get_json()
        assert body["class_id"] == str(klass["_id"])
        assert body["date"] == "2020-01-01"
        assert body["source"] == "photo"
        assert body["taken_by"] == str(original_taken_by)
        assert body["created_at"] == session["created_at"].isoformat()

    def test_updated_by_is_the_caller_never_the_body(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        spoofed_id = str(ObjectId())
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )
            real_id = _acting_user_id(client)

            response = _update_session(
                client, session["_id"], headers,
                records=[
                    {
                        "student_id": str(students[0]["_id"]), "status": "absent",
                        "marked_by": "faculty", "updated_by": spoofed_id,
                    }
                ],
            )

        body = response.get_json()
        assert body["updated_by"] == real_id
        assert body["updated_by"] != spoofed_id

    def test_edited_and_updated_at_reflect_an_edit(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            before = _get_session_by_id(client, session["_id"], headers).get_json()
            response = _update_session(
                client, session["_id"], headers,
                records=[{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}],
            )

        assert before["edited"] is False
        assert before["updated_by"] is None
        after = response.get_json()
        assert after["edited"] is True
        assert after["updated_by"] is not None
        assert after["updated_at"] >= before["updated_at"]

    def test_a_changed_row_is_stored_faculty_and_an_unchanged_row_keeps_recognition(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        session, records = make_session_with_records(
            klass["_id"], students, statuses=["present", "present"],
            marked_by=["recognition", "recognition"],
        )
        with app_instance.test_client() as client:
            fake_db, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            _update_session(
                client, session["_id"], headers,
                records=[
                    # Unchanged: resubmitted exactly as stored.
                    {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
                    # Changed: status flipped.
                    {"student_id": str(students[1]["_id"]), "status": "absent", "marked_by": "faculty"},
                ],
            )

        stored = {r["student_id"]: r for r in fake_db[ATTENDANCE_RECORDS].find({})}
        assert stored[students[0]["_id"]]["marked_by"] == "recognition"
        assert stored[students[1]["_id"]]["marked_by"] == "faculty"

    def test_missing_a_session_student_returns_400_and_changes_nothing(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        session, records = make_session_with_records(
            klass["_id"], students, statuses=["present", "present"],
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _update_session(
                client, session["_id"], headers,
                records=[{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}],
            )
            after = _get_session_by_id(client, session["_id"], headers).get_json()

        assert response.status_code == 400
        assert all(r["status"] == "present" for r in after["records"])

    def test_a_duplicate_student_returns_400_and_changes_nothing(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _update_session(
                client, session["_id"], headers,
                records=[
                    {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
                    {"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"},
                ],
            )
            after = _get_session_by_id(client, session["_id"], headers).get_json()

        assert response.status_code == 400
        assert after["records"][0]["status"] == "present"

    def test_a_student_not_in_the_session_returns_400_and_changes_nothing(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _update_session(
                client, session["_id"], headers,
                records=[
                    {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
                    {"student_id": str(ObjectId()), "status": "present", "marked_by": "faculty"},
                ],
            )
            after = _get_session_by_id(client, session["_id"], headers).get_json()

        assert response.status_code == 400
        assert len(after["records"]) == 1

    def test_an_invalid_status_returns_400_and_changes_nothing(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _update_session(
                client, session["_id"], headers,
                records=[{"student_id": str(students[0]["_id"]), "status": "late", "marked_by": "faculty"}],
            )
            after = _get_session_by_id(client, session["_id"], headers).get_json()

        assert response.status_code == 400
        assert after["records"][0]["status"] == "present"

    def test_an_invalid_marked_by_returns_400_and_changes_nothing(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _update_session(
                client, session["_id"], headers,
                records=[{"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "robot"}],
            )
            after = _get_session_by_id(client, session["_id"], headers).get_json()

        assert response.status_code == 400
        assert after["records"][0]["marked_by"] == "recognition"

    def test_edit_is_validated_against_the_sessions_students_not_the_current_roster(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        new_student = make_user(email=f"new-{ObjectId()}@college.test", role="student")
        new_enrollment = make_class_enrollment(klass["_id"], new_student["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students + [new_student],
                enrollments=enrollments + [new_enrollment],
                attendance_sessions=[session], attendance_records=records,
            )

            # Enrolling a new student since the lecture does not require
            # them in an otherwise-valid edit.
            still_valid = _update_session(
                client, session["_id"], headers,
                records=[{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}],
            )
            # Including the newly enrolled student is rejected: they were
            # not part of this session.
            rejected = _update_session(
                client, session["_id"], headers,
                records=[
                    {"student_id": str(students[0]["_id"]), "status": "present", "marked_by": "recognition"},
                    {"student_id": str(new_student["_id"]), "status": "present", "marked_by": "faculty"},
                ],
            )

        assert still_valid.status_code == 200
        assert rejected.status_code == 400

    def test_nonexistent_session_id_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = _update_session(client, ObjectId(), headers, records=[])

        assert response.status_code == 404

    def test_a_session_under_someone_elses_class_returns_403(self, app_instance, monkeypatch):
        klass, students, enrollments = build_owned_class_with_roster(ObjectId(), student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "faculty",
                extra_users=students, classes=[klass], class_enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _update_session(
                client, session["_id"], headers,
                records=[{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}],
            )

        assert response.status_code == 403

    def test_malformed_session_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = _update_session(client, "not-a-valid-id", headers, records=[])

        assert response.status_code == 400


# --- DELETE /api/attendance/sessions/<id> (08-faculty-attendance-history.md) --


class TestDeleteSessionRoute:
    def test_deletes_the_session_and_its_records_and_returns_deleted_true(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=2)
        session, records = make_session_with_records(klass["_id"], students)
        with app_instance.test_client() as client:
            fake_db, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _delete_session_request(client, session["_id"], headers)

        assert response.status_code == 200
        # Matches routes/faces.py's delete shape exactly, per the spec's
        # documented response -- {"deleted": true} and nothing else, so a
        # count or id an attacker didn't already know is never handed back.
        assert response.get_json() == {"deleted": True}
        assert fake_db[ATTENDANCE_SESSIONS].find_one({"_id": session["_id"]}) is None
        assert list(fake_db[ATTENDANCE_RECORDS].find({"session_id": session["_id"]})) == []

    def test_a_subsequent_get_of_the_deleted_id_returns_404(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            _delete_session_request(client, session["_id"], headers)
            response = _get_session_by_id(client, session["_id"], headers)

        assert response.status_code == 404

    def test_deleting_one_session_leaves_other_sessions_of_the_same_class_untouched(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session_a, records_a = make_session_with_records(
            klass["_id"], students, date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        session_b, records_b = make_session_with_records(
            klass["_id"], students, date=datetime(2020, 1, 2, tzinfo=timezone.utc),
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session_a, session_b], attendance_records=records_a + records_b,
            )

            _delete_session_request(client, session_a["_id"], headers)
            response = _get_session_by_id(client, session_b["_id"], headers)

        assert response.status_code == 200
        assert len(response.get_json()["records"]) == 1

    def test_after_delete_the_same_class_and_date_can_be_saved_again_without_409(
        self, app_instance, monkeypatch
    ):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(
            klass["_id"], students, date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            _delete_session_request(client, session["_id"], headers)
            response = _save(
                client, headers, class_id=klass["_id"], date="2020-01-01", source="manual",
                records=[{"student_id": str(students[0]["_id"]), "status": "present"}],
            )

        assert response.status_code == 201

    def test_nonexistent_session_id_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = _delete_session_request(client, ObjectId(), headers)

        assert response.status_code == 404

    def test_a_session_under_someone_elses_class_returns_403(self, app_instance, monkeypatch):
        klass, students, enrollments = build_owned_class_with_roster(ObjectId(), student_count=1)
        session, records = make_session_with_records(klass["_id"], students)
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "faculty",
                extra_users=students, classes=[klass], class_enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _delete_session_request(client, session["_id"], headers)

        assert response.status_code == 403

    def test_malformed_session_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = _delete_session_request(client, "not-a-valid-id", headers)

        assert response.status_code == 400


# --- History: class-scoped ownership across all four endpoints ----------------


class TestHistoryClassScopedAuthorization:
    def test_an_unassigned_class_returns_403_not_500_on_list(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), faculty_id=None)
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty", classes=[klass])

            response = _list_sessions(client, klass["_id"], headers)

        assert response.status_code == 403

    def test_a_nonexistent_class_returns_404_not_500_on_list(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            response = _list_sessions(client, ObjectId(), headers)

        assert response.status_code == 404

    def test_none_of_the_four_history_endpoints_return_409_or_503(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )
            _stub_recognition(monkeypatch, recognition_available=False, video_available=False)

            responses = [
                _list_sessions(client, klass["_id"], headers),
                _get_session_by_id(client, session["_id"], headers),
                _update_session(
                    client, session["_id"], headers,
                    records=[{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}],
                ),
                _delete_session_request(client, session["_id"], headers),
            ]

        codes = [r.status_code for r in responses]
        assert 409 not in codes
        assert 503 not in codes


# --- History: role enforcement -------------------------------------------------


class TestHistoryRoleEnforcement:
    def test_admin_receives_403_on_all_four_history_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            responses = _all_history_endpoint_requests(client, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_student_receives_403_on_all_four_history_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "student")

            responses = _all_history_endpoint_requests(client, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_unauthenticated_receives_401_on_all_four_history_endpoints(
        self, app_instance, monkeypatch
    ):
        fake_db = make_fake_attendance_db()
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            responses = _all_history_endpoint_requests(client, headers={})

        assert all(r.status_code == 401 for r in responses), [r.status_code for r in responses]


# --- History: CSRF enforcement -------------------------------------------------


class TestHistoryCsrfEnforcement:
    def test_put_without_a_csrf_header_is_rejected(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        email = f"owner-{klass['faculty_id']}@college.test"
        faculty = make_user(email=email, password=FAKE_PASSWORD, role="faculty")
        faculty["_id"] = klass["faculty_id"]
        fake_db = make_fake_attendance_db(
            users=[faculty] + students, classes=[klass], class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, email, FAKE_PASSWORD)

            response = client.put(
                f"/api/attendance/sessions/{session['_id']}",
                json={"records": [{"student_id": str(students[0]["_id"]), "status": "absent"}]},
            )  # no X-CSRF-TOKEN

        assert response.status_code == 401

    def test_delete_without_a_csrf_header_is_rejected(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        email = f"owner-{klass['faculty_id']}@college.test"
        faculty = make_user(email=email, password=FAKE_PASSWORD, role="faculty")
        faculty["_id"] = klass["faculty_id"]
        fake_db = make_fake_attendance_db(
            users=[faculty] + students, classes=[klass], class_enrollments=enrollments,
            attendance_sessions=[session], attendance_records=records,
        )
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, email, FAKE_PASSWORD)

            response = client.delete(f"/api/attendance/sessions/{session['_id']}")  # no X-CSRF-TOKEN

        assert response.status_code == 401

    def test_put_with_the_correct_csrf_header_is_accepted(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _update_session(
                client, session["_id"], headers,
                records=[{"student_id": str(students[0]["_id"]), "status": "absent", "marked_by": "faculty"}],
            )

        assert response.status_code == 200

    def test_delete_with_the_correct_csrf_header_is_accepted(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        session, records = make_session_with_records(klass["_id"], students, statuses=["present"])
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
                attendance_sessions=[session], attendance_records=records,
            )

            response = _delete_session_request(client, session["_id"], headers)

        assert response.status_code == 200


# --- Class-scoped authorization across all three class-scoped endpoints --------


class TestClassScopedAuthorization:
    def _requests(self, client, headers, class_id):
        return {
            "recognize": lambda: _recognize(client, class_id, headers),
            "session": lambda: _get_session(client, class_id, "2020-01-01", headers),
            "save": lambda: _save(
                client, headers, class_id=class_id, date="2020-01-01", source="manual", records=[]
            ),
        }

    def test_a_class_assigned_to_someone_else_returns_403_on_all_three(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), faculty_id=ObjectId())
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty", classes=[klass])
            _stub_recognition(monkeypatch)

            responses = {n: r() for n, r in self._requests(client, headers, str(klass["_id"])).items()}

        assert all(r.status_code == 403 for r in responses.values()), responses

    def test_an_unassigned_class_returns_403_not_500_on_all_three(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), faculty_id=None)
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty", classes=[klass])
            _stub_recognition(monkeypatch)

            responses = {n: r() for n, r in self._requests(client, headers, str(klass["_id"])).items()}

        assert all(r.status_code == 403 for r in responses.values()), responses

    def test_a_nonexistent_class_returns_404_on_all_three(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")
            _stub_recognition(monkeypatch)

            responses = {n: r() for n, r in self._requests(client, headers, str(ObjectId())).items()}

        assert all(r.status_code == 404 for r in responses.values()), responses


# --- Role enforcement -------------------------------------------------------------


class TestRoleEnforcement:
    def test_admin_receives_403_on_all_four_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            responses = _all_four_endpoint_requests(client, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_student_receives_403_on_all_four_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "student")

            responses = _all_four_endpoint_requests(client, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_unauthenticated_receives_401_on_all_four_endpoints(self, app_instance, monkeypatch):
        fake_db = make_fake_attendance_db()
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            responses = _all_four_endpoint_requests(client, headers={})

        assert all(r.status_code == 401 for r in responses), [r.status_code for r in responses]


# --- CSRF enforcement --------------------------------------------------------------


class TestCsrfEnforcement:
    def test_recognize_without_a_csrf_header_is_rejected(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        email = f"owner-{klass['faculty_id']}@college.test"
        faculty = make_user(email=email, password=FAKE_PASSWORD, role="faculty")
        faculty["_id"] = klass["faculty_id"]
        fake_db = make_fake_attendance_db(
            users=[faculty] + students, classes=[klass], class_enrollments=enrollments,
        )
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, email, FAKE_PASSWORD)
            _stub_recognition(monkeypatch)

            response = _recognize(client, klass["_id"], headers={})  # no X-CSRF-TOKEN

        assert response.status_code == 401

    def test_save_without_a_csrf_header_is_rejected(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        email = f"owner-{klass['faculty_id']}@college.test"
        faculty = make_user(email=email, password=FAKE_PASSWORD, role="faculty")
        faculty["_id"] = klass["faculty_id"]
        fake_db = make_fake_attendance_db(
            users=[faculty] + students, classes=[klass], class_enrollments=enrollments,
        )
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, email, FAKE_PASSWORD)

            response = client.post(
                "/api/attendance",
                json={
                    "class_id": str(klass["_id"]),
                    "date": "2020-01-01",
                    "source": "manual",
                    "records": [{"student_id": str(students[0]["_id"]), "status": "present"}],
                },
            )  # no X-CSRF-TOKEN

        assert response.status_code == 401

    def test_recognize_with_the_correct_csrf_header_is_accepted(self, app_instance, monkeypatch):
        faculty_id = ObjectId()
        klass, students, enrollments = build_owned_class_with_roster(faculty_id, student_count=1)
        with app_instance.test_client() as client:
            _, headers = _login_as_owner(
                monkeypatch, client, klass, students=students, enrollments=enrollments,
            )
            _stub_recognition(monkeypatch)

            response = _recognize(client, klass["_id"], headers)

        assert response.status_code == 200


# --- Database error handling ------------------------------------------------------


class TestDatabaseErrorHandling:
    def test_a_database_error_returns_500_as_json_not_a_traceback(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            def _raise():
                raise RuntimeError("MONGODB_URI environment variable must be set")

            monkeypatch.setattr("routes.attendance.get_db", _raise)

            response = client.get("/api/classes/assigned", headers=headers)

        assert response.status_code == 500
        assert "error" in response.get_json()
        assert "MONGODB_URI" not in response.get_data(as_text=True)


# =============================================================================
# A student's own attendance (09-student-attendance-dashboard.md)
# =============================================================================
#
# Spec contract under test ("APIs" + "Rules for implementation" +
# "Definition of done"):
#   - GET /api/attendance/me and GET /api/attendance/me/sessions are
#     student-only and identify the caller from the verified JWT alone.
#     No student id is ever read from a path segment, query string, or
#     JSON body -- sending one changes nothing about the response.
#   - The overview lists one entry per enrolled class, labelled with its
#     course/semester/department/institute names, plus an `overall`
#     roll-up whose counts equal the sum of the per-class counts; empty
#     enrollment is 200 with an empty list and a zeroed roll-up; an
#     unattended class is total_count 0 / percentage null, never 0; the
#     denominator is only the records this student has, never a class's
#     session count; a stray record naming this student for a class they
#     are not enrolled in, or a classmate's record in a shared class,
#     never changes a response.
#   - The class-detail endpoint returns this student's own record newest
#     first with a month-by-month trend oldest first, both narrowed
#     together by an inclusive from/to filter; present_count + absent_count
#     equals total_count in the header, in every monthly bucket, and
#     against len(sessions); 403 for an unenrolled class, 404 for a
#     nonexistent one, 400 for a missing/malformed class_id or a
#     malformed/backwards date range.
#   - Neither response ever carries another student's id/name/email/
#     status, a roster, a class average, marked_by, taken_by, updated_by,
#     source, or a session id.
#
# `recognition.encoder`/`recognition.frames` are never imported by this
# path; `_stub_recognition(recognition_available=False, ...)` is used only
# to prove that unavailability, which is a 503 elsewhere in this blueprint,
# has no effect here.


def _build_class_with_hierarchy(
    *,
    class_name="A",
    course_name="Data Structures",
    semester_name="Semester 3",
    department_name="Computer Engineering",
    institute_name="Test Institute",
    faculty_id=None,
):
    """A class plus the four-level chain above it, in the shape
    `make_fake_attendance_db(**hierarchy)` expects -- so a test can label a
    class the way `class_hierarchy_context` would and assert on the names
    the student actually sees.
    """
    institute = make_institute(name=institute_name)
    department = make_department(institute["_id"], name=department_name)
    semester = make_semester(department["_id"], name=semester_name)
    course = make_course(semester["_id"], name=course_name)
    klass = make_class(course["_id"], name=class_name, faculty_id=faculty_id)
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


def _make_student(**overrides):
    overrides.setdefault("email", f"student-{ObjectId()}@college.test")
    overrides.setdefault("password", FAKE_PASSWORD)
    overrides.setdefault("role", "student")
    return make_user(**overrides)


def _login_as_student(
    monkeypatch, client, student, *, classes=None, class_enrollments=None,
    extra_users=None, **collections
):
    """Logs in as `student`, whose id already appears in whatever
    `class_enrollments`/`attendance_records` the caller built -- the
    enrolled-student counterpart of `_login_as_owner` above.
    """
    fake_db = make_fake_attendance_db(
        users=[student] + list(extra_users or []),
        classes=list(classes or []),
        class_enrollments=list(class_enrollments or []),
        **collections,
    )
    _patch_db(monkeypatch, fake_db)
    _login(client, student["email"], FAKE_PASSWORD)
    return fake_db, _csrf_headers(client)


def _get_my_attendance(client, headers=None):
    return client.get("/api/attendance/me", headers=headers or {})


def _get_my_class_attendance(client, class_id, headers=None, *, query=""):
    suffix = f"&{query}" if query else ""
    return client.get(
        f"/api/attendance/me/sessions?class_id={class_id}{suffix}", headers=headers or {}
    )


def _both_student_dashboard_requests(client, headers):
    """One representative request per 09-student-attendance-dashboard.md
    endpoint, used the same way `_all_four_endpoint_requests` /
    `_all_history_endpoint_requests` are: to prove role enforcement /
    authentication short-circuits before any id is resolved. A dummy
    class_id is fine for the same reason.
    """
    class_id = str(ObjectId())
    return [
        _get_my_attendance(client, headers),
        _get_my_class_attendance(client, class_id, headers),
    ]


_FORBIDDEN_STUDENT_RESPONSE_KEYS = {"marked_by", "taken_by", "updated_by", "source"}


def _assert_no_forbidden_keys(payload):
    """No key anywhere in a student response may be attendance provenance
    -- these are audit fields for judging recognition, not something a
    student's own dashboard is entitled to see (09's "Rules for
    implementation").
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key not in _FORBIDDEN_STUDENT_RESPONSE_KEYS, (
                f"forbidden key {key!r} present in {payload}"
            )
            _assert_no_forbidden_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_forbidden_keys(item)


def _leaf_values(payload):
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _leaf_values(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _leaf_values(item)
    else:
        yield payload


def _assert_none_of_these_values_present(payload, forbidden_values):
    """Checks the parsed JSON structurally -- every leaf value in the body,
    not a substring search over the raw text -- for anything that must
    never appear in a student's own attendance response: another
    student's id/name/email, a session id, provenance values, and the
    like.
    """
    leaked = set(_leaf_values(payload)) & set(forbidden_values)
    assert not leaked, f"forbidden values leaked into response: {leaked}"


# --- GET /api/attendance/me ---------------------------------------------------


class TestGetMyAttendanceRoute:
    def test_returns_one_entry_per_enrolled_class_with_hierarchy_names_and_overall_rollup(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        session = make_attendance_session(klass["_id"], date=datetime(2020, 1, 1, tzinfo=timezone.utc))
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present"),
        ]
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[session], attendance_records=records,
                **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        assert response.status_code == 200
        body = response.get_json()
        assert len(body["classes"]) == 1
        entry = body["classes"][0]
        assert entry["id"] == str(klass["_id"])
        assert entry["course"] == "Data Structures"
        assert entry["semester"] == "Semester 3"
        assert entry["department"] == "Computer Engineering"
        assert entry["institute"] == "Test Institute"
        assert entry["present_count"] == 1
        assert entry["absent_count"] == 0
        assert entry["total_count"] == 1
        assert entry["percentage"] == 100.0
        assert body["overall"] == {
            "present_count": 1, "absent_count": 0, "total_count": 1, "percentage": 100.0,
        }

    def test_empty_enrollment_returns_200_with_empty_classes_and_zeroed_overall(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        with app_instance.test_client() as client:
            _, headers = _login_as_student(monkeypatch, client, student)

            response = _get_my_attendance(client, headers)

        assert response.status_code == 200
        body = response.get_json()
        assert body["classes"] == []
        assert body["overall"] == {
            "present_count": 0, "absent_count": 0, "total_count": 0, "percentage": None,
        }

    def test_a_class_with_no_attendance_taken_yet_has_total_zero_and_percentage_null(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment], **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        entry = response.get_json()["classes"][0]
        assert entry["total_count"] == 0
        assert entry["present_count"] == 0
        assert entry["absent_count"] == 0
        assert entry["percentage"] is None

    def test_student_enrolled_after_some_lectures_is_not_marked_down(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        # Two lectures were held before this student joined the class; a
        # record exists for them only from the one held afterwards.
        before_joining = make_attendance_session(
            klass["_id"], date=datetime(2020, 1, 1, tzinfo=timezone.utc)
        )
        after_joining = make_attendance_session(
            klass["_id"], date=datetime(2020, 2, 1, tzinfo=timezone.utc)
        )
        records = [
            make_attendance_record(after_joining["_id"], klass["_id"], student["_id"], status="present"),
        ]
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[before_joining, after_joining], attendance_records=records,
                **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        entry = response.get_json()["classes"][0]
        assert entry["total_count"] == 1
        assert entry["present_count"] == 1
        assert entry["percentage"] == 100.0

    def test_a_record_for_a_class_the_student_is_not_enrolled_in_never_appears(
        self, app_instance, monkeypatch
    ):
        """The trap case the spec calls out by name: a stored
        attendance_records row that references this student's id but a
        class_id they are not enrolled in must not appear in `classes` or
        contribute to `overall`.
        """
        student = _make_student()
        enrolled_class, enrolled_hierarchy = _build_class_with_hierarchy(
            class_name="A", course_name="Data Structures",
        )
        other_class, other_hierarchy = _build_class_with_hierarchy(
            class_name="B", course_name="Other Course",
        )
        enrollment = make_class_enrollment(enrolled_class["_id"], student["_id"])
        enrolled_session = make_attendance_session(
            enrolled_class["_id"], date=datetime(2020, 1, 1, tzinfo=timezone.utc)
        )
        enrolled_record = make_attendance_record(
            enrolled_session["_id"], enrolled_class["_id"], student["_id"], status="present",
        )
        # This student has no enrollment in `other_class`, yet a record
        # names them there anyway (e.g. left behind by a since-reverted
        # enrollment).
        stray_session = make_attendance_session(
            other_class["_id"], date=datetime(2020, 1, 2, tzinfo=timezone.utc)
        )
        stray_record = make_attendance_record(
            stray_session["_id"], other_class["_id"], student["_id"], status="absent",
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[enrolled_class, other_class], class_enrollments=[enrollment],
                attendance_sessions=[enrolled_session, stray_session],
                attendance_records=[enrolled_record, stray_record],
                **_merge_hierarchies(enrolled_hierarchy, other_hierarchy),
            )

            response = _get_my_attendance(client, headers)

        body = response.get_json()
        assert [c["id"] for c in body["classes"]] == [str(enrolled_class["_id"])]
        # If the stray absence had leaked in, overall would show 1 present,
        # 1 absent, 2 total instead.
        assert body["overall"] == {
            "present_count": 1, "absent_count": 0, "total_count": 1, "percentage": 100.0,
        }

    def test_a_classmates_record_in_a_shared_class_does_not_affect_this_students_counts(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        classmate = _make_student(name="Classmate", email=f"classmate-{ObjectId()}@college.test")
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        classmate_enrollment = make_class_enrollment(klass["_id"], classmate["_id"])
        session = make_attendance_session(klass["_id"], date=datetime(2020, 1, 1, tzinfo=timezone.utc))
        my_record = make_attendance_record(
            session["_id"], klass["_id"], student["_id"], status="present",
        )
        classmates_records = [
            make_attendance_record(session["_id"], klass["_id"], classmate["_id"], status="absent"),
        ]
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                extra_users=[classmate],
                classes=[klass], class_enrollments=[enrollment, classmate_enrollment],
                attendance_sessions=[session], attendance_records=[my_record] + classmates_records,
                **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        entry = response.get_json()["classes"][0]
        assert entry["present_count"] == 1
        assert entry["absent_count"] == 0
        assert entry["total_count"] == 1


# --- GET /api/attendance/me/sessions ------------------------------------------


class TestGetMyClassAttendanceRoute:
    def test_returns_own_status_for_every_lecture_newest_first_with_monthly_oldest_first(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        january = make_attendance_session(klass["_id"], date=datetime(2020, 1, 10, tzinfo=timezone.utc))
        february = make_attendance_session(klass["_id"], date=datetime(2020, 2, 10, tzinfo=timezone.utc))
        records = [
            make_attendance_record(january["_id"], klass["_id"], student["_id"], status="present"),
            make_attendance_record(february["_id"], klass["_id"], student["_id"], status="absent"),
        ]
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[january, february], attendance_records=records,
                **hierarchy,
            )

            response = _get_my_class_attendance(client, klass["_id"], headers)

        assert response.status_code == 200
        body = response.get_json()
        assert body["class"]["id"] == str(klass["_id"])
        assert body["class"]["course"] == "Data Structures"
        assert [row["date"] for row in body["sessions"]] == ["2020-02-10", "2020-01-10"]
        assert [row["status"] for row in body["sessions"]] == ["absent", "present"]
        assert [bucket["month"] for bucket in body["monthly"]] == ["2020-01", "2020-02"]

    def test_present_plus_absent_equals_total_in_header_monthly_and_session_length(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        sessions = [
            make_attendance_session(klass["_id"], date=datetime(2020, 1, day, tzinfo=timezone.utc))
            for day in (1, 2, 3)
        ]
        statuses = ["present", "absent", "present"]
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status=status)
            for session, status in zip(sessions, statuses)
        ]
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=sessions, attendance_records=records,
                **hierarchy,
            )

            response = _get_my_class_attendance(client, klass["_id"], headers)

        body = response.get_json()
        assert body["present_count"] + body["absent_count"] == body["total_count"] == 3
        assert len(body["sessions"]) == body["total_count"]
        for bucket in body["monthly"]:
            assert bucket["present_count"] + bucket["absent_count"] == bucket["total_count"]

    def test_denominator_is_the_students_own_records_only(self, app_instance, monkeypatch):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        before_joining = make_attendance_session(
            klass["_id"], date=datetime(2020, 1, 1, tzinfo=timezone.utc)
        )
        after_joining = make_attendance_session(
            klass["_id"], date=datetime(2020, 2, 1, tzinfo=timezone.utc)
        )
        records = [
            make_attendance_record(after_joining["_id"], klass["_id"], student["_id"], status="present"),
        ]
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[before_joining, after_joining], attendance_records=records,
                **hierarchy,
            )

            response = _get_my_class_attendance(client, klass["_id"], headers)

        body = response.get_json()
        assert body["total_count"] == 1
        assert len(body["sessions"]) == 1
        assert body["sessions"][0]["date"] == "2020-02-01"

    def test_from_and_to_filter_inclusively_and_narrow_sessions_and_monthly_together(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        january = make_attendance_session(klass["_id"], date=datetime(2020, 1, 1, tzinfo=timezone.utc))
        february = make_attendance_session(klass["_id"], date=datetime(2020, 2, 1, tzinfo=timezone.utc))
        march = make_attendance_session(klass["_id"], date=datetime(2020, 3, 1, tzinfo=timezone.utc))
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present")
            for session in (january, february, march)
        ]
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[january, february, march], attendance_records=records,
                **hierarchy,
            )

            response = _get_my_class_attendance(
                client, klass["_id"], headers, query="from=2020-01-01&to=2020-02-01",
            )

        body = response.get_json()
        assert {row["date"] for row in body["sessions"]} == {"2020-01-01", "2020-02-01"}
        assert {bucket["month"] for bucket in body["monthly"]} == {"2020-01", "2020-02"}
        assert body["total_count"] == 2

    def test_to_earlier_than_from_returns_400(self, app_instance, monkeypatch):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment], **hierarchy,
            )

            response = _get_my_class_attendance(
                client, klass["_id"], headers, query="from=2020-02-01&to=2020-01-01",
            )

        assert response.status_code == 400

    def test_a_malformed_from_returns_400(self, app_instance, monkeypatch):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment], **hierarchy,
            )

            response = _get_my_class_attendance(
                client, klass["_id"], headers, query="from=not-a-date",
            )

        assert response.status_code == 400

    def test_missing_class_id_returns_400(self, app_instance, monkeypatch):
        student = _make_student()
        with app_instance.test_client() as client:
            _, headers = _login_as_student(monkeypatch, client, student)

            response = client.get("/api/attendance/me/sessions", headers=headers)

        assert response.status_code == 400

    def test_malformed_class_id_returns_400(self, app_instance, monkeypatch):
        student = _make_student()
        with app_instance.test_client() as client:
            _, headers = _login_as_student(monkeypatch, client, student)

            response = _get_my_class_attendance(client, "not-a-valid-id", headers)

        assert response.status_code == 400

    def test_unenrolled_class_returns_403(self, app_instance, monkeypatch):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        # No enrollment for this student in this class.
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student, classes=[klass], **hierarchy,
            )

            response = _get_my_class_attendance(client, klass["_id"], headers)

        assert response.status_code == 403

    def test_nonexistent_class_returns_404(self, app_instance, monkeypatch):
        student = _make_student()
        with app_instance.test_client() as client:
            _, headers = _login_as_student(monkeypatch, client, student)

            response = _get_my_class_attendance(client, ObjectId(), headers)

        assert response.status_code == 404

    def test_neither_403_nor_404_is_a_500(self, app_instance, monkeypatch):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student, classes=[klass], **hierarchy,
            )

            unenrolled = _get_my_class_attendance(client, klass["_id"], headers)
            nonexistent = _get_my_class_attendance(client, ObjectId(), headers)

        assert {unenrolled.status_code, nonexistent.status_code} == {403, 404}


# --- 11-student-attendance-threshold.md: the bar on the two responses --------
#
# `attendance/serializers.py` and `attendance/threshold.py` are covered
# directly by test_attendance_threshold.py; the tests below are only
# concerned with the two endpoints wiring the bar in correctly -- the same
# configured value reaching both responses, applied to the right scope,
# and degrading safely when unusable.


def _five_lectures_four_present_one_absent(klass, student):
    """A roster of five recorded lectures at 80% (4 present, 1 absent) --
    comfortably above the default 75% bar, used wherever a test needs a
    class that meets the bar with a known, non-boundary percentage.
    """
    sessions = [
        make_attendance_session(klass["_id"], date=datetime(2020, 1, day, tzinfo=timezone.utc))
        for day in range(1, 6)
    ]
    statuses = ["present", "present", "present", "present", "absent"]
    records = [
        make_attendance_record(session["_id"], klass["_id"], student["_id"], status=status)
        for session, status in zip(sessions, statuses)
    ]
    return sessions, records


class TestOverviewThresholdFields:
    def test_returns_the_configured_threshold_and_a_class_below_the_bar(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        # 1 present, 3 absent -> 25%, below the default 75% bar.
        sessions = [
            make_attendance_session(klass["_id"], date=datetime(2020, 1, day, tzinfo=timezone.utc))
            for day in range(1, 5)
        ]
        statuses = ["present", "absent", "absent", "absent"]
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status=status)
            for session, status in zip(sessions, statuses)
        ]
        with app_instance.test_client() as client:
            monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 75.0)
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=sessions, attendance_records=records,
                **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        body = response.get_json()
        assert body["threshold"] == 75.0
        entry = body["classes"][0]
        assert entry["percentage"] == 25.0
        assert entry["meets_threshold"] is False
        assert isinstance(entry["lectures_to_reach"], int)
        assert entry["lectures_to_reach"] > 0

    def test_a_class_above_the_bar_reports_met_and_a_zero_catch_up_figure(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        sessions, records = _five_lectures_four_present_one_absent(klass, student)
        with app_instance.test_client() as client:
            monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 75.0)
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=sessions, attendance_records=records,
                **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        entry = response.get_json()["classes"][0]
        assert entry["percentage"] == 80.0
        assert entry["meets_threshold"] is True
        assert entry["lectures_to_reach"] == 0

    def test_a_class_exactly_at_the_bar_reports_meets_threshold_true(
        self, app_instance, monkeypatch
    ):
        """DoD: "matching flask notify-low-attendance, which does not mail
        that student." The sweep only mails `percentage < threshold`, so a
        student sitting exactly on it must read as met here too.
        """
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        # 3 present, 1 absent -> exactly 75%.
        sessions = [
            make_attendance_session(klass["_id"], date=datetime(2020, 1, day, tzinfo=timezone.utc))
            for day in range(1, 5)
        ]
        statuses = ["present", "present", "present", "absent"]
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status=status)
            for session, status in zip(sessions, statuses)
        ]
        with app_instance.test_client() as client:
            monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 75.0)
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=sessions, attendance_records=records,
                **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        entry = response.get_json()["classes"][0]
        assert entry["percentage"] == 75.0
        assert entry["meets_threshold"] is True
        assert entry["lectures_to_reach"] == 0

    def test_a_class_with_nothing_recorded_reports_null_not_false_or_zero(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        with app_instance.test_client() as client:
            monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 75.0)
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment], **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        entry = response.get_json()["classes"][0]
        assert entry["percentage"] is None
        assert entry["meets_threshold"] is None
        assert entry["lectures_to_reach"] is None


class TestClassDetailThresholdFields:
    def test_returns_threshold_and_comparison_fields_for_the_one_class(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        sessions, records = _five_lectures_four_present_one_absent(klass, student)
        with app_instance.test_client() as client:
            monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 75.0)
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=sessions, attendance_records=records,
                **hierarchy,
            )

            response = _get_my_class_attendance(client, klass["_id"], headers)

        body = response.get_json()
        assert body["threshold"] == 75.0
        assert body["percentage"] == 80.0
        assert body["meets_threshold"] is True
        assert body["lectures_to_reach"] == 0

    def test_a_class_with_nothing_recorded_reports_null_comparison_fields(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        with app_instance.test_client() as client:
            monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 75.0)
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment], **hierarchy,
            )

            response = _get_my_class_attendance(client, klass["_id"], headers)

        body = response.get_json()
        # The bar itself is still readable even though nothing has been
        # recorded -- only the comparison against it is unanswerable.
        assert body["threshold"] == 75.0
        assert body["percentage"] is None
        assert body["meets_threshold"] is None
        assert body["lectures_to_reach"] is None


class TestThresholdIsScopedToPerClassOnly:
    """DoD: "overall carries no threshold, meets_threshold, or
    lectures_to_reach, and neither does any monthly bucket" -- exercised
    here with a mix of one class below the bar and one above it, so an
    `overall` verdict (if one leaked in) could not coincidentally agree
    with either class's.
    """

    def test_overall_has_no_threshold_fields_even_when_a_class_is_below_the_bar(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        strong_class, strong_hierarchy = _build_class_with_hierarchy(
            class_name="A", course_name="Course A",
        )
        weak_class, weak_hierarchy = _build_class_with_hierarchy(
            class_name="B", course_name="Course B",
        )
        enrollments = [
            make_class_enrollment(strong_class["_id"], student["_id"]),
            make_class_enrollment(weak_class["_id"], student["_id"]),
        ]
        strong_sessions, strong_records = _five_lectures_four_present_one_absent(
            strong_class, student,
        )
        weak_session = make_attendance_session(weak_class["_id"])
        weak_records = [
            make_attendance_record(weak_session["_id"], weak_class["_id"], student["_id"], status="absent"),
        ]
        with app_instance.test_client() as client:
            monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 75.0)
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[strong_class, weak_class], class_enrollments=enrollments,
                attendance_sessions=strong_sessions + [weak_session],
                attendance_records=strong_records + weak_records,
                **_merge_hierarchies(strong_hierarchy, weak_hierarchy),
            )

            overview = _get_my_attendance(client, headers).get_json()

        entries_by_id = {entry["id"]: entry for entry in overview["classes"]}
        assert entries_by_id[str(strong_class["_id"])]["meets_threshold"] is True
        assert entries_by_id[str(weak_class["_id"])]["meets_threshold"] is False
        assert "threshold" not in overview["overall"]
        assert "meets_threshold" not in overview["overall"]
        assert "lectures_to_reach" not in overview["overall"]

    def test_no_monthly_bucket_carries_a_threshold_field(self, app_instance, monkeypatch):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        sessions, records = _five_lectures_four_present_one_absent(klass, student)
        with app_instance.test_client() as client:
            monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 75.0)
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=sessions, attendance_records=records,
                **hierarchy,
            )

            response = _get_my_class_attendance(client, klass["_id"], headers)

        monthly = response.get_json()["monthly"]
        assert monthly, "expected at least one monthly bucket to check"
        for bucket in monthly:
            assert "threshold" not in bucket
            assert "meets_threshold" not in bucket
            assert "lectures_to_reach" not in bucket


class TestMisconfiguredThresholdDegradesSafely:
    """DoD: "With the threshold misconfigured, both endpoints still return
    200 with the same attendance figures as before, threshold: null, and
    meets_threshold/lectures_to_reach null on every class."
    """

    @pytest.mark.parametrize("bad_value", [0, -5, 150, "not-a-number", True])
    def test_overview_degrades_to_null_threshold_fields_without_losing_attendance_figures(
        self, app_instance, monkeypatch, bad_value
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        session = make_attendance_session(klass["_id"])
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present"),
        ]
        with app_instance.test_client() as client:
            monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", bad_value)
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[session], attendance_records=records,
                **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        assert response.status_code == 200
        body = response.get_json()
        assert body["threshold"] is None
        entry = body["classes"][0]
        assert entry["present_count"] == 1
        assert entry["absent_count"] == 0
        assert entry["total_count"] == 1
        assert entry["percentage"] == 100.0
        assert entry["meets_threshold"] is None
        assert entry["lectures_to_reach"] is None

    @pytest.mark.parametrize("bad_value", [0, -5, 150, "not-a-number", True])
    def test_class_detail_degrades_to_null_threshold_fields_without_losing_attendance_figures(
        self, app_instance, monkeypatch, bad_value
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        session = make_attendance_session(klass["_id"])
        records = [
            make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present"),
        ]
        with app_instance.test_client() as client:
            monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", bad_value)
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[session], attendance_records=records,
                **hierarchy,
            )

            response = _get_my_class_attendance(client, klass["_id"], headers)

        assert response.status_code == 200
        body = response.get_json()
        assert body["threshold"] is None
        assert body["present_count"] == 1
        assert body["absent_count"] == 0
        assert body["total_count"] == 1
        assert body["percentage"] == 100.0
        assert body["meets_threshold"] is None
        assert body["lectures_to_reach"] is None


# --- Never leaks another student, a roster, or provenance ---------------------


class TestStudentDashboardNeverLeaksAnything:
    def test_overview_class_row_and_overall_carry_only_the_documented_fields(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        session = make_attendance_session(klass["_id"], taken_by=ObjectId(), source="video")
        record = make_attendance_record(
            session["_id"], klass["_id"], student["_id"], status="present", marked_by="recognition",
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[session], attendance_records=[record],
                **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        body = response.get_json()
        entry = body["classes"][0]
        assert set(entry.keys()) == {
            "id", "name", "course", "semester", "department", "institute",
            "present_count", "absent_count", "total_count", "percentage",
            # 11-student-attendance-threshold.md: the bar is applied per
            # class and nowhere else.
            "meets_threshold", "lectures_to_reach",
        }
        # Unchanged, and deliberately so: `overall` carries no comparison.
        # An average hides the class a student is actually short in, and
        # the sweep applies the bar per class, so a verdict here would be
        # one the institute never defined.
        assert set(body["overall"].keys()) == {
            "present_count", "absent_count", "total_count", "percentage",
        }
        _assert_no_forbidden_keys(body)

    def test_overview_never_contains_a_classmates_identity_a_session_id_or_a_faculty_id(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        classmate = _make_student(name="Classmate Person")
        faculty_id = ObjectId()
        klass, hierarchy = _build_class_with_hierarchy(faculty_id=faculty_id)
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        classmate_enrollment = make_class_enrollment(klass["_id"], classmate["_id"])
        session = make_attendance_session(klass["_id"], taken_by=faculty_id)
        my_record = make_attendance_record(
            session["_id"], klass["_id"], student["_id"], status="present", marked_by="recognition",
        )
        classmates_record = make_attendance_record(
            session["_id"], klass["_id"], classmate["_id"], status="absent", marked_by="faculty",
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                extra_users=[classmate],
                classes=[klass], class_enrollments=[enrollment, classmate_enrollment],
                attendance_sessions=[session], attendance_records=[my_record, classmates_record],
                **hierarchy,
            )

            response = _get_my_attendance(client, headers)

        forbidden_values = {
            str(classmate["_id"]), classmate["name"], classmate["email"],
            str(session["_id"]), str(faculty_id),
        }
        _assert_none_of_these_values_present(response.get_json(), forbidden_values)

    def test_class_detail_carries_only_the_documented_fields_at_every_level(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        session = make_attendance_session(klass["_id"], taken_by=ObjectId(), source="photo")
        record = make_attendance_record(
            session["_id"], klass["_id"], student["_id"], status="present", marked_by="recognition",
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[session], attendance_records=[record],
                **hierarchy,
            )

            response = _get_my_class_attendance(client, klass["_id"], headers)

        body = response.get_json()
        assert set(body.keys()) == {
            "class", "present_count", "absent_count", "total_count",
            "percentage", "monthly", "sessions",
            # 11-student-attendance-threshold.md: the bar, and how this
            # one class stands against it.
            "threshold", "meets_threshold", "lectures_to_reach",
        }
        assert set(body["class"].keys()) == {
            "id", "name", "course", "semester", "department", "institute",
        }
        for row in body["sessions"]:
            assert set(row.keys()) == {"date", "status"}
        # Unchanged, and deliberately so: a month is not a window anybody
        # is assessed on, so no bucket carries a comparison.
        for bucket in body["monthly"]:
            assert set(bucket.keys()) == {
                "month", "present_count", "absent_count", "total_count", "percentage",
            }
        _assert_no_forbidden_keys(body)

    def test_class_detail_never_contains_a_session_id_or_a_faculty_id_value(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        faculty_id = ObjectId()
        klass, hierarchy = _build_class_with_hierarchy(faculty_id=faculty_id)
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        session = make_attendance_session(klass["_id"], taken_by=faculty_id, source="video")
        record = make_attendance_record(
            session["_id"], klass["_id"], student["_id"], status="present", marked_by="recognition",
        )
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[session], attendance_records=[record],
                **hierarchy,
            )

            response = _get_my_class_attendance(client, klass["_id"], headers)

        forbidden_values = {str(session["_id"]), str(faculty_id)}
        _assert_none_of_these_values_present(response.get_json(), forbidden_values)


# --- No student id is ever read from the request -------------------------------


class TestNoStudentIdIsEverReadFromTheRequest:
    def test_a_student_id_query_param_on_the_overview_does_not_change_the_response(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment], **hierarchy,
            )

            baseline = _get_my_attendance(client, headers).get_json()
            spoofed = client.get(
                f"/api/attendance/me?student_id={ObjectId()}", headers=headers,
            ).get_json()

        assert spoofed == baseline

    def test_a_student_id_query_param_on_the_class_detail_does_not_change_the_response(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        session = make_attendance_session(klass["_id"])
        record = make_attendance_record(session["_id"], klass["_id"], student["_id"], status="present")
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment],
                attendance_sessions=[session], attendance_records=[record],
                **hierarchy,
            )

            baseline = _get_my_class_attendance(client, klass["_id"], headers).get_json()
            spoofed = client.get(
                f"/api/attendance/me/sessions?class_id={klass['_id']}&student_id={ObjectId()}",
                headers=headers,
            ).get_json()

        assert spoofed == baseline

    def test_a_student_id_in_a_json_body_does_not_change_the_overview_response(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment], **hierarchy,
            )

            baseline = _get_my_attendance(client, headers).get_json()
            spoofed = client.open(
                "/api/attendance/me", method="GET", headers=headers,
                json={"student_id": str(ObjectId())},
            ).get_json()

        assert spoofed == baseline


# --- Role enforcement -----------------------------------------------------------


class TestStudentDashboardRoleEnforcement:
    def test_faculty_receives_403_on_both_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            responses = _both_student_dashboard_requests(client, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_admin_receives_403_on_both_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            responses = _both_student_dashboard_requests(client, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_unauthenticated_receives_401_on_both_endpoints(self, app_instance, monkeypatch):
        fake_db = make_fake_attendance_db()
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            responses = _both_student_dashboard_requests(client, headers={})

        assert all(r.status_code == 401 for r in responses), [r.status_code for r in responses]


# --- No CV dependency on this path ----------------------------------------------


class TestStudentDashboardHasNoCvDependency:
    def test_neither_endpoint_returns_503_when_recognition_and_video_are_unavailable(
        self, app_instance, monkeypatch
    ):
        student = _make_student()
        klass, hierarchy = _build_class_with_hierarchy()
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as_student(
                monkeypatch, client, student,
                classes=[klass], class_enrollments=[enrollment], **hierarchy,
            )
            _stub_recognition(monkeypatch, recognition_available=False, video_available=False)

            overview = _get_my_attendance(client, headers)
            detail = _get_my_class_attendance(client, klass["_id"], headers)

        assert overview.status_code == 200
        assert detail.status_code == 200
