"""Whether six digits from an email may still reset an account.

Pure functions -- no Flask, no pymongo, no `config`, no I/O. This module
generates a code and builds the filters that decide which stored code, if
any, is still usable. It is isolated for the reason `auth/tokens.py` and
`recognition/filenames.py` are: this is the rule that decides whether a
message in somebody's inbox can take over their account, and a rule with
that much riding on it should be readable and testable without a
database.

**The four numbers live here, not in `config.py`.** This project has no
rate limiting anywhere, and a 6-digit code is one in 10^6 -- so the cap,
the expiry and the cooldown below are not tuning parameters, they are the
entire compensating control. An operator raising MAX_ATTEMPTS in a `.env`
would be changing a security property from outside code review, so the
numbers sit beside the rule that reads them. Nothing here is read from
the environment.

**The comparisons are expressed as query filters, deliberately.** The
obvious shape -- read the document, compare `expires_at` to `now` in
Python -- walks into a bug this project has already shipped once:
pymongo hands back naive datetimes while anything freshly built carries a
timezone, and comparing the two raises TypeError. `notifications/
service.py::cooldown_skips` fixed that by letting MongoDB compare two
BSON dates, and this module makes the same call. It also means a code's
four disqualifying conditions cannot be applied half-way: there is one
filter, and a code that does not match it is simply not found.

**A code that fails the filter is not distinguished from one that does
not exist.** Expired, consumed, exhausted, and never-issued all produce
the same "no usable code", which is what lets the route answer with a
single message. Only a code that IS usable and whose hash does not match
counts as a wrong guess, and only that increments `attempts`.

No code, code hash, email, or user id is logged or raised from here.
"""

import secrets
from datetime import timedelta

__all__ = [
    "CODE_LENGTH",
    "CODE_TTL_MINUTES",
    "MAX_ATTEMPTS",
    "RESEND_COOLDOWN_SECONDS",
    "generate_code",
    "expiry_from",
    "usable_code_filter",
    "reissue_filter",
    "MAX_SUBMITTED_CODE_LENGTH",
]

# Six digits, which is what a person will retype off a phone screen
# without resenting it. The length is only safe in company: on its own it
# is one guess in a million, and it is MAX_ATTEMPTS that makes that a
# real bound rather than a statistic.
CODE_LENGTH = 6

# Long enough to fetch a phone and read an email, short enough that a
# code left in an inbox stops working before it is forgotten about.
CODE_TTL_MINUTES = 15

# Five wrong guesses and the code is dead -- 5 in 10^6 per issued code,
# and recovery costs the attacker a fresh request, which the cooldown
# below then paces. Without this cap the expiry window would be the only
# limit, and 15 minutes is a great many guesses.
MAX_ATTEMPTS = 5

# One request per account per minute. This is not about guessing at all:
# it is what stops the request endpoint being an anonymous mail bomb
# aimed at somebody else's inbox.
RESEND_COOLDOWN_SECONDS = 60

# The longest string the reset endpoint will even look at. Deliberately
# far above CODE_LENGTH rather than equal to it: this is not a format
# check -- a wrong-length code should fail as a wrong code, indistinctly
# from any other wrong one -- it is a ceiling on what gets fed to a
# password-hash comparison, whose cost scales with the input a caller
# chooses. Six digits is the code; 64 characters is the point past which
# somebody is not typing a code at all.
MAX_SUBMITTED_CODE_LENGTH = 64


def generate_code():
    """A uniformly random CODE_LENGTH-digit code, as a string.

    `secrets`, never `random`: the latter is seeded predictably and is
    not for anything anyone would want to guess.

    Zero-padded rather than range-shifted, so every value including
    "000123" is reachable. Drawing from 100000..999999 to avoid the
    padding would silently discard a tenth of the space.

    A string rather than an int for the same reason -- "000123" is the
    code, and an int cannot hold its leading zeros.
    """
    return f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"


def expiry_from(now):
    """When a code issued at `now` stops working."""
    return now + timedelta(minutes=CODE_TTL_MINUTES)


def usable_code_filter(user_id, now):
    """Matches this user's outstanding code, if it may still be spent.

    Four conditions in one filter, and a code failing any of them is
    simply not found:

      - it belongs to this account (never matched by email, so an address
        corrected after issuing cannot repoint a live code),
      - it has not been spent -- `None` matches a missing field as well
        as a null one, which is what makes `consumed_at` optional in the
        schema,
      - it has not expired, checked here rather than trusted to a sweep,
        because there is no TTL index (see database/schema.py), and
      - its attempts are not exhausted.
    """
    return {
        "user_id": user_id,
        "consumed_at": None,
        "expires_at": {"$gt": now},
        "attempts": {"$lt": MAX_ATTEMPTS},
    }


def reissue_filter(user_id, now):
    """Matches this user's code row when it may be replaced with a new one.

    The inverse of "inside the cooldown": it matches only a row last
    written longer ago than RESEND_COOLDOWN_SECONDS. Used as the filter of
    an **upserting** update, which is what makes issuing atomic --

      - no row at all         -> no match, so the upsert inserts one,
      - row outside cooldown  -> matches, so it is replaced in place,
      - row inside cooldown   -> no match, so the upsert tries to insert
                                 and the unique index on `user_id`
                                 refuses it.

    That last case is the whole point. Written as a read followed by a
    separate write, the cooldown is a check nobody has to respect:
    concurrent requests all read before any of them writes, all pass, and
    all send mail -- which turns the endpoint into an anonymous mail bomb
    aimed at somebody else's inbox, the one thing this cooldown exists to
    prevent. Expressed this way the database serialises them, and the
    losers cannot tell they lost from anything in the response.

    Keyed on the account and not on the address: an address with no
    account never reaches this, because nothing is ever sent to one, so
    there is no row to write and no cooldown to keep. That is deliberate
    -- storing a row for an unknown address would build a log of who has
    tried to reset an account they do not have.

    Consumed and expired rows still match once they are old enough. The
    question is "when did we last put a message in this inbox", not "is
    that code still good".
    """
    return {
        "user_id": user_id,
        "created_at": {"$lt": now - timedelta(seconds=RESEND_COOLDOWN_SECONDS)},
    }
