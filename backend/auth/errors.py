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

__all__ = ["IncorrectPasswordError", "InactiveAccountError"]


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
    was issued. 05-admin-user-management deferred token invalidation, so a
    deactivated account keeps a usable access token for up to
    JWT_ACCESS_TOKEN_EXPIRES (15 minutes); re-reading `is_active` from the
    database is what stops that window being used to set a new password
    and walk back in.

    The message is deliberately the same generic "Authentication required"
    the rest of the blueprint uses, so this cannot be used to tell a
    deactivated account apart from a deleted one.
    """
