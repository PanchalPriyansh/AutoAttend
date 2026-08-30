"""Tests for POST /api/auth/password (23-change-password.md).

Spec contract under test ("APIs" + "Rules for implementation" +
"Definition of done"):
  - Any signed-in role may change their OWN password. The account is the
    one the JWT identifies; no user id is accepted in the path or body.
  - 200 -> {"message": "Password changed"} and nothing else.
  - 403 (NOT 401) for a wrong current password, so the frontend's
    apiFetch does not transparently refresh-and-retry and submit the same
    wrong password twice.
  - 401 for no token, for a deactivated account, and for a token whose
    identity no longer exists -- `is_active` is re-read from the database
    rather than trusted from the token claim.
  - 400 for a missing/blank/non-string current_password, for a
    missing/non-string/too-short new_password, and for a new password
    equal to the current one.
  - MIN_PASSWORD_LENGTH applies to the NEW password only; the current one
    is never length-checked and never trimmed.
  - No password, hash, length, or prefix appears in any response body or
    log record.

`routes.auth.get_db` is monkeypatched at its point of use, backed by the
in-memory FakeUsersCollection from auth_test_helpers.py -- no live
MongoDB. Every password here is an obviously-fake, test-only value.
"""

import logging

import pytest
from auth.errors import InactiveAccountError, IncorrectPasswordError
from auth.password_change import change_password
from auth.passwords import verify_password
from auth_test_helpers import make_fake_db, make_user
from common.errors import ValidationError
from database.schema import USERS
from pymongo.errors import PyMongoError
from users.validators import MIN_PASSWORD_LENGTH, require_existing_password

FAKE_CURRENT = "a-fake-current-password-1"
FAKE_NEW = "a-fake-new-password-2"


def _patch_db(monkeypatch, users):
    fake_db = make_fake_db(users)
    monkeypatch.setattr("routes.auth.get_db", lambda: fake_db)
    return fake_db


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _csrf_header(client):
    cookie = client.get_cookie("csrf_access_token", path="/")
    assert cookie is not None, "csrf_access_token cookie was not set after login"
    return {"X-CSRF-TOKEN": cookie.value}


def _change(client, body):
    return client.post("/api/auth/password", json=body, headers=_csrf_header(client))


def _signed_in(app_instance, monkeypatch, user):
    """Yield a test client already holding this user's auth cookies, plus
    the fake db backing it."""
    fake_db = _patch_db(monkeypatch, [user])
    client = app_instance.test_client()
    assert _login(client, user["email"], FAKE_CURRENT).status_code == 200
    return client, fake_db


def _stored(fake_db, user):
    return fake_db[USERS].find_one({"_id": user["_id"]})


