"""Tests for backend/notifications/reset_messages.py (25-forgot-password.md).

Spec contract under test ("Backend" + rule 13 + "Definition of done"):
  - `build_reset_subject()` takes no arguments and returns a fixed
    subject line carrying no code and no figure of any kind (a subject
    is visible in a lock-screen preview).
  - `build_reset_body(name, code, ttl_minutes)` greets the recipient by
    name (or falls back to a generic greeting), states the code exactly
    once, states the expiry in minutes (singular/plural), states that
    the code is single-use, and reassures an unrequested recipient
    without alarm.
  - The body is plain text: no link, no URL of any kind, and nothing
    that could hide one -- there is no HTML part in this project's mail
    at all.
  - Pure string construction: no document, no transport, no socket, no
    credential reaches this module, so every test here calls it with
    plain arguments only (DoD 21).

No real code, name, or address is used anywhere below -- every value is
an obviously-fake placeholder.
"""

import pytest

from notifications.reset_messages import build_reset_body, build_reset_subject

FAKE_CODE = "482913"


# --- build_reset_subject -----------------------------------------------------


class TestBuildResetSubject:
    def test_takes_no_arguments_and_returns_a_string(self):
        subject = build_reset_subject()

        assert isinstance(subject, str)
        assert subject

    def test_names_autoattend(self):
        subject = build_reset_subject()

        assert subject.startswith("AutoAttend")

    def test_is_deterministic(self):
        assert build_reset_subject() == build_reset_subject()

    def test_contains_no_digit_and_therefore_no_code_or_figure(self):
        """Rule 13: a subject line shows up in a lock-screen preview,
        which is precisely the reader the code is not for."""
        subject = build_reset_subject()

        assert not any(character.isdigit() for character in subject)

    def test_does_not_contain_the_word_code_followed_by_digits(self):
        """A light guard against a rewrite that quietly starts
        interpolating the actual code into the subject."""
        assert FAKE_CODE not in build_reset_subject()


# --- build_reset_body: greeting ----------------------------------------------


class TestBuildResetBodyGreeting:
    def test_greets_the_recipient_by_their_own_name(self):
        body = build_reset_body("Alice Test", FAKE_CODE, 15)

        assert body.startswith("Hello Alice Test,")

    def test_falls_back_to_a_generic_greeting_for_no_name(self):
        body = build_reset_body(None, FAKE_CODE, 15)

        assert body.startswith("Hello,")
        assert "Hello None" not in body

    def test_falls_back_to_a_generic_greeting_for_an_empty_name(self):
        body = build_reset_body("", FAKE_CODE, 15)

        assert body.startswith("Hello,")


# --- build_reset_body: the code ----------------------------------------------


class TestBuildResetBodyCode:
    def test_body_contains_the_code_exactly_once(self):
        body = build_reset_body("Alice Test", FAKE_CODE, 15)

        assert body.count(FAKE_CODE) == 1

    def test_a_different_code_appears_verbatim(self):
        body = build_reset_body("Alice Test", "007000", 15)

        assert "007000" in body

    def test_the_subject_never_carries_the_code_the_body_does(self):
        code = "913482"
        subject = build_reset_subject()
        body = build_reset_body("Alice Test", code, 15)

        assert code in body
        assert code not in subject


# --- build_reset_body: expiry -------------------------------------------------


class TestBuildResetBodyExpiry:
    def test_states_the_expiry_in_minutes(self):
        body = build_reset_body("Alice Test", FAKE_CODE, 15)

        assert "15 minutes" in body

    def test_uses_the_singular_word_for_exactly_one_minute(self):
        body = build_reset_body("Alice Test", FAKE_CODE, 1)

        assert "1 minute" in body
        assert "1 minutes" not in body

    def test_uses_the_plural_word_for_more_than_one_minute(self):
        body = build_reset_body("Alice Test", FAKE_CODE, 30)

        assert "30 minutes" in body


# --- build_reset_body: single use and reassurance -----------------------------


class TestBuildResetBodySingleUseAndReassurance:
    def test_states_the_code_can_be_used_once(self):
        body = build_reset_body("Alice Test", FAKE_CODE, 15)

        assert "once" in body

    def test_reassures_an_unrequested_recipient_without_alarm(self):
        body = build_reset_body("Alice Test", FAKE_CODE, 15).lower()

        assert "if you did not ask for this" in body
        # Whitespace-collapsed before matching: the body is hard-wrapped
        # for plain-text mail, so this sentence legitimately falls across
        # a line break. A wording test must not depend on where the wrap
        # lands, or rewrapping a paragraph would break it.
        assert "nothing has changed" in " ".join(body.split())

    def test_does_not_accuse_or_alarm(self):
        """Rule 13: a reset request is not evidence anything is wrong."""
        body = build_reset_body("Alice Test", FAKE_CODE, 15).lower()

        for word in ("warning", "alert", "suspicious", "compromised", "hacked"):
            assert word not in body


# --- build_reset_body: no link, no HTML ---------------------------------------


class TestBuildResetBodyNoLinkOrHtml:
    def test_body_contains_no_url_of_any_kind(self):
        body = build_reset_body("Alice Test", FAKE_CODE, 15).lower()

        for fragment in ("http://", "https://", "www.", "<a ", "click here"):
            assert fragment not in body

    def test_body_is_a_plain_string_with_no_html_tags(self):
        body = build_reset_body("Alice Test", FAKE_CODE, 15)

        assert isinstance(body, str)
        assert "<" not in body
        assert ">" not in body


# --- pure-function contract (DoD 21) ------------------------------------------


class TestPureFunctionContract:
    """No document, transport, socket, or credential reaches this module
    -- proved by exercising both functions with nothing but plain
    strings/ints, and by inspecting the source for anything that could
    read one.
    """

    @pytest.mark.parametrize(
        "name,code,ttl_minutes",
        [
            ("Alice Test", "482913", 15),
            (None, "000000", 1),
            ("", "999999", 60),
        ],
    )
    def test_build_reset_body_accepts_only_plain_arguments(self, name, code, ttl_minutes):
        body = build_reset_body(name, code, ttl_minutes)

        assert isinstance(body, str)

    def test_module_imports_nothing_from_flask_pymongo_smtplib_or_email(self):
        import notifications.reset_messages as module

        source = open(module.__file__, encoding="utf-8").read()

        for banned in (
            "import flask",
            "from flask",
            "import pymongo",
            "from pymongo",
            "import smtplib",
            "from smtplib",
            "import email",
            "from email",
        ):
            assert banned not in source, (
                f"notifications/reset_messages.py must not contain {banned!r}"
            )
