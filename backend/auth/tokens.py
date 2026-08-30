"""Whether a token still speaks for the credential it was minted under.

Pure functions -- no Flask, no pymongo, no `config`, no I/O of any kind.
This module takes a claims dict and a user document and returns a bool.
It is isolated because that bool decides whether a week-old cookie still
authenticates somebody, and a rule with that much riding on it should be
readable and testable without a database -- the same call 22 made for
`recognition/filenames.py`.

**The counter.** `users.token_version` is a plain integer that moves by
one every time `password_hash` is written, and it is written in exactly
one place (`users/service.py::set_user_password`). A refresh token is
stamped with the value current when it was minted; `POST
/api/auth/refresh` compares the stamp to the document. Equal means the
session was established with the password that is still in force.
Unequal means the credential has been replaced since, and the token is
spent -- which is what ends every *other* session when somebody changes
their password.

**Absent is zero, on both sides, and that is load-bearing.** A user
document written before this feature has no field, and a token minted
before it has no claim; both read as 0, so they match and the session
continues. Deploying this therefore signs nobody out. Treating an absent
claim as invalid instead would have logged out every user in the
database on deploy, to close a window that the account's very next
password change closes properly.

**A counter and not a timestamp**, compared with `==` and not `>=`. The
comparison is exact because the value is exact. A `password_changed_at`
date would have to survive BSON millisecond truncation on one side and
JWT float seconds on the other, and would then be compared with an
inequality that quietly accepts a token minted in the same second as the
change it was supposed to invalidate.

**Only the refresh token carries the claim.** Nothing checks a version
on an access token -- doing so would need a database read on every
authenticated route in the project -- so putting one there would be
decoration that reads like a guarantee. The access token's own 15-minute
lifetime is the residual window, and it is stated rather than papered
over.

No password, hash, or version is logged or raised from here; the caller
turns a False into the same generic refusal it gives a deactivated
account, so a holder of stolen cookies cannot learn *why* they stopped.
"""

TOKEN_VERSION_CLAIM = "token_version"

__all__ = [
    "TOKEN_VERSION_CLAIM",
    "token_version_of",
    "claimed_token_version",
    "refresh_claims_for",
    "is_token_current",
]


def _as_version(value):
    """Read `value` as a version number, or None if it cannot be read.

    None is the "unreadable" answer rather than 0, so that a malformed
    claim fails closed at the comparison instead of silently passing as
    the absent-means-zero case. Nothing we sign can produce one -- the
    claim is written by `refresh_claims_for` from an int -- but a rule
    this cheap to make total should be total.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        # bool is an int subclass in Python, and True == 1 would make a
        # `token_version: true` compare equal to a real version of 1.
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def token_version_of(user):
    """The version stored on a user document. Absent or unreadable -> 0."""
    version = _as_version((user or {}).get(TOKEN_VERSION_CLAIM))
    return 0 if version is None else version


def claimed_token_version(claims):
    """The version stamped into a token, or None if it cannot be read.

    Absent is 0 (see the module docstring); unreadable stays None so the
    comparison can refuse it.
    """
    return _as_version((claims or {}).get(TOKEN_VERSION_CLAIM))


def refresh_claims_for(user):
    """The additional claims a refresh token is minted with."""
    return {TOKEN_VERSION_CLAIM: token_version_of(user)}


def is_token_current(claims, user):
    """True when `claims` was minted under the credential `user` holds now."""
    claimed = claimed_token_version(claims)
    if claimed is None:
        return False
    return claimed == token_version_of(user)
