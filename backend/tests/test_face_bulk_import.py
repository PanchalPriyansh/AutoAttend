"""Tests for backend/recognition/bulk_import.py (the orchestration) and
`POST /api/classes/<class_id>/face-enrollment/import` in
backend/routes/faces.py (the endpoint).

Spec contract under test (22-bulk-face-enrollment-import.md, "APIs" +
"Rules for implementation" + "Definition of done" -- backend items):

  - Admin-only: faculty, student, and unauthenticated callers are
    rejected and nothing is written; a mutating request without a valid
    CSRF header is rejected the same way every other mutating endpoint
    in this blueprint is.
  - An unknown `class_id` is 404 and a malformed one is 400, both before
    any image is decoded and both writing nothing.
  - No `images` part is 400; more than `MAX_IMPORT_FILES` parts is 400
    naming the cap; a disallowed content type or an over-`MAX_IMAGE_BYTES`
    part is 400 through the existing `require_image` -- and in every one
    of those cases, no part of that same request is registered, even a
    perfectly good one sent alongside the bad one.
  - `MAX_REQUEST_BYTES` (`MAX_CONTENT_LENGTH`) is not raised to fit a
    full batch, and a body that exceeds it is refused as a 413 by
    Werkzeug regardless of how the per-file/per-batch checks would have
    resolved it.
  - `face_recognition` unavailable is a 503 with a JSON error, checked
    once before any file's bytes ever reach the encoder.
  - The server -- never the client -- resolves which student a file
    belongs to: `registered`/`no_match`/`ambiguous`/`rejected` all appear
    in one report, in submitted order, with `summary` counts matching.
  - Every per-file domain rule (`register_encoding`, unwrapped) still
    applies: a zero-face or multi-face photo, a student already at
    `MAX_SAMPLES_PER_STUDENT`, and a face matching *another* student's
    sample -- including one inserted earlier in the very same batch --
    are each a `rejected` row, and the rest of the batch still runs.
  - A stored document is identical in shape to one written by the
    single-upload endpoint, with `source` always `"upload"` (a
    client-supplied `source` field is ignored) and `created_by` the
    acting admin.
  - No response, on any status, contains an `encoding` field or any part
    of the vector.
  - A request where every file fails still returns 200 with a complete
    report.
  - A database error during the import surfaces as the blueprint's 500
    JSON error, never flattened into a per-file `rejected` row.

Matching itself (student ID, the full-email fallback, case-
insensitivity, no suffix stripping, ambiguity) is the pure `recognition/filenames.py`
contract and is exhaustively covered by test_face_filenames.py; the
tests here exercise it only enough to prove the endpoint is wired to it
correctly and reports the outcome, not to re-derive the matching rule.

Nothing here depends on `face_recognition`/`dlib`/`numpy` being
installed: `recognition.encoder.is_available` / `.encode_faces` /
`.closest_match` are monkeypatched at the point `recognition/service.py`
calls them, exactly as test_face_routes.py does for the single-upload
endpoint. All "images" used are plain synthetic bytes, never a real
photograph, and all "encodings" are short, obviously-fake lists of
floats -- no real biometric data is involved anywhere in this file.

No mongomock and no real test database: `routes.auth.get_db` and
`routes.faces.get_db` are monkeypatched to the same in-memory fake db
(faces_test_helpers.py) so a real `/api/auth/login` call mints genuine
JWT + CSRF cookies while the endpoint under test reads/writes fake
collections.
"""

from datetime import datetime

from bson import ObjectId
from faces_test_helpers import (
    bulk_image_parts,
    make_class,
    make_class_enrollment,
    make_face_encoding,
    make_fake_faces_db,
    make_user,
)
from pymongo.errors import PyMongoError

from database.schema import FACE_ENCODINGS
from recognition.service import MAX_SAMPLES_PER_STUDENT
from recognition.validators import (
    MAX_IMAGE_BYTES,
    MAX_IMPORT_FILES,
    MAX_REQUEST_BYTES,
    MAX_VIDEO_BYTES,
)

FAKE_PASSWORD = "a-fake-test-password-1"

# Plain synthetic bytes -- never a real image, and decoding is always
# monkeypatched, so the actual content is irrelevant except where a test
# deliberately varies it to steer the stubbed encoder.
FAKE_IMAGE_BYTES = b"synthetic-test-bytes-not-a-real-photograph"

