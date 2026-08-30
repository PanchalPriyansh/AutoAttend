"""Tests for backend/routes/users.py (the `users_bp` blueprint) and
backend/users/{service,assignments,validators,serializers}.py, plus the
`backend/common/{errors,validators}.py` extraction that feature 04
re-exports unchanged through `academic/errors.py` / `academic/validators.py`.

Spec contract under test (05-admin-user-management.md, "APIs" +
"Database changes" + "Rules for implementation" + "Definition of done"):
  - Ten endpoints under `/api`, every one **admin-only**, reads included:
    `GET/POST /api/users`, `GET/PUT /api/users/<id>`,
    `PUT /api/users/<id>/status`, `PUT /api/users/<id>/password`,
    `PUT /api/classes/<id>/faculty`, `GET/POST /api/classes/<id>/students`,
    `DELETE /api/classes/<id>/students/<student_id>`.
  - There is no `DELETE /api/users/<id>` -- accounts are deactivated, never
    hard-deleted.
  - `role` is immutable after creation; `PUT /api/users/<id>` silently
    ignores a `role` in the body.
  - `password_hash` must never appear in any response, under any endpoint.
  - Self-protection: an admin cannot deactivate their own account, and
    cannot deactivate the last active admin; both are 409.
  - `institute_id` must resolve in `institutes`; `faculty_id` must resolve
    to an active `faculty` user; `student_id` must resolve to an active
    `student` user -- a wrong-role or inactive reference is 400, a
    non-existent id is 404.
  - Duplicate email and duplicate enrollment are 409, translated from the
    unique-index `DuplicateKeyError`.
  - `enrolled_at` is stored as a real BSON date, never an ISO string.
  - Every response serializes ids as strings and dates as ISO 8601
    strings -- no raw `ObjectId(...)`/BSON leaks into JSON.
  - CSRF: a mutating request with valid cookies but a missing/incorrect
    `X-CSRF-TOKEN` header is rejected, same as `03`/`04`.

`routes.auth.get_db`, `routes.users.get_db`, and `routes.academic.get_db`
are all monkeypatched to the *same* in-memory fake db (users_test_helpers.py,
built on the existing academic_test_helpers.py / auth_test_helpers.py
fakes) so a real `/api/auth/login` call can mint genuine JWT + CSRF
cookies while the endpoints under test read/write fake collections -- no
live MongoDB is required. All emails/passwords/institutional data used are
obviously fake, test-only values; no biometric data is involved.
"""

from datetime import datetime

import pytest
from bson import ObjectId
from users_test_helpers import (
    make_class,
    make_fake_users_db,
    make_institute,
    make_user,
)

from database.schema import CLASS_ENROLLMENTS, USERS
from users.validators import MIN_PASSWORD_LENGTH

FAKE_PASSWORD = "a-fake-test-password-1"
SHORT_PASSWORD = "a" * (MIN_PASSWORD_LENGTH - 1)


def _patch_db(monkeypatch, fake_db):
    """All three blueprints must see the same fake db: login goes through
    routes.auth.get_db, user/class/enrollment management goes through
    routes.users.get_db, and the class-list/delete endpoints used by a
    couple of cross-feature integration checks go through
    routes.academic.get_db.
    """
    monkeypatch.setattr("routes.auth.get_db", lambda: fake_db)
    monkeypatch.setattr("routes.users.get_db", lambda: fake_db)
    monkeypatch.setattr("routes.academic.get_db", lambda: fake_db)
    return fake_db


def _login(client, email, password=FAKE_PASSWORD):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, f"login setup failed: {response.get_json()}"
    return response


def _csrf_headers(client):
    cookie = client.get_cookie("csrf_access_token", path="/")
    assert cookie is not None, "csrf_access_token cookie was not set after login"
    return {"X-CSRF-TOKEN": cookie.value}


def _refresh_csrf_headers(client):
    cookie = client.get_cookie("csrf_refresh_token", path="/")
    assert cookie is not None, "csrf_refresh_token cookie was not set after login"
    return {"X-CSRF-TOKEN": cookie.value}


def _login_as(monkeypatch, client, role, *, email=None, extra_users=None, **collections):
    """Builds a fake db with one user of `role` (plus any extra users and
    collections passed in), patches all three blueprints' get_db, logs in,
    and returns (fake_db, csrf_headers).
    """
    email = email or f"{role}@college.test"
    user = make_user(email=email, password=FAKE_PASSWORD, role=role)
    fake_db = make_fake_users_db(users=[user] + list(extra_users or []), **collections)
    _patch_db(monkeypatch, fake_db)
    _login(client, email, FAKE_PASSWORD)
    return fake_db, _csrf_headers(client)


def _dummy_ids():
    return str(ObjectId()), str(ObjectId()), str(ObjectId())


def _all_ten_endpoint_requests(client, headers):
    """One representative request per endpoint, used to prove role
    enforcement / authentication is checked before anything else. Dummy
    ids are fine: role_required and jwt_required short-circuit before any
    id is ever looked up.
    """
    user_id, class_id, student_id = _dummy_ids()
    return [
        client.get("/api/users", headers=headers),
        client.post(
            "/api/users",
            json={"name": "N", "email": "n@x.test", "password": FAKE_PASSWORD, "role": "student"},
            headers=headers,
        ),
        client.get(f"/api/users/{user_id}", headers=headers),
        client.put(
            f"/api/users/{user_id}",
            json={"name": "N", "email": "n@x.test", "institute_id": None},
            headers=headers,
        ),
        client.put(f"/api/users/{user_id}/status", json={"is_active": False}, headers=headers),
        client.put(f"/api/users/{user_id}/password", json={"password": FAKE_PASSWORD}, headers=headers),
        client.put(f"/api/classes/{class_id}/faculty", json={"faculty_id": None}, headers=headers),
        client.get(f"/api/classes/{class_id}/students", headers=headers),
        client.post(f"/api/classes/{class_id}/students", json={"student_id": student_id}, headers=headers),
        client.delete(f"/api/classes/{class_id}/students/{student_id}", headers=headers),
    ]


# --- Create user: happy path --------------------------------------------


