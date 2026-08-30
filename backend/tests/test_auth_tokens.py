"""Tests for backend/auth/tokens.py -- the token-version rule.

Spec contract under test (24-invalidate-tokens-on-password-change.md,
"Rules for implementation" 3, 4, 10 + "Definition of done" 16):
  - A version is read from a user document; absent means 0.
  - A version is read from a token's claims; absent means 0, so a token
    minted before this feature still matches a document written before
    it -- deploying the feature signs nobody out.
  - The comparison is exact integer equality, never `>=` and never a
    timestamp.
  - A malformed claim fails closed rather than raising.

Everything here runs on plain dicts: no Flask app context, no database
double, no fixture. That is the property the module exists to have, and
DoD 16 asks for it explicitly -- so this module is imported and called
directly, and if that ever stops being possible these tests stop
collecting rather than quietly acquiring a fixture.
"""

import auth.tokens as tokens_module
from auth.tokens import (
    TOKEN_VERSION_CLAIM,
    claimed_token_version,
    is_token_current,
    refresh_claims_for,
    token_version_of,
)


class TestPurity:
    """DoD 16: no Flask, no pymongo, no config anywhere in the module."""

    def test_module_imports_nothing_from_flask_pymongo_or_config(self):
        source = open(tokens_module.__file__, encoding="utf-8").read()
        # Import statements only -- the docstring mentions Flask by name
        # when explaining what the module deliberately does not touch.
        import_lines = [
            line
            for line in source.splitlines()
            if line.startswith(("import ", "from "))
        ]
        for forbidden in ("flask", "pymongo", "bson", "config", "database"):
            assert not any(forbidden in line for line in import_lines), (
                f"auth/tokens.py must not import {forbidden}: {import_lines}"
            )

    def test_module_has_no_imports_at_all(self):
        """Stronger than the above and true today: the rule needs nothing.

        If a genuine need for an import appears, this is the assertion to
        delete -- but deleting it should be a deliberate act, since a
        module with no imports cannot acquire a dependency by accident.
        """
        source = open(tokens_module.__file__, encoding="utf-8").read()
        assert not [
            line
            for line in source.splitlines()
            if line.startswith(("import ", "from "))
        ]


class TestTokenVersionOf:
    def test_reads_the_stored_version(self):
        assert token_version_of({TOKEN_VERSION_CLAIM: 7}) == 7

    def test_absent_field_is_zero(self):
        """The live-data case: every user document written before 24."""
        assert token_version_of({"email": "someone@college.edu"}) == 0

    def test_explicit_zero_is_zero(self):
        assert token_version_of({TOKEN_VERSION_CLAIM: 0}) == 0

    def test_none_document_is_zero(self):
        assert token_version_of(None) == 0

    def test_unreadable_stored_value_is_zero(self):
        """A document is not a token: it is ours, and there is nothing to
        fail closed against. 0 keeps it comparable rather than raising in
        the middle of a refresh."""
        assert token_version_of({TOKEN_VERSION_CLAIM: "not-a-number"}) == 0


class TestClaimedTokenVersion:
    def test_reads_the_claim(self):
        assert claimed_token_version({TOKEN_VERSION_CLAIM: 3}) == 3

    def test_absent_claim_is_zero(self):
        """A refresh token minted before 24 carries no claim."""
        assert claimed_token_version({"sub": "abc", "type": "refresh"}) == 0

    def test_none_claims_is_zero(self):
        assert claimed_token_version(None) == 0

    def test_unreadable_claim_is_none_not_zero(self):
        """None, so is_token_current can refuse it. Reporting 0 would let
        a malformed claim pass as the absent-means-zero case."""
        assert claimed_token_version({TOKEN_VERSION_CLAIM: "nope"}) is None

    def test_boolean_claim_is_unreadable(self):
        """bool is an int subclass in Python, so True == 1 would make
        `token_version: true` compare equal to a real version of 1."""
        assert claimed_token_version({TOKEN_VERSION_CLAIM: True}) is None


class TestRefreshClaimsFor:
    def test_stamps_the_users_current_version(self):
        assert refresh_claims_for({TOKEN_VERSION_CLAIM: 4}) == {TOKEN_VERSION_CLAIM: 4}

    def test_stamps_zero_for_a_document_without_the_field(self):
        assert refresh_claims_for({"email": "x@college.edu"}) == {TOKEN_VERSION_CLAIM: 0}

    def test_produces_a_claim_the_reader_agrees_with(self):
        """The writer and the reader must not drift: whatever
        refresh_claims_for stamps, is_token_current must accept against
        the same document."""
        for stored in ({}, {TOKEN_VERSION_CLAIM: 0}, {TOKEN_VERSION_CLAIM: 11}):
            assert is_token_current(refresh_claims_for(stored), stored)


class TestIsTokenCurrent:
    def test_matching_versions_are_current(self):
        assert is_token_current({TOKEN_VERSION_CLAIM: 2}, {TOKEN_VERSION_CLAIM: 2})

    def test_older_claim_is_not_current(self):
        """The whole feature in one assertion: the password moved on."""
        assert not is_token_current({TOKEN_VERSION_CLAIM: 1}, {TOKEN_VERSION_CLAIM: 2})

    def test_newer_claim_is_not_current_either(self):
        """Equality, not `>=`. A claim ahead of the document cannot arise
        from anything this project signs, so it is refused rather than
        being read as 'at least as new'."""
        assert not is_token_current({TOKEN_VERSION_CLAIM: 3}, {TOKEN_VERSION_CLAIM: 2})

    def test_absent_on_both_sides_is_current(self):
        """Rule 3, and the reason deploying 24 signs nobody out: a
        pre-existing token against a pre-existing document."""
        assert is_token_current({"sub": "abc"}, {"email": "x@college.edu"})

    def test_absent_claim_against_a_bumped_document_is_not_current(self):
        """...and the moment that pre-existing token dies: the account's
        first password change after the deploy."""
        assert not is_token_current({"sub": "abc"}, {TOKEN_VERSION_CLAIM: 1})

    def test_a_numeric_string_claim_is_readable_and_matches(self):
        """Nothing we sign produces one, but "1" is unambiguously 1 --
        fail-closed is for values that cannot be read, not for values
        that arrive in an unexpected type."""
        assert is_token_current({TOKEN_VERSION_CLAIM: "1"}, {TOKEN_VERSION_CLAIM: 1})

    def test_unreadable_claims_fail_closed(self):
        assert not is_token_current({TOKEN_VERSION_CLAIM: "one"}, {TOKEN_VERSION_CLAIM: 1})
        assert not is_token_current({TOKEN_VERSION_CLAIM: []}, {TOKEN_VERSION_CLAIM: 0})
        assert not is_token_current({TOKEN_VERSION_CLAIM: {}}, {TOKEN_VERSION_CLAIM: 0})

    def test_boolean_claim_does_not_match_version_one(self):
        assert not is_token_current({TOKEN_VERSION_CLAIM: True}, {TOKEN_VERSION_CLAIM: 1})

    def test_none_claims_against_a_bumped_document_is_not_current(self):
        assert not is_token_current(None, {TOKEN_VERSION_CLAIM: 1})
