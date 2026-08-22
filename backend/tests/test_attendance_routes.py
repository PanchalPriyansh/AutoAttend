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

from attendance_test_helpers import (
    build_owned_class_with_roster,
    fake_closest_match,
    make_class,
    make_face_encoding,
    make_fake_attendance_db,
    make_user,
    synthetic_encoding,
)
from bson import ObjectId

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