class TestAuthorization:
    def test_unauthenticated_request_is_401_and_changes_nothing(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="nobody@college.edu", password=FAKE_CURRENT)
        fake_db = _patch_db(monkeypatch, [user])

        with app_instance.test_client() as client:
            response = client.post(
                "/api/auth/password",
                json={"current_password": FAKE_CURRENT, "new_password": FAKE_NEW},
            )

        assert response.status_code == 401
        assert verify_password(FAKE_CURRENT, _stored(fake_db, user)["password_hash"])

    @pytest.mark.parametrize("role", ["admin", "faculty", "student"])
    def test_every_role_can_change_their_own_password(
        self, app_instance, monkeypatch, role
    ):
        user = make_user(email=f"{role}@college.edu", password=FAKE_CURRENT, role=role)
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}
        )

        assert response.status_code == 200
        assert verify_password(FAKE_NEW, _stored(fake_db, user)["password_hash"])

    def test_written_document_has_the_same_shape_as_before(
        self, app_instance, monkeypatch
    ):
        """The write goes through set_user_password, so password_hash,
        updated_at and token_version move and nothing else does.

        `token_version` is named exactly rather than the assertion being
        loosened to "some fields may appear": 24 added precisely one
        field to this write, and the point of this test is that a future
        change cannot quietly add a second.
        """
        user = make_user(email="shape@college.edu", password=FAKE_CURRENT)
        before = set(user.keys())
        assert "token_version" not in before, (
            "make_user omits token_version by default, so this test also "
            "covers the live-data case of a document written before 24"
        )
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        _change(client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW})

        after = _stored(fake_db, user)
        assert set(after.keys()) == before | {"token_version"}
        assert after["role"] == user["role"]
        assert after["email"] == user["email"]
        assert after["is_active"] is True

    def test_deactivated_account_with_a_valid_token_is_401(
        self, app_instance, monkeypatch
    ):
        """`is_active` is re-read from the database, not trusted from the
        token claim -- 05 leaves a deactivated account holding a usable
        access token for up to 15 minutes."""
        user = make_user(email="gone@college.edu", password=FAKE_CURRENT)
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        _stored(fake_db, user)["is_active"] = False

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}
        )

        assert response.status_code == 401
        assert verify_password(FAKE_CURRENT, _stored(fake_db, user)["password_hash"])

    def test_token_whose_identity_no_longer_exists_is_401(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="deleted@college.edu", password=FAKE_CURRENT)
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        fake_db[USERS]._users.clear()

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}
        )

        assert response.status_code == 401

    def test_body_cannot_choose_which_account_is_written(
        self, app_instance, monkeypatch
    ):
        """id-shaped fields in the body are never read: the account is the
        one the token identifies."""
        victim = make_user(email="victim@college.edu", password=FAKE_CURRENT)
        actor = make_user(email="actor@college.edu", password=FAKE_CURRENT)
        fake_db = _patch_db(monkeypatch, [victim, actor])

        client = app_instance.test_client()
        assert _login(client, "actor@college.edu", FAKE_CURRENT).status_code == 200

        response = _change(
            client,
            {
                "current_password": FAKE_CURRENT,
                "new_password": FAKE_NEW,
                "user_id": str(victim["_id"]),
                "id": str(victim["_id"]),
                "email": victim["email"],
            },
        )

        assert response.status_code == 200
        assert verify_password(FAKE_NEW, _stored(fake_db, actor)["password_hash"])
        assert verify_password(FAKE_CURRENT, _stored(fake_db, victim)["password_hash"])


class TestValidation:
    @pytest.fixture
    def signed_in(self, app_instance, monkeypatch):
        user = make_user(email="valid@college.edu", password=FAKE_CURRENT)
        return _signed_in(app_instance, monkeypatch, user) + (user,)

    @pytest.mark.parametrize(
        "body",
        [
            {"new_password": FAKE_NEW},
            {"current_password": "", "new_password": FAKE_NEW},
            {"current_password": None, "new_password": FAKE_NEW},
            {"current_password": 12345678, "new_password": FAKE_NEW},
            {"current_password": True, "new_password": FAKE_NEW},
        ],
        ids=["missing", "empty", "null", "int", "bool"],
    )
    def test_bad_current_password_field_is_400(self, signed_in, body):
        client, fake_db, user = signed_in

        response = _change(client, body)

        assert response.status_code == 400
        assert verify_password(FAKE_CURRENT, _stored(fake_db, user)["password_hash"])

    @pytest.mark.parametrize(
        "body",
        [
            {"current_password": FAKE_CURRENT},
            {"current_password": FAKE_CURRENT, "new_password": None},
            {"current_password": FAKE_CURRENT, "new_password": 123456789},
            {"current_password": FAKE_CURRENT, "new_password": "short"},
            {"current_password": FAKE_CURRENT, "new_password": ""},
        ],
        ids=["missing", "null", "int", "too-short", "empty"],
    )
    def test_bad_new_password_field_is_400(self, signed_in, body):
        client, fake_db, user = signed_in

        response = _change(client, body)

        assert response.status_code == 400
        assert verify_password(FAKE_CURRENT, _stored(fake_db, user)["password_hash"])

    def test_too_short_new_password_names_the_minimum(self, signed_in):
        client, _, _ = signed_in

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": "short"}
        )

        assert str(MIN_PASSWORD_LENGTH) in response.get_json()["error"]

    def test_new_password_equal_to_current_is_400_and_does_not_touch_updated_at(
        self, signed_in
    ):
        client, fake_db, user = signed_in
        before = _stored(fake_db, user)["updated_at"]

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_CURRENT}
        )

        assert response.status_code == 400
        assert _stored(fake_db, user)["updated_at"] == before

    def test_no_op_change_reports_the_spec_message(self, signed_in):
        """Rule 6 gives the exact refusal text; this pins it down rather
        than a bare 400, so a future rewording is a deliberate spec change
        and not an accidental drift."""
        client, _, _ = signed_in

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_CURRENT}
        )

        assert response.get_json() == {
            "error": "New password must be different from the current password"
        }

    def test_current_password_is_never_length_checked(self, app_instance, monkeypatch):
        """A stored password shorter than today's minimum must still be
        usable to change itself -- otherwise raising the minimum locks out
        exactly the people who need this endpoint."""
        short = "old7chr"
        assert len(short) < MIN_PASSWORD_LENGTH
        user = make_user(email="legacy@college.edu", password=short)
        fake_db = _patch_db(monkeypatch, [user])

        client = app_instance.test_client()
        assert _login(client, "legacy@college.edu", short).status_code == 200

        response = _change(client, {"current_password": short, "new_password": FAKE_NEW})

        assert response.status_code == 200
        assert verify_password(FAKE_NEW, _stored(fake_db, user)["password_hash"])

    def test_confirm_password_in_the_body_is_ignored(self, signed_in):
        client, fake_db, user = signed_in

        response = _change(
            client,
            {
                "current_password": FAKE_CURRENT,
                "new_password": FAKE_NEW,
                "confirm_password": "something-completely-different",
            },
        )

        assert response.status_code == 200
        assert verify_password(FAKE_NEW, _stored(fake_db, user)["password_hash"])