class TestCreateUserHappyPath:
    def test_create_faculty_user_returns_201_with_serialized_user(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/users",
                json={
                    "name": "Fiona Faculty",
                    "email": "fiona@college.test",
                    "password": FAKE_PASSWORD,
                    "role": "faculty",
                },
                headers=headers,
            )

        assert response.status_code == 201
        body = response.get_json()
        assert body["name"] == "Fiona Faculty"
        assert body["email"] == "fiona@college.test"
        assert body["role"] == "faculty"
        assert body["is_active"] is True
        assert isinstance(body["id"], str) and body["id"]
        assert isinstance(body["created_at"], str)
        assert isinstance(body["updated_at"], str)

    def test_create_student_user_returns_201(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/users",
                json={
                    "name": "Sam Student",
                    "email": "sam@college.test",
                    "password": FAKE_PASSWORD,
                    "role": "student",
                },
                headers=headers,
            )

        assert response.status_code == 201
        assert response.get_json()["role"] == "student"

    def test_created_faculty_can_login_with_the_password_the_admin_set(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            client.post(
                "/api/users",
                json={
                    "name": "New Faculty",
                    "email": "newfaculty@college.test",
                    "password": "faculty-password-1",
                    "role": "faculty",
                },
                headers=headers,
            )

            login_response = client.post(
                "/api/auth/login",
                json={"email": "newfaculty@college.test", "password": "faculty-password-1"},
            )

        assert login_response.status_code == 200
        assert login_response.get_json()["role"] == "faculty"

    def test_created_student_can_login_with_the_password_the_admin_set(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            client.post(
                "/api/users",
                json={
                    "name": "New Student",
                    "email": "newstudent@college.test",
                    "password": "student-password-1",
                    "role": "student",
                },
                headers=headers,
            )

            login_response = client.post(
                "/api/auth/login",
                json={"email": "newstudent@college.test", "password": "student-password-1"},
            )

        assert login_response.status_code == 200
        assert login_response.get_json()["role"] == "student"

    def test_create_user_with_valid_institute_id_returns_it_in_the_response(
        self, app_instance, monkeypatch
    ):
        institute = make_institute()
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", institutes=[institute])

            response = client.post(
                "/api/users",
                json={
                    "name": "Scoped Faculty",
                    "email": "scoped@college.test",
                    "password": FAKE_PASSWORD,
                    "role": "faculty",
                    "institute_id": str(institute["_id"]),
                },
                headers=headers,
            )

        assert response.status_code == 201
        assert response.get_json()["institute_id"] == str(institute["_id"])

    def test_create_user_without_institute_id_stores_null(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/users",
                json={
                    "name": "No Institute",
                    "email": "noinst@college.test",
                    "password": FAKE_PASSWORD,
                    "role": "student",
                },
                headers=headers,
            )

        assert response.status_code == 201
        assert response.get_json()["institute_id"] is None


# --- Create user: validation ----------------------------------------------


class TestCreateUserValidation:
    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": "a@b.test", "password": FAKE_PASSWORD, "role": "student"},
            {"name": "No Email", "password": FAKE_PASSWORD, "role": "student"},
            {"name": "No Password", "email": "a@b.test", "role": "student"},
            {"name": "No Role", "email": "a@b.test", "password": FAKE_PASSWORD},
            {"name": "", "email": "a@b.test", "password": FAKE_PASSWORD, "role": "student"},
            {"name": "   ", "email": "a@b.test", "password": FAKE_PASSWORD, "role": "student"},
            {"name": "Bad Email", "email": "not-an-email", "password": FAKE_PASSWORD, "role": "student"},
            {"name": "Bad Email 2", "email": "no-domain-dot@test", "password": FAKE_PASSWORD, "role": "student"},
            {"name": "Bad Role", "email": "a@b.test", "password": FAKE_PASSWORD, "role": "superadmin"},
            {"name": "NonString Role", "email": "a@b.test", "password": FAKE_PASSWORD, "role": 123},
            {"name": "Short Pw", "email": "a@b.test", "password": SHORT_PASSWORD, "role": "student"},
            {"name": "NonString Pw", "email": "a@b.test", "password": 12345678, "role": "student"},
        ],
        ids=[
            "empty-body",
            "missing-name",
            "missing-email",
            "missing-password",
            "missing-role",
            "blank-name",
            "whitespace-only-name",
            "malformed-email-no-at",
            "malformed-email-no-domain-dot",
            "invalid-role",
            "non-string-role",
            "password-too-short",
            "non-string-password",
        ],
    )
    def test_create_user_with_invalid_body_returns_400(self, app_instance, monkeypatch, body):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post("/api/users", json=body, headers=headers)

        assert response.status_code == 400

    def test_create_user_with_malformed_institute_id_returns_400(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/users",
                json={
                    "name": "Bad Institute",
                    "email": "badinst@college.test",
                    "password": FAKE_PASSWORD,
                    "role": "student",
                    "institute_id": "not-an-object-id",
                },
                headers=headers,
            )

        assert response.status_code == 400

    def test_create_user_with_nonexistent_institute_id_returns_404(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/users",
                json={
                    "name": "Ghost Institute",
                    "email": "ghostinst@college.test",
                    "password": FAKE_PASSWORD,
                    "role": "student",
                    "institute_id": str(ObjectId()),
                },
                headers=headers,
            )

        assert response.status_code == 404


# --- Create user: duplicate email -------------------------------------------


