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

import logging
from datetime import datetime, timedelta, timezone

import pytest
from auth.passwords import hash_password, verify_password
from auth.reset_codes import (
    CODE_TTL_MINUTES,
    MAX_ATTEMPTS,
    MAX_SUBMITTED_CODE_LENGTH,
    RESEND_COOLDOWN_SECONDS,
)
from auth_test_helpers import make_fake_db, make_user
from database.schema import PASSWORD_RESET_CODES, USERS
from flask_jwt_extended import create_refresh_token, decode_token, get_csrf_token
from notifications.settings import SmtpSettings
from notifications.errors import MailerNotConfiguredError, MailerSendError

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


# --------------------------------------------------------------------
# 24-invalidate-tokens-on-password-change
# --------------------------------------------------------------------


def _install_refresh_token(app, client, token):
    """Give `client` a refresh cookie built outside the login route.

    Used to hold a token this codebase can no longer mint: one with no
    `token_version` claim, which is what every refresh cookie issued
    before 24 looks like. The CSRF cookie has to be planted alongside it
    and has to match, since JWT_COOKIE_CSRF_PROTECT is on -- the value
    is carried inside the token itself, which is what get_csrf_token
    reads (decoding it, hence the app context).
    """
    with app.app_context():
        csrf = get_csrf_token(token)
    client.set_cookie("refresh_token_cookie", token, path="/api/auth/refresh")
    client.set_cookie("csrf_refresh_token", csrf, path="/")


def _refresh(client):
    return client.post("/api/auth/refresh", headers=_refresh_csrf_header(client))


class TestRefreshCarriesTheTokenVersion:
    """The claim exists and says what the document says (DoD contract for
    login; the checking half lives in test_auth_password_change.py).
    """

    def test_login_stamps_the_users_version_into_the_refresh_token(
        self, app_instance, monkeypatch
    ):
        user = make_user(
            email="stamp@college.edu", password=FAKE_PASSWORD, token_version=6
        )
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "stamp@college.edu", FAKE_PASSWORD)
            token = client.get_cookie(
                "refresh_token_cookie", path="/api/auth/refresh"
            ).value
            with app_instance.app_context():
                claims = decode_token(token)

        assert claims["token_version"] == 6

    def test_login_stamps_zero_for_a_document_without_the_field(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="stamp0@college.edu", password=FAKE_PASSWORD)
        assert "token_version" not in user
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "stamp0@college.edu", FAKE_PASSWORD)
            token = client.get_cookie(
                "refresh_token_cookie", path="/api/auth/refresh"
            ).value
            with app_instance.app_context():
                claims = decode_token(token)

        assert claims["token_version"] == 0

    def test_the_access_token_carries_no_version_claim(
        self, app_instance, monkeypatch
    ):
        """Rule 9: nothing reads a version on an access token, and a
        claim nobody checks reads like a guarantee it is not.
        """
        user = make_user(
            email="noclaim@college.edu", password=FAKE_PASSWORD, token_version=2
        )
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "noclaim@college.edu", FAKE_PASSWORD)
            token = client.get_cookie("access_token_cookie", path="/").value
            with app_instance.app_context():
                claims = decode_token(token)

        assert "token_version" not in claims
        assert claims["role"] == "student"


