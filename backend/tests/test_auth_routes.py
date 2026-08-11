"""Tests for backend/routes/auth.py (the `/api/auth/*` blueprint) and the
`role_required` authorization primitive from backend/auth/decorators.py.

Spec contract under test (03-authentication.md, "APIs" + "Rules for
implementation" + "Definition of done"):
  - `POST /api/auth/login` -- public. Valid credentials -> 200, the safe
    user profile (no `password_hash`), and HttpOnly access/refresh
    cookies plus their CSRF cookies are set. Unknown email, wrong
    password, and a deactivated account all -> 401 with an identical,
    generic error body, and no cookies are set. Missing/malformed fields
    -> 400. Email lookup is normalized (case/whitespace-insensitive).
  - `POST /api/auth/refresh` -- requires a valid refresh token cookie;
    issues a fresh access cookie. An access-token-only request (no
    refresh cookie) is rejected.
  - `POST /api/auth/logout` -- any authenticated role; clears the auth
    cookies, after which `GET /api/auth/me` returns 401.
  - `GET /api/auth/me` -- the current user's safe profile with valid
    cookies, 401 without them.
  - CSRF: a state-changing (mutating) request with valid auth cookies but
    a missing/incorrect `X-CSRF-TOKEN` header is rejected; a GET request
    needs no CSRF header.
  - `role_required("admin")` returns 403 for an authenticated faculty or
    student token, 200 for an admin, and 401 (not 403) when unauthenticated
    entirely -- proving role enforcement lives on the backend.

`routes.auth.get_db` is monkeypatched at its point of use (matching the
existing `routes.health.get_db_status` pattern in test_health.py), backed
by the in-memory `FakeUsersCollection` from auth_test_helpers.py -- no
live MongoDB is required. All emails/passwords used are obviously-fake,
test-only values.
"""

import pytest
from auth_test_helpers import make_fake_db, make_user

FAKE_PASSWORD = "a-fake-test-password-1"


def _patch_db(monkeypatch, users):
    fake_db = make_fake_db(users)
    monkeypatch.setattr("routes.auth.get_db", lambda: fake_db)
    return fake_db


def _login(client, email=None, password=None):
    body = {}
    if email is not None:
        body["email"] = email
    if password is not None:
        body["password"] = password
    return client.post("/api/auth/login", json=body)


def _access_csrf_header(client):
    cookie = client.get_cookie("csrf_access_token", path="/")
    assert cookie is not None, "csrf_access_token cookie was not set after login"
    return {"X-CSRF-TOKEN": cookie.value}


def _refresh_csrf_header(client):
    cookie = client.get_cookie("csrf_refresh_token", path="/")
    assert cookie is not None, "csrf_refresh_token cookie was not set after login"
    return {"X-CSRF-TOKEN": cookie.value}