class TestOutcomes:
    def test_success_body_is_only_the_message(self, app_instance, monkeypatch):
        user = make_user(email="ok@college.edu", password=FAKE_CURRENT)
        client, _ = _signed_in(app_instance, monkeypatch, user)

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}
        )

        assert response.status_code == 200
        assert response.get_json() == {"message": "Password changed"}

    def test_new_password_logs_in_and_the_old_one_does_not(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="cycle@college.edu", password=FAKE_CURRENT)
        client, _ = _signed_in(app_instance, monkeypatch, user)

        assert (
            _change(
                client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}
            ).status_code
            == 200
        )

        fresh = app_instance.test_client()
        assert _login(fresh, "cycle@college.edu", FAKE_NEW).status_code == 200
        assert _login(fresh, "cycle@college.edu", FAKE_CURRENT).status_code == 401

    def test_replaying_the_same_request_fails_on_the_second_call(
        self, app_instance, monkeypatch
    ):
        """POST and not PUT: this consumes a proof rather than replacing a
        field, so sending the identical body twice must not succeed twice
        -- the second call's `current_password` is the password that was
        just replaced and is therefore wrong."""
        user = make_user(email="replay@college.edu", password=FAKE_CURRENT)
        client, fake_db = _signed_in(app_instance, monkeypatch, user)
        body = {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}

        first = _change(client, body)
        second = _change(client, body)

        assert first.status_code == 200
        assert second.status_code == 403
        assert verify_password(FAKE_NEW, _stored(fake_db, user)["password_hash"])

    def test_wrong_current_password_is_403_not_401(self, app_instance, monkeypatch):
        """403 is load-bearing: a 401 would be transparently retried by the
        frontend's apiFetch, submitting the wrong password twice."""
        user = make_user(email="wrong@college.edu", password=FAKE_CURRENT)
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        response = _change(
            client,
            {"current_password": "not-the-right-password", "new_password": FAKE_NEW},
        )

        assert response.status_code == 403
        assert verify_password(FAKE_CURRENT, _stored(fake_db, user)["password_hash"])

    def test_new_password_keeps_its_surrounding_spaces(self, app_instance, monkeypatch):
        padded = "  a-fake-padded-password  "
        user = make_user(email="spaces@college.edu", password=FAKE_CURRENT)
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        assert (
            _change(
                client, {"current_password": FAKE_CURRENT, "new_password": padded}
            ).status_code
            == 200
        )

        assert verify_password(padded, _stored(fake_db, user)["password_hash"])
        assert not verify_password(padded.strip(), _stored(fake_db, user)["password_hash"])

        fresh = app_instance.test_client()
        assert _login(fresh, "spaces@college.edu", padded).status_code == 200

    @pytest.mark.parametrize(
        "body,expected",
        [
            ({"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}, 200),
            ({"current_password": "wrong-password-here", "new_password": FAKE_NEW}, 403),
            ({"current_password": FAKE_CURRENT, "new_password": "short"}, 400),
            ({"current_password": FAKE_CURRENT, "new_password": FAKE_CURRENT}, 400),
        ],
        ids=["success", "wrong-current", "too-short", "no-op"],
    )
    def test_no_response_ever_contains_a_password_or_hash(
        self, app_instance, monkeypatch, body, expected
    ):
        user = make_user(email="leak@college.edu", password=FAKE_CURRENT)
        client, fake_db = _signed_in(app_instance, monkeypatch, user)
        stored_hash = _stored(fake_db, user)["password_hash"]

        response = _change(client, body)
        text = response.get_data(as_text=True)

        assert response.status_code == expected
        for secret in (FAKE_CURRENT, FAKE_NEW, "wrong-password-here", stored_hash):
            assert secret not in text
        assert "password_hash" not in text

    def test_a_wrong_attempt_writes_no_password_to_the_logs(
        self, app_instance, monkeypatch, caplog
    ):
        user = make_user(email="quiet@college.edu", password=FAKE_CURRENT)
        client, _ = _signed_in(app_instance, monkeypatch, user)

        with caplog.at_level(logging.DEBUG):
            response = _change(
                client,
                {"current_password": "a-fake-wrong-password", "new_password": FAKE_NEW},
            )

        assert response.status_code == 403
        assert "a-fake-wrong-password" not in caplog.text
        assert FAKE_NEW not in caplog.text

    def test_database_failure_is_the_blueprint_500_not_a_400_or_403(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="down@college.edu", password=FAKE_CURRENT)
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        def _explode(*args, **kwargs):
            raise PyMongoError("connection lost")

        monkeypatch.setattr(fake_db[USERS], "find_one_and_update", _explode)

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}
        )

        assert response.status_code == 500
        assert response.get_json() == {"error": "Service temporarily unavailable"}