class TestTokensMintedBeforeThisFeature:
    """DoD 11 and 12: deploying 24 must not sign anybody out.

    A refresh token in the wild right now carries no `token_version`
    claim, and the user document it points at has no `token_version`
    field. Both read as 0, so they match and the session continues --
    until that account's first password change, which is the moment it
    is supposed to stop.
    """

    def _pre_24_token(self, app_instance, user):
        """A refresh token exactly as 03-authentication minted them:
        identity only, no additional claims."""
        with app_instance.app_context():
            return create_refresh_token(identity=str(user["_id"]))

    def test_a_claimless_token_still_refreshes_against_a_fieldless_document(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="legacy@college.edu", password=FAKE_PASSWORD)
        assert "token_version" not in user
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _install_refresh_token(
                app_instance, client, self._pre_24_token(app_instance, user)
            )
            response = _refresh(client)

        assert response.status_code == 200

    def test_a_claimless_token_still_refreshes_against_a_document_at_zero(
        self, app_instance, monkeypatch
    ):
        """The other half of the same case: accounts created after 24
        carry an explicit 0, and a token from before it must still
        match."""
        user = make_user(
            email="legacy0@college.edu", password=FAKE_PASSWORD, token_version=0
        )
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _install_refresh_token(
                app_instance, client, self._pre_24_token(app_instance, user)
            )
            response = _refresh(client)

        assert response.status_code == 200

    def test_a_claimless_token_is_rejected_once_the_account_moves_on(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="legacy1@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _install_refresh_token(
                app_instance, client, self._pre_24_token(app_instance, user)
            )
            assert _refresh(client).status_code == 200, "precondition"

            # One password change on this account, by any route.
            fake_db[USERS].find_one_and_update(
                {"_id": user["_id"]}, {"$inc": {"token_version": 1}}
            )

            assert _refresh(client).status_code == 401

    def test_a_deactivated_account_is_still_rejected_regardless_of_version(
        self, app_instance, monkeypatch
    ):
        """The is_active check did not move or weaken."""
        user = make_user(
            email="legacy2@college.edu", password=FAKE_PASSWORD, token_version=0
        )
        fake_db = _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _install_refresh_token(
                app_instance, client, self._pre_24_token(app_instance, user)
            )
            fake_db[USERS].find_one_and_update(
                {"_id": user["_id"]}, {"$set": {"is_active": False}}
            )

            assert _refresh(client).status_code == 401


class TestTokenVersionNeverAppearsInPublicResponses:
    """DoD 21: the counter travels in a signed cookie and in MongoDB and
    nowhere else -- `to_safe_profile` (login, /me) never grows the field.

    Each user carries an explicit, non-zero version so this proves the
    key is excluded rather than merely absent from the source document.
    """

    def test_login_response_body_never_contains_token_version(
        self, app_instance, monkeypatch
    ):
        user = make_user(
            email="noleaklogin@college.edu", password=FAKE_PASSWORD, token_version=3
        )
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            response = _login(client, "noleaklogin@college.edu", FAKE_PASSWORD)

        assert response.status_code == 200
        assert "token_version" not in response.get_data(as_text=True)

    def test_me_response_body_never_contains_token_version(
        self, app_instance, monkeypatch
    ):
        user = make_user(
            email="noleakme@college.edu", password=FAKE_PASSWORD, token_version=3
        )
        _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            _login(client, "noleakme@college.edu", FAKE_PASSWORD)
            response = client.get("/api/auth/me")

        assert response.status_code == 200
        assert "token_version" not in response.get_data(as_text=True)


# --------------------------------------------------------------------
# 25-forgot-password
# --------------------------------------------------------------------

FAKE_RESET_CODE = "482913"
FAKE_RESET_PASSWORD = "a-fake-reset-password-1"
GENERIC_REQUEST_BODY = {
    "message": "If that email is registered, a reset code has been sent."
}
INVALID_CODE_BODY = {"error": "That code is not valid or has expired."}


def _patch_reset_db(monkeypatch, users=None, reset_codes=None):
    fake_db = make_fake_db(users, reset_codes)
    monkeypatch.setattr("routes.auth.get_db", lambda: fake_db)
    return fake_db


def _forgot(client, email=None):
    body = {} if email is None else {"email": email}
    return client.post("/api/auth/forgot-password", json=body)


def _reset(client, email=None, code=None, new_password=None):
    body = {}
    if email is not None:
        body["email"] = email
    if code is not None:
        body["code"] = code
    if new_password is not None:
        body["new_password"] = new_password
    return client.post("/api/auth/reset-password", json=body)


def _fake_smtp_settings():
    """A stand-in for what `load_smtp_settings` returns.

    The real `SmtpSettings` rather than an ad-hoc object, deliberately:
    routes/auth.py reads `.sender` and `.sender_name` off it to build the
    message, so a double that does not carry the real shape would pass
    while the route was broken -- and a bare object() fails with an
    AttributeError that looks like a route bug rather than a test one.

    Every value is obviously fake and nothing here opens a connection;
    the transport is stubbed separately.
    """
    return SmtpSettings(
        host="localhost.test",
        port=1025,
        username="fake-test-user",
        password="fake-test-password",
        sender="autoattend@localhost.test",
        sender_name="AutoAttend Test",
        use_tls=False,
    )


