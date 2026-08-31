"""Tests for backend/auth/reset_codes.py (25-forgot-password.md).

Spec contract under test ("Backend" + "Rules for implementation" +
"Definition of done"):
  - `generate_code()` draws from `secrets.randbelow(10 ** CODE_LENGTH)`,
    zero-padded to `CODE_LENGTH` digits, as a string -- so every value in
    the whole space, including one like "000123", is reachable (rule 2,
    DoD 22).
  - The five constants -- `CODE_LENGTH` (6), `CODE_TTL_MINUTES` (15),
    `MAX_ATTEMPTS` (5), `RESEND_COOLDOWN_SECONDS` (60),
    `MAX_SUBMITTED_CODE_LENGTH` (64) -- are fixed values in this module,
    not environment-configurable (rule 4).
  - `expiry_from(now)` is `now + timedelta(minutes=CODE_TTL_MINUTES)`.
  - `usable_code_filter(user_id, now)` and `reissue_filter(user_id, now)`
    are MongoDB query filters, asserted field by field (DoD 22): the
    comparisons happen inside the database, never in Python, because a
    stored (naive) datetime compared to a constructed (aware) one raises
    `TypeError`. `reissue_filter` is the inverse of "inside the
    cooldown" -- it matches only a row OLDER than the cooldown window, so
    that using it as the filter of an upserting update makes a row still
    inside the cooldown fall through to an insert that the unique index
    on `user_id` refuses.
  - The module is pure: no Flask, no pymongo, no `bson`, no `config`
    (DoD 22), so every test here runs with no app context and no
    database double.

No real code, credential, or biometric data appears anywhere below --
every value is an obviously-fake, hardcoded test value.
"""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from auth.reset_codes import (
    CODE_LENGTH,
    CODE_TTL_MINUTES,
    MAX_ATTEMPTS,
    MAX_SUBMITTED_CODE_LENGTH,
    RESEND_COOLDOWN_SECONDS,
    expiry_from,
    generate_code,
    reissue_filter,
    usable_code_filter,
)


class TestConstants:
    """Rule 4: these numbers are the entire compensating control for a
    project with no rate limiting, so they are pinned exactly."""

    def test_code_length_is_six_digits(self):
        assert CODE_LENGTH == 6

    def test_code_ttl_is_fifteen_minutes(self):
        assert CODE_TTL_MINUTES == 15

    def test_max_attempts_is_five(self):
        assert MAX_ATTEMPTS == 5

    def test_resend_cooldown_is_sixty_seconds(self):
        assert RESEND_COOLDOWN_SECONDS == 60

    def test_max_submitted_code_length_is_sixty_four(self):
        assert MAX_SUBMITTED_CODE_LENGTH == 64

    def test_max_submitted_code_length_is_well_above_the_real_code_length(self):
        """Not a format check -- a wrong-length code fails as a wrong
        code, indistinguishably from any other. This is a ceiling on what
        reaches a password-hash comparison at all."""
        assert MAX_SUBMITTED_CODE_LENGTH > CODE_LENGTH


class TestGenerateCode:
    def test_returns_a_string_of_exactly_code_length_digits(self):
        code = generate_code()

        assert isinstance(code, str)
        assert len(code) == CODE_LENGTH
        assert code.isdigit()

    def test_repeated_calls_stay_within_the_declared_shape(self):
        """Not a statistical uniformity proof -- that needs the
        monkeypatched boundary tests below -- but pins that nothing about
        the shape (length, digit-only) ever varies across draws."""
        for _ in range(50):
            code = generate_code()
            assert len(code) == CODE_LENGTH
            assert code.isdigit()

    def test_draws_from_the_whole_code_length_space(self, monkeypatch):
        """`secrets.randbelow` is called with 10 ** CODE_LENGTH, not a
        range that would silently discard some padded values."""
        captured = {}

        def fake_randbelow(bound):
            captured["bound"] = bound
            return 0

        monkeypatch.setattr("auth.reset_codes.secrets.randbelow", fake_randbelow)

        generate_code()

        assert captured["bound"] == 10 ** CODE_LENGTH

    def test_zero_is_zero_padded_to_the_full_length(self, monkeypatch):
        monkeypatch.setattr("auth.reset_codes.secrets.randbelow", lambda bound: 0)

        assert generate_code() == "0" * CODE_LENGTH

    def test_a_small_value_keeps_its_leading_zeros(self, monkeypatch):
        """The exact case the spec calls out: 123 must render as
        "000123", not "123" -- a range-shifted draw would silently
        discard a tenth of the space to avoid needing this."""
        monkeypatch.setattr("auth.reset_codes.secrets.randbelow", lambda bound: 123)

        assert generate_code() == "000123"

    def test_the_maximum_value_in_the_space_is_reachable(self, monkeypatch):
        monkeypatch.setattr(
            "auth.reset_codes.secrets.randbelow", lambda bound: 10 ** CODE_LENGTH - 1
        )

        assert generate_code() == "9" * CODE_LENGTH

    def test_uses_the_secrets_module_and_not_random(self):
        """Rule 2: `random` is seeded predictably and must never appear
        as the source of a code."""
        import auth.reset_codes as module

        source = open(module.__file__, encoding="utf-8").read()

        assert "import random" not in source
        assert "secrets.randbelow" in source