class TestChangePasswordUnit:
    """auth/password_change.py exercised directly -- no app context, no
    Flask objects, just a database double."""

    def test_returns_the_updated_document(self):
        user = make_user(password=FAKE_CURRENT)
        db = make_fake_db([user])

        updated = change_password(
            db,
            str(user["_id"]),
            current_password=FAKE_CURRENT,
            new_password=FAKE_NEW,
        )

        assert verify_password(FAKE_NEW, updated["password_hash"])

    def test_document_shape_matches_the_admin_reset_path(self):
        """Definition of done #2: the write goes through the same
        set_user_password an admin's PUT /api/users/<id>/password uses, so
        the two paths must leave a document with the same set of fields --
        proved here against the admin path itself, not just against the
        document's own pre-change shape."""
        from users.service import set_user_password

        via_self_service = make_user(password=FAKE_CURRENT)
        via_admin = make_user(password=FAKE_CURRENT)
        db = make_fake_db([via_self_service, via_admin])

        self_service_result = change_password(
            db,
            str(via_self_service["_id"]),
            current_password=FAKE_CURRENT,
            new_password=FAKE_NEW,
        )
        admin_result = set_user_password(db, via_admin["_id"], password=FAKE_NEW)

        assert set(self_service_result.keys()) == set(admin_result.keys())

    def test_wrong_current_password_raises_incorrect_password_error(self):
        user = make_user(password=FAKE_CURRENT)
        db = make_fake_db([user])

        with pytest.raises(IncorrectPasswordError):
            change_password(
                db, str(user["_id"]), current_password="nope-wrong", new_password=FAKE_NEW
            )

    def test_same_password_raises_validation_error(self):
        user = make_user(password=FAKE_CURRENT)
        db = make_fake_db([user])

        with pytest.raises(ValidationError):
            change_password(
                db,
                str(user["_id"]),
                current_password=FAKE_CURRENT,
                new_password=FAKE_CURRENT,
            )

    @pytest.mark.parametrize("is_active", [False])
    def test_inactive_account_raises_inactive_account_error(self, is_active):
        user = make_user(password=FAKE_CURRENT, is_active=is_active)
        db = make_fake_db([user])

        with pytest.raises(InactiveAccountError):
            change_password(
                db, str(user["_id"]), current_password=FAKE_CURRENT, new_password=FAKE_NEW
            )

    def test_unknown_and_malformed_ids_raise_inactive_account_error(self):
        db = make_fake_db([])

        for user_id in ("64b7f9c2a1e4d3b2c1a09876", "not-an-object-id", None):
            with pytest.raises(InactiveAccountError):
                change_password(
                    db, user_id, current_password=FAKE_CURRENT, new_password=FAKE_NEW
                )

    def test_deleted_between_read_and_write_raises_inactive_account_error(
        self, monkeypatch
    ):
        """The delete-during-request race: set_user_password raises
        NotFoundError, and it is reported as authenticate-again rather
        than as a 404."""
        user = make_user(password=FAKE_CURRENT)
        db = make_fake_db([user])
        monkeypatch.setattr(
            db[USERS], "find_one_and_update", lambda *a, **k: None
        )

        with pytest.raises(InactiveAccountError):
            change_password(
                db, str(user["_id"]), current_password=FAKE_CURRENT, new_password=FAKE_NEW
            )

    def test_module_imports_no_flask(self):
        import auth.password_change as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "import flask" not in source
        assert "from flask" not in source