def _stub_configured_smtp(monkeypatch):
    """Fakes routes.auth's mail path so no test opens a socket.

    Returns the list of `EmailMessage`s `forgot_password` "sent" -- empty
    when the endpoint decided not to send at all (unknown address,
    deactivated account, or inside the cooldown).
    """
    sent = []

    class _FakeTransport:
        def __init__(self, settings):
            self._settings = settings

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def send(self, message):
            sent.append(message)

    monkeypatch.setattr("routes.auth.load_smtp_settings", lambda config: _fake_smtp_settings())
    monkeypatch.setattr("routes.auth.SmtpTransport", _FakeTransport)
    return sent


def _stub_failing_smtp(monkeypatch):
    """Like `_stub_configured_smtp`, but every send raises
    `MailerSendError` -- for proving DoD 6."""

    class _FailingTransport:
        def __init__(self, settings):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def send(self, message):
            raise MailerSendError("The mail server refused a message.")

    monkeypatch.setattr("routes.auth.load_smtp_settings", lambda config: _fake_smtp_settings())
    monkeypatch.setattr("routes.auth.SmtpTransport", _FailingTransport)


def _stub_unconfigured_smtp(monkeypatch):
    """No SMTP_* variables set -- load_smtp_settings raises before any
    user lookup, per rule 1."""

    def _raise(config):
        raise MailerNotConfiguredError("SMTP_HOST is not set")

    monkeypatch.setattr("routes.auth.load_smtp_settings", _raise)


def _seed_reset_code(
    fake_db,
    user,
    *,
    code=FAKE_RESET_CODE,
    attempts=0,
    expires_at=None,
    consumed_at=None,
    created_at=None,
    email=None,
):
    now = datetime.now(timezone.utc)
    document = {
        "user_id": user["_id"],
        "code_hash": hash_password(code),
        "expires_at": expires_at if expires_at is not None else now + timedelta(minutes=CODE_TTL_MINUTES),
        "attempts": attempts,
        "created_at": created_at if created_at is not None else now,
        "email": email or user["email"],
    }
    if consumed_at is not None:
        document["consumed_at"] = consumed_at
    fake_db[PASSWORD_RESET_CODES].insert_one(document)
    return document


def _reset_codes_for(fake_db, user):
    return list(fake_db[PASSWORD_RESET_CODES].find({"user_id": user["_id"]}))


