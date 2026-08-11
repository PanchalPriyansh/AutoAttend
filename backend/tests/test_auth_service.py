"""Tests for backend/auth/service.py.

Spec contract under test (03-authentication.md, "Database changes" +
"Backend" + "Rules for implementation"):
  - `normalize_email()` lowercases and trims, so the case-sensitive unique
    index on `email` behaves as a case-insensitive one in practice.
  - `authenticate_user()` returns the user document on success, and
    returns `None` -- identically -- for an unknown email, a wrong
    password, and a deactivated (`is_active: False`) account, so a caller
    cannot distinguish them (prevents account enumeration).
  - `get_user_by_id()` returns the user document for a valid ObjectId
    string, and `None` (never raises) for a missing or malformed id.
  - `create_user()` inserts a hashed (never plaintext), normalized,
    `is_active: True` user with real `datetime` timestamps, and raises
    `DuplicateEmailError` (not a raw pymongo error) on a duplicate email.
  - `to_safe_profile()` strips `password_hash` and returns the documented
    public shape (`id`, `name`, `email`, `role`, `institute_id`).

`auth/service.py` takes a `db`-like object with no Flask/pymongo
dependency beyond `db[USERS]`, so these tests use the in-memory
`FakeUsersCollection` from auth_test_helpers.py instead of a live
MongoDB connection. All emails/passwords are fake, test-only values.
"""

from datetime import datetime

import pytest
from auth.passwords import verify_password
from auth.service import (
    DuplicateEmailError,
    authenticate_user,
    create_user,
    get_user_by_id,
    normalize_email,
    to_safe_profile,
)
from auth_test_helpers import make_fake_db, make_user
from bson import ObjectId
from database.schema import USERS


class TestNormalizeEmail:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Student@College.edu", "student@college.edu"),
            ("  admin@college.edu  ", "admin@college.edu"),
            (" Admin@College.EDU ", "admin@college.edu"),
            ("already@lower.case", "already@lower.case"),
        ],
    )
    def test_normalize_email_lowercases_and_trims(self, raw, expected):
        assert normalize_email(raw) == expected


class TestAuthenticateUser:
    def test_returns_the_user_document_for_correct_credentials(self):
        password = "correct-fake-password-1"
        user = make_user(email="student@college.edu", password=password, role="student")
        db = make_fake_db([user])

        result = authenticate_user(db, "student@college.edu", password)

        assert result is not None
        assert result["_id"] == user["_id"]
        assert result["role"] == "student"

    def test_returns_none_for_an_unknown_email(self):
        db = make_fake_db([])

        result = authenticate_user(db, "nobody@college.edu", "any-fake-password")

        assert result is None

    def test_returns_none_for_a_wrong_password(self):
        user = make_user(email="student@college.edu", password="the-real-fake-password")
        db = make_fake_db([user])

        result = authenticate_user(db, "student@college.edu", "a-wrong-fake-password")

        assert result is None

    def test_returns_none_for_a_deactivated_account_even_with_correct_password(self):
        password = "correct-fake-password-2"
        user = make_user(email="inactive@college.edu", password=password, is_active=False)
        db = make_fake_db([user])

        result = authenticate_user(db, "inactive@college.edu", password)

        assert result is None

    def test_the_three_rejection_reasons_are_indistinguishable(self):
        """Per spec: unknown email, wrong password, and deactivated account
        must all be rejected identically by this function so the route
        layer cannot leak (even accidentally) which case occurred."""
        password = "correct-fake-password-3"
        active_user = make_user(email="known@college.edu", password=password)
        inactive_user = make_user(email="inactive3@college.edu", password=password, is_active=False)
        db = make_fake_db([active_user, inactive_user])

        unknown_result = authenticate_user(db, "nobody4@college.edu", password)
        wrong_password_result = authenticate_user(db, "known@college.edu", "not-the-password")
        deactivated_result = authenticate_user(db, "inactive3@college.edu", password)

        assert unknown_result is None
        assert wrong_password_result is None
        assert deactivated_result is None

    def test_authentication_is_case_and_whitespace_insensitive_on_email(self):
        password = "correct-fake-password-4"
        user = make_user(email="admin@college.edu", password=password, role="admin")
        db = make_fake_db([user])

        result = authenticate_user(db, " Admin@College.edu ", password)

        assert result is not None
        assert result["_id"] == user["_id"]