class TestRequireExistingPassword:
    def test_returns_the_value_untrimmed(self):
        assert require_existing_password({"current_password": "  spaced  "}) == "  spaced  "

    def test_accepts_a_value_shorter_than_the_minimum(self):
        short = "a" * (MIN_PASSWORD_LENGTH - 1)
        assert require_existing_password({"current_password": short}) == short

    def test_accepts_a_whitespace_only_value(self):
        """Not trimmed means not blank-checked after trimming: a
        whitespace-only value is a real password and fails verification,
        not validation."""
        assert require_existing_password({"current_password": "   "}) == "   "

    @pytest.mark.parametrize("value", [None, "", 12345678, True, [], {}])
    def test_rejects_missing_empty_and_non_string_values(self, value):
        with pytest.raises(ValidationError):
            require_existing_password({"current_password": value})

    def test_message_never_contains_the_value(self):
        with pytest.raises(ValidationError) as excinfo:
            require_existing_password({"current_password": 987654321})

        assert "987654321" not in str(excinfo.value)

    def test_field_name_is_configurable(self):
        with pytest.raises(ValidationError) as excinfo:
            require_existing_password({}, "old_password")

        assert "old_password" in str(excinfo.value)


# --------------------------------------------------------------------
# 24-invalidate-tokens-on-password-change
# --------------------------------------------------------------------


def _second_session(app_instance, fake_db, user):
    """A SECOND client signed in as the same user, sharing one fake db.

    Two clients are two cookie jars, which is the only way to express the
    thing this feature is about: one session changes the password and the
    other must stop working, both against the same account.

    `fake_db` is checked rather than merely documented. The shared
    database is a precondition of every test that calls this -- both
    sessions must resolve to the SAME user document, or the test would
    be watching two unrelated accounts and would pass for the wrong
    reason.
    """
    assert fake_db[USERS].find_one({"_id": user["_id"]}) is not None, (
        "the second session must share the first session's database"
    )
    client = app_instance.test_client()
    assert _login(client, user["email"], FAKE_CURRENT).status_code == 200
    return client


def _refresh(client):
    cookie = client.get_cookie("csrf_refresh_token", path="/")
    assert cookie is not None, "csrf_refresh_token cookie was not set after login"
    return client.post("/api/auth/refresh", headers={"X-CSRF-TOKEN": cookie.value})


