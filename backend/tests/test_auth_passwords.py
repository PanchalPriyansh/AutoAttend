"""Tests for backend/auth/passwords.py.

Spec contract under test (03-authentication.md, "Backend" section +
"Rules for implementation"):
  - `hash_password(plaintext)` / `verify_password(plaintext, password_hash)`
    wrap Werkzeug's `generate_password_hash` / `check_password_hash`.
  - Plaintext passwords are never stored -- only a hash is ever produced
    or compared against.

All passwords used below are obviously-fake, hardcoded test values -- no
real or production credentials appear in this file.
"""

from auth.passwords import hash_password, verify_password


class TestHashPassword:
    def test_hash_password_does_not_return_the_plaintext(self):
        plaintext = "Correct-Horse-Battery-Staple-1"

        hashed = hash_password(plaintext)

        assert hashed != plaintext
        assert plaintext not in hashed

    def test_hash_password_returns_a_non_empty_string(self):
        hashed = hash_password("some-fake-test-password")

        assert isinstance(hashed, str)
        assert hashed

    def test_hashing_the_same_password_twice_produces_different_hashes(self):
        # Werkzeug salts each hash, so two hashes of the same plaintext
        # must differ even though both verify successfully against it.
        plaintext = "same-password-both-times"

        assert hash_password(plaintext) != hash_password(plaintext)


class TestVerifyPassword:
    def test_verify_password_succeeds_for_the_correct_plaintext(self):
        plaintext = "a-fake-test-password"
        hashed = hash_password(plaintext)

        assert verify_password(plaintext, hashed) is True

    def test_verify_password_fails_for_an_incorrect_plaintext(self):
        hashed = hash_password("the-real-fake-password")

        assert verify_password("a-wrong-guess", hashed) is False

    def test_verify_password_fails_for_empty_plaintext_against_a_real_hash(self):
        hashed = hash_password("non-empty-fake-password")

        assert verify_password("", hashed) is False

    def test_verify_password_round_trip_is_case_sensitive(self):
        hashed = hash_password("CaseSensitive-Fake-Pass1")

        assert verify_password("casesensitive-fake-pass1", hashed) is False
