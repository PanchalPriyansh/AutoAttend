"""Tests for backend/auth/password_reset.py (25-forgot-password.md).

Spec contract under test ("Backend" + "Rules for implementation" +
"Definition of done"), as revised by the concurrency fix from
`/code-review-feature`:
  - `issue_reset_code(db, email, *, now=None)` replaces the old
    `prepare_reset_code`/`record_reset_code` pair. It writes the row
    ITSELF, atomically, via one upserting `find_one_and_update` filtered
    on `reissue_filter` (a row older than the cooldown), before the
    caller sends any mail. It returns `None` -- indistinguishably -- for
    an unknown address, a deactivated account, and an account whose code
    was issued inside the cooldown (the upsert falls through to an
    insert that the unique index on `user_id` refuses, raising
    `DuplicateKeyError`, which is caught); otherwise a `PendingResetCode`
    built from the STORED account, never the request's casing of the
    address. The written row is hashed (never plaintext), starts at
    `attempts: 0` with no `consumed_at` (an `$unset` clears any marker
    left by a row being reused), and there is never more than one row per
    account -- concurrent callers are serialised by the unique index, not
    by application logic.
  - `reset_password_with_code(db, email, code, *, new_password, now=None)`
    raises `InvalidResetCodeError` with the single `INVALID_CODE_MESSAGE`
    for every one of: no code issued, wrong code, expired, already
    consumed, attempts exhausted, wrong account, and a deactivated/
    missing account. The attempt is now charged ATOMICALLY, in the same
    `find_one_and_update` that finds a usable code (`$inc` on `attempts`,
    `return_document=AFTER`) -- so even a CORRECT code is charged one
    attempt (harmless, since the row is deleted on success), and an
    expired/consumed/exhausted row is never charged because it is never
    matched. Consuming is a separate atomic conditional update (never
    read-then-write) and happens BEFORE the password write, so a write
    failure leaves the code safely spent rather than replayable. A
    successful reset delegates to `set_user_password` unwrapped (bumping
    `token_version`) and deletes the spent row.
  - No Flask objects appear in any signature in this module.

`auth_test_helpers.make_fake_db`/`make_user` back every test -- no live
MongoDB. Every code/email/password used is an obviously-fake, hardcoded
test value.
"""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from auth.errors import InvalidResetCodeError
from auth.password_reset import (
    INVALID_CODE_MESSAGE,
    PendingResetCode,
    issue_reset_code,
    reset_password_with_code,
)
from auth.passwords import hash_password, verify_password
from auth.reset_codes import CODE_TTL_MINUTES, MAX_ATTEMPTS, RESEND_COOLDOWN_SECONDS
from auth_test_helpers import make_fake_db, make_user
from common.errors import NotFoundError
from database.schema import PASSWORD_RESET_CODES, USERS

FAKE_PASSWORD = "a-fake-current-password-1"
FAKE_NEW_PASSWORD = "a-fake-new-password-1"
FAKE_CODE = "482913"


def _seed_code(
    db,
    user,
    *,
    code=FAKE_CODE,
    attempts=0,
    now=None,
    expires_at=None,
    consumed_at=None,
    created_at=None,
    email=None,
):
    now = now or datetime.now(timezone.utc)
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
    db[PASSWORD_RESET_CODES].insert_one(document)
    return document


def _codes_for(db, user):
    return list(db[PASSWORD_RESET_CODES].find({"user_id": user["_id"]}))