class TestLoginHappyPath:
    def test_login_with_valid_credentials_returns_200(self, app_instance, monkeypatch):
        user = make_user(email="student@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            response = _login(client, "student@college.edu", FAKE_PASSWORD)

        assert response.status_code == 200

    def test_login_response_body_is_the_safe_profile_without_password_hash(
        self, app_instance, monkeypatch
    ):
        user = make_user(
            name="Jane Student", email="jane@college.edu", password=FAKE_PASSWORD, role="student"
        )
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            response = _login(client, "jane@college.edu", FAKE_PASSWORD)

        body = response.get_json()
        assert body["email"] == "jane@college.edu"
        assert body["name"] == "Jane Student"
        assert body["role"] == "student"
        assert body["id"] == str(user["_id"])
        assert "password_hash" not in body

    def test_login_response_never_leaks_the_password_hash_in_the_raw_body(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="jane2@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            response = _login(client, "jane2@college.edu", FAKE_PASSWORD)

        assert user["password_hash"] not in response.get_data(as_text=True)

    def test_login_sets_httponly_access_and_refresh_cookies(self, app_instance, monkeypatch):
        user = make_user(email="student2@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "student2@college.edu", FAKE_PASSWORD)

            access_cookie = client.get_cookie("access_token_cookie", path="/")
            refresh_cookie = client.get_cookie("refresh_token_cookie", path="/api/auth/refresh")

        assert access_cookie is not None
        assert refresh_cookie is not None
        assert access_cookie.http_only is True
        assert refresh_cookie.http_only is True

    def test_login_sets_readable_csrf_cookies_for_access_and_refresh(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="student3@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "student3@college.edu", FAKE_PASSWORD)

            access_csrf = client.get_cookie("csrf_access_token", path="/")
            refresh_csrf = client.get_cookie("csrf_refresh_token", path="/")

        assert access_csrf is not None
        assert refresh_csrf is not None
        # CSRF cookies must be JS-readable (not HttpOnly) -- the frontend
        # client reads them to populate the X-CSRF-TOKEN header.
        assert access_csrf.http_only is False
        assert refresh_csrf.http_only is False


class TestLoginFailureCasesAreIndistinguishable:
    def test_unknown_email_returns_401(self, app_instance, monkeypatch):
        _patch_db(monkeypatch, [])

        with app_instance.test_client() as client:
            response = _login(client, "nobody@college.edu", FAKE_PASSWORD)

        assert response.status_code == 401

    def test_wrong_password_returns_401(self, app_instance, monkeypatch):
        user = make_user(email="student4@college.edu", password=FAKE_PASSWORD)
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            response = _login(client, "student4@college.edu", "totally-wrong-fake-password")

        assert response.status_code == 401

    def test_deactivated_account_with_correct_password_returns_401(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="inactive@college.edu", password=FAKE_PASSWORD, is_active=False)
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            response = _login(client, "inactive@college.edu", FAKE_PASSWORD)

        assert response.status_code == 401

    def test_all_three_failure_modes_return_the_same_status_and_error_body(
        self, app_instance, monkeypatch
    ):
        active_user = make_user(email="student5@college.edu", password=FAKE_PASSWORD)
        inactive_user = make_user(
            email="inactive2@college.edu", password=FAKE_PASSWORD, is_active=False
        )
        _patch_db(monkeypatch, [active_user, inactive_user])

        with app_instance.test_client() as client:
            unknown_response = _login(client, "nobody2@college.edu", FAKE_PASSWORD)
            wrong_password_response = _login(client, "student5@college.edu", "nope-fake-password")
            deactivated_response = _login(client, "inactive2@college.edu", FAKE_PASSWORD)

        responses = (unknown_response, wrong_password_response, deactivated_response)
        assert all(r.status_code == 401 for r in responses)

        error_messages = {r.get_json().get("error") for r in responses}
        assert len(error_messages) == 1, "login failures must be indistinguishable"

    def test_failed_login_sets_no_auth_cookies(self, app_instance, monkeypatch):
        _patch_db(monkeypatch, [])

        with app_instance.test_client() as client:
            _login(client, "nobody3@college.edu", FAKE_PASSWORD)

            assert client.get_cookie("access_token_cookie", path="/") is None
            assert client.get_cookie("refresh_token_cookie", path="/api/auth/refresh") is None


class TestLoginValidation:
    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": "student@college.edu"},
            {"password": "some-fake-password"},
            {"email": "", "password": "some-fake-password"},
            {"email": "student@college.edu", "password": ""},
            {"email": 12345, "password": "some-fake-password"},
        ],
        ids=[
            "empty-body",
            "missing-password",
            "missing-email",
            "empty-email",
            "empty-password",
            "non-string-email",
        ],
    )
    def test_missing_or_malformed_fields_return_400(self, app_instance, monkeypatch, body):
        _patch_db(monkeypatch, [])

        with app_instance.test_client() as client:
            response = client.post("/api/auth/login", json=body)

        assert response.status_code == 400


class TestLoginEmailNormalization:
    def test_login_authenticates_with_uppercase_and_padded_email(self, app_instance, monkeypatch):
        user = make_user(email="admin@college.edu", password=FAKE_PASSWORD, role="admin")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            response = _login(client, " Admin@College.edu ", FAKE_PASSWORD)

        assert response.status_code == 200
        assert response.get_json()["email"] == "admin@college.edu"


class TestMeEndpoint:
    def test_me_returns_401_without_any_cookies(self, app_instance, monkeypatch):
        _patch_db(monkeypatch, [])

        with app_instance.test_client() as client:
            response = client.get("/api/auth/me")

        assert response.status_code == 401

    def test_me_returns_the_current_users_safe_profile_with_valid_cookies(
        self, app_instance, monkeypatch
    ):
        user = make_user(
            name="Faculty Member",
            email="faculty@college.edu",
            password=FAKE_PASSWORD,
            role="faculty",
        )
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "faculty@college.edu", FAKE_PASSWORD)
            response = client.get("/api/auth/me")

        assert response.status_code == 200
        body = response.get_json()
        assert body["email"] == "faculty@college.edu"
        assert body["role"] == "faculty"
        assert "password_hash" not in body

    def test_me_does_not_require_a_csrf_header_since_it_is_a_get(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="reader@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "reader@college.edu", FAKE_PASSWORD)
            response = client.get("/api/auth/me")  # deliberately no X-CSRF-TOKEN header

        assert response.status_code == 200


class TestRefreshEndpoint:
    def test_refresh_with_valid_refresh_cookie_issues_a_new_access_cookie(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="refresh1@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "refresh1@college.edu", FAKE_PASSWORD)

            response = client.post("/api/auth/refresh", headers=_refresh_csrf_header(client))

            new_access_cookie = client.get_cookie("access_token_cookie", path="/")

        assert response.status_code == 200
        assert new_access_cookie is not None

    def test_refresh_with_only_an_access_cookie_is_rejected(self, app_instance, monkeypatch):
        user = make_user(email="refresh2@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "refresh2@college.edu", FAKE_PASSWORD)

            # Drop the refresh cookie (and its CSRF cookie) so only the
            # access cookie remains, then attempt to refresh with it.
            client.delete_cookie("refresh_token_cookie", path="/api/auth/refresh")
            client.delete_cookie("csrf_refresh_token", path="/")

            response = client.post(
                "/api/auth/refresh",
                headers={"X-CSRF-TOKEN": "irrelevant-because-refresh-cookie-is-gone"},
            )

        assert response.status_code == 401

    def test_refresh_without_any_cookies_is_rejected(self, app_instance, monkeypatch):
        _patch_db(monkeypatch, [])

        with app_instance.test_client() as client:
            response = client.post("/api/auth/refresh")

        assert response.status_code == 401


class TestLogoutEndpoint:
    def test_logout_requires_authentication(self, app_instance, monkeypatch):
        _patch_db(monkeypatch, [])

        with app_instance.test_client() as client:
            response = client.post("/api/auth/logout")

        assert response.status_code == 401

    def test_logout_clears_cookies_so_me_is_then_unauthorized(self, app_instance, monkeypatch):
        user = make_user(email="logout1@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "logout1@college.edu", FAKE_PASSWORD)

            logout_response = client.post(
                "/api/auth/logout", headers=_access_csrf_header(client)
            )
            me_response = client.get("/api/auth/me")

        assert logout_response.status_code == 200
        assert me_response.status_code == 401


class TestCsrfEnforcementOnMutatingEndpoints:
    """Per spec: a state-changing request with valid auth cookies but a
    missing/incorrect X-CSRF-TOKEN header must be rejected -- proving CSRF
    protection (JWT_COOKIE_CSRF_PROTECT) is demonstrably active. `logout`
    is used as the representative mutating (POST), authenticated endpoint.
    """

    def test_mutating_request_without_a_csrf_header_is_rejected(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="csrf1@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "csrf1@college.edu", FAKE_PASSWORD)

            response = client.post("/api/auth/logout")  # no X-CSRF-TOKEN header

        assert response.status_code == 401

    def test_mutating_request_with_an_incorrect_csrf_header_is_rejected(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="csrf2@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "csrf2@college.edu", FAKE_PASSWORD)

            response = client.post(
                "/api/auth/logout", headers={"X-CSRF-TOKEN": "not-the-real-csrf-token"}
            )

        assert response.status_code == 401

    def test_mutating_request_with_the_correct_csrf_header_is_accepted(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="csrf3@college.edu", password=FAKE_PASSWORD, role="student")
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "csrf3@college.edu", FAKE_PASSWORD)

            response = client.post("/api/auth/logout", headers=_access_csrf_header(client))

        assert response.status_code == 200


class TestRoleRequiredEnforcement:
    """Exercises `auth.decorators.role_required` end-to-end through a
    temporary, test-only protected route registered directly on the test's
    own `app_instance` (function-scoped, so this never leaks into other
    tests). This is the primitive every later protected route in the
    project builds on, per the spec's Definition of done:
    "An endpoint guarded by role_required('admin') returns 403 for an
    authenticated faculty or student token, and 200 for an admin".
    """

    @staticmethod
    def _register_admin_only_route(app_instance):
        from auth.decorators import role_required
        from flask import jsonify

        @app_instance.route("/api/test-only/admin-only", methods=["GET"])
        @role_required("admin")
        def _admin_only_probe():
            return jsonify({"ok": True}), 200

    def test_admin_can_access_an_admin_only_route(self, app_instance, monkeypatch):
        self._register_admin_only_route(app_instance)
        admin = make_user(email="admin-role@college.edu", password=FAKE_PASSWORD, role="admin")
        _patch_db(monkeypatch, [admin])

        with app_instance.test_client() as client:
            _login(client, "admin-role@college.edu", FAKE_PASSWORD)
            response = client.get("/api/test-only/admin-only")

        assert response.status_code == 200

    @pytest.mark.parametrize("role", ["faculty", "student"])
    def test_non_admin_roles_are_forbidden_from_an_admin_only_route(
        self, app_instance, monkeypatch, role
    ):
        self._register_admin_only_route(app_instance)
        user = make_user(email=f"{role}-role@college.edu", password=FAKE_PASSWORD, role=role)
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, f"{role}-role@college.edu", FAKE_PASSWORD)
            response = client.get("/api/test-only/admin-only")

        assert response.status_code == 403

    def test_unauthenticated_request_to_a_role_guarded_route_is_401_not_403(
        self, app_instance, monkeypatch
    ):
        self._register_admin_only_route(app_instance)
        _patch_db(monkeypatch, [])

        with app_instance.test_client() as client:
            response = client.get("/api/test-only/admin-only")

        assert response.status_code == 401
