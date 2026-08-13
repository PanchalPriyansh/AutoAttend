"""Tests for backend/routes/faces.py (the `faces_bp` blueprint) and
backend/recognition/{service,validators,serializers,errors}.py.

Spec contract under test (06-face-enrollment.md, "APIs" + "Database
changes" + "Rules for implementation" + "Definition of done"):
  - Five endpoints under `/api`, every one **admin-only**, reads
    included: `GET/POST /api/students/<id>/face-encodings`,
    `DELETE /api/students/<id>/face-encodings/<encoding_id>`,
    `DELETE /api/students/<id>/face-encodings`,
    `GET /api/classes/<id>/face-enrollment`.
  - The `encoding` vector never leaves the backend, under any endpoint
    or condition.
  - A single-face image registers (201); a zero-face or two-face image
    is rejected (400) and writes nothing.
  - A face that matches a *different* student's existing encoding is a
    409 and stores nothing; another sample of the *same* student
    succeeds.
  - The per-student sample cap is a 409, and does not touch existing
    samples.
  - A non-image file, a corrupt image, and a missing `image` part are
    each a 400 -- never a 500 or a traceback.
  - An upload above the size limit is a 413 on the JSON `{"error": ...}`
    contract, not an HTML page.
  - `student_id` referencing faculty/admin is 400, nonexistent is 404,
    malformed is 400.
  - Listing is student-scoped, newest first, no cross-student leakage.
  - Deleting one sample is 200 then 404 on repeat; deleting all reports
    the count.
  - The class enrolment-status endpoint includes zero-sample students,
    with accurate counts/timestamps and no cross-class leakage.
  - With the recognition library unavailable, an enrolment request
    returns 503 with a clear message rather than a 500.
  - Faculty/student get 403 on all five endpoints; unauthenticated gets
    401; a mutating request without a valid CSRF header is rejected.
  - `created_by` is taken from the JWT identity, never the request body.

`routes.auth.get_db` and `routes.faces.get_db` are monkeypatched to the
same in-memory fake db (faces_test_helpers.py, built on the existing
academic_test_helpers.py / auth_test_helpers.py fakes) so a real
`/api/auth/login` call can mint genuine JWT + CSRF cookies while the
endpoints under test read/write fake collections -- no live MongoDB is
required.

Nothing here depends on `face_recognition`/`dlib`/`numpy` being
installed: `recognition.encoder.is_available` / `.encode_faces` /
`.closest_match` are monkeypatched at the point `recognition/service.py`
calls them, so the CV library itself is never exercised. All "images"
used are plain synthetic bytes (`FAKE_IMAGE_BYTES` below), never a real
photograph, and all "encodings" are short, obviously-fake lists of
floats -- no real biometric data is involved anywhere in this file.
"""

import io
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId
from faces_test_helpers import (
    make_class,
    make_class_enrollment,
    make_face_encoding,
    make_fake_faces_db,
    make_user,
)

from database.schema import FACE_ENCODINGS
from recognition.errors import ImageDecodeError
from recognition.service import MAX_SAMPLES_PER_STUDENT
from recognition.validators import MAX_IMAGE_BYTES, MAX_REQUEST_BYTES

FAKE_PASSWORD = "a-fake-test-password-1"

# Plain synthetic bytes -- never a real image, and decoding is always
# monkeypatched, so the actual content is irrelevant to every test below.
FAKE_IMAGE_BYTES = b"synthetic-test-bytes-not-a-real-photograph"

# A short, obviously-fake 128-length vector -- never a real
# face_recognition descriptor derived from an actual photograph.
VALID_ENCODING = [round(0.001 * i, 6) for i in range(128)]


def _patch_db(monkeypatch, fake_db):
    """Both blueprints must see the same fake db: login goes through
    routes.auth.get_db, and every face-enrolment endpoint under test
    goes through routes.faces.get_db.
    """
    monkeypatch.setattr("routes.auth.get_db", lambda: fake_db)
    monkeypatch.setattr("routes.faces.get_db", lambda: fake_db)
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
    """Builds a fake db with one user of `role` (plus any extra users and
    collections passed in), patches both blueprints' get_db, logs in,
    and returns (fake_db, csrf_headers).
    """
    email = email or f"{role}@college.test"
    user = make_user(email=email, password=FAKE_PASSWORD, role=role)
    fake_db = make_fake_faces_db(users=[user] + list(extra_users or []), **collections)
    _patch_db(monkeypatch, fake_db)
    _login(client, email, FAKE_PASSWORD)
    return fake_db, _csrf_headers(client)