class TestGetUserById:
    def test_returns_the_user_document_for_a_valid_id(self):
        user = make_user()
        db = make_fake_db([user])

        result = get_user_by_id(db, str(user["_id"]))

        assert result is not None
        assert result["_id"] == user["_id"]

    def test_returns_none_for_an_id_that_does_not_exist(self):
        db = make_fake_db([make_user()])

        result = get_user_by_id(db, str(ObjectId()))

        assert result is None

    @pytest.mark.parametrize("malformed_id", ["not-an-object-id", "", "123"])
    def test_returns_none_rather_than_raising_for_a_malformed_id(self, malformed_id):
        db = make_fake_db([make_user()])

        result = get_user_by_id(db, malformed_id)

        assert result is None


class TestCreateUser:
    def test_creates_a_user_with_a_hashed_password_not_plaintext(self):
        db = make_fake_db([])

        user = create_user(
            db,
            name="New Admin",
            email="new-admin@college.edu",
            password="a-fake-test-password",
            role="admin",
        )

        assert user["password_hash"] != "a-fake-test-password"
        assert verify_password("a-fake-test-password", user["password_hash"]) is True

    def test_normalizes_the_email_before_storing(self):
        db = make_fake_db([])

        user = create_user(
            db,
            name="Case Test",
            email=" MixedCase@College.EDU ",
            password="a-fake-test-password",
            role="faculty",
        )

        assert user["email"] == "mixedcase@college.edu"

    def test_sets_role_and_is_active_true_by_default(self):
        db = make_fake_db([])

        user = create_user(
            db,
            name="Active Check",
            email="active@college.edu",
            password="a-fake-test-password",
            role="student",
        )

        assert user["role"] == "student"
        assert user["is_active"] is True

    def test_sets_created_at_and_updated_at_as_real_datetimes(self):
        db = make_fake_db([])

        user = create_user(
            db,
            name="Date Check",
            email="dates@college.edu",
            password="a-fake-test-password",
            role="student",
        )

        assert isinstance(user["created_at"], datetime)
        assert isinstance(user["updated_at"], datetime)

    def test_returns_the_document_with_an_assigned_id(self):
        db = make_fake_db([])

        user = create_user(
            db,
            name="Id Check",
            email="idcheck@college.edu",
            password="a-fake-test-password",
            role="student",
        )

        assert "_id" in user
        assert db[USERS].find_one({"_id": user["_id"]}) is not None

    def test_raises_duplicate_email_error_on_a_second_insert_with_the_same_email(self):
        db = make_fake_db([])
        create_user(
            db, name="First", email="dup@college.edu", password="a-fake-test-password", role="student"
        )

        with pytest.raises(DuplicateEmailError):
            create_user(
                db,
                name="Second",
                email="dup@college.edu",
                password="another-fake-test-password",
                role="student",
            )

    def test_duplicate_email_check_is_normalized(self):
        """A duplicate that only differs by case/whitespace must still be
        rejected, since the unique index is case-sensitive and normalization
        is this feature's responsibility."""
        db = make_fake_db([])
        create_user(
            db, name="First", email="dup2@college.edu", password="a-fake-test-password", role="student"
        )

        with pytest.raises(DuplicateEmailError):
            create_user(
                db,
                name="Second",
                email=" Dup2@College.EDU ",
                password="another-fake-test-password",
                role="student",
            )

    def test_duplicate_email_error_message_does_not_leak_the_password(self):
        db = make_fake_db([])
        create_user(
            db,
            name="First",
            email="dup3@college.edu",
            password="a-secret-fake-password",
            role="student",
        )

        with pytest.raises(DuplicateEmailError) as excinfo:
            create_user(
                db,
                name="Second",
                email="dup3@college.edu",
                password="a-secret-fake-password",
                role="student",
            )

        assert "a-secret-fake-password" not in str(excinfo.value)


class TestToSafeProfile:
    def test_safe_profile_never_includes_password_hash(self):
        user = make_user()

        profile = to_safe_profile(user)

        assert "password_hash" not in profile

    def test_safe_profile_contains_the_documented_public_fields(self):
        user = make_user(name="Jane Student", email="jane@college.edu", role="student")

        profile = to_safe_profile(user)

        assert profile["id"] == str(user["_id"])
        assert profile["name"] == "Jane Student"
        assert profile["email"] == "jane@college.edu"
        assert profile["role"] == "student"
        assert "institute_id" in profile

    def test_safe_profile_institute_id_is_none_when_user_has_none(self):
        user = make_user(institute_id=None)

        profile = to_safe_profile(user)

        assert profile["institute_id"] is None

    def test_safe_profile_institute_id_is_stringified_when_present(self):
        institute_id = ObjectId()
        user = make_user(institute_id=institute_id)

        profile = to_safe_profile(user)

        assert profile["institute_id"] == str(institute_id)