class TestIssueResetCodeIndistinguishableNones:
    """DoD 1, 2: unknown, deactivated, and cooling-down accounts must all
    produce the same `None` -- the caller cannot tell them apart."""

    def test_returns_none_for_an_unknown_address(self):
        db = make_fake_db([])

        assert issue_reset_code(db, "nobody@college.edu") is None

    def test_returns_none_for_a_deactivated_account(self):
        user = make_user(email="inactive@college.edu", password=FAKE_PASSWORD, is_active=False)
        db = make_fake_db([user])

        assert issue_reset_code(db, "inactive@college.edu") is None

    def test_returns_none_when_a_code_was_issued_within_the_cooldown_window(self):
        user = make_user(email="cooling@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)
        _seed_code(db, user, now=now, created_at=now)

        assert issue_reset_code(db, "cooling@college.edu", now=now) is None

    def test_the_unknown_and_deactivated_cases_write_nothing(self):
        no_account_db = make_fake_db([])
        inactive_user = make_user(
            email="inactive2@college.edu", password=FAKE_PASSWORD, is_active=False
        )
        inactive_db = make_fake_db([inactive_user])

        issue_reset_code(no_account_db, "nobody2@college.edu")
        issue_reset_code(inactive_db, "inactive2@college.edu")

        assert no_account_db[PASSWORD_RESET_CODES]._documents == []
        assert inactive_db[PASSWORD_RESET_CODES]._documents == []

    def test_the_cooldown_case_leaves_the_existing_row_completely_unchanged(self):
        """The DuplicateKeyError is raised by the FAILED insert attempt --
        the row that made the upsert miss is never touched."""
        user = make_user(email="cooling2@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)
        _seed_code(db, user, code=FAKE_CODE, now=now, created_at=now)
        existing = _codes_for(db, user)[0]

        assert issue_reset_code(db, "cooling2@college.edu", now=now) is None

        rows = _codes_for(db, user)
        assert len(rows) == 1
        assert rows[0] == existing


class TestIssueResetCodeIsAtomic:
    """Pins the concurrency fix directly: the whole point of moving the
    write inside `issue_reset_code`, ahead of the send, is that a second
    call landing inside the cooldown can never produce a second row --
    the unique index on `user_id` makes that true regardless of how many
    callers race, and a single-threaded sequential call is enough to
    prove the INVARIANT (at most one row, ever) even though it cannot
    exercise true interleaving.
    """

    def test_a_second_call_inside_the_cooldown_returns_none_and_leaves_exactly_one_row(self):
        user = make_user(email="atomic@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)

        first = issue_reset_code(db, "atomic@college.edu", now=now)
        second = issue_reset_code(db, "atomic@college.edu", now=now)

        assert first is not None
        assert second is None
        rows = _codes_for(db, user)
        assert len(rows) == 1
        assert verify_password(first.code, rows[0]["code_hash"])

    def test_repeated_calls_inside_the_cooldown_never_duplicate_the_row(self):
        user = make_user(email="atomic2@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)

        results = [issue_reset_code(db, "atomic2@college.edu", now=now) for _ in range(5)]

        assert results[0] is not None
        assert all(result is None for result in results[1:])
        assert len(_codes_for(db, user)) == 1


class TestIssueResetCodeSuccess:
    def test_returns_a_pending_code_for_a_registered_active_address(self):
        user = make_user(email="active@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)

        pending = issue_reset_code(db, "active@college.edu", now=now)

        assert isinstance(pending, PendingResetCode)
        assert pending.user_id == user["_id"]
        assert pending.email == "active@college.edu"
        assert pending.expires_at == now + timedelta(minutes=CODE_TTL_MINUTES)
        assert isinstance(pending.code, str)
        assert pending.code.isdigit()

    def test_pending_email_is_the_stored_address_not_the_requests_casing(self):
        """The stored address, so a code cannot be sent anywhere except
        to the account it resets -- even if the caller's normalized
        request differs in whitespace or case from what is on file."""
        user = make_user(email="stored@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])

        pending = issue_reset_code(db, "  Stored@College.edu  ")

        assert pending.email == "stored@college.edu"

    def test_writes_the_row_itself_before_returning(self):
        """The reversed ordering: issuing writes BEFORE the caller sends
        any mail, unlike the old prepare/record split."""
        user = make_user(email="writes@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])

        pending = issue_reset_code(db, "writes@college.edu")

        rows = _codes_for(db, user)
        assert len(rows) == 1
        assert verify_password(pending.code, rows[0]["code_hash"])

    def test_a_row_older_than_the_cooldown_does_not_block_a_new_request(self):
        user = make_user(email="stale-cooldown@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)
        old = _seed_code(
            db,
            user,
            code="111111",
            created_at=now - timedelta(seconds=RESEND_COOLDOWN_SECONDS + 1),
        )

        pending = issue_reset_code(db, "stale-cooldown@college.edu", now=now)

        assert pending is not None
        rows = _codes_for(db, user)
        assert len(rows) == 1
        assert rows[0]["code_hash"] != old["code_hash"]
        assert verify_password(pending.code, rows[0]["code_hash"])

    def test_a_consumed_code_still_counts_toward_the_cooldown(self):
        """Consumed and expired codes still count: the cooldown question
        is "when did we last send", not "is that code still good"."""
        user = make_user(email="consumed-cooldown@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)
        _seed_code(db, user, created_at=now, consumed_at=now)

        assert issue_reset_code(db, "consumed-cooldown@college.edu", now=now) is None

    def test_an_expired_code_still_counts_toward_the_cooldown(self):
        user = make_user(email="expired-cooldown@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)
        _seed_code(db, user, created_at=now, expires_at=now - timedelta(minutes=1))

        assert issue_reset_code(db, "expired-cooldown@college.edu", now=now) is None


class TestIssueResetCodeWritesTheRow:
    """What used to be `record_reset_code`'s contract, now folded into
    `issue_reset_code` itself since there is no longer a separate write
    step."""

    def test_stores_the_hash_and_never_the_plaintext_code(self):
        user = make_user(email="record@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])

        pending = issue_reset_code(db, "record@college.edu")

        row = _codes_for(db, user)[0]
        assert row["code_hash"] != pending.code
        assert verify_password(pending.code, row["code_hash"])

    def test_stored_row_starts_at_zero_attempts_with_no_consumed_at(self):
        user = make_user(email="fresh-row@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)

        issue_reset_code(db, "fresh-row@college.edu", now=now)

        row = _codes_for(db, user)[0]
        assert row["attempts"] == 0
        assert "consumed_at" not in row
        assert row["email"] == "fresh-row@college.edu"
        assert row["created_at"] == now

    def test_replacing_a_stale_row_clears_a_previous_consumed_marker(self):
        """The `$unset` on `consumed_at`: a row being reused for a new
        code must not still look spent."""
        user = make_user(email="reuse@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)
        _seed_code(
            db,
            user,
            code="111111",
            created_at=now - timedelta(seconds=RESEND_COOLDOWN_SECONDS + 1),
            consumed_at=now - timedelta(seconds=RESEND_COOLDOWN_SECONDS + 1),
        )

        issue_reset_code(db, "reuse@college.edu", now=now)

        row = _codes_for(db, user)[0]
        assert "consumed_at" not in row
        assert row["attempts"] == 0

    def test_replacing_a_stale_row_resets_its_attempts_to_zero(self):
        """A new code is a new five guesses -- inheriting the old count
        would let an attacker exhaust a victim's next code before it was
        even issued."""
        user = make_user(email="resetattempts@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        now = datetime.now(timezone.utc)
        _seed_code(
            db,
            user,
            code="111111",
            attempts=MAX_ATTEMPTS,
            created_at=now - timedelta(seconds=RESEND_COOLDOWN_SECONDS + 1),
        )

        issue_reset_code(db, "resetattempts@college.edu", now=now)

        assert _codes_for(db, user)[0]["attempts"] == 0

    def test_leaves_other_accounts_rows_untouched(self):
        user_a = make_user(email="user-a@college.edu", password=FAKE_PASSWORD)
        user_b = make_user(email="user-b@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user_a, user_b])
        _seed_code(db, user_b, code="999999")
        existing_b = _codes_for(db, user_b)[0]

        issue_reset_code(db, "user-a@college.edu")

        assert len(_codes_for(db, user_a)) == 1
        rows_b = _codes_for(db, user_b)
        assert len(rows_b) == 1
        assert rows_b[0] == existing_b


class TestPendingResetCodeDoesNotLeakThroughRepr:
    def test_repr_omits_the_plaintext_code(self):
        pending = PendingResetCode(
            user_id=ObjectId(),
            name="Test User",
            email="repr@college.edu",
            expires_at=datetime.now(timezone.utc),
            code=FAKE_CODE,
        )

        assert FAKE_CODE not in repr(pending)


class TestResetPasswordWithCodeSuccess:
    def test_correct_code_resets_the_password(self):
        user = make_user(email="success@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)

        updated = reset_password_with_code(
            db, "success@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
        )

        assert verify_password(FAKE_NEW_PASSWORD, updated["password_hash"])
        assert not verify_password(FAKE_PASSWORD, updated["password_hash"])

    def test_delegates_to_set_user_password_and_bumps_token_version(self):
        user = make_user(
            email="bump@college.edu", password=FAKE_PASSWORD, token_version=4
        )
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)

        updated = reset_password_with_code(
            db, "bump@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
        )

        assert updated["token_version"] == 5

    def test_a_document_with_no_prior_token_version_gains_one_at_one(self):
        user = make_user(email="firstbump@college.edu", password=FAKE_PASSWORD)
        assert "token_version" not in user
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)

        updated = reset_password_with_code(
            db, "firstbump@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
        )

        assert updated["token_version"] == 1

    def test_the_spent_row_is_deleted_after_a_successful_reset(self):
        """Cleanup, not correctness -- there is no TTL index to sweep it,
        so this is what keeps the collection from accumulating rows."""
        user = make_user(email="cleanup@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)

        reset_password_with_code(
            db, "cleanup@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
        )

        assert _codes_for(db, user) == []

    def test_a_correct_code_is_also_charged_one_attempt_before_the_row_is_deleted(
        self, monkeypatch
    ):
        """The concurrency fix charges the attempt in the SAME update
        that finds a usable code, so even a correct guess is charged --
        harmless in the end, since the row is deleted on success, but
        this pins the intermediate state the fix produces rather than
        assuming the old "successful reset leaves attempts at 0"
        behaviour, which no longer applies (there is no observable
        "attempts" left once the row is deleted, so `delete_many` is
        stubbed out here purely to inspect it)."""
        user = make_user(email="chargedcorrect@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)
        monkeypatch.setattr(db[PASSWORD_RESET_CODES], "delete_many", lambda query: None)

        reset_password_with_code(
            db, "chargedcorrect@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
        )

        row = _codes_for(db, user)[0]
        assert row["attempts"] == 1
        assert row["consumed_at"] is not None


class TestResetPasswordWithCodeOneMessageForEverything:
    """DoD 9-15: every distinct failure raises the same
    InvalidResetCodeError with the same INVALID_CODE_MESSAGE."""

    def test_exact_message_text_is_pinned(self):
        assert INVALID_CODE_MESSAGE == "That code is not valid or has expired."

    def test_a_wrong_code_raises_with_the_generic_message(self):
        user = make_user(email="wrong@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)

        with pytest.raises(InvalidResetCodeError) as excinfo:
            reset_password_with_code(
                db, "wrong@college.edu", "000000", new_password=FAKE_NEW_PASSWORD
            )

        assert str(excinfo.value) == INVALID_CODE_MESSAGE

    def test_a_wrong_code_increments_attempts_by_exactly_one_and_stays_usable(self):
        user = make_user(email="attempts@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "attempts@college.edu", "000000", new_password=FAKE_NEW_PASSWORD
            )

        rows = _codes_for(db, user)
        assert len(rows) == 1
        assert rows[0]["attempts"] == 1

        # Still usable -- the correct code now succeeds.
        updated = reset_password_with_code(
            db, "attempts@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
        )
        assert verify_password(FAKE_NEW_PASSWORD, updated["password_hash"])

    def test_an_expired_or_consumed_code_is_never_charged_an_attempt(self):
        """Only a code matching `usable_code_filter` is ever touched by
        the atomic `$inc` -- an expired or already-spent code must not
        let attempts be burned against it."""
        user = make_user(email="noattempt@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(
            db, user, code=FAKE_CODE, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "noattempt@college.edu", "000000", new_password=FAKE_NEW_PASSWORD
            )

        assert _codes_for(db, user)[0]["attempts"] == 0

    def test_replaying_an_already_consumed_code_is_refused(self):
        user = make_user(email="replay@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)

        reset_password_with_code(
            db, "replay@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
        )

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "replay@college.edu", FAKE_CODE, new_password="a-fake-second-password-1"
            )

    def test_an_expired_code_is_refused_even_though_the_row_still_exists(self):
        """DoD 12: proves expiry is checked in code, since there is no
        TTL index to have swept it."""
        user = make_user(email="expired@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(
            db, user, code=FAKE_CODE, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "expired@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
            )

        assert _codes_for(db, user) != []

    def test_attempts_exhausted_refuses_even_the_correct_code(self):
        user = make_user(email="exhausted@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE, attempts=MAX_ATTEMPTS)

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "exhausted@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
            )

    def test_a_code_issued_for_one_account_cannot_reset_another(self):
        owner = make_user(email="owner@college.edu", password=FAKE_PASSWORD)
        bystander = make_user(email="bystander@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([owner, bystander])
        _seed_code(db, owner, code=FAKE_CODE)

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "bystander@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
            )

        assert verify_password(
            FAKE_PASSWORD, db[USERS].find_one({"_id": bystander["_id"]})["password_hash"]
        )
        assert verify_password(
            FAKE_PASSWORD, db[USERS].find_one({"_id": owner["_id"]})["password_hash"]
        )
        # The owner's code is untouched by a guess made under someone
        # else's email -- it was never even looked up.
        assert _codes_for(db, owner)[0]["attempts"] == 0

    def test_no_code_ever_issued_is_refused(self):
        user = make_user(email="nocode@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "nocode@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
            )

    def test_an_unknown_email_is_refused(self):
        db = make_fake_db([])

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "ghost@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
            )

    def test_a_deactivated_account_is_refused_and_writes_no_password(self):
        user = make_user(email="deactivated@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)
        db[USERS].find_one_and_update({"_id": user["_id"]}, {"$set": {"is_active": False}})

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "deactivated@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
            )

        stored = db[USERS].find_one({"_id": user["_id"]})
        assert verify_password(FAKE_PASSWORD, stored["password_hash"])


class TestResetPasswordWithCodeConsumeOrdering:
    """Rule 7, 8: single use is an atomic conditional update, and it
    happens BEFORE the password write -- so a write that then fails still
    leaves the code spent (the safe direction) rather than replayable."""

    def test_consume_happens_before_the_write_so_a_failed_write_still_spends_the_code(
        self, monkeypatch
    ):
        user = make_user(email="raceordering@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)

        def _explode(*args, **kwargs):
            raise NotFoundError("User not found")

        monkeypatch.setattr(db[USERS], "find_one_and_update", _explode)

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "raceordering@college.edu", FAKE_CODE, new_password=FAKE_NEW_PASSWORD
            )

        rows = _codes_for(db, user)
        assert len(rows) == 1
        assert rows[0]["consumed_at"] is not None, (
            "the code must be marked spent even though the password write "
            "failed -- replaying it must not be possible"
        )

    def test_two_sequential_submissions_of_the_same_valid_code_yield_exactly_one_write(self):
        """DoD 16, proxied without real threads: the fake has no
        concurrency to race, so this exercises the outcome rather than
        the interleaving -- the winner's success deletes the row (via
        the same conditional consume-then-cleanup path), and the second
        submission of the identical code finds nothing usable and is
        refused. A true concurrent race, which needs the conditional
        `find_one_and_update` to see the row mid-flight, is exactly the
        case the module docstring says only a real MongoDB can prove
        (see DoD 22b) -- this pins the single-write OUTCOME, which the
        fake can verify."""
        user = make_user(email="race@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)

        first = reset_password_with_code(
            db, "race@college.edu", FAKE_CODE, new_password="a-fake-race-password-1"
        )

        with pytest.raises(InvalidResetCodeError):
            reset_password_with_code(
                db, "race@college.edu", FAKE_CODE, new_password="a-fake-race-password-2"
            )

        assert verify_password("a-fake-race-password-1", first["password_hash"])
        stored = db[USERS].find_one({"_id": user["_id"]})
        assert verify_password("a-fake-race-password-1", stored["password_hash"])
        assert not verify_password("a-fake-race-password-2", stored["password_hash"])


class TestResetPasswordWithCodeMessageNeverLeaksASecret:
    def test_the_exception_message_never_contains_the_code_email_or_a_hash(self):
        user = make_user(email="noleak@college.edu", password=FAKE_PASSWORD)
        db = make_fake_db([user])
        _seed_code(db, user, code=FAKE_CODE)
        stored_hash = _codes_for(db, user)[0]["code_hash"]

        with pytest.raises(InvalidResetCodeError) as excinfo:
            reset_password_with_code(
                db, "noleak@college.edu", "000000", new_password=FAKE_NEW_PASSWORD
            )

        message = str(excinfo.value)
        assert FAKE_CODE not in message
        assert "000000" not in message
        assert "noleak@college.edu" not in message
        assert stored_hash not in message


class TestModuleImportsNoFlask:
    def test_module_imports_no_flask_objects(self):
        import auth.password_reset as module

        source = open(module.__file__, encoding="utf-8").read()

        assert "import flask" not in source
        assert "from flask" not in source