class TestOtherSessionsAreEnded:
    """DoD 1, 2, 3, 9 -- the defect this feature exists to fix.

    Before 24, /api/auth/refresh checked only `is_active`, so the loser
    of these tests kept minting access tokens for the refresh cookie's
    full seven days after the owner changed their password.
    """

    def test_another_sessions_refresh_is_dead_after_a_password_change(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="two@college.edu", password=FAKE_CURRENT)
        changer, fake_db = _signed_in(app_instance, monkeypatch, user)
        other = _second_session(app_instance, fake_db, user)

        assert _refresh(other).status_code == 200, "precondition: it worked before"

        assert (
            _change(
                changer, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}
            ).status_code
            == 200
        )

        assert _refresh(other).status_code == 401

    def test_the_stale_refresh_stays_dead_on_repeated_attempts(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="retry@college.edu", password=FAKE_CURRENT)
        changer, fake_db = _signed_in(app_instance, monkeypatch, user)
        other = _second_session(app_instance, fake_db, user)

        _change(changer, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW})

        for _ in range(3):
            response = _refresh(other)
            assert response.status_code == 401
            # The point is that no FRESH access token is minted -- the
            # client still holds the one login gave it, which expires on
            # its own; what must never happen is this session renewing
            # itself, which is what it did for seven days before 24.
            assert not [
                c
                for c in response.headers.getlist("Set-Cookie")
                if c.startswith("access_token_cookie=")
            ]

    def test_stale_version_is_indistinguishable_from_a_deactivated_account(
        self, app_instance, monkeypatch
    ):
        """DoD 3. The refusal must not tell a holder of stolen cookies
        WHY they were stopped: "your password just changed" is itself
        information about the account.
        """
        changed_user = make_user(email="stale@college.edu", password=FAKE_CURRENT)
        changer, fake_db = _signed_in(app_instance, monkeypatch, changed_user)
        stale = _second_session(app_instance, fake_db, changed_user)
        _change(changer, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW})
        stale_response = _refresh(stale)

        deactivated_user = make_user(email="off@college.edu", password=FAKE_CURRENT)
        deactivated_db = _patch_db(monkeypatch, [deactivated_user])
        deactivated_client = app_instance.test_client()
        assert (
            _login(deactivated_client, "off@college.edu", FAKE_CURRENT).status_code
            == 200
        )
        deactivated_db[USERS].find_one_and_update(
            {"_id": deactivated_user["_id"]}, {"$set": {"is_active": False}}
        )
        deactivated_response = _refresh(deactivated_client)

        assert stale_response.status_code == deactivated_response.status_code == 401
        assert stale_response.get_data() == deactivated_response.get_data()


class TestActingSessionSurvives:
    """DoD 6, 7, 8 -- and 23's rule 8, which this must not break.

    The naive implementation of this feature signs the caller out too:
    the bump strands their own refresh token along with everybody
    else's. That failure is invisible for fifteen minutes and then drops
    the user at /login for the crime of changing their password.
    """

    def test_response_sets_a_fresh_refresh_cookie_scoped_to_the_refresh_path(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="reissue@college.edu", password=FAKE_CURRENT)
        client, _ = _signed_in(app_instance, monkeypatch, user)

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}
        )

        set_cookies = response.headers.getlist("Set-Cookie")
        refresh_cookies = [
            c for c in set_cookies if c.startswith("refresh_token_cookie=")
        ]
        assert len(refresh_cookies) == 1, set_cookies
        assert "Path=/api/auth/refresh" in refresh_cookies[0]

    def test_the_access_cookie_is_not_re_issued(self, app_instance, monkeypatch):
        """Nothing invalidated it, so replacing it would change what the
        user holds without changing what is true, and would quietly
        extend a session from a response about a credential.
        """
        user = make_user(email="noaccess@college.edu", password=FAKE_CURRENT)
        client, _ = _signed_in(app_instance, monkeypatch, user)

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}
        )

        assert not [
            c
            for c in response.headers.getlist("Set-Cookie")
            if c.startswith("access_token_cookie=")
        ]

    def test_the_changer_can_still_refresh(self, app_instance, monkeypatch):
        user = make_user(email="survives@college.edu", password=FAKE_CURRENT)
        client, _ = _signed_in(app_instance, monkeypatch, user)

        _change(client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW})

        assert _refresh(client).status_code == 200

    def test_the_changer_survives_while_the_other_session_dies(
        self, app_instance, monkeypatch
    ):
        """DoD 9: both outcomes hold at once, for one account."""
        user = make_user(email="both@college.edu", password=FAKE_CURRENT)
        changer, fake_db = _signed_in(app_instance, monkeypatch, user)
        other = _second_session(app_instance, fake_db, user)

        _change(changer, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW})

        assert _refresh(changer).status_code == 200
        assert _refresh(other).status_code == 401

    def test_the_changer_can_still_reach_an_ordinary_endpoint(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="stillme@college.edu", password=FAKE_CURRENT)
        client, _ = _signed_in(app_instance, monkeypatch, user)

        _change(client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW})

        assert client.get("/api/auth/me").status_code == 200

    def test_response_body_is_unchanged_and_carries_no_version(
        self, app_instance, monkeypatch
    ):
        """DoD 8 and 21: the counter is internal. It travels in a signed
        cookie and in MongoDB, and nowhere else.
        """
        user = make_user(email="body@college.edu", password=FAKE_CURRENT)
        client, _ = _signed_in(app_instance, monkeypatch, user)

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW}
        )

        assert response.get_json() == {"message": "Password changed"}
        assert "token_version" not in response.get_data(as_text=True)