class TestForgotPasswordIsNotAnEnumerationOracle:
    """DoD 1, 2: every dimension of "does this address have an account"
    -- unknown, deactivated, inside its cooldown -- produces the
    identical response as a real, sendable address.
    """

    def test_a_registered_active_address_returns_the_generic_200(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="fp-active@college.edu", password=FAKE_PASSWORD)
        _patch_reset_db(monkeypatch, [user])
        _stub_configured_smtp(monkeypatch)

        with app_instance.test_client() as client:
            response = _forgot(client, "fp-active@college.edu")

        assert response.status_code == 200
        assert response.get_json() == GENERIC_REQUEST_BODY

    def test_unknown_deactivated_and_cooldown_addresses_answer_identically_to_a_real_one(
        self, app_instance, monkeypatch
    ):
        active = make_user(email="fp-real@college.edu", password=FAKE_PASSWORD)
        inactive = make_user(
            email="fp-inactive@college.edu", password=FAKE_PASSWORD, is_active=False
        )
        cooling = make_user(email="fp-cooling@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [active, inactive, cooling])
        _seed_reset_code(fake_db, cooling)
        _stub_configured_smtp(monkeypatch)

        with app_instance.test_client() as client:
            real_response = _forgot(client, "fp-real@college.edu")
            unknown_response = _forgot(client, "fp-nobody@college.edu")
            inactive_response = _forgot(client, "fp-inactive@college.edu")
            cooldown_response = _forgot(client, "fp-cooling@college.edu")

        responses = (real_response, unknown_response, inactive_response, cooldown_response)
        assert {r.status_code for r in responses} == {200}
        assert len({r.get_data() for r in responses}) == 1, (
            "forgot-password must answer byte-identically regardless of "
            "whether, or why, an address does not resolve"
        )

    def test_a_send_failure_still_returns_the_generic_200(self, app_instance, monkeypatch):
        user = make_user(email="fp-failsend@college.edu", password=FAKE_PASSWORD)
        _patch_reset_db(monkeypatch, [user])
        _stub_failing_smtp(monkeypatch)

        with app_instance.test_client() as client:
            response = _forgot(client, "fp-failsend@college.edu")

        assert response.status_code == 200
        assert response.get_json() == GENERIC_REQUEST_BODY


class TestForgotPasswordWritesAndSends:
    def test_a_registered_address_gets_exactly_one_usable_code_document(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="fp-write@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        sent = _stub_configured_smtp(monkeypatch)

        with app_instance.test_client() as client:
            _forgot(client, "fp-write@college.edu")

        rows = _reset_codes_for(fake_db, user)
        assert len(rows) == 1
        row = rows[0]
        assert row["code_hash"] != FAKE_RESET_CODE
        assert row["attempts"] == 0
        assert "consumed_at" not in row
        assert row["expires_at"] > datetime.now(timezone.utc)
        assert len(sent) == 1

    def test_unknown_deactivated_and_cooldown_addresses_write_no_document_and_send_no_mail(
        self, app_instance, monkeypatch
    ):
        inactive = make_user(
            email="fp-nowrite-inactive@college.edu", password=FAKE_PASSWORD, is_active=False
        )
        cooling = make_user(email="fp-nowrite-cooling@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [inactive, cooling])
        _seed_reset_code(fake_db, cooling)
        sent = _stub_configured_smtp(monkeypatch)

        with app_instance.test_client() as client:
            _forgot(client, "fp-nowrite-unknown@college.edu")
            _forgot(client, "fp-nowrite-inactive@college.edu")
            _forgot(client, "fp-nowrite-cooling@college.edu")

        assert _reset_codes_for(fake_db, inactive) == []
        # cooling already had exactly the one row it started with -- a
        # second request inside the cooldown must not add another.
        assert len(_reset_codes_for(fake_db, cooling)) == 1
        assert sent == []

    def test_a_send_failure_still_leaves_the_written_row_in_place(
        self, app_instance, monkeypatch
    ):
        """Concurrency-fix behaviour (reversed from the original design):
        `issue_reset_code` writes the row BEFORE any mail is sent, so a
        failed send afterwards does not roll it back -- the row holds a
        code the user never received, which is harmless (unguessable,
        expiring, replaced by the next request past the cooldown), and
        removing it would hand back the cooldown slot this request just
        claimed."""
        user = make_user(email="fp-failwrite@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _stub_failing_smtp(monkeypatch)

        with app_instance.test_client() as client:
            _forgot(client, "fp-failwrite@college.edu")

        rows = _reset_codes_for(fake_db, user)
        assert len(rows) == 1
        assert rows[0]["attempts"] == 0
        assert "consumed_at" not in rows[0]

    def test_a_send_failure_still_replaces_whatever_code_the_user_was_holding(
        self, app_instance, monkeypatch
    ):
        """The price named in the module docstring: issuing claims the
        cooldown slot and writes the new row unconditionally, so a send
        that then fails has already replaced the previous code -- the old
        one stops working even though the new one was never delivered."""
        user = make_user(email="fp-keepcode@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        existing = _seed_reset_code(
            fake_db,
            user,
            code=FAKE_RESET_CODE,
            created_at=datetime.now(timezone.utc)
            - timedelta(seconds=RESEND_COOLDOWN_SECONDS + 5),
        )
        _stub_failing_smtp(monkeypatch)

        with app_instance.test_client() as client:
            _forgot(client, "fp-keepcode@college.edu")

        rows = _reset_codes_for(fake_db, user)
        assert len(rows) == 1
        assert rows[0]["code_hash"] != existing["code_hash"], (
            "the old code must no longer be the one stored -- it has been "
            "replaced even though the replacement was never delivered"
        )


class TestForgotPasswordValidation:
    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"email": ""},
            {"email": 12345},
            {"email": "not-an-email-address"},
        ],
        ids=["missing", "empty", "non-string", "not-address-shaped"],
    )
    def test_malformed_email_is_400_and_writes_nothing(
        self, app_instance, monkeypatch, body
    ):
        fake_db = _patch_reset_db(monkeypatch, [])
        _stub_configured_smtp(monkeypatch)

        with app_instance.test_client() as client:
            response = client.post("/api/auth/forgot-password", json=body)

        assert response.status_code == 400
        assert fake_db[PASSWORD_RESET_CODES]._documents == []

    def test_no_authentication_is_required(self, app_instance, monkeypatch):
        user = make_user(email="fp-noauth@college.edu", password=FAKE_PASSWORD)
        _patch_reset_db(monkeypatch, [user])
        _stub_configured_smtp(monkeypatch)

        with app_instance.test_client() as client:  # no login call at all
            response = _forgot(client, "fp-noauth@college.edu")

        assert response.status_code == 200


class TestForgotPasswordSmtpMisconfiguration:
    """Rule 1: `load_smtp_settings` runs BEFORE any user lookup, so a
    misconfigured server answers every address alike."""

    def test_unconfigured_smtp_returns_503_for_a_registered_address(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="fp-noserver@college.edu", password=FAKE_PASSWORD)
        _patch_reset_db(monkeypatch, [user])
        _stub_unconfigured_smtp(monkeypatch)

        with app_instance.test_client() as client:
            response = _forgot(client, "fp-noserver@college.edu")

        assert response.status_code == 503

    def test_unconfigured_smtp_returns_the_same_503_for_an_unknown_address(
        self, app_instance, monkeypatch
    ):
        _patch_reset_db(monkeypatch, [])
        _stub_unconfigured_smtp(monkeypatch)

        with app_instance.test_client() as client:
            response = _forgot(client, "fp-noaccount@college.edu")

        assert response.status_code == 503

    def test_unconfigured_smtp_response_body_names_no_setting(
        self, app_instance, monkeypatch
    ):
        """The exception's own message names a variable; the HTTP body
        must not."""
        _patch_reset_db(monkeypatch, [])
        _stub_unconfigured_smtp(monkeypatch)

        with app_instance.test_client() as client:
            response = _forgot(client, "fp-anything@college.edu")

        assert "SMTP_HOST" not in response.get_data(as_text=True)

    def test_unconfigured_smtp_writes_no_document(self, app_instance, monkeypatch):
        user = make_user(email="fp-nowrite-noserver@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _stub_unconfigured_smtp(monkeypatch)

        with app_instance.test_client() as client:
            _forgot(client, "fp-nowrite-noserver@college.edu")

        assert _reset_codes_for(fake_db, user) == []


class TestResetPasswordSpendsACodeAndSetsAPassword:
    def test_the_correct_code_resets_the_password(self, app_instance, monkeypatch):
        user = make_user(email="rp-ok@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-ok@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert response.status_code == 200
        assert response.get_json() == {"message": "Password reset"}

    def test_the_new_password_logs_in_and_the_old_one_does_not(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="rp-cycle@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            _reset(client, "rp-cycle@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD)

            new_login = _login(client, "rp-cycle@college.edu", FAKE_RESET_PASSWORD)
            old_login = _login(client, "rp-cycle@college.edu", FAKE_PASSWORD)

        assert new_login.status_code == 200
        assert old_login.status_code == 401

    def test_the_response_sets_no_cookies_at_all(self, app_instance, monkeypatch):
        """DoD 8: not a login -- no access, refresh, or CSRF cookie."""
        user = make_user(email="rp-nocookie@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-nocookie@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert response.headers.getlist("Set-Cookie") == []

    def test_the_response_body_carries_no_profile_or_id(self, app_instance, monkeypatch):
        user = make_user(email="rp-noprofile@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-noprofile@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        text = response.get_data(as_text=True)
        assert str(user["_id"]) not in text
        assert "role" not in text
        assert "email" not in text

    def test_no_authentication_is_required(self, app_instance, monkeypatch):
        user = make_user(email="rp-noauth@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:  # no login call at all
            response = _reset(
                client, "rp-noauth@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert response.status_code == 200


class TestResetPasswordCodeFailuresAreOneMessageAndNever401:
    def test_a_wrong_code_is_400_with_the_generic_message(self, app_instance, monkeypatch):
        user = make_user(email="rp-wrong@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            response = _reset(client, "rp-wrong@college.edu", "000000", FAKE_RESET_PASSWORD)

        assert response.status_code == 400
        assert response.get_json() == INVALID_CODE_BODY

    def test_a_wrong_code_increments_attempts_and_leaves_the_code_usable(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="rp-attempts@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            wrong = _reset(client, "rp-attempts@college.edu", "000000", FAKE_RESET_PASSWORD)
            assert wrong.status_code == 400

            rows = _reset_codes_for(fake_db, user)
            assert len(rows) == 1
            assert rows[0]["attempts"] == 1

            correct = _reset(
                client, "rp-attempts@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert correct.status_code == 200

    def test_no_route_on_this_path_ever_returns_401(self, app_instance, monkeypatch):
        """Rule 6: `apiFetch` retries any non-login 401, which would burn
        a second attempt per wrong guess."""
        user = make_user(email="rp-not401@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            response = _reset(client, "rp-not401@college.edu", "000000", FAKE_RESET_PASSWORD)

        assert response.status_code == 400

    def test_replaying_the_same_code_is_refused_and_the_password_is_unchanged(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="rp-replay@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            first = _reset(
                client, "rp-replay@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )
            second = _reset(
                client, "rp-replay@college.edu", FAKE_RESET_CODE, "a-fake-second-password-1"
            )

        assert first.status_code == 200
        assert second.status_code == 400
        assert second.get_json() == INVALID_CODE_BODY
        stored = fake_db[USERS].find_one({"_id": user["_id"]})
        assert verify_password(FAKE_RESET_PASSWORD, stored["password_hash"])
        assert not verify_password("a-fake-second-password-1", stored["password_hash"])

    def test_an_expired_code_is_refused_even_though_the_row_still_exists(
        self, app_instance, monkeypatch
    ):
        """DoD 12: proves expiry is enforced in code, not by a sweep --
        there is no TTL index."""
        user = make_user(email="rp-expired@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(
            fake_db,
            user,
            code=FAKE_RESET_CODE,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-expired@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert response.status_code == 400
        assert response.get_json() == INVALID_CODE_BODY
        assert _reset_codes_for(fake_db, user), "no sweep has run -- the row is still there"

    def test_attempts_exhausted_refuses_even_the_correct_code(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="rp-exhausted@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE, attempts=MAX_ATTEMPTS)

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-exhausted@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert response.status_code == 400
        assert response.get_json() == INVALID_CODE_BODY

    def test_a_code_issued_for_one_account_cannot_reset_another(
        self, app_instance, monkeypatch
    ):
        owner = make_user(email="rp-owner@college.edu", password=FAKE_PASSWORD)
        bystander = make_user(email="rp-bystander@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [owner, bystander])
        _seed_reset_code(fake_db, owner, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-bystander@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert response.status_code == 400
        assert response.get_json() == INVALID_CODE_BODY
        assert verify_password(
            FAKE_PASSWORD,
            fake_db[USERS].find_one({"_id": bystander["_id"]})["password_hash"],
        )
        assert verify_password(
            FAKE_PASSWORD,
            fake_db[USERS].find_one({"_id": owner["_id"]})["password_hash"],
        )

    def test_no_code_ever_issued_returns_the_generic_error(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="rp-nocode@college.edu", password=FAKE_PASSWORD)
        _patch_reset_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-nocode@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert response.status_code == 400
        assert response.get_json() == INVALID_CODE_BODY

    def test_deactivated_account_between_issue_and_use_is_refused(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="rp-deactivated@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)
        fake_db[USERS].find_one_and_update(
            {"_id": user["_id"]}, {"$set": {"is_active": False}}
        )

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-deactivated@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert response.status_code == 400
        assert response.get_json() == INVALID_CODE_BODY
        assert verify_password(
            FAKE_PASSWORD,
            fake_db[USERS].find_one({"_id": user["_id"]})["password_hash"],
        )

    def test_an_unknown_email_returns_the_generic_error(self, app_instance, monkeypatch):
        _patch_reset_db(monkeypatch, [])

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-ghost@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert response.status_code == 400
        assert response.get_json() == INVALID_CODE_BODY


class TestResetPasswordValidation:
    @pytest.mark.parametrize(
        "body",
        [
            {"code": FAKE_RESET_CODE, "new_password": FAKE_RESET_PASSWORD},
            {"email": "", "code": FAKE_RESET_CODE, "new_password": FAKE_RESET_PASSWORD},
            {"email": 12345, "code": FAKE_RESET_CODE, "new_password": FAKE_RESET_PASSWORD},
            {
                "email": "not-an-email",
                "code": FAKE_RESET_CODE,
                "new_password": FAKE_RESET_PASSWORD,
            },
        ],
        ids=["missing-email", "empty-email", "non-string-email", "not-address-shaped"],
    )
    def test_a_malformed_email_is_400(self, app_instance, monkeypatch, body):
        _patch_reset_db(monkeypatch, [])

        with app_instance.test_client() as client:
            response = client.post("/api/auth/reset-password", json=body)

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "body",
        [
            {"email": "rp-code@college.edu", "new_password": FAKE_RESET_PASSWORD},
            {"email": "rp-code@college.edu", "code": "", "new_password": FAKE_RESET_PASSWORD},
            {"email": "rp-code@college.edu", "code": 123456, "new_password": FAKE_RESET_PASSWORD},
        ],
        ids=["missing-code", "empty-code", "non-string-code"],
    )
    def test_a_malformed_code_is_400(self, app_instance, monkeypatch, body):
        user = make_user(email="rp-code@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            response = client.post("/api/auth/reset-password", json=body)

        assert response.status_code == 400

    def test_a_code_longer_than_the_maximum_is_400_before_any_hash_comparison(
        self, app_instance, monkeypatch
    ):
        """A code is six digits; without a ceiling a caller could post a
        very long string and make the server hash all of it. The cap is
        enforced by `require_bounded_string` before `reset_password_with_code`
        is ever called, so this must not consume or even look at any
        stored code."""
        user = make_user(email="rp-toolong@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)
        too_long_code = "1" * (MAX_SUBMITTED_CODE_LENGTH + 1)

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-toolong@college.edu", too_long_code, FAKE_RESET_PASSWORD
            )

        assert response.status_code == 400
        rows = _reset_codes_for(fake_db, user)
        assert len(rows) == 1
        assert rows[0]["attempts"] == 0
        assert "consumed_at" not in rows[0]

    def test_a_code_at_exactly_the_maximum_length_is_not_rejected_for_its_length(
        self, app_instance, monkeypatch
    ):
        """The boundary itself is accepted by the length check -- it then
        fails as an ordinary wrong code, indistinguishably from any other,
        which is the generic 400 rather than a length-specific one."""
        user = make_user(email="rp-atmax@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)
        exactly_max_code = "1" * MAX_SUBMITTED_CODE_LENGTH

        with app_instance.test_client() as client:
            response = _reset(
                client, "rp-atmax@college.edu", exactly_max_code, FAKE_RESET_PASSWORD
            )

        assert response.status_code == 400
        assert response.get_json() == INVALID_CODE_BODY

    def test_a_new_password_shorter_than_the_minimum_does_not_consume_the_code(
        self, app_instance, monkeypatch
    ):
        """DoD 14: a length mistake must not cost the user their code."""
        user = make_user(email="rp-short@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            short_response = _reset(client, "rp-short@college.edu", FAKE_RESET_CODE, "short")

            rows = _reset_codes_for(fake_db, user)
            assert len(rows) == 1
            assert "consumed_at" not in rows[0]
            assert rows[0]["attempts"] == 0

            recovered_response = _reset(
                client, "rp-short@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert short_response.status_code == 400
        assert short_response.get_json() != INVALID_CODE_BODY
        assert recovered_response.status_code == 200


class TestResetPasswordInheritsSessionInvalidation:
    """DoD 17, 18: a reset ends every existing session on the account,
    with no reset-specific invalidation code -- inherited from
    `set_user_password` exactly as `24` built it.
    """

    def test_a_session_established_before_the_reset_fails_its_next_refresh(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="rp-session@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        other = app_instance.test_client()
        assert _login(other, "rp-session@college.edu", FAKE_PASSWORD).status_code == 200
        assert _refresh(other).status_code == 200, "precondition: it worked before"

        with app_instance.test_client() as client:
            reset_response = _reset(
                client, "rp-session@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        assert reset_response.status_code == 200
        assert _refresh(other).status_code == 401

    def test_password_hash_and_token_version_move_together(
        self, app_instance, monkeypatch
    ):
        user = make_user(
            email="rp-together@college.edu", password=FAKE_PASSWORD, token_version=2
        )
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            _reset(client, "rp-together@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD)

        stored = fake_db[USERS].find_one({"_id": user["_id"]})
        assert verify_password(FAKE_RESET_PASSWORD, stored["password_hash"])
        assert stored["token_version"] == 3


class TestResetPasswordConcurrentSubmissions:
    """DoD 16: two submissions of the same valid code must not both
    reset the password."""

    def test_only_one_of_two_submissions_of_the_same_code_succeeds(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="rp-race@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        with app_instance.test_client() as client:
            first = _reset(
                client, "rp-race@college.edu", FAKE_RESET_CODE, "a-fake-race-password-1"
            )
            second = _reset(
                client, "rp-race@college.edu", FAKE_RESET_CODE, "a-fake-race-password-2"
            )

        statuses = {first.status_code, second.status_code}
        assert statuses == {200, 400}
        stored = fake_db[USERS].find_one({"_id": user["_id"]})
        assert verify_password("a-fake-race-password-1", stored["password_hash"])
        assert not verify_password("a-fake-race-password-2", stored["password_hash"])


class TestNoSecretEverAppearsInAResponseOrLog:
    def test_reset_password_responses_never_contain_the_code_or_a_hash(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="rp-leak@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)
        stored_hash = _reset_codes_for(fake_db, user)[0]["code_hash"]

        with app_instance.test_client() as client:
            wrong_response = _reset(
                client, "rp-leak@college.edu", "000000", FAKE_RESET_PASSWORD
            )
            right_response = _reset(
                client, "rp-leak@college.edu", FAKE_RESET_CODE, FAKE_RESET_PASSWORD
            )

        for response in (wrong_response, right_response):
            text = response.get_data(as_text=True)
            assert FAKE_RESET_CODE not in text
            assert stored_hash not in text
            assert "000000" not in text

    def test_forgot_password_never_logs_the_address(
        self, app_instance, monkeypatch, caplog
    ):
        user = make_user(email="fp-quiet@college.edu", password=FAKE_PASSWORD)
        _patch_reset_db(monkeypatch, [user])
        _stub_configured_smtp(monkeypatch)

        client = app_instance.test_client()
        with caplog.at_level(logging.DEBUG):
            _forgot(client, "fp-quiet@college.edu")

        assert "fp-quiet@college.edu" not in caplog.text

    def test_reset_password_wrong_attempt_never_logs_the_code_or_address(
        self, app_instance, monkeypatch, caplog
    ):
        user = make_user(email="rp-quiet@college.edu", password=FAKE_PASSWORD)
        fake_db = _patch_reset_db(monkeypatch, [user])
        _seed_reset_code(fake_db, user, code=FAKE_RESET_CODE)

        client = app_instance.test_client()
        with caplog.at_level(logging.DEBUG):
            _reset(client, "rp-quiet@college.edu", "000000", FAKE_RESET_PASSWORD)

        assert FAKE_RESET_CODE not in caplog.text
        assert "rp-quiet@college.edu" not in caplog.text
