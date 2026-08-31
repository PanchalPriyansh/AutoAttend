"""Resetting a forgotten password with a code sent by email.

The other half of password management. `auth/password_change.py` serves
somebody who knows their password; this serves somebody who does not, and
therefore cannot present a token or prove anything except possession of
their inbox.

Its own module for the same reason `password_change.py` is one:
`users/service.py` already imports `auth/service.py`, so an
`auth/service.py` that imported `users/service.py` back would be a
circular import at module load. Nothing imports this module, so that
cycle does not form here either.

No Flask request/response objects appear in any signature -- HTTP
concerns belong to routes/auth.py, which maps the one exception raised
here onto 400 and, critically, refuses to report which of this module's
outcomes produced it.

**Issuing writes before it sends, which is the opposite of what this
feature's spec originally said.** `10-low-attendance-notifications`'s
send-then-record rule was carried over here, and it was wrong for this
endpoint: checking the cooldown with a read and writing the row after an
SMTP round trip leaves a window as wide as that round trip in which every
concurrent request passes the check. The cooldown is the only thing
stopping this endpoint being used as an anonymous mail bomb aimed at
somebody else's inbox, so a cooldown that concurrency walks past is not a
control at all. `issue_reset_code` therefore claims the row and the
cooldown slot in ONE upserting update, which the unique index on
`user_id` refuses while a row is still inside the window -- so the
database serialises the requests instead of the code hoping they arrive
apart.

The price is named rather than hidden: a send that fails afterwards
leaves a stored code the user never received, and has already replaced
whatever code they were holding. Both are recovered by asking again a
minute later, and neither is reachable by anyone but the account owner --
whereas being mail-bombed by a stranger is not recoverable at all.

**Nothing here distinguishes a missing account from an inactive one, or
an expired code from a wrong one.** Every failure raises one exception
with one message. The distinctions exist inside the function bodies
because the logic needs them, and go no further.

No code, code hash, email address, or user id is logged or placed in an
exception message anywhere in this module.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from auth.errors import InvalidResetCodeError
from auth.passwords import hash_password, verify_password
from auth.reset_codes import (
    expiry_from,
    generate_code,
    reissue_filter,
    usable_code_filter,
)
from auth.service import normalize_email
from common.errors import NotFoundError
from database.schema import PASSWORD_RESET_CODES, USERS
from users.service import set_user_password

# The single message every failure of a code produces. Defined once so
# that two call sites cannot drift into two different strings, which
# would be an oracle assembled by accident.
INVALID_CODE_MESSAGE = "That code is not valid or has expired."


@dataclass(frozen=True)
class PendingResetCode:
    """A code that has been generated but not yet sent or stored.

    `code` is declared `repr=False`, the same guard
    `notifications/settings.py::SmtpSettings` puts on its password: this
    object is passed between two functions with a mail send in the
    middle, and a traceback from that send must not be able to print the
    code through an automatic repr.

    `email` is the address stored on the account, never the one the
    request supplied. They are usually the same string, but only one of
    them is a fact about the account.
    """

    user_id: object
    name: str
    email: str
    expires_at: datetime
    code: str = field(repr=False)


def issue_reset_code(db, email, *, now=None):
    """Claim the cooldown slot, store a fresh code, and return it to send.

    Returns a `PendingResetCode`, or `None` when nothing should be sent:
    no such account, a deactivated one, or one whose last code was issued
    inside the cooldown. The caller must treat all three identically --
    they are one answer with three causes, and reporting the difference is
    exactly the account enumeration this endpoint exists to avoid.

    **One update does the whole thing**, because a check and a write that
    are separate are a check anybody can walk past. `reissue_filter`
    matches only a row older than the cooldown, so:

      - no row yet          -> the upsert inserts one,
      - row old enough      -> it is replaced in place,
      - row inside cooldown -> the filter misses, the upsert falls
                               through to an insert, and the unique index
                               on `user_id` raises DuplicateKeyError.

    That last branch is the cooldown, enforced by the database. It is also
    what makes concurrent requests safe: the first one moves `created_at`
    to now, so every other request in flight lands in it.

    The row is written BEFORE the mail is sent, which is the reverse of
    this project's send-then-record habit -- see the module docstring for
    why, and for what it costs.

    Only the hash is stored. The plaintext code goes to the caller, into
    one email body, and nowhere else.
    """
    now = now or datetime.now(timezone.utc)

    user = db[USERS].find_one({"email": normalize_email(email)})
    if user is None or not user.get("is_active", False):
        return None

    code = generate_code()
    expires_at = expiry_from(now)

    try:
        db[PASSWORD_RESET_CODES].find_one_and_update(
            reissue_filter(user["_id"], now),
            {
                "$set": {
                    "code_hash": hash_password(code),
                    "expires_at": expires_at,
                    # Reset with the code rather than carried over: a new
                    # code is a new five guesses, and inheriting the count
                    # would let an attacker exhaust a victim's next code
                    # before it was even issued.
                    "attempts": 0,
                    "created_at": now,
                    # The stored address, so a code cannot be sent
                    # anywhere except to the account it resets.
                    "email": user["email"],
                },
                # Clears the marker left by a previously spent code when
                # the row is reused. A no-op on insert.
                "$unset": {"consumed_at": ""},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        # Inside the cooldown. Indistinguishable to the caller from an
        # address that has no account at all.
        return None

    return PendingResetCode(
        user_id=user["_id"],
        name=user.get("name") or "",
        email=user["email"],
        expires_at=expires_at,
        code=code,
    )


def reset_password_with_code(db, email, code, *, new_password, now=None):
    """Spend a code and set a new password. Returns the updated user.

    Raises `InvalidResetCodeError` -- with one message, whatever the
    cause -- when the code cannot be spent.

    The caller is responsible for having validated `new_password`
    BEFORE calling this, because reaching here costs the code: a password
    that is merely too short must not consume it, or a length mistake
    would send the user back to their inbox for a fresh one.
    """
    now = now or datetime.now(timezone.utc)

    user = db[USERS].find_one({"email": normalize_email(email)})
    if user is None or not user.get("is_active", False):
        raise InvalidResetCodeError(INVALID_CODE_MESSAGE)

    # **The attempt is charged before it is spent**, in one update that
    # both finds a usable code and counts a guess against it. Reading
    # first and incrementing afterwards would let concurrent guesses each
    # read `attempts` before the other increment landed, so five would
    # bound requests-in-flight rather than guesses -- and that cap is the
    # only reason a 6-digit code is safe at all. The unique index
    # guarantees at most one row per account, so nothing needs ordering.
    #
    # One filter decides expired, consumed, exhausted and wrong-account
    # at once; a code failing any of them is simply not found, and no
    # guess is charged for it -- so an expired or already-spent code
    # cannot be used to burn somebody else's remaining attempts.
    stored = db[PASSWORD_RESET_CODES].find_one_and_update(
        usable_code_filter(user["_id"], now),
        {"$inc": {"attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if stored is None:
        raise InvalidResetCodeError(INVALID_CODE_MESSAGE)

    # verify_password, not a comparison: one code path in this project
    # decides whether a submitted secret matches a stored one, and it is
    # the one login uses.
    #
    # A correct code has been charged an attempt too. That costs nothing
    # -- the row is deleted below -- and it is the price of the count
    # being exact rather than optimistic.
    if not verify_password(code, stored["code_hash"]):
        raise InvalidResetCodeError(INVALID_CODE_MESSAGE)

    # Consume before writing, and conditionally on it not already being
    # consumed. Two simultaneous submissions of one valid code both reach
    # this line; exactly one of them matches, and the loser is refused
    # like any other spent code. A read-then-write would let both through.
    if (
        db[PASSWORD_RESET_CODES].find_one_and_update(
            {"_id": stored["_id"], "consumed_at": None},
            {"$set": {"consumed_at": now}},
        )
        is None
    ):
        raise InvalidResetCodeError(INVALID_CODE_MESSAGE)

    try:
        # Unwrapped, so a reset is byte-for-byte what an admin reset and a
        # self-service change already write -- including the token_version
        # bump that ends every existing session on the account. That is
        # the right outcome here: somebody resetting a forgotten password
        # may be recovering from a compromise rather than forgetfulness.
        updated = set_user_password(db, user["_id"], password=new_password)
    except NotFoundError as exc:
        # Deleted between the read above and this write. The code is
        # already spent, which is the safe direction to fail in.
        raise InvalidResetCodeError(INVALID_CODE_MESSAGE) from exc

    # Cleanup, not correctness -- the conditional consume above is what
    # makes the code single-use. This only keeps the collection from
    # accumulating spent rows, which matters because there is no TTL
    # index to sweep them.
    db[PASSWORD_RESET_CODES].delete_many({"user_id": user["_id"]})

    return updated