def _acting_user_id(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    return response.get_json()["id"]


def _stub_recognition(monkeypatch, *, available=True, encode_faces=None, closest_match=None):
    """Replaces the CV library entirely so no test depends on
    face_recognition/dlib/numpy being installed.

    Defaults to "happy path": recognition available, one valid
    synthetic encoding detected, no collision with another student.
    """
    monkeypatch.setattr("recognition.encoder.is_available", lambda: available)
    monkeypatch.setattr(
        "recognition.encoder.encode_faces",
        encode_faces or (lambda image_bytes: [list(VALID_ENCODING)]),
    )
    monkeypatch.setattr(
        "recognition.encoder.closest_match",
        closest_match or (lambda candidate, known: None),
    )


def _raise_decode_error(image_bytes):
    raise ImageDecodeError("The uploaded file could not be read as an image")


def _image_part(data=FAKE_IMAGE_BYTES, filename="photo.jpg", content_type="image/jpeg"):
    return (io.BytesIO(data), filename, content_type)


def _register(client, student_id, headers, *, image=True, source=None, image_bytes=FAKE_IMAGE_BYTES,
              content_type="image/jpeg", filename="photo.jpg", extra_fields=None):
    form = {}
    if image:
        form["image"] = _image_part(image_bytes, filename, content_type)
    if source is not None:
        form["source"] = source
    if extra_fields:
        form.update(extra_fields)
    return client.post(f"/api/students/{student_id}/face-encodings", data=form, headers=headers)


def _assert_no_encoding_leak(payload):
    """Recursively asserts no dict anywhere in `payload` has the literal
    key "encoding" (distinct from the "encodings" list-wrapper key that
    legitimately appears in list responses).
    """
    if isinstance(payload, dict):
        assert "encoding" not in payload, f"encoding leaked in {payload}"
        for value in payload.values():
            _assert_no_encoding_leak(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_encoding_leak(item)


def _dummy_ids():
    return str(ObjectId()), str(ObjectId()), str(ObjectId())


def _all_five_endpoint_requests(client, headers):
    """One representative request per endpoint, used to prove role
    enforcement / authentication is checked before anything else. Dummy
    ids are fine: role_required and jwt_required short-circuit before
    any id is ever looked up or any file is read.
    """
    student_id, class_id, encoding_id = _dummy_ids()
    return [
        client.get(f"/api/students/{student_id}/face-encodings", headers=headers),
        client.post(
            f"/api/students/{student_id}/face-encodings",
            data={"image": _image_part()},
            headers=headers,
        ),
        client.delete(f"/api/students/{student_id}/face-encodings/{encoding_id}", headers=headers),
        client.delete(f"/api/students/{student_id}/face-encodings", headers=headers),
        client.get(f"/api/classes/{class_id}/face-enrollment", headers=headers),
    ]


# --- Register encoding: happy path ---------------------------------------------


class TestRegisterEncodingHappyPath:
    def test_single_face_image_returns_201_with_serialized_metadata(self, app_instance, monkeypatch):
        student = make_user(email="student1@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch)

            response = _register(client, str(student["_id"]), headers, source="camera")

        assert response.status_code == 201
        body = response.get_json()
        assert body["id"]
        assert body["student_id"] == str(student["_id"])
        assert body["model"] == "hog"
        assert body["source"] == "camera"
        assert isinstance(body["created_at"], str)

    def test_source_defaults_to_upload_when_omitted(self, app_instance, monkeypatch):
        student = make_user(email="student2@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch)

            response = _register(client, str(student["_id"]), headers)

        assert response.status_code == 201
        assert response.get_json()["source"] == "upload"

    def test_stored_document_has_128_doubles_a_real_bson_date_and_matching_created_by(
        self, app_instance, monkeypatch
    ):
        student = make_user(email="student3@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch)
            admin_id = _acting_user_id(client)

            response = _register(client, str(student["_id"]), headers)
            created = response.get_json()

        raw = fake_db[FACE_ENCODINGS].find_one({"_id": ObjectId(created["id"])})
        assert raw is not None
        assert len(raw["encoding"]) == 128
        assert all(isinstance(value, float) for value in raw["encoding"])
        assert isinstance(raw["created_at"], datetime)
        assert not isinstance(raw["created_at"], str)
        assert raw["created_by"] == ObjectId(admin_id)

    def test_stored_document_contains_no_image_or_photo_field(self, app_instance, monkeypatch):
        """06's Database changes section: the source image is never
        persisted -- there is no field for a photo, a thumbnail, or a
        file path on the stored document.
        """
        student = make_user(email="student4@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch)

            created = _register(client, str(student["_id"]), headers).get_json()

        raw = fake_db[FACE_ENCODINGS].find_one({"_id": ObjectId(created["id"])})
        assert set(raw.keys()) == {
            "_id", "student_id", "encoding", "model", "source", "created_at", "created_by",
        }

    def test_created_by_comes_from_jwt_identity_not_request_body(self, app_instance, monkeypatch):
        student = make_user(email="student5@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch)
            admin_id = _acting_user_id(client)

            spoofed_id = str(ObjectId())
            created = _register(
                client, str(student["_id"]), headers, extra_fields={"created_by": spoofed_id}
            ).get_json()

        raw = fake_db[FACE_ENCODINGS].find_one({"_id": ObjectId(created["id"])})
        assert raw["created_by"] == ObjectId(admin_id)
        assert raw["created_by"] != ObjectId(spoofed_id)

    def test_registering_a_second_sample_for_the_same_student_succeeds(self, app_instance, monkeypatch):
        student = make_user(email="student6@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch)

            first = _register(client, str(student["_id"]), headers)
            second = _register(client, str(student["_id"]), headers)

            listed = client.get(f"/api/students/{student['_id']}/face-encodings")

        assert first.status_code == 201
        assert second.status_code == 201
        assert len(listed.get_json()["encodings"]) == 2


# --- No response anywhere contains the encoding vector -------------------------


class TestEncodingVectorNeverLeaks:
    def test_create_list_status_and_both_deletes_never_expose_the_encoding_array(
        self, app_instance, monkeypatch
    ):
        student_a = make_user(email="leaka@college.test", password=FAKE_PASSWORD, role="student")
        student_b = make_user(email="leakb@college.test", password=FAKE_PASSWORD, role="student")
        klass = make_class(ObjectId(), name="Leak Class")
        enrollment = make_class_enrollment(klass["_id"], student_a["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[student_a, student_b],
                classes=[klass],
                class_enrollments=[enrollment],
            )
            _stub_recognition(monkeypatch)

            created = _register(client, str(student_a["_id"]), headers).get_json()
            listed = client.get(f"/api/students/{student_a['_id']}/face-encodings")
            status = client.get(f"/api/classes/{klass['_id']}/face-enrollment")
            deleted_one = client.delete(
                f"/api/students/{student_a['_id']}/face-encodings/{created['id']}", headers=headers
            )
            _register(client, str(student_a["_id"]), headers)
            deleted_all = client.delete(f"/api/students/{student_a['_id']}/face-encodings", headers=headers)

        for label, response in [
            ("create", created),
            ("list", listed.get_json()),
            ("status", status.get_json()),
            ("delete-one", deleted_one.get_json()),
            ("delete-all", deleted_all.get_json()),
        ]:
            _assert_no_encoding_leak(response)


# --- Register encoding: face-count validation -----------------------------------


class TestRegisterEncodingFaceCountValidation:
    def test_zero_face_image_returns_400_and_writes_nothing(self, app_instance, monkeypatch):
        student = make_user(email="noface@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch, encode_faces=lambda image_bytes: [])

            response = _register(client, str(student["_id"]), headers)
            listed = client.get(f"/api/students/{student['_id']}/face-encodings")

        assert response.status_code == 400
        assert listed.get_json()["encodings"] == []

    def test_two_face_image_returns_400_and_writes_nothing(self, app_instance, monkeypatch):
        student = make_user(email="twofaces@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(
                monkeypatch, encode_faces=lambda image_bytes: [list(VALID_ENCODING), list(VALID_ENCODING)]
            )

            response = _register(client, str(student["_id"]), headers)
            listed = client.get(f"/api/students/{student['_id']}/face-encodings")

        assert response.status_code == 400
        assert listed.get_json()["encodings"] == []


# --- Register encoding: cross-student duplicate match ---------------------------


class TestRegisterEncodingCrossStudentDuplicate:
    def test_face_matching_a_different_students_encoding_returns_409_and_stores_nothing(
        self, app_instance, monkeypatch
    ):
        student_a = make_user(email="dupa@college.test", password=FAKE_PASSWORD, role="student")
        student_b = make_user(email="dupb@college.test", password=FAKE_PASSWORD, role="student")
        existing = make_face_encoding(student_a["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[student_a, student_b],
                face_encodings=[existing],
            )
            _stub_recognition(monkeypatch, closest_match=lambda candidate, known: (0, 0.3))

            response = _register(client, str(student_b["_id"]), headers)
            listed_b = client.get(f"/api/students/{student_b['_id']}/face-encodings")

        assert response.status_code == 409
        assert listed_b.get_json()["encodings"] == []

    def test_a_distant_match_below_tolerance_does_not_block_registration(self, app_instance, monkeypatch):
        student_a = make_user(email="fara@college.test", password=FAKE_PASSWORD, role="student")
        student_b = make_user(email="farb@college.test", password=FAKE_PASSWORD, role="student")
        existing = make_face_encoding(student_a["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[student_a, student_b],
                face_encodings=[existing],
            )
            # A distance far beyond MATCH_TOLERANCE (0.6): a different
            # person's face, not a collision.
            _stub_recognition(monkeypatch, closest_match=lambda candidate, known: (0, 0.95))

            response = _register(client, str(student_b["_id"]), headers)

        assert response.status_code == 201


# --- Register encoding: sample cap -----------------------------------------------


class TestRegisterEncodingSampleCap:
    def test_registering_beyond_the_cap_returns_409_and_leaves_existing_samples_untouched(
        self, app_instance, monkeypatch
    ):
        student = make_user(email="capped@college.test", password=FAKE_PASSWORD, role="student")
        existing_samples = [make_face_encoding(student["_id"]) for _ in range(MAX_SAMPLES_PER_STUDENT)]
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[student], face_encodings=existing_samples
            )
            _stub_recognition(monkeypatch)

            response = _register(client, str(student["_id"]), headers)
            listed = client.get(f"/api/students/{student['_id']}/face-encodings")

        assert response.status_code == 409
        assert len(listed.get_json()["encodings"]) == MAX_SAMPLES_PER_STUDENT
        stored_ids = {sample["_id"] for sample in existing_samples}
        listed_ids = {encoding["id"] for encoding in listed.get_json()["encodings"]}
        assert listed_ids == {str(i) for i in stored_ids}


# --- Register encoding: upload validation ----------------------------------------


class TestRegisterEncodingUploadValidation:
    def test_missing_image_part_returns_400(self, app_instance, monkeypatch):
        student = make_user(email="noimage@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])

            response = _register(client, str(student["_id"]), headers, image=False, source="upload")

        assert response.status_code == 400

    def test_non_image_content_type_returns_400(self, app_instance, monkeypatch):
        student = make_user(email="wrongtype@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])

            response = _register(
                client, str(student["_id"]), headers,
                content_type="text/plain", filename="not-an-image.txt",
            )

        assert response.status_code == 400

    def test_corrupt_image_returns_400_not_a_500(self, app_instance, monkeypatch):
        student = make_user(email="corrupt@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch, encode_faces=_raise_decode_error)

            response = _register(client, str(student["_id"]), headers)

        assert response.status_code == 400

    def test_image_over_the_per_file_size_limit_but_under_the_request_limit_returns_400(
        self, app_instance, monkeypatch
    ):
        student = make_user(email="oversized@college.test", password=FAKE_PASSWORD, role="student")
        oversized = b"x" * (MAX_IMAGE_BYTES + 1024)
        assert len(oversized) < MAX_REQUEST_BYTES
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])

            response = _register(client, str(student["_id"]), headers, image_bytes=oversized)

        assert response.status_code == 400

    def test_upload_over_the_request_size_limit_returns_413_as_json(self, app_instance, monkeypatch):
        student = make_user(email="toolarge@college.test", password=FAKE_PASSWORD, role="student")
        too_large = b"x" * (MAX_REQUEST_BYTES + 1024 * 200)
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])

            response = _register(client, str(student["_id"]), headers, image_bytes=too_large)

        assert response.status_code == 413
        body_text = response.get_data(as_text=True)
        assert "<html" not in body_text.lower()
        assert "error" in response.get_json()

    @pytest.mark.parametrize(
        "bad_source",
        ["not-a-real-source", "Upload"],
        ids=["unknown-source-value", "wrong-case-is-not-normalized"],
    )
    def test_invalid_source_value_returns_400(self, app_instance, monkeypatch, bad_source):
        # A multipart form field is always transmitted as text, so only
        # string values are reachable here -- require_source's "must be a
        # string" branch is exercised at the unit level, not over HTTP.
        student = make_user(email="badsource@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch)

            response = _register(client, str(student["_id"]), headers, source=bad_source)

        assert response.status_code == 400


# --- Register encoding: student-id reference validation -------------------------


class TestRegisterEncodingStudentReferenceValidation:
    def test_student_id_referencing_faculty_returns_400(self, app_instance, monkeypatch):
        faculty = make_user(email="notstudent1@college.test", password=FAKE_PASSWORD, role="faculty")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[faculty])
            _stub_recognition(monkeypatch)

            response = _register(client, str(faculty["_id"]), headers)

        assert response.status_code == 400

    def test_student_id_referencing_admin_returns_400(self, app_instance, monkeypatch):
        other_admin = make_user(email="notstudent2@college.test", password=FAKE_PASSWORD, role="admin")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[other_admin])
            _stub_recognition(monkeypatch)

            response = _register(client, str(other_admin["_id"]), headers)

        assert response.status_code == 400

    def test_inactive_student_id_returns_400(self, app_instance, monkeypatch):
        inactive_student = make_user(
            email="inactivestudent@college.test", password=FAKE_PASSWORD, role="student", is_active=False
        )
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[inactive_student])
            _stub_recognition(monkeypatch)

            response = _register(client, str(inactive_student["_id"]), headers)

        assert response.status_code == 400

    def test_nonexistent_student_id_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            _stub_recognition(monkeypatch)

            response = _register(client, str(ObjectId()), headers)

        assert response.status_code == 404

    def test_malformed_student_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            _stub_recognition(monkeypatch)

            response = _register(client, "not-a-valid-id", headers)

        assert response.status_code == 400


# --- Recognition library unavailable ---------------------------------------------


class TestRecognitionUnavailable:
    def test_register_returns_503_and_writes_nothing_when_recognition_is_unavailable(
        self, app_instance, monkeypatch
    ):
        student = make_user(email="unavailable@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch, available=False)

            response = _register(client, str(student["_id"]), headers)
            listed = client.get(f"/api/students/{student['_id']}/face-encodings")

        assert response.status_code == 503
        assert "error" in response.get_json()
        assert listed.get_json()["encodings"] == []

    def test_health_endpoint_still_responds_when_recognition_is_unavailable(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})
        monkeypatch.setattr("recognition.encoder.is_available", lambda: False)

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        assert response.status_code == 200


# --- List encodings ---------------------------------------------------------------


class TestListEncodings:
    def test_list_is_keyed_under_encodings(self, app_instance, monkeypatch):
        student = make_user(email="listkey@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])

            response = client.get(f"/api/students/{student['_id']}/face-encodings")

        assert response.status_code == 200
        assert "encodings" in response.get_json()

    def test_list_for_student_with_no_samples_returns_empty_list(self, app_instance, monkeypatch):
        student = make_user(email="empty@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])

            response = client.get(f"/api/students/{student['_id']}/face-encodings")

        assert response.get_json()["encodings"] == []

    def test_list_returns_only_that_students_samples_newest_first_with_no_cross_student_leakage(
        self, app_instance, monkeypatch
    ):
        student_a = make_user(email="orderA@college.test", password=FAKE_PASSWORD, role="student")
        student_b = make_user(email="orderB@college.test", password=FAKE_PASSWORD, role="student")
        base = datetime.now(timezone.utc)
        oldest = make_face_encoding(student_a["_id"], created_at=base - timedelta(minutes=10))
        middle = make_face_encoding(student_a["_id"], created_at=base - timedelta(minutes=5))
        newest = make_face_encoding(student_a["_id"], created_at=base)
        other_students_sample = make_face_encoding(student_b["_id"], created_at=base)
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[student_a, student_b],
                face_encodings=[oldest, middle, newest, other_students_sample],
            )

            response = client.get(f"/api/students/{student_a['_id']}/face-encodings")

        listed_ids = [item["id"] for item in response.get_json()["encodings"]]
        assert listed_ids == [str(newest["_id"]), str(middle["_id"]), str(oldest["_id"])]

    def test_list_for_nonexistent_student_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get(f"/api/students/{ObjectId()}/face-encodings")

        assert response.status_code == 404

    def test_list_with_malformed_student_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get("/api/students/not-a-valid-id/face-encodings")

        assert response.status_code == 400

    def test_list_still_works_for_a_deactivated_student(self, app_instance, monkeypatch):
        """Deactivating a user does not delete their encodings, and the
        admin must still be able to see -- and later erase -- them.
        """
        inactive_student = make_user(
            email="deactivatedlist@college.test", password=FAKE_PASSWORD, role="student", is_active=False
        )
        sample = make_face_encoding(inactive_student["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[inactive_student], face_encodings=[sample]
            )

            response = client.get(f"/api/students/{inactive_student['_id']}/face-encodings")

        assert response.status_code == 200
        assert len(response.get_json()["encodings"]) == 1


# --- Delete one encoding -----------------------------------------------------------


class TestDeleteOneEncoding:
    def test_delete_removes_one_sample_with_200_then_repeat_returns_404(self, app_instance, monkeypatch):
        student = make_user(email="deleteone@college.test", password=FAKE_PASSWORD, role="student")
        sample = make_face_encoding(student["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[student], face_encodings=[sample]
            )

            first = client.delete(
                f"/api/students/{student['_id']}/face-encodings/{sample['_id']}", headers=headers
            )
            second = client.delete(
                f"/api/students/{student['_id']}/face-encodings/{sample['_id']}", headers=headers
            )

        assert first.status_code == 200
        assert first.get_json() == {"deleted": True}
        assert second.status_code == 404

    def test_delete_does_not_remove_a_different_students_sample(self, app_instance, monkeypatch):
        student_a = make_user(email="scopeA@college.test", password=FAKE_PASSWORD, role="student")
        student_b = make_user(email="scopeB@college.test", password=FAKE_PASSWORD, role="student")
        sample_a = make_face_encoding(student_a["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[student_a, student_b],
                face_encodings=[sample_a],
            )

            response = client.delete(
                f"/api/students/{student_b['_id']}/face-encodings/{sample_a['_id']}", headers=headers
            )
            still_there = client.get(f"/api/students/{student_a['_id']}/face-encodings")

        assert response.status_code == 404
        assert len(still_there.get_json()["encodings"]) == 1

    def test_delete_nonexistent_encoding_returns_404(self, app_instance, monkeypatch):
        student = make_user(email="ghostencoding@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])

            response = client.delete(
                f"/api/students/{student['_id']}/face-encodings/{ObjectId()}", headers=headers
            )

        assert response.status_code == 404

    def test_delete_with_malformed_encoding_id_returns_400(self, app_instance, monkeypatch):
        student = make_user(email="malformedenc@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])

            response = client.delete(
                f"/api/students/{student['_id']}/face-encodings/not-a-valid-id", headers=headers
            )

        assert response.status_code == 400

    def test_delete_with_malformed_student_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.delete(
                f"/api/students/not-a-valid-id/face-encodings/{ObjectId()}", headers=headers
            )

        assert response.status_code == 400


# --- Delete all encodings ------------------------------------------------------------


class TestDeleteAllEncodings:
    def test_delete_all_removes_every_sample_and_reports_the_count(self, app_instance, monkeypatch):
        student = make_user(email="deleteall@college.test", password=FAKE_PASSWORD, role="student")
        samples = [make_face_encoding(student["_id"]) for _ in range(3)]
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[student], face_encodings=samples
            )

            response = client.delete(f"/api/students/{student['_id']}/face-encodings", headers=headers)
            listed_after = client.get(f"/api/students/{student['_id']}/face-encodings")

        assert response.status_code == 200
        assert response.get_json() == {"deleted": 3}
        assert listed_after.get_json()["encodings"] == []

    def test_delete_all_for_student_with_no_samples_reports_zero(self, app_instance, monkeypatch):
        student = make_user(email="deleteallzero@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])

            response = client.delete(f"/api/students/{student['_id']}/face-encodings", headers=headers)

        assert response.status_code == 200
        assert response.get_json() == {"deleted": 0}

    def test_delete_all_does_not_affect_other_students_samples(self, app_instance, monkeypatch):
        student_a = make_user(email="bulkA@college.test", password=FAKE_PASSWORD, role="student")
        student_b = make_user(email="bulkB@college.test", password=FAKE_PASSWORD, role="student")
        samples_a = [make_face_encoding(student_a["_id"]) for _ in range(2)]
        samples_b = [make_face_encoding(student_b["_id"]) for _ in range(2)]
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[student_a, student_b],
                face_encodings=samples_a + samples_b,
            )

            client.delete(f"/api/students/{student_a['_id']}/face-encodings", headers=headers)
            remaining_b = client.get(f"/api/students/{student_b['_id']}/face-encodings")

        assert len(remaining_b.get_json()["encodings"]) == 2

    def test_delete_all_for_nonexistent_student_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.delete(f"/api/students/{ObjectId()}/face-encodings", headers=headers)

        assert response.status_code == 404


# --- Class face-enrollment status ------------------------------------------------------


class TestClassFaceEnrollmentStatus:
    def test_status_is_keyed_under_students(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Status Key Class")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", classes=[klass])

            response = client.get(f"/api/classes/{klass['_id']}/face-enrollment")

        assert response.status_code == 200
        assert "students" in response.get_json()

    def test_status_includes_zero_sample_students_with_accurate_counts_and_timestamps(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Mixed Roster Class")
        enrolled_student = make_user(email="enrolled1@college.test", password=FAKE_PASSWORD, role="student")
        unregistered_student = make_user(
            email="unregistered1@college.test", password=FAKE_PASSWORD, role="student"
        )
        enrollment_1 = make_class_enrollment(klass["_id"], enrolled_student["_id"])
        enrollment_2 = make_class_enrollment(klass["_id"], unregistered_student["_id"])
        base = datetime.now(timezone.utc)
        older_sample = make_face_encoding(enrolled_student["_id"], created_at=base - timedelta(minutes=1))
        newest_sample = make_face_encoding(enrolled_student["_id"], created_at=base)
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[enrolled_student, unregistered_student],
                classes=[klass],
                class_enrollments=[enrollment_1, enrollment_2],
                face_encodings=[older_sample, newest_sample],
            )

            response = client.get(f"/api/classes/{klass['_id']}/face-enrollment")

        entries = {entry["student"]["id"]: entry for entry in response.get_json()["students"]}
        assert entries[str(enrolled_student["_id"])]["sample_count"] == 2
        assert entries[str(enrolled_student["_id"])]["last_enrolled_at"] is not None
        assert entries[str(unregistered_student["_id"])]["sample_count"] == 0
        assert entries[str(unregistered_student["_id"])]["last_enrolled_at"] is None

    def test_status_has_no_cross_class_leakage(self, app_instance, monkeypatch):
        class_a = make_class(ObjectId(), name="Class A")
        class_b = make_class(ObjectId(), name="Class B")
        student_a = make_user(email="classA@college.test", password=FAKE_PASSWORD, role="student")
        student_b = make_user(email="classB@college.test", password=FAKE_PASSWORD, role="student")
        enrollment_a = make_class_enrollment(class_a["_id"], student_a["_id"])
        enrollment_b = make_class_enrollment(class_b["_id"], student_b["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[student_a, student_b],
                classes=[class_a, class_b],
                class_enrollments=[enrollment_a, enrollment_b],
            )

            response_a = client.get(f"/api/classes/{class_a['_id']}/face-enrollment")
            response_b = client.get(f"/api/classes/{class_b['_id']}/face-enrollment")

        students_a = {entry["student"]["id"] for entry in response_a.get_json()["students"]}
        students_b = {entry["student"]["id"] for entry in response_b.get_json()["students"]}
        assert students_a == {str(student_a["_id"])}
        assert students_b == {str(student_b["_id"])}

    def test_status_for_nonexistent_class_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get(f"/api/classes/{ObjectId()}/face-enrollment")

        assert response.status_code == 404

    def test_status_with_malformed_class_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get("/api/classes/not-a-valid-id/face-enrollment")

        assert response.status_code == 400


# --- Role enforcement -------------------------------------------------------------------


class TestRoleEnforcement:
    def test_faculty_receives_403_on_all_five_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            faculty = make_user(email="facultyrole@college.test", password=FAKE_PASSWORD, role="faculty")
            fake_db = make_fake_faces_db(users=[faculty])
            _patch_db(monkeypatch, fake_db)
            _login(client, "facultyrole@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            responses = _all_five_endpoint_requests(client, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_student_receives_403_on_all_five_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            student = make_user(email="studentrole@college.test", password=FAKE_PASSWORD, role="student")
            fake_db = make_fake_faces_db(users=[student])
            _patch_db(monkeypatch, fake_db)
            _login(client, "studentrole@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            responses = _all_five_endpoint_requests(client, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_unauthenticated_receives_401_on_all_five_endpoints(self, app_instance, monkeypatch):
        fake_db = make_fake_faces_db()
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            responses = _all_five_endpoint_requests(client, headers={})

        assert all(r.status_code == 401 for r in responses), [r.status_code for r in responses]

    def test_admin_is_permitted_on_all_five_read_and_write_endpoints(self, app_instance, monkeypatch):
        student = make_user(email="adminok@college.test", password=FAKE_PASSWORD, role="student")
        klass = make_class(ObjectId(), name="Admin OK Class")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student], classes=[klass])
            _stub_recognition(monkeypatch)

            list_before = client.get(f"/api/students/{student['_id']}/face-encodings", headers=headers)
            created = _register(client, str(student["_id"]), headers)
            status = client.get(f"/api/classes/{klass['_id']}/face-enrollment", headers=headers)
            deleted_one = client.delete(
                f"/api/students/{student['_id']}/face-encodings/{created.get_json()['id']}", headers=headers
            )
            deleted_all = client.delete(f"/api/students/{student['_id']}/face-encodings", headers=headers)

        assert list_before.status_code == 200
        assert created.status_code == 201
        assert status.status_code == 200
        assert deleted_one.status_code == 200
        assert deleted_all.status_code == 200


# --- CSRF enforcement on mutating endpoints -----------------------------------------------


class TestCsrfEnforcementOnMutatingEndpoints:
    def test_create_encoding_without_a_csrf_header_is_rejected(self, app_instance, monkeypatch):
        student = make_user(email="csrfface1@college.test", password=FAKE_PASSWORD, role="student")
        admin = make_user(email="csrfadmin1@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_faces_db(users=[admin, student])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "csrfadmin1@college.test", FAKE_PASSWORD)
            _stub_recognition(monkeypatch)

            response = _register(client, str(student["_id"]), headers={})  # no X-CSRF-TOKEN header

        assert response.status_code == 401

    def test_create_encoding_with_an_incorrect_csrf_header_is_rejected(self, app_instance, monkeypatch):
        student = make_user(email="csrfface2@college.test", password=FAKE_PASSWORD, role="student")
        admin = make_user(email="csrfadmin2@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_faces_db(users=[admin, student])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "csrfadmin2@college.test", FAKE_PASSWORD)
            _stub_recognition(monkeypatch)

            response = _register(
                client, str(student["_id"]), headers={"X-CSRF-TOKEN": "not-the-real-token"}
            )

        assert response.status_code == 401

    def test_create_encoding_with_the_correct_csrf_header_is_accepted(self, app_instance, monkeypatch):
        student = make_user(email="csrfface3@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])
            _stub_recognition(monkeypatch)

            response = _register(client, str(student["_id"]), headers)

        assert response.status_code == 201

    def test_delete_encoding_without_a_csrf_header_is_rejected(self, app_instance, monkeypatch):
        student = make_user(email="csrfdelete@college.test", password=FAKE_PASSWORD, role="student")
        sample = make_face_encoding(student["_id"])
        admin = make_user(email="csrfadmin4@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_faces_db(users=[admin, student], face_encodings=[sample])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "csrfadmin4@college.test", FAKE_PASSWORD)

            response = client.delete(
                f"/api/students/{student['_id']}/face-encodings/{sample['_id']}"
            )  # no X-CSRF-TOKEN header

        assert response.status_code == 401