class TestCreateUserDuplicateEmail:
    def test_duplicate_email_returns_409(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            client.post(
                "/api/users",
                json={"name": "First", "email": "dup@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            )

            response = client.post(
                "/api/users",
                json={"name": "Second", "email": "dup@college.test", "password": FAKE_PASSWORD, "role": "faculty"},
                headers=headers,
            )

        assert response.status_code == 409

    def test_email_comparison_is_case_insensitive(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            client.post(
                "/api/users",
                json={"name": "First", "email": "A@b.com", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            )

            response = client.post(
                "/api/users",
                json={"name": "Second", "email": "a@b.com", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            )

        assert response.status_code == 409

    def test_stored_email_is_lowercased_and_trimmed(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            created = client.post(
                "/api/users",
                json={
                    "name": "Mixed Case",
                    "email": " Mixed@EMAIL.test ",
                    "password": FAKE_PASSWORD,
                    "role": "student",
                },
                headers=headers,
            ).get_json()

        assert created["email"] == "mixed@email.test"


# --- Get user ----------------------------------------------------------------


class TestGetUser:
    def test_get_user_returns_serialized_document(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "Gettable", "email": "gettable@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.get(f"/api/users/{created['id']}")

        assert response.status_code == 200
        assert response.get_json()["id"] == created["id"]

    def test_get_nonexistent_user_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get(f"/api/users/{ObjectId()}")

        assert response.status_code == 404

    def test_get_user_with_malformed_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get("/api/users/not-a-valid-id")

        assert response.status_code == 400


# --- List users ----------------------------------------------------------------


class TestListUsers:
    def test_list_is_keyed_under_users(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get("/api/users")

        assert response.status_code == 200
        assert "users" in response.get_json()

    def test_list_filtered_by_role_returns_only_that_role(self, app_instance, monkeypatch):
        faculty = make_user(email="f@college.test", password=FAKE_PASSWORD, role="faculty")
        student = make_user(email="s@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[faculty, student])

            response = client.get("/api/users?role=faculty")

        roles = {u["role"] for u in response.get_json()["users"]}
        assert roles == {"faculty"}

    def test_list_with_invalid_role_filter_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get("/api/users?role=bogus")

        assert response.status_code == 400

    def test_list_filtered_by_institute_id_returns_only_scoped_users(
        self, app_instance, monkeypatch
    ):
        institute_a = make_institute(name="Inst A", code="IA")
        institute_b = make_institute(name="Inst B", code="IB")
        student_a = make_user(email="sa@college.test", password=FAKE_PASSWORD, role="student", institute_id=institute_a["_id"])
        student_b = make_user(email="sb@college.test", password=FAKE_PASSWORD, role="student", institute_id=institute_b["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[student_a, student_b],
                institutes=[institute_a, institute_b],
            )

            response = client.get(f"/api/users?institute_id={institute_a['_id']}")

        emails = [u["email"] for u in response.get_json()["users"]]
        assert emails == ["sa@college.test"]

    def test_list_with_malformed_institute_id_filter_returns_400(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get("/api/users?institute_id=not-an-id")

        assert response.status_code == 400

    def test_list_search_by_q_matches_name_or_email_case_insensitively(
        self, app_instance, monkeypatch
    ):
        jane = make_user(name="Jane Faculty", email="jane@college.test", password=FAKE_PASSWORD, role="faculty")
        john = make_user(name="John Student", email="john@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[jane, john])

            by_name = client.get("/api/users?q=jane")
            by_email = client.get("/api/users?q=JOHN@college")

        assert [u["email"] for u in by_name.get_json()["users"]] == ["jane@college.test"]
        assert [u["email"] for u in by_email.get_json()["users"]] == ["john@college.test"]

    def test_list_combines_role_and_q_filters(self, app_instance, monkeypatch):
        john_faculty = make_user(name="John Faculty", email="johnf@college.test", password=FAKE_PASSWORD, role="faculty")
        john_student = make_user(name="John Student", email="johns@college.test", password=FAKE_PASSWORD, role="student")
        jane_student = make_user(name="Jane Student", email="janes@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[john_faculty, john_student, jane_student]
            )

            response = client.get("/api/users?role=student&q=john")

        emails = [u["email"] for u in response.get_json()["users"]]
        assert emails == ["johns@college.test"]


# --- Update user ----------------------------------------------------------------


class TestUpdateUser:
    def test_update_changes_name_email_institute_and_bumps_updated_at(
        self, app_instance, monkeypatch
    ):
        institute = make_institute()
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", institutes=[institute])
            created = client.post(
                "/api/users",
                json={"name": "Old Name", "email": "old@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{created['id']}",
                json={"name": "New Name", "email": "new@college.test", "institute_id": str(institute["_id"])},
                headers=headers,
            )

        assert response.status_code == 200
        body = response.get_json()
        assert body["name"] == "New Name"
        assert body["email"] == "new@college.test"
        assert body["institute_id"] == str(institute["_id"])
        # ISO 8601 strings with a consistent offset sort lexicographically
        # in the same order as chronologically, so this holds even if the
        # two timestamps land in the same microsecond.
        assert body["updated_at"] >= created["updated_at"]

    def test_update_ignores_role_in_body(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "Fixed Role", "email": "fixedrole@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{created['id']}",
                json={"name": "Fixed Role", "email": "fixedrole@college.test", "institute_id": None, "role": "admin"},
                headers=headers,
            )

        assert response.status_code == 200
        assert response.get_json()["role"] == "student"

    def test_update_nonexistent_user_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.put(
                f"/api/users/{ObjectId()}",
                json={"name": "Nope", "email": "nope@college.test", "institute_id": None},
                headers=headers,
            )

        assert response.status_code == 404

    def test_update_with_malformed_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.put(
                "/api/users/not-a-valid-id",
                json={"name": "Nope", "email": "nope@college.test", "institute_id": None},
                headers=headers,
            )

        assert response.status_code == 400

    def test_update_with_duplicate_email_returns_409(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            client.post(
                "/api/users",
                json={"name": "First", "email": "first@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            )
            second = client.post(
                "/api/users",
                json={"name": "Second", "email": "second@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{second['id']}",
                json={"name": "Second", "email": "first@college.test", "institute_id": None},
                headers=headers,
            )

        assert response.status_code == 409

    def test_update_with_nonexistent_institute_id_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "N", "email": "instcheck@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{created['id']}",
                json={"name": "N", "email": "instcheck@college.test", "institute_id": str(ObjectId())},
                headers=headers,
            )

        assert response.status_code == 404

    def test_update_with_malformed_institute_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "N", "email": "instcheck2@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{created['id']}",
                json={"name": "N", "email": "instcheck2@college.test", "institute_id": "not-an-id"},
                headers=headers,
            )

        assert response.status_code == 400

    def test_update_with_institute_id_key_omitted_returns_400(self, app_instance, monkeypatch):
        """PUT replaces the full set of editable fields, so a missing
        institute_id key is a malformed request -- an admin who forgets to
        send it must get a 400, not have it silently unassigned as if they
        had sent an explicit null.
        """
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "N", "email": "instomit@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{created['id']}",
                json={"name": "N", "email": "instomit@college.test"},
                headers=headers,
            )

        assert response.status_code == 400

    def test_update_missing_name_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "N", "email": "missingname@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{created['id']}",
                json={"email": "missingname@college.test", "institute_id": None},
                headers=headers,
            )

        assert response.status_code == 400


# --- User status (deactivate / reactivate) -----------------------------------


class TestUserStatus:
    def test_deactivate_sets_is_active_false(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "Target", "email": "target@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{created['id']}/status", json={"is_active": False}, headers=headers
            )

        assert response.status_code == 200
        assert response.get_json()["is_active"] is False

    def test_reactivate_sets_is_active_true(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "Target2", "email": "target2@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()
            client.put(f"/api/users/{created['id']}/status", json={"is_active": False}, headers=headers)

            response = client.put(
                f"/api/users/{created['id']}/status", json={"is_active": True}, headers=headers
            )

        assert response.status_code == 200
        assert response.get_json()["is_active"] is True

    def test_status_requires_a_real_boolean(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "T", "email": "boolcheck@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{created['id']}/status", json={"is_active": "false"}, headers=headers
            )

        assert response.status_code == 400

    def test_status_missing_is_active_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "T", "email": "missingflag@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(f"/api/users/{created['id']}/status", json={}, headers=headers)

        assert response.status_code == 400

    def test_status_nonexistent_user_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.put(
                f"/api/users/{ObjectId()}/status", json={"is_active": False}, headers=headers
            )

        assert response.status_code == 404

    def test_status_malformed_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.put(
                "/api/users/not-a-valid-id/status", json={"is_active": False}, headers=headers
            )

        assert response.status_code == 400

    def test_admin_cannot_deactivate_their_own_account(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin", email="self@college.test")
            me = client.get("/api/auth/me").get_json()

            response = client.put(f"/api/users/{me['id']}/status", json={"is_active": False}, headers=headers)
            still_active = client.get(f"/api/users/{me['id']}").get_json()

        assert response.status_code == 409
        assert still_active["is_active"] is True

    def test_deactivating_last_active_admin_returns_409(self, app_instance, monkeypatch):
        """Simulates the documented known limitation: an admin's access
        token stays valid for up to its 15-minute lifetime even after
        their own account is deactivated by someone else in the interim.
        Login happens first (while active) so genuine cookies exist, then
        their stored record is flipped to inactive directly, isolating the
        "last active admin" rule from the separate self-protection rule.
        """
        actor = make_user(email="actor@college.test", password=FAKE_PASSWORD, role="admin")
        target = make_user(email="lastadmin@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_users_db(users=[actor, target])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "actor@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            for stored in fake_db[USERS]._users:
                if stored["_id"] == actor["_id"]:
                    stored["is_active"] = False

            response = client.put(
                f"/api/users/{target['_id']}/status", json={"is_active": False}, headers=headers
            )

        assert response.status_code == 409
        assert "last active admin" in response.get_json()["error"].lower()

    def test_deactivating_an_admin_succeeds_when_a_second_active_admin_exists(
        self, app_instance, monkeypatch
    ):
        actor = make_user(email="actor2@college.test", password=FAKE_PASSWORD, role="admin")
        target = make_user(email="secondadmin@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_users_db(users=[actor, target])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "actor2@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            response = client.put(
                f"/api/users/{target['_id']}/status", json={"is_active": False}, headers=headers
            )

        assert response.status_code == 200
        assert response.get_json()["is_active"] is False

    def test_deactivating_user_blocks_login_and_reactivating_restores_it(
        self, app_instance, monkeypatch
    ):
        admin = make_user(email="statusadmin@college.test", password=FAKE_PASSWORD, role="admin")
        subject = make_user(email="subject@college.test", password=FAKE_PASSWORD, role="student")
        fake_db = make_fake_users_db(users=[admin, subject])
        _patch_db(monkeypatch, fake_db)

        # Two independent, non-context-manager clients: `with client:` keeps
        # each request's context pushed on a shared stack until its own
        # __exit__ runs, and combining two such context managers in one
        # `with` statement tears them down in the wrong order relative to
        # how the requests below actually interleave. Neither _login nor
        # the CSRF helpers need the preserved request context -- they only
        # read cookies off the client -- so plain (non-`with`) clients are
        # sufficient here and avoid that teardown hazard entirely.
        admin_client = app_instance.test_client()
        subject_client = app_instance.test_client()

        _login(admin_client, "statusadmin@college.test", FAKE_PASSWORD)
        admin_headers = _csrf_headers(admin_client)

        _login(subject_client, "subject@college.test", FAKE_PASSWORD)
        subject_refresh_headers = _refresh_csrf_headers(subject_client)

        status_response = admin_client.put(
            f"/api/users/{subject['_id']}/status", json={"is_active": False}, headers=admin_headers
        )

        login_after_deactivate = subject_client.post(
            "/api/auth/login", json={"email": "subject@college.test", "password": FAKE_PASSWORD}
        )
        refresh_after_deactivate = subject_client.post(
            "/api/auth/refresh", headers=subject_refresh_headers
        )

        reactivate_response = admin_client.put(
            f"/api/users/{subject['_id']}/status", json={"is_active": True}, headers=admin_headers
        )
        login_after_reactivate = subject_client.post(
            "/api/auth/login", json={"email": "subject@college.test", "password": FAKE_PASSWORD}
        )

        assert status_response.status_code == 200
        assert login_after_deactivate.status_code == 401
        assert refresh_after_deactivate.status_code == 401
        assert reactivate_response.status_code == 200
        assert login_after_reactivate.status_code == 200


# --- Password reset ------------------------------------------------------------


class TestUserPasswordReset:
    def test_password_reset_old_fails_new_succeeds(self, app_instance, monkeypatch):
        admin = make_user(email="pwadmin@college.test", password=FAKE_PASSWORD, role="admin")
        subject = make_user(email="pwsubject@college.test", password="old-password-1", role="student")
        fake_db = make_fake_users_db(users=[admin, subject])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "pwadmin@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            reset_response = client.put(
                f"/api/users/{subject['_id']}/password",
                json={"password": "new-password-1"},
                headers=headers,
            )

            old_login = client.post(
                "/api/auth/login", json={"email": "pwsubject@college.test", "password": "old-password-1"}
            )
            new_login = client.post(
                "/api/auth/login", json={"email": "pwsubject@college.test", "password": "new-password-1"}
            )

        assert reset_response.status_code == 200
        assert old_login.status_code == 401
        assert new_login.status_code == 200

    def test_password_reset_nonexistent_user_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.put(
                f"/api/users/{ObjectId()}/password", json={"password": FAKE_PASSWORD}, headers=headers
            )

        assert response.status_code == 404

    def test_password_reset_too_short_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "T", "email": "shortpw@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{created['id']}/password", json={"password": SHORT_PASSWORD}, headers=headers
            )

        assert response.status_code == 400

    def test_password_reset_malformed_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.put(
                "/api/users/not-a-valid-id/password", json={"password": FAKE_PASSWORD}, headers=headers
            )

        assert response.status_code == 400

    def test_password_reset_response_does_not_echo_the_password(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "T", "email": "noecho@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            response = client.put(
                f"/api/users/{created['id']}/password",
                json={"password": "brand-new-password-1"},
                headers=headers,
            )

        assert "brand-new-password-1" not in response.get_data(as_text=True)


# --- password_hash never leaks -------------------------------------------------


class TestPasswordHashNeverLeaks:
    def test_password_hash_absent_from_create_get_list_update_status_password_and_roster(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Leak Check Class")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", classes=[klass])

            created = client.post(
                "/api/users",
                json={"name": "Leak Check", "email": "leak@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            )
            get_resp = client.get(f"/api/users/{created.get_json()['id']}")
            list_resp = client.get("/api/users")
            update_resp = client.put(
                f"/api/users/{created.get_json()['id']}",
                json={"name": "Leak Check", "email": "leak@college.test", "institute_id": None},
                headers=headers,
            )
            status_resp = client.put(
                f"/api/users/{created.get_json()['id']}/status", json={"is_active": True}, headers=headers
            )
            password_resp = client.put(
                f"/api/users/{created.get_json()['id']}/password",
                json={"password": "another-fake-password-1"},
                headers=headers,
            )
            enroll_resp = client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": created.get_json()["id"]},
                headers=headers,
            )
            roster_resp = client.get(f"/api/classes/{klass['_id']}/students")

        labelled_responses = [
            ("create", created),
            ("get", get_resp),
            ("list", list_resp),
            ("update", update_resp),
            ("status", status_resp),
            ("password", password_resp),
            ("enroll", enroll_resp),
            ("roster", roster_resp),
        ]
        for label, response in labelled_responses:
            assert response.status_code < 500, f"{label} failed with {response.status_code}"
            body_text = response.get_data(as_text=True)
            assert "password_hash" not in body_text, f"password_hash leaked in {label} response"


# --- Faculty assignment ---------------------------------------------------------


class TestFacultyAssignment:
    def test_assign_faculty_returns_serialized_class_with_faculty_id_as_string(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Assign Me")
        faculty = make_user(email="assignfac@college.test", password=FAKE_PASSWORD, role="faculty")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[faculty], classes=[klass])

            response = client.put(
                f"/api/classes/{klass['_id']}/faculty",
                json={"faculty_id": str(faculty["_id"])},
                headers=headers,
            )

        assert response.status_code == 200
        body = response.get_json()
        assert body["id"] == str(klass["_id"])
        assert body["faculty_id"] == str(faculty["_id"])
        assert body["name"] == "Assign Me"

    def test_assigned_faculty_is_visible_via_classes_list(self, app_instance, monkeypatch):
        course_id = ObjectId()
        klass = make_class(course_id, name="Listed Class")
        faculty = make_user(email="listedfac@college.test", password=FAKE_PASSWORD, role="faculty")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[faculty], classes=[klass])

            client.put(
                f"/api/classes/{klass['_id']}/faculty",
                json={"faculty_id": str(faculty["_id"])},
                headers=headers,
            )
            response = client.get(f"/api/classes?course_id={course_id}")

        classes = response.get_json()["classes"]
        assert len(classes) == 1
        assert classes[0]["faculty_id"] == str(faculty["_id"])

    def test_assign_faculty_replaces_previous_assignment(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Reassign Me")
        faculty_1 = make_user(email="firstfac@college.test", password=FAKE_PASSWORD, role="faculty")
        faculty_2 = make_user(email="secondfac@college.test", password=FAKE_PASSWORD, role="faculty")
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[faculty_1, faculty_2], classes=[klass]
            )
            client.put(
                f"/api/classes/{klass['_id']}/faculty",
                json={"faculty_id": str(faculty_1["_id"])},
                headers=headers,
            )

            response = client.put(
                f"/api/classes/{klass['_id']}/faculty",
                json={"faculty_id": str(faculty_2["_id"])},
                headers=headers,
            )

        assert response.status_code == 200
        assert response.get_json()["faculty_id"] == str(faculty_2["_id"])

    def test_assign_null_faculty_id_clears_the_assignment(self, app_instance, monkeypatch):
        faculty = make_user(email="clearme@college.test", password=FAKE_PASSWORD, role="faculty")
        klass = make_class(ObjectId(), name="Clear Me", faculty_id=faculty["_id"])
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[faculty], classes=[klass])

            response = client.put(
                f"/api/classes/{klass['_id']}/faculty", json={"faculty_id": None}, headers=headers
            )

        assert response.status_code == 200
        assert response.get_json()["faculty_id"] is None

    def test_assign_student_id_as_faculty_returns_400(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Wrong Role Class")
        student = make_user(email="wrongrole@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student], classes=[klass])

            response = client.put(
                f"/api/classes/{klass['_id']}/faculty",
                json={"faculty_id": str(student["_id"])},
                headers=headers,
            )

        assert response.status_code == 400

    def test_assign_admin_id_as_faculty_returns_400(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Admin As Faculty")
        other_admin = make_user(email="otheradmin@college.test", password=FAKE_PASSWORD, role="admin")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[other_admin], classes=[klass])

            response = client.put(
                f"/api/classes/{klass['_id']}/faculty",
                json={"faculty_id": str(other_admin["_id"])},
                headers=headers,
            )

        assert response.status_code == 400

    def test_assign_inactive_faculty_returns_400(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Inactive Faculty Class")
        inactive_faculty = make_user(
            email="inactivefac@college.test", password=FAKE_PASSWORD, role="faculty", is_active=False
        )
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[inactive_faculty], classes=[klass]
            )

            response = client.put(
                f"/api/classes/{klass['_id']}/faculty",
                json={"faculty_id": str(inactive_faculty["_id"])},
                headers=headers,
            )

        assert response.status_code == 400

    def test_assign_nonexistent_faculty_id_returns_404(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Ghost Faculty Class")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", classes=[klass])

            response = client.put(
                f"/api/classes/{klass['_id']}/faculty",
                json={"faculty_id": str(ObjectId())},
                headers=headers,
            )

        assert response.status_code == 404

    def test_assign_faculty_to_nonexistent_class_returns_404(self, app_instance, monkeypatch):
        faculty = make_user(email="orphanfac@college.test", password=FAKE_PASSWORD, role="faculty")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[faculty])

            response = client.put(
                f"/api/classes/{ObjectId()}/faculty",
                json={"faculty_id": str(faculty["_id"])},
                headers=headers,
            )

        assert response.status_code == 404

    def test_assign_faculty_with_malformed_class_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.put(
                "/api/classes/not-a-valid-id/faculty", json={"faculty_id": None}, headers=headers
            )

        assert response.status_code == 400

    def test_assign_faculty_with_malformed_faculty_id_returns_400(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Malformed Faculty Id Class")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", classes=[klass])

            response = client.put(
                f"/api/classes/{klass['_id']}/faculty",
                json={"faculty_id": "not-a-valid-id"},
                headers=headers,
            )

        assert response.status_code == 400

    def test_assign_faculty_missing_key_in_body_returns_400(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Missing Key Class")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", classes=[klass])

            response = client.put(f"/api/classes/{klass['_id']}/faculty", json={}, headers=headers)

        assert response.status_code == 400

    def test_one_faculty_member_may_hold_multiple_classes(self, app_instance, monkeypatch):
        klass_a = make_class(ObjectId(), name="Class A")
        klass_b = make_class(ObjectId(), name="Class B")
        faculty = make_user(email="multiclass@college.test", password=FAKE_PASSWORD, role="faculty")
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[faculty], classes=[klass_a, klass_b]
            )

            first = client.put(
                f"/api/classes/{klass_a['_id']}/faculty",
                json={"faculty_id": str(faculty["_id"])},
                headers=headers,
            )
            second = client.put(
                f"/api/classes/{klass_b['_id']}/faculty",
                json={"faculty_id": str(faculty["_id"])},
                headers=headers,
            )

        assert first.status_code == 200
        assert second.status_code == 200


# --- Student enrollment ----------------------------------------------------------


class TestStudentEnrollment:
    def test_enroll_student_returns_201_with_serialized_enrollment(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Enroll Class")
        student = make_user(email="enrollee@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student], classes=[klass])

            response = client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            )

        assert response.status_code == 201
        body = response.get_json()
        assert body["student"]["id"] == str(student["_id"])
        assert body["student"]["email"] == "enrollee@college.test"
        assert body["student"]["is_active"] is True
        assert isinstance(body["enrolled_at"], str)

    def test_enrolled_at_is_stored_as_a_bson_datetime_not_a_string(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Date Check Class")
        student = make_user(email="datecheck@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[student], classes=[klass]
            )

            created = client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            ).get_json()

        raw = fake_db[CLASS_ENROLLMENTS].find_one({"_id": ObjectId(created["id"])})
        assert isinstance(raw["enrolled_at"], datetime)
        assert not isinstance(raw["enrolled_at"], str)

    def test_enrolling_the_same_student_twice_returns_409(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Dup Enroll Class")
        student = make_user(email="dupenroll@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student], classes=[klass])
            client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            )

            response = client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            )

        assert response.status_code == 409

    def test_enrolling_a_faculty_id_returns_400(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Faculty Not Student Class")
        faculty = make_user(email="notstudent@college.test", password=FAKE_PASSWORD, role="faculty")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[faculty], classes=[klass])

            response = client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(faculty["_id"])},
                headers=headers,
            )

        assert response.status_code == 400

    def test_enrolling_an_admin_id_returns_400(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Admin Not Student Class")
        other_admin = make_user(email="notstudent2@college.test", password=FAKE_PASSWORD, role="admin")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[other_admin], classes=[klass])

            response = client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(other_admin["_id"])},
                headers=headers,
            )

        assert response.status_code == 400

    def test_enrolling_an_inactive_student_returns_400(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Inactive Student Class")
        inactive_student = make_user(
            email="inactivestudent@college.test", password=FAKE_PASSWORD, role="student", is_active=False
        )
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[inactive_student], classes=[klass]
            )

            response = client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(inactive_student["_id"])},
                headers=headers,
            )

        assert response.status_code == 400

    def test_enrolling_a_nonexistent_student_id_returns_404(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Ghost Student Class")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", classes=[klass])

            response = client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(ObjectId())},
                headers=headers,
            )

        assert response.status_code == 404

    def test_enrolling_into_a_nonexistent_class_returns_404(self, app_instance, monkeypatch):
        student = make_user(email="orphanstudent@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student])

            response = client.post(
                f"/api/classes/{ObjectId()}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            )

        assert response.status_code == 404

    def test_enroll_with_malformed_class_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/classes/not-a-valid-id/students",
                json={"student_id": str(ObjectId())},
                headers=headers,
            )

        assert response.status_code == 400

    def test_enroll_missing_student_id_returns_400(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Missing Student Id Class")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", classes=[klass])

            response = client.post(f"/api/classes/{klass['_id']}/students", json={}, headers=headers)

        assert response.status_code == 400

    def test_enroll_with_malformed_student_id_returns_400(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Malformed Student Id Class")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", classes=[klass])

            response = client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": "not-a-valid-id"},
                headers=headers,
            )

        assert response.status_code == 400


# --- Class roster (GET /api/classes/<id>/students) --------------------------------


class TestClassRoster:
    def test_roster_returns_only_this_classs_students_with_no_cross_class_leakage(
        self, app_instance, monkeypatch
    ):
        class_a = make_class(ObjectId(), name="Roster Class A")
        class_b = make_class(ObjectId(), name="Roster Class B")
        student_a = make_user(email="rostera@college.test", password=FAKE_PASSWORD, role="student")
        student_b = make_user(email="rosterb@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin",
                extra_users=[student_a, student_b],
                classes=[class_a, class_b],
            )
            client.post(
                f"/api/classes/{class_a['_id']}/students",
                json={"student_id": str(student_a["_id"])},
                headers=headers,
            )
            client.post(
                f"/api/classes/{class_b['_id']}/students",
                json={"student_id": str(student_b["_id"])},
                headers=headers,
            )

            response = client.get(f"/api/classes/{class_a['_id']}/students")

        emails = [e["student"]["email"] for e in response.get_json()["enrollments"]]
        assert emails == ["rostera@college.test"]

    def test_roster_item_shape_has_student_profile_and_enrolled_at(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Shape Class")
        student = make_user(name="Shaped Student", email="shaped@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student], classes=[klass])
            client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            )

            response = client.get(f"/api/classes/{klass['_id']}/students")

        enrollment = response.get_json()["enrollments"][0]
        assert set(enrollment.keys()) == {"id", "student", "enrolled_at"}
        assert set(enrollment["student"].keys()) == {"id", "name", "email", "is_active"}
        assert enrollment["student"]["name"] == "Shaped Student"
        datetime.fromisoformat(enrollment["enrolled_at"])

    def test_roster_for_class_with_no_enrollments_returns_empty_list(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Empty Roster Class")
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin", classes=[klass])

            response = client.get(f"/api/classes/{klass['_id']}/students")

        assert response.status_code == 200
        assert response.get_json()["enrollments"] == []

    def test_roster_for_nonexistent_class_returns_404(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get(f"/api/classes/{ObjectId()}/students")

        assert response.status_code == 404

    def test_roster_still_shows_a_deactivated_student_with_is_active_false(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Deactivated Roster Class")
        student = make_user(email="deactivated@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student], classes=[klass])
            client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            )
            client.put(
                f"/api/users/{student['_id']}/status", json={"is_active": False}, headers=headers
            )

            response = client.get(f"/api/classes/{klass['_id']}/students")

        enrollments = response.get_json()["enrollments"]
        assert len(enrollments) == 1
        assert enrollments[0]["student"]["is_active"] is False


# --- Unenroll ---------------------------------------------------------------------


class TestUnenrollStudent:
    def test_unenroll_returns_200_with_deleted_true(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Unenroll Class")
        student = make_user(email="unenroll@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student], classes=[klass])
            client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            )

            response = client.delete(
                f"/api/classes/{klass['_id']}/students/{student['_id']}", headers=headers
            )

        assert response.status_code == 200
        assert response.get_json() == {"deleted": True}

    def test_unenroll_removes_the_student_from_the_roster(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Removal Class")
        student = make_user(email="removeme@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student], classes=[klass])
            client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            )

            client.delete(f"/api/classes/{klass['_id']}/students/{student['_id']}", headers=headers)
            response = client.get(f"/api/classes/{klass['_id']}/students")

        assert response.get_json()["enrollments"] == []

    def test_repeating_unenroll_returns_404(self, app_instance, monkeypatch):
        klass = make_class(ObjectId(), name="Repeat Unenroll Class")
        student = make_user(email="repeatunenroll@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student], classes=[klass])
            client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            )
            client.delete(f"/api/classes/{klass['_id']}/students/{student['_id']}", headers=headers)

            response = client.delete(
                f"/api/classes/{klass['_id']}/students/{student['_id']}", headers=headers
            )

        assert response.status_code == 404

    def test_unenroll_with_malformed_class_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.delete(
                f"/api/classes/not-a-valid-id/students/{ObjectId()}", headers=headers
            )

        assert response.status_code == 400

    def test_unenroll_with_malformed_student_id_returns_400(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.delete(
                f"/api/classes/{ObjectId()}/students/not-a-valid-id", headers=headers
            )

        assert response.status_code == 400


# --- Class deletion stays blocked by real enrollment data (integration) -----------


class TestClassDeletionBlockedByRealEnrollment:
    def test_delete_class_blocked_while_enrolled_then_succeeds_after_unenroll(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Delete Me Class")
        student = make_user(email="blockdelete@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[student], classes=[klass])
            client.post(
                f"/api/classes/{klass['_id']}/students",
                json={"student_id": str(student["_id"])},
                headers=headers,
            )

            blocked = client.delete(f"/api/classes/{klass['_id']}", headers=headers)

            client.delete(f"/api/classes/{klass['_id']}/students/{student['_id']}", headers=headers)
            allowed = client.delete(f"/api/classes/{klass['_id']}", headers=headers)

        assert blocked.status_code == 409
        assert allowed.status_code == 200


# --- There is no DELETE /api/users/<id> --------------------------------------------


class TestNoDeleteUserEndpoint:
    def test_delete_user_endpoint_does_not_exist_and_user_is_not_removed(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")
            created = client.post(
                "/api/users",
                json={"name": "Undeletable", "email": "undeletable@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            ).get_json()

            delete_response = client.delete(f"/api/users/{created['id']}", headers=headers)
            still_there = client.get(f"/api/users/{created['id']}")

        assert delete_response.status_code in (404, 405)
        assert still_there.status_code == 200
        assert still_there.get_json()["id"] == created["id"]


# --- Serialization shape -----------------------------------------------------------


class TestSerializationShape:
    def test_created_user_has_string_id_and_iso_dates_no_bson_repr(
        self, app_instance, monkeypatch
    ):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/users",
                json={"name": "Shape User", "email": "shapeuser@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            )

        body = response.get_json()
        assert isinstance(body["id"], str) and ObjectId.is_valid(body["id"])
        datetime.fromisoformat(body["created_at"])
        datetime.fromisoformat(body["updated_at"])
        assert "ObjectId(" not in response.get_data(as_text=True)

    def test_class_assignment_response_matches_classes_list_shape(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Shape Match Class")
        faculty = make_user(email="shapefac@college.test", password=FAKE_PASSWORD, role="faculty")
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[faculty], classes=[klass])

            response = client.put(
                f"/api/classes/{klass['_id']}/faculty",
                json={"faculty_id": str(faculty["_id"])},
                headers=headers,
            )

        assert set(response.get_json().keys()) == {"id", "course_id", "name", "faculty_id", "created_at"}

    def test_list_response_never_leaks_bson_objectid_repr(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _login_as(monkeypatch, client, "admin")

            response = client.get("/api/users")

        assert "ObjectId(" not in response.get_data(as_text=True)


# --- Role enforcement ----------------------------------------------------------------


class TestRoleEnforcement:
    def test_faculty_receives_403_on_all_ten_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "faculty")

            responses = _all_ten_endpoint_requests(client, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_student_receives_403_on_all_ten_endpoints(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "student")

            responses = _all_ten_endpoint_requests(client, headers)

        assert all(r.status_code == 403 for r in responses), [r.status_code for r in responses]

    def test_unauthenticated_request_receives_401_on_all_ten_endpoints(
        self, app_instance, monkeypatch
    ):
        fake_db = make_fake_users_db()
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            responses = _all_ten_endpoint_requests(client, headers={})

        assert all(r.status_code == 401 for r in responses), [r.status_code for r in responses]

    def test_admin_is_not_blocked_by_role_enforcement_on_representative_endpoints(
        self, app_instance, monkeypatch
    ):
        klass = make_class(ObjectId(), name="Admin Access Class")
        student = make_user(email="adminaccess@college.test", password=FAKE_PASSWORD, role="student")
        with app_instance.test_client() as client:
            _, headers = _login_as(
                monkeypatch, client, "admin", extra_users=[student], classes=[klass]
            )

            # Use real ids where the endpoint needs a resolvable one, so a
            # 403 (role) is never confused with a 400/404 (validation).
            responses = [
                client.get("/api/users", headers=headers),
                client.get(f"/api/classes/{klass['_id']}/students", headers=headers),
                client.put(f"/api/classes/{klass['_id']}/faculty", json={"faculty_id": None}, headers=headers),
            ]

        assert all(r.status_code != 403 for r in responses), [r.status_code for r in responses]


# --- CSRF enforcement on mutating endpoints -------------------------------------------


class TestCsrfEnforcementOnMutatingEndpoints:
    """Mirrors test_academic_routes.py's equivalent class, using user
    creation as the representative mutating endpoint.
    """

    def test_write_without_a_csrf_header_is_rejected(self, app_instance, monkeypatch):
        admin = make_user(email="csrfuser1@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_users_db(users=[admin])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "csrfuser1@college.test", FAKE_PASSWORD)

            response = client.post(
                "/api/users",
                json={"name": "No CSRF", "email": "nocsrf@college.test", "password": FAKE_PASSWORD, "role": "student"},
            )  # deliberately no X-CSRF-TOKEN header

        assert response.status_code == 401

    def test_write_with_an_incorrect_csrf_header_is_rejected(self, app_instance, monkeypatch):
        admin = make_user(email="csrfuser2@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_users_db(users=[admin])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "csrfuser2@college.test", FAKE_PASSWORD)

            response = client.post(
                "/api/users",
                json={"name": "Wrong CSRF", "email": "wrongcsrf@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers={"X-CSRF-TOKEN": "not-the-real-token"},
            )

        assert response.status_code == 401

    def test_write_with_the_correct_csrf_header_is_accepted(self, app_instance, monkeypatch):
        admin = make_user(email="csrfuser3@college.test", password=FAKE_PASSWORD, role="admin")
        fake_db = make_fake_users_db(users=[admin])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "csrfuser3@college.test", FAKE_PASSWORD)
            headers = _csrf_headers(client)

            response = client.post(
                "/api/users",
                json={"name": "Right CSRF", "email": "rightcsrf@college.test", "password": FAKE_PASSWORD, "role": "student"},
                headers=headers,
            )

        assert response.status_code == 201


# --------------------------------------------------------------------
# 24-invalidate-tokens-on-password-change
# --------------------------------------------------------------------


class TestAdminResetEndsTheTargetsSessions:
    """DoD 4, 5, 18.

    The admin reset carries no code for any of this. `set_user_password`
    is the one function that writes a password, the version bump lives
    inside it, and that is the whole mechanism -- which is the point:
    the self-service change and the admin reset cannot drift apart
    because there is nothing here to drift.
    """

    def test_reset_strands_the_targets_refresh_token(self, app_instance, monkeypatch):
        admin = make_user(email="reset1@college.test", password=FAKE_PASSWORD, role="admin")
        subject = make_user(
            email="subject1@college.test", password="old-password-1", role="student"
        )
        fake_db = make_fake_users_db(users=[admin, subject])
        _patch_db(monkeypatch, fake_db)

        # The subject is signed in on their own device, in their own
        # cookie jar, before the admin touches anything.
        subject_client = app_instance.test_client()
        _login(subject_client, "subject1@college.test", "old-password-1")
        assert (
            subject_client.post(
                "/api/auth/refresh", headers=_refresh_csrf_headers(subject_client)
            ).status_code
            == 200
        ), "precondition: the subject session works before the reset"

        admin_client = app_instance.test_client()
        _login(admin_client, "reset1@college.test", FAKE_PASSWORD)
        reset = admin_client.put(
            f"/api/users/{subject['_id']}/password",
            json={"password": "new-password-1"},
            headers=_csrf_headers(admin_client),
        )

        assert reset.status_code == 200
        assert (
            subject_client.post(
                "/api/auth/refresh", headers=_refresh_csrf_headers(subject_client)
            ).status_code
            == 401
        )

    def test_reset_bumps_the_targets_version_by_one(self, app_instance, monkeypatch):
        admin = make_user(email="reset2@college.test", password=FAKE_PASSWORD, role="admin")
        subject = make_user(
            email="subject2@college.test",
            password="old-password-1",
            role="student",
            token_version=3,
        )
        fake_db = make_fake_users_db(users=[admin, subject])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "reset2@college.test", FAKE_PASSWORD)
            client.put(
                f"/api/users/{subject['_id']}/password",
                json={"password": "new-password-1"},
                headers=_csrf_headers(client),
            )

        assert fake_db[USERS].find_one({"_id": subject["_id"]})["token_version"] == 4

    def test_reset_does_not_re_issue_any_cookie(self, app_instance, monkeypatch):
        """Rule 7. It acts on a named user who is normally not the
        caller, and there is no cookie of theirs to replace. An admin who
        points it at themselves is resetting their own password, and
        being signed out everywhere is the right outcome.
        """
        admin = make_user(email="reset3@college.test", password=FAKE_PASSWORD, role="admin")
        subject = make_user(
            email="subject3@college.test", password="old-password-1", role="student"
        )
        fake_db = make_fake_users_db(users=[admin, subject])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "reset3@college.test", FAKE_PASSWORD)
            response = client.put(
                f"/api/users/{subject['_id']}/password",
                json={"password": "new-password-1"},
                headers=_csrf_headers(client),
            )

        assert not [
            c
            for c in response.headers.getlist("Set-Cookie")
            if c.startswith(("access_token_cookie=", "refresh_token_cookie="))
        ]

    def test_reset_response_never_carries_the_version(self, app_instance, monkeypatch):
        """DoD 21: the counter is internal."""
        admin = make_user(email="reset4@college.test", password=FAKE_PASSWORD, role="admin")
        subject = make_user(
            email="subject4@college.test", password="old-password-1", role="student"
        )
        fake_db = make_fake_users_db(users=[admin, subject])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "reset4@college.test", FAKE_PASSWORD)
            response = client.put(
                f"/api/users/{subject['_id']}/password",
                json={"password": "new-password-1"},
                headers=_csrf_headers(client),
            )

        assert "token_version" not in response.get_data(as_text=True)


class TestStatusChangesLeaveTheVersionAlone:
    """DoD 18. A version bump means "this credential was replaced".

    Deactivation is a different fact and is enforced by a different
    check -- refresh re-reads `is_active` -- so stretching the counter to
    cover it is how a counter stops meaning anything.
    """

    def test_deactivating_does_not_change_the_version(self, app_instance, monkeypatch):
        admin = make_user(email="status1@college.test", password=FAKE_PASSWORD, role="admin")
        subject = make_user(
            email="target1@college.test",
            password=FAKE_PASSWORD,
            role="student",
            token_version=2,
        )
        fake_db = make_fake_users_db(users=[admin, subject])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "status1@college.test", FAKE_PASSWORD)
            response = client.put(
                f"/api/users/{subject['_id']}/status",
                json={"is_active": False},
                headers=_csrf_headers(client),
            )

        assert response.status_code == 200
        assert fake_db[USERS].find_one({"_id": subject["_id"]})["token_version"] == 2

    def test_reactivating_does_not_change_the_version(self, app_instance, monkeypatch):
        admin = make_user(email="status2@college.test", password=FAKE_PASSWORD, role="admin")
        subject = make_user(
            email="target2@college.test",
            password=FAKE_PASSWORD,
            role="student",
            is_active=False,
            token_version=5,
        )
        fake_db = make_fake_users_db(users=[admin, subject])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "status2@college.test", FAKE_PASSWORD)
            response = client.put(
                f"/api/users/{subject['_id']}/status",
                json={"is_active": True},
                headers=_csrf_headers(client),
            )

        assert response.status_code == 200
        assert fake_db[USERS].find_one({"_id": subject["_id"]})["token_version"] == 5

    def test_an_ordinary_profile_update_does_not_change_the_version(
        self, app_instance, monkeypatch
    ):
        admin = make_user(email="status3@college.test", password=FAKE_PASSWORD, role="admin")
        subject = make_user(
            email="target3@college.test",
            password=FAKE_PASSWORD,
            role="student",
            token_version=1,
        )
        fake_db = make_fake_users_db(users=[admin, subject])
        _patch_db(monkeypatch, fake_db)

        with app_instance.test_client() as client:
            _login(client, "status3@college.test", FAKE_PASSWORD)
            # PUT replaces the full set of editable fields, so all three
            # are required -- an omitted key is a 400, not "unchanged".
            response = client.put(
                f"/api/users/{subject['_id']}",
                json={
                    "name": "A New Name",
                    "email": "target3@college.test",
                    "institute_id": None,
                },
                headers=_csrf_headers(client),
            )

        assert response.status_code == 200
        assert fake_db[USERS].find_one({"_id": subject["_id"]})["token_version"] == 1


class TestCreatedAccountsCarryAnExplicitVersion:
    """DoD 13: a document written from now on says what it is rather than
    relying on auth/tokens.py's absent-means-zero default, which exists
    for documents written before 24.
    """

    def test_admin_created_user_starts_at_zero(self, app_instance, monkeypatch):
        with app_instance.test_client() as client:
            fake_db, headers = _login_as(monkeypatch, client, "admin")

            response = client.post(
                "/api/users",
                json={
                    "name": "New Student",
                    "email": "brandnew@college.test",
                    "password": FAKE_PASSWORD,
                    "role": "student",
                },
                headers=headers,
            )

        assert response.status_code == 201
        stored = fake_db[USERS].find_one({"email": "brandnew@college.test"})
        assert stored["token_version"] == 0
        assert "token_version" not in response.get_data(as_text=True)


class TestTokenVersionNeverLeaksFromUserManagementEndpoints:
    """DoD 21, the admin-facing half: `serialize_user` is built from a
    literal allow-list (users/serializers.py), so `token_version` is
    structurally unable to reach `GET /api/users`, `GET /api/users/<id>`,
    or `PUT /api/users/<id>`. A target with an explicit, non-zero version
    is used so a leak would be visible even though the value itself is
    never asserted -- only the key's absence is.
    """

    def test_list_and_get_responses_never_contain_token_version(
        self, app_instance, monkeypatch
    ):
        target = make_user(
            email="versioned@college.test",
            password=FAKE_PASSWORD,
            role="student",
            token_version=6,
        )
        with app_instance.test_client() as client:
            _, headers = _login_as(monkeypatch, client, "admin", extra_users=[target])

            list_response = client.get("/api/users")
            get_response = client.get(f"/api/users/{target['_id']}")
            update_response = client.put(
                f"/api/users/{target['_id']}",
                json={"name": "Versioned", "email": "versioned@college.test", "institute_id": None},
                headers=headers,
            )
            status_response = client.put(
                f"/api/users/{target['_id']}/status", json={"is_active": True}, headers=headers
            )

        for label, response in (
            ("list", list_response),
            ("get", get_response),
            ("update", update_response),
            ("status", status_response),
        ):
            assert response.status_code == 200, f"{label} failed: {response.get_json()}"
            assert "token_version" not in response.get_data(as_text=True), (
                f"token_version leaked in the {label} response"
            )