class TestExpiryFrom:
    def test_adds_the_ttl_in_minutes_to_the_given_instant(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        assert expiry_from(now) == now + timedelta(minutes=CODE_TTL_MINUTES)

    def test_is_a_pure_function_of_its_argument(self):
        first = datetime(2026, 3, 1, tzinfo=timezone.utc)
        second = datetime(2026, 6, 1, tzinfo=timezone.utc)

        assert expiry_from(first) != expiry_from(second)
        assert (expiry_from(second) - second) == (expiry_from(first) - first)


class TestUsableCodeFilter:
    """DoD 22: the four disqualifying conditions -- wrong account,
    consumed, expired, exhausted -- expressed as one filter, asserted
    field by field so a change to any one of them is caught here rather
    than only through an end-to-end test.
    """

    def test_filter_matches_exactly_these_four_fields(self):
        user_id = ObjectId()
        now = datetime.now(timezone.utc)

        assert usable_code_filter(user_id, now) == {
            "user_id": user_id,
            "consumed_at": None,
            "expires_at": {"$gt": now},
            "attempts": {"$lt": MAX_ATTEMPTS},
        }

    def test_scopes_to_the_given_account_and_never_by_email(self):
        user_id = ObjectId()
        now = datetime.now(timezone.utc)

        filt = usable_code_filter(user_id, now)

        assert filt["user_id"] == user_id
        assert "email" not in filt

    def test_the_expiry_and_attempts_bounds_are_expressed_as_query_operators(self):
        """Never a Python comparison: pymongo hands back naive datetimes
        and a freshly built `now` is aware, so comparing them outside a
        query raises TypeError -- see the module docstring."""
        user_id = ObjectId()
        now = datetime.now(timezone.utc)

        filt = usable_code_filter(user_id, now)

        assert filt["expires_at"] == {"$gt": now}
        assert filt["attempts"] == {"$lt": MAX_ATTEMPTS}
        assert filt["attempts"]["$lt"] == MAX_ATTEMPTS

    def test_different_instants_produce_different_filters(self):
        user_id = ObjectId()
        earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
        later = datetime(2026, 1, 2, tzinfo=timezone.utc)

        assert usable_code_filter(user_id, earlier) != usable_code_filter(user_id, later)


class TestReissueFilter:
    """`reissue_filter` is the INVERSE of "inside the cooldown": it
    matches only a row last written longer ago than
    RESEND_COOLDOWN_SECONDS, which is what makes it safe as the filter of
    an upserting update -- a row still inside the cooldown makes the
    filter miss, so the upsert falls through to an insert that the
    unique index on `user_id` refuses.
    """

    def test_filter_matches_exactly_these_two_fields(self):
        user_id = ObjectId()
        now = datetime.now(timezone.utc)

        assert reissue_filter(user_id, now) == {
            "user_id": user_id,
            "created_at": {"$lt": now - timedelta(seconds=RESEND_COOLDOWN_SECONDS)},
        }

    def test_the_window_is_exactly_the_resend_cooldown_constant(self):
        user_id = ObjectId()
        now = datetime.now(timezone.utc)

        filt = reissue_filter(user_id, now)

        assert filt["created_at"]["$lt"] == now - timedelta(
            seconds=RESEND_COOLDOWN_SECONDS
        )

    def test_uses_lt_and_not_gte_because_it_is_the_inverse_of_the_cooldown(self):
        """The whole point: a row inside the window must NOT match, so
        that the upsert it filters falls through to an insert the unique
        index refuses. `$gte` would match a row still inside the window
        and let it be silently replaced, defeating the cooldown."""
        user_id = ObjectId()
        now = datetime.now(timezone.utc)

        filt = reissue_filter(user_id, now)

        assert "$lt" in filt["created_at"]
        assert "$gte" not in filt["created_at"]
        assert "$gt" not in filt["created_at"]

    def test_is_keyed_on_the_account_and_carries_no_email_field(self):
        """Deliberate: an address with no account never reaches this
        filter at all, because nothing is ever sent to it -- so there is
        no row and no cooldown to keep for an unknown address."""
        user_id = ObjectId()
        now = datetime.now(timezone.utc)

        filt = reissue_filter(user_id, now)

        assert filt["user_id"] == user_id
        assert "email" not in filt

    def test_usable_code_filter_and_reissue_filter_read_different_things(self):
        """They answer different questions -- "is this code still good"
        versus "may this row be replaced" -- and must not collapse into
        the same query."""
        user_id = ObjectId()
        now = datetime.now(timezone.utc)

        assert usable_code_filter(user_id, now) != reissue_filter(user_id, now)


class TestModuleIsolation:
    """DoD 22: `auth/reset_codes.py` imports nothing from Flask, pymongo,
    `bson`, or `config` -- it must be readable and testable without a
    database, and every test in this file proves that by never importing
    Flask, a fake db, or an app fixture.
    """

    def test_imports_nothing_from_flask_pymongo_bson_or_config(self):
        import auth.reset_codes as module

        source = open(module.__file__, encoding="utf-8").read()

        for banned in (
            "import flask",
            "from flask",
            "import pymongo",
            "from pymongo",
            "import bson",
            "from bson",
            "import config",
            "from config",
        ):
            assert banned not in source, f"auth/reset_codes.py must not contain {banned!r}"

    def test_module_performs_no_logging(self):
        """Rule 12, restated for this module: no code, hash, email, or
        user id is logged from here -- proved structurally, since the
        module has no I/O of any kind to log through."""
        import auth.reset_codes as module

        source = open(module.__file__, encoding="utf-8").read()

        assert "import logging" not in source
        assert "print(" not in source
