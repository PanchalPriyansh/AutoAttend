"""Domain exceptions raised by the authentication package.

Follows the per-package pattern already used by academic/errors.py,
attendance/errors.py, notifications/errors.py and recognition/errors.py:
the generic ideas -- bad input, missing target, conflicting state -- live
in common/errors.py, and only the errors that are about *authentication*
live here.

Route blueprints map these onto status codes; the mapping is spelled out
in each class's docstring so the two never drift.

As with every other domain error in this project, the message carried by
one of these is returned verbatim in a JSON error body -- so it must
never carry a password, a hash, or any part of either.
"""

from common.errors import AppError

__all__ = ["IncorrectPasswordError", "InactiveAccountError", "InvalidResetCodeError"]


class IncorrectPasswordError(AppError):
    """The caller failed to prove the password they claim to hold -- maps to 403.

    Deliberately 403 and not 401, and that is not a style preference.
    frontend/src/api/client.js transparently refreshes and *retries* any
    401 whose path is not /api/auth/login or /api/auth/refresh, so a 401
    here would silently re-submit the same wrong password a second time on
    every attempt -- halving the cost of a brute-force attempt against a
    stolen session and making the audit story wrong.

    403 is also the more accurate reading: the caller IS authenticated,
    and is being refused an action they have not proved they may take.
    """


class InactiveAccountError(AppError):
    """A valid token whose account cannot act -- maps to 401.

    Raised when the identity in an otherwise-valid access token no longer
    resolves to an active user: deleted, or deactivated since the token
    was issued. A deactivated account keeps a usable ACCESS token until it
    expires, for up to JWT_ACCESS_TOKEN_EXPIRES (15 minutes), because
    nothing checks the database on an ordinary request; re-reading
    `is_active` here is what stops that window being used to set a new
    password and walk back in.

    24-invalidate-tokens-on-password-change closed the far larger window
    that sat beside this one -- a refresh token outliving the password it
    was minted under, for up to seven days -- but it deliberately did not
    add a per-request check, so the 15 minutes above is real and remains
    the reason this re-read exists.

    The message is deliberately the same generic "Authentication required"
    the rest of the blueprint uses, so this cannot be used to tell a
    deactivated account apart from a deleted one.
    """


class InvalidResetCodeError(AppError):
    """A forgot-password code that cannot be spent -- maps to 400.

    **One error for six outcomes.** No code was ever issued for that
    address, the code is wrong, it has expired, it has already been used,
    its attempts are exhausted, or the account has since been deactivated
    or deleted -- all of them raise this, with the same message. Each of
    the alternatives is an oracle: "no code has been issued" confirms the
    address is real and unspent, "you have used all your attempts"
    confirms the account is worth coming back to, and "that code has
    expired" confirms the address is real. The person who mistyped a
    digit is served just as well by one sentence, and their next step --
    ask for another code -- is the same in every case.

    **400 and not 401**, and unlike IncorrectPasswordError's 403 the
    reason is not that the caller is authenticated (they are not; this
    endpoint is reached signed out). It is that
    frontend/src/api/client.js transparently refreshes and *retries* any
    401 outside /api/auth/login and /api/auth/refresh. Here a retry would
    resubmit the same code and burn a second of the five attempts the
    code is allowed -- so the intuitive status would halve the user's
    budget on every wrong guess. 23 found this for a wrong password,
    where the cost was an extra hash; on this path the cost comes out of
    a limited resource.

    403 was considered and rejected for the opposite reason: it reads as
    "you are known and not permitted", which credits the caller with an
    identity they have not established.
    """