# A short, obviously-fake 128-length vector -- never a real
# face_recognition descriptor derived from an actual photograph.
VALID_ENCODING = [round(0.001 * i, 6) for i in range(128)]


def _patch_db(monkeypatch, fake_db):
    """Both blueprints must see the same fake db: login goes through
    routes.auth.get_db, and the import endpoint under test goes through
    routes.faces.get_db.
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
    collections passed in), patches both blueprints' get_db, logs in, and
    returns (fake_db, csrf_headers).
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

    Defaults to "happy path": recognition available, one valid synthetic
    encoding detected, no collision with another student. Copied from
    test_face_routes.py's helper of the same name/shape, kept local to
    this file rather than imported so this module has no dependency on
    another test module's internals.
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


def _file(filename, data=FAKE_IMAGE_BYTES, content_type="image/jpeg"):
    """One `(filename, data, content_type)` triple, as `bulk_image_parts`
    (faces_test_helpers.py) expects.
    """
    return (filename, data, content_type)


def _import(client, class_id, headers, files, *, extra_fields=None):
    """POST the bulk-import endpoint. `files` is a list of
    (filename, data, content_type) triples built by `_file`.
    """
    form = {"images": bulk_image_parts(files)}
    if extra_fields:
        form.update(extra_fields)
    return client.post(
        f"/api/classes/{class_id}/face-enrollment/import", data=form, headers=headers
    )


def _assert_no_encoding_leak(payload):
    """Recursively asserts no dict anywhere in `payload` has the literal
    key "encoding". Matches test_face_routes.py's helper of the same
    name/behaviour.
    """
    if isinstance(payload, dict):
        assert "encoding" not in payload, f"encoding leaked in {payload}"
        for value in payload.values():
            _assert_no_encoding_leak(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_encoding_leak(item)


# --- Authorization -----------------------------------------------------------


class TestBulkImportAuthorization:
    def test_faculty_gets_403_and_nothing_is_written(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Auth Faculty Class")
        student = make_user(email="authfacstudent@college.test", password=FAKE_PASSWORD, role="student")
        faculty = make_user(email="authfaculty@college.test", password=FAKE_PASSWORD, role="faculty")
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        fake_db = make_fake_faces_db(
            users=[faculty, student], classes=[klass], class_enrollments=[enrollment]
        )
        _patch_db(monkeypatch, fake_db)
        with app_instance.test_client() as client:
            _login(client, "authfaculty@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)
            _stub_recognition(monkeypatch)

            response = _import(
                client, klass["_id"], headers, [_file("authfacstudent@college.test.jpg")]
            )

        assert response.status_code == 403
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0

    def test_student_gets_403_and_nothing_is_written(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Auth Student Class")
        student = make_user(email="authstudentrole@college.test", password=FAKE_PASSWORD, role="student")
        fake_db = make_fake_faces_db(users=[student], classes=[klass])
        _patch_db(monkeypatch, fake_db)
        with app_instance.test_client() as client:
            _login(client, "authstudentrole@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)
            _stub_recognition(monkeypatch)

            response = _import(client, klass["_id"], headers, [_file("someone@college.test.jpg")])

        assert response.status_code == 403
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0

    def test_unauthenticated_gets_401_and_nothing_is_written(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Auth Unauthenticated Class")
        fake_db = make_fake_faces_db(classes=[klass])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            response = _import(client, klass["_id"], headers={}, files=[_file("someone@college.test.jpg")])

        assert response.status_code == 401
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0

    def test_missing_csrf_header_is_rejected_and_nothing_is_written(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Auth CSRF Class")
        admin = make_user(email="csrfimportadmin@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_faces_db(users=[admin], classes=[klass])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "csrfimportadmin@college.test", FAKE_PASSWORD)
            _stub_recognition(monkeypatch)

            response = _import(client, klass["_id"], headers={}, files=[_file("someone@college.test.jpg")])

        assert response.status_code == 401
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0

    def test_admin_with_valid_csrf_is_permitted(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Auth Admin OK Class")
        student = make_user(email="authadminok@college.test", password=FAKE_PASSWORD, role="student")
        enrollment = make_class_enrollment(klass["_id"], student["_id"])
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[student],
                classes=[klass], class_enrollments=[enrollment],
            )
            _stub_recognition(monkeypatch)

            response = _import(client, klass["_id"], headers, [_file("authadminok@college.test.jpg")])

        assert response.status_code == 200
        assert response.get_json()["results"][0]["status"] == "registered"


# --- class_id validation ------------------------------------------------------


class TestClassIdValidation:
    def test_unknown_class_id_returns_404_and_nothing_is_written(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin")
            _stub_recognition(monkeypatch)

            response = _import(client, str(ObjectId()), headers, [_file("someone@college.test.jpg")])

        assert response.status_code == 404
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0

    def test_malformed_class_id_returns_400_and_nothing_is_written(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin")
            _stub_recognition(monkeypatch)

            response = _import(client, "not-a-valid-id", headers, [_file("someone@college.test.jpg")])

        assert response.status_code == 400
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0


# --- Batch-level file validation ----------------------------------------------


class TestBulkImportFileValidation:
    def test_no_images_part_returns_400_and_nothing_is_written(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="No Images Class")
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin", classes=[klass])
            _stub_recognition(monkeypatch)

            response = client.post(
                f"/api/classes/{klass['_id']}/face-enrollment/import", data={}, headers=headers
            )

        assert response.status_code == 400
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0

    def test_more_files_than_the_cap_returns_400_naming_the_cap(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Too Many Files Class")
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin", classes=[klass])
            _stub_recognition(monkeypatch)

            files = [_file(f"student{i}@college.test.jpg") for i in range(MAX_IMPORT_FILES + 1)]
            response = _import(client, klass["_id"], headers, files)

        assert response.status_code == 400
        assert str(MAX_IMPORT_FILES) in response.get_json()["error"]
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0

    def test_exactly_the_cap_is_not_rejected_by_the_count_check(self, app_instance, monkeypatch):
        """The boundary case for the cap named above: MAX_IMPORT_FILES
        itself must be accepted, so the 400 in the test above is really
        about the +1 and not an off-by-one in the check.
        """
        klass = make_class(ObjectId(), name="Exactly The Cap Class")
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin", classes=[klass])
            _stub_recognition(monkeypatch)

            files = [_file(f"student{i}@college.test.jpg") for i in range(MAX_IMPORT_FILES)]
            response = _import(client, klass["_id"], headers, files)

        assert response.status_code == 200
        assert len(response.get_json()["results"]) == MAX_IMPORT_FILES

    def test_a_disallowed_content_type_part_rejects_the_whole_request(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Bad Content Type Class")
        aarti = make_user(email="aarti.badtype@example.edu", password=FAKE_PASSWORD, role="student")
        enrollment = make_class_enrollment(klass["_id"], aarti["_id"])
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[aarti], classes=[klass], class_enrollments=[enrollment],
            )
            _stub_recognition(monkeypatch)

            files = [
                _file("aarti.badtype@example.edu.jpg"),
                _file("not-an-image.txt", content_type="text/plain"),
            ]
            response = _import(client, klass["_id"], headers, files)

        assert response.status_code == 400
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0

    def test_an_oversized_part_rejects_the_whole_request(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Oversized Class")
        aarti = make_user(email="aarti.oversized@example.edu", password=FAKE_PASSWORD, role="student")
        enrollment = make_class_enrollment(klass["_id"], aarti["_id"])
        oversized = b"x" * (MAX_IMAGE_BYTES + 1024)
        assert len(oversized) < MAX_REQUEST_BYTES
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[aarti], classes=[klass], class_enrollments=[enrollment],
            )
            _stub_recognition(monkeypatch)

            files = [
                _file("aarti.oversized@example.edu.jpg"),
                _file("huge.jpg", data=oversized),
            ]
            response = _import(client, klass["_id"], headers, files)

        assert response.status_code == 400
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0


# --- The request ceiling is not raised for this feature -----------------------


class TestRequestSizeCeilingUnchanged:
    def test_max_request_bytes_is_not_raised_to_fit_a_full_batch(self):
        """Rule 12 / Definition of done #5: MAX_CONTENT_LENGTH stays at
        MAX_REQUEST_BYTES, and MAX_IMPORT_FILES x MAX_IMAGE_BYTES
        deliberately exceeds it -- so the value here must be exactly what
        06/07's video-sized formula already produced, not something
        widened to fit ten maximal photos.
        """
        assert MAX_REQUEST_BYTES == MAX_VIDEO_BYTES + 1024 * 1024
        assert MAX_IMPORT_FILES * MAX_IMAGE_BYTES > MAX_REQUEST_BYTES

    def test_a_batch_whose_total_size_exceeds_the_request_ceiling_is_refused_as_413(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Ceiling Class")
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin", classes=[klass])
            _stub_recognition(monkeypatch)

            # Each part is comfortably under MAX_IMAGE_BYTES and the count
            # is under MAX_IMPORT_FILES, so neither per-file check nor the
            # count cap is what fires here -- only the cumulative body
            # size, which Werkzeug refuses before the route runs at all.
            four_mb = 4 * 1024 * 1024
            files = [_file(f"student{i}@college.test.jpg", data=b"x" * four_mb) for i in range(8)]
            assert four_mb < MAX_IMAGE_BYTES
            assert len(files) < MAX_IMPORT_FILES
            assert four_mb * len(files) > MAX_REQUEST_BYTES

            response = _import(client, klass["_id"], headers, files)

        assert response.status_code == 413
        assert "error" in response.get_json()
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0


# --- Recognition library unavailable ------------------------------------------


class TestRecognitionUnavailable:
    def test_returns_503_and_decodes_nothing_when_recognition_is_unavailable(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Unavailable Class")
        aarti = make_user(email="aarti.unavail@example.edu", password=FAKE_PASSWORD, role="student")
        enrollment = make_class_enrollment(klass["_id"], aarti["_id"])

        def _fail_if_called(image_bytes):
            raise AssertionError("encode_faces must not be called when recognition is unavailable")

        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[aarti], classes=[klass], class_enrollments=[enrollment],
            )
            _stub_recognition(monkeypatch, available=False, encode_faces=_fail_if_called)

            response = _import(client, klass["_id"], headers, [_file("aarti.unavail@example.edu.jpg")])

        assert response.status_code == 503
        assert "error" in response.get_json()
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0


# --- The full report: registered / no_match / ambiguous / rejected together ---


class TestBulkImportReportShape:
    def test_all_four_statuses_appear_in_one_report_in_submitted_order(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Report Shape Class")
        aarti = make_user(
            email="aarti.desai@example.edu", password=FAKE_PASSWORD, role="student", name="Aarti Desai"
        )
        rohit_a = make_user(email="rohit@abc.edu", password=FAKE_PASSWORD, role="student", name="Rohit A")
        rohit_b = make_user(email="rohit@xyz.edu", password=FAKE_PASSWORD, role="student", name="Rohit B")
        sneha = make_user(
            email="sneha.patel@example.edu", password=FAKE_PASSWORD, role="student", name="Sneha Patel"
        )
        enrollments = [
            make_class_enrollment(klass["_id"], student["_id"])
            for student in (aarti, rohit_a, rohit_b, sneha)
        ]

        def encode_faces(image_bytes):
            return [] if image_bytes == b"blank" else [list(VALID_ENCODING)]

        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[aarti, rohit_a, rohit_b, sneha],
                classes=[klass], class_enrollments=enrollments,
            )
            _stub_recognition(monkeypatch, encode_faces=encode_faces)

            files = [
                _file("aarti.desai@example.edu.jpg"),
                _file("group-photo.jpg"),
                _file("rohit.jpg"),
                _file("sneha.patel@example.edu.jpg", data=b"blank"),
            ]
            response = _import(client, klass["_id"], headers, files)

        assert response.status_code == 200
        body = response.get_json()
        results = body["results"]

        assert [r["filename"] for r in results] == [f[0] for f in files]

        registered, no_match, ambiguous, rejected = results
        assert registered["status"] == "registered"
        assert registered["student"]["email"] == "aarti.desai@example.edu"
        assert registered["message"] is None

        assert no_match["status"] == "no_match"
        assert no_match["student"] is None
        assert no_match["message"]

        assert ambiguous["status"] == "ambiguous"
        assert ambiguous["student"] is None
        assert "2" in ambiguous["message"]

        assert rejected["status"] == "rejected"
        assert rejected["student"]["email"] == "sneha.patel@example.edu"
        assert rejected["message"]

        assert body["summary"] == {
            "submitted": 4,
            "registered": 1,
            "no_match": 1,
            "ambiguous": 1,
            "rejected": 1,
        }


# --- Per-file outcomes: face-count rejection -----------------------------------


class TestPerFileFaceCountRejection:
    def test_zero_face_photo_is_rejected_for_the_matched_student_while_others_still_register(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Zero Face Class")
        aarti = make_user(email="aarti.zeroface@example.edu", password=FAKE_PASSWORD, role="student")
        sneha = make_user(email="sneha.zeroface@example.edu", password=FAKE_PASSWORD, role="student")
        enrollments = [
            make_class_enrollment(klass["_id"], aarti["_id"]),
            make_class_enrollment(klass["_id"], sneha["_id"]),
        ]

        def encode_faces(image_bytes):
            return [] if image_bytes == b"blank" else [list(VALID_ENCODING)]

        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[aarti, sneha], classes=[klass], class_enrollments=enrollments,
            )
            _stub_recognition(monkeypatch, encode_faces=encode_faces)

            files = [
                _file("aarti.zeroface@example.edu.jpg", data=b"blank"),
                _file("sneha.zeroface@example.edu.jpg"),
            ]
            response = _import(client, klass["_id"], headers, files)

        results = response.get_json()["results"]
        assert results[0]["status"] == "rejected"
        assert results[0]["student"]["email"] == "aarti.zeroface@example.edu"
        assert "No face was detected" in results[0]["message"]
        assert results[1]["status"] == "registered"
        assert fake_db[FACE_ENCODINGS].count_documents({"student_id": sneha["_id"]}) == 1
        assert fake_db[FACE_ENCODINGS].count_documents({"student_id": aarti["_id"]}) == 0

    def test_multi_face_photo_is_rejected_for_the_matched_student_while_others_still_register(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Multi Face Class")
        aarti = make_user(email="aarti.multiface@example.edu", password=FAKE_PASSWORD, role="student")
        sneha = make_user(email="sneha.multiface@example.edu", password=FAKE_PASSWORD, role="student")
        enrollments = [
            make_class_enrollment(klass["_id"], aarti["_id"]),
            make_class_enrollment(klass["_id"], sneha["_id"]),
        ]

        def encode_faces(image_bytes):
            if image_bytes == b"group":
                return [list(VALID_ENCODING), list(VALID_ENCODING)]
            return [list(VALID_ENCODING)]

        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[aarti, sneha], classes=[klass], class_enrollments=enrollments,
            )
            _stub_recognition(monkeypatch, encode_faces=encode_faces)

            files = [
                _file("aarti.multiface@example.edu.jpg", data=b"group"),
                _file("sneha.multiface@example.edu.jpg"),
            ]
            response = _import(client, klass["_id"], headers, files)

        results = response.get_json()["results"]
        assert results[0]["status"] == "rejected"
        assert results[0]["student"]["email"] == "aarti.multiface@example.edu"
        assert "faces were detected" in results[0]["message"]
        assert results[1]["status"] == "registered"
        assert fake_db[FACE_ENCODINGS].count_documents({"student_id": aarti["_id"]}) == 0


# --- Per-file outcomes: the per-student sample cap -----------------------------


class TestPerFileSampleCap:
    def test_student_already_at_the_cap_is_rejected_and_existing_samples_are_untouched(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Cap Class")
        capped = make_user(email="capped.import@example.edu", password=FAKE_PASSWORD, role="student")
        existing = [make_face_encoding(capped["_id"]) for _ in range(MAX_SAMPLES_PER_STUDENT)]
        enrollment = make_class_enrollment(klass["_id"], capped["_id"])
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[capped], classes=[klass], class_enrollments=[enrollment],
                face_encodings=existing,
            )
            _stub_recognition(monkeypatch)

            response = _import(client, klass["_id"], headers, [_file("capped.import@example.edu.jpg")])

        results = response.get_json()["results"]
        assert results[0]["status"] == "rejected"
        assert results[0]["student"]["email"] == "capped.import@example.edu"
        assert "maximum" in results[0]["message"]

        stored_ids = {sample["_id"] for sample in existing}
        current_ids = {
            document["_id"] for document in fake_db[FACE_ENCODINGS].find({"student_id": capped["_id"]})
        }
        assert current_ids == stored_ids
        assert len(current_ids) == MAX_SAMPLES_PER_STUDENT


# --- Per-file outcomes: cross-student duplicate, including within one batch ---


class TestCrossStudentDuplicateWithinSameBatch:
    def test_a_face_matching_a_sample_inserted_earlier_in_the_same_batch_is_rejected(
        self, app_instance, monkeypatch
    ):
        """Definition of done #15, the case the spec calls out as
        mattering most: the cross-student duplicate check must see a
        sample that this same request inserted moments earlier, not just
        samples that already existed before the batch started -- because
        successes commit as they go (rule 5) rather than at the end.
        """
        klass = make_class(ObjectId(), name="Cross Student Class")
        student_a = make_user(
            email="dupbatch.a@example.edu", password=FAKE_PASSWORD, role="student", name="Dup Student A"
        )
        student_b = make_user(
            email="dupbatch.b@example.edu", password=FAKE_PASSWORD, role="student", name="Dup Student B"
        )
        enrollments = [
            make_class_enrollment(klass["_id"], student_a["_id"]),
            make_class_enrollment(klass["_id"], student_b["_id"]),
        ]

        shared_face = list(VALID_ENCODING)

        def encode_faces(image_bytes):
            # Both files decode to the same face -- as if a stray photo of
            # student A were mistakenly named after student B.
            return [list(shared_face)]

        def closest_match(candidate, known):
            for index, existing in enumerate(known):
                if existing == candidate:
                    return (index, 0.1)  # well within MATCH_TOLERANCE
            return None

        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[student_a, student_b], classes=[klass], class_enrollments=enrollments,
            )
            _stub_recognition(monkeypatch, encode_faces=encode_faces, closest_match=closest_match)

            files = [
                _file("dupbatch.a@example.edu.jpg"),
                _file("dupbatch.b@example.edu.jpg"),
            ]
            response = _import(client, klass["_id"], headers, files)

        results = response.get_json()["results"]
        assert results[0]["status"] == "registered"
        assert results[1]["status"] == "rejected"
        assert results[1]["student"]["email"] == "dupbatch.b@example.edu"
        assert "closely matches" in results[1]["message"]
        assert "Dup Student A" in results[1]["message"]

        assert fake_db[FACE_ENCODINGS].count_documents({"student_id": student_a["_id"]}) == 1
        assert fake_db[FACE_ENCODINGS].count_documents({"student_id": student_b["_id"]}) == 0

    def test_a_pre_existing_cross_student_sample_still_blocks_registration(
        self, app_instance, monkeypatch
    ):
        """The same rule, but the conflicting sample predates the batch
        entirely -- the boundary case for the test above, proving the
        check is not somehow *only* looking at same-batch insertions.
        """
        klass = make_class(ObjectId(), name="Pre-existing Duplicate Class")
        owner = make_user(
            email="preexisting.owner@example.edu", password=FAKE_PASSWORD, role="student", name="Existing Owner"
        )
        importer_target = make_user(
            email="preexisting.target@example.edu", password=FAKE_PASSWORD, role="student"
        )
        existing_sample = make_face_encoding(owner["_id"], encoding=list(VALID_ENCODING))
        enrollment = make_class_enrollment(klass["_id"], importer_target["_id"])

        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[owner, importer_target],
                classes=[klass], class_enrollments=[enrollment],
                face_encodings=[existing_sample],
            )
            _stub_recognition(monkeypatch, closest_match=lambda candidate, known: (0, 0.1))

            response = _import(
                client, klass["_id"], headers, [_file("preexisting.target@example.edu.jpg")]
            )

        results = response.get_json()["results"]
        assert results[0]["status"] == "rejected"
        assert "Existing Owner" in results[0]["message"]
        assert fake_db[FACE_ENCODINGS].count_documents({"student_id": importer_target["_id"]}) == 0


# --- Stored document shape and the fixed "upload" source -----------------------


class TestStoredDocumentShapeAndSource:
    def test_stored_document_matches_single_upload_shape_with_upload_source_and_created_by(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Shape Class")
        aarti = make_user(email="aarti.shape@example.edu", password=FAKE_PASSWORD, role="student")
        enrollment = make_class_enrollment(klass["_id"], aarti["_id"])
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[aarti], classes=[klass], class_enrollments=[enrollment],
            )
            _stub_recognition(monkeypatch)
            admin_id = _acting_user_id(client)

            _import(client, klass["_id"], headers, [_file("aarti.shape@example.edu.jpg")])

        raw = fake_db[FACE_ENCODINGS].find_one({"student_id": aarti["_id"]})
        assert raw is not None
        assert set(raw.keys()) == {
            "_id", "student_id", "encoding", "model", "source", "created_at", "created_by",
        }
        assert raw["source"] == "upload"
        assert raw["created_by"] == ObjectId(admin_id)
        assert len(raw["encoding"]) == 128
        assert isinstance(raw["created_at"], datetime)

    def test_a_client_supplied_source_field_is_ignored(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Ignore Source Class")
        aarti = make_user(email="aarti.ignoresource@example.edu", password=FAKE_PASSWORD, role="student")
        enrollment = make_class_enrollment(klass["_id"], aarti["_id"])
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[aarti], classes=[klass], class_enrollments=[enrollment],
            )
            _stub_recognition(monkeypatch)

            response = _import(
                client, klass["_id"], headers,
                [_file("aarti.ignoresource@example.edu.jpg")],
                extra_fields={"source": "camera"},
            )

        assert response.status_code == 200
        raw = fake_db[FACE_ENCODINGS].find_one({"student_id": aarti["_id"]})
        assert raw["source"] == "upload"


# --- No response, on any status, ever carries the encoding vector -------------


class TestEncodingNeverLeaks:
    def test_no_response_on_any_status_contains_the_encoding_vector(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Leak Class")
        aarti = make_user(email="aarti.leak@example.edu", password=FAKE_PASSWORD, role="student")
        sneha = make_user(email="sneha.leak@example.edu", password=FAKE_PASSWORD, role="student")
        rohit_a = make_user(email="rohitleak@abc.edu", password=FAKE_PASSWORD, role="student")
        rohit_b = make_user(email="rohitleak@xyz.edu", password=FAKE_PASSWORD, role="student")
        roster = [aarti, sneha, rohit_a, rohit_b]
        enrollments = [make_class_enrollment(klass["_id"], student["_id"]) for student in roster]

        def encode_faces(image_bytes):
            return [] if image_bytes == b"blank" else [list(VALID_ENCODING)]

        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=roster, classes=[klass], class_enrollments=enrollments,
            )
            _stub_recognition(monkeypatch, encode_faces=encode_faces)

            files = [
                _file("aarti.leak@example.edu.jpg"),
                _file("nobody-here.jpg"),
                _file("rohitleak.jpg"),
                _file("sneha.leak@example.edu.jpg", data=b"blank"),
            ]
            response = _import(client, klass["_id"], headers, files)

        body = response.get_json()
        assert {r["status"] for r in body["results"]} == {
            "registered", "no_match", "ambiguous", "rejected",
        }
        _assert_no_encoding_leak(body)


# --- Every file failing is still a 200 with a complete report -----------------


class TestAllFilesFailStillReturns200:
    def test_a_request_where_every_file_fails_returns_200_with_a_complete_report(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="All Fail Class")
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin", classes=[klass])
            _stub_recognition(monkeypatch)

            files = [_file("nobody1.jpg"), _file("nobody2.jpg"), _file("nobody3.jpg")]
            response = _import(client, klass["_id"], headers, files)

        assert response.status_code == 200
        body = response.get_json()
        assert len(body["results"]) == 3
        assert all(r["status"] == "no_match" for r in body["results"])
        assert body["summary"] == {
            "submitted": 3, "registered": 0, "no_match": 3, "ambiguous": 0, "rejected": 0,
        }
        assert fake_db[FACE_ENCODINGS].count_documents({}) == 0


# --- A database error is a 500, never flattened into a per-file rejection -----


class TestDatabaseErrorSurfacesAsFiveHundred:
    def test_a_database_error_during_registration_is_a_500_not_a_per_file_rejection(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="DB Error Class")
        aarti = make_user(email="aarti.dberror@example.edu", password=FAKE_PASSWORD, role="student")
        enrollment = make_class_enrollment(klass["_id"], aarti["_id"])
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[aarti], classes=[klass], class_enrollments=[enrollment],
            )
            _stub_recognition(monkeypatch)

            def _raise_pymongo_error(document):
                raise PyMongoError("simulated driver failure")

            fake_db[FACE_ENCODINGS].insert_one = _raise_pymongo_error

            response = _import(client, klass["_id"], headers, [_file("aarti.dberror@example.edu.jpg")])

        assert response.status_code == 500
        body = response.get_json()
        assert "error" in body
        assert "results" not in body