class TestVersionMovesOnlyOnASuccessfulWrite:
    """DoD 5, 19, 20. A failed attempt must not strand anybody."""

    def test_a_successful_change_bumps_the_version_by_one(
        self, app_instance, monkeypatch
    ):
        user = make_user(
            email="bump@college.edu", password=FAKE_CURRENT, token_version=4
        )
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        _change(client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW})

        assert _stored(fake_db, user)["token_version"] == 5

    def test_a_document_without_the_field_gains_it_at_one(
        self, app_instance, monkeypatch
    ):
        """DoD 10: the live-data case, a user written before 24."""
        user = make_user(email="fresh@college.edu", password=FAKE_CURRENT)
        assert "token_version" not in user
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        _change(client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW})

        assert _stored(fake_db, user)["token_version"] == 1

    def test_the_hash_and_the_version_move_together(self, app_instance, monkeypatch):
        """DoD 5: one update, so the document is never observable with a
        new hash and an old version, which is the state the feature
        exists to prevent.
        """
        user = make_user(
            email="together@college.edu", password=FAKE_CURRENT, token_version=0
        )
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        _change(client, {"current_password": FAKE_CURRENT, "new_password": FAKE_NEW})

        stored = _stored(fake_db, user)
        assert verify_password(FAKE_NEW, stored["password_hash"])
        assert stored["token_version"] == 1

    def test_a_wrong_current_password_leaves_the_version_alone(
        self, app_instance, monkeypatch
    ):
        """DoD 19: a failed attempt must not end anyone's sessions,
        otherwise the endpoint becomes a way to log a user out by
        guessing wrongly at their password.
        """
        user = make_user(
            email="wrong@college.edu", password=FAKE_CURRENT, token_version=2
        )
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        response = _change(
            client, {"current_password": "not-the-password", "new_password": FAKE_NEW}
        )

        assert response.status_code == 403
        assert _stored(fake_db, user)["token_version"] == 2

    def test_a_wrong_current_password_does_not_end_another_session(
        self, app_instance, monkeypatch
    ):
        user = make_user(email="wrong2@college.edu", password=FAKE_CURRENT)
        client, fake_db = _signed_in(app_instance, monkeypatch, user)
        other = _second_session(app_instance, fake_db, user)

        _change(
            client, {"current_password": "not-the-password", "new_password": FAKE_NEW}
        )

        assert _refresh(other).status_code == 200

    def test_a_no_op_change_leaves_the_version_alone(self, app_instance, monkeypatch):
        """DoD 20: refused at 400 before any write."""
        user = make_user(
            email="noop@college.edu", password=FAKE_CURRENT, token_version=3
        )
        client, fake_db = _signed_in(app_instance, monkeypatch, user)

        response = _change(
            client, {"current_password": FAKE_CURRENT, "new_password": FAKE_CURRENT}
        )

        assert response.status_code == 400
        assert _stored(fake_db, user)["token_version"] == 3

    def test_logout_does_not_change_the_version(self, app_instance, monkeypatch):
        """DoD 17: logout clears cookies on one device and says nothing
        about the credential, so another device keeps working.
        """
        user = make_user(email="out@college.edu", password=FAKE_CURRENT, token_version=1)
        client, fake_db = _signed_in(app_instance, monkeypatch, user)
        other = _second_session(app_instance, fake_db, user)

        assert (
            client.post("/api/auth/logout", headers=_csrf_header(client)).status_code
            == 200
        )

        assert _stored(fake_db, user)["token_version"] == 1
        assert _refresh(other).status_code == 200
