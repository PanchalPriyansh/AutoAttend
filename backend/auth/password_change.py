"""Changing your own password: prove the current one, then set a new one.

Its own module rather than a function in auth/service.py because
users/service.py already imports auth/service.py (`create_user`,
`normalize_email`, `DuplicateEmailError`). An auth/service.py that
imported users/service.py back would be a circular import at module load.
Nothing imports this module, so that cycle does not form here.

No Flask request/response objects appear in any signature -- HTTP
concerns (status codes, cookies, JSON bodies) belong to routes/auth.py,
which maps the exceptions raised here onto 400/401/403.

The write itself is NOT re-implemented: it delegates to
users/service.py::set_user_password, so a password changed here is
byte-for-byte what an admin reset already writes. Two modules writing the
same credential field is how they drift.

No password, and no part of one -- not its length, not a prefix -- is
ever logged or placed in an exception message here.
"""

from auth.errors import InactiveAccountError, IncorrectPasswordError
from auth.passwords import verify_password
from auth.service import get_user_by_id
from common.errors import NotFoundError, ValidationError
from users.service import set_user_password


def change_password(db, user_id, *, current_password, new_password):
    """Set `user_id`'s password, having first proved `current_password`.

    Returns the updated user document.

    The account is identified by an id the caller cannot supply: the route
    reads it from the JWT and passes it here. This function is given an
    id, and it is the route's job that no request body can influence which
    one -- the same rule 09 applied to the student attendance endpoints.
    """
    user = get_user_by_id(db, user_id)

    # Re-checked against the database rather than trusted from the token
    # claim. jwt_required only inspects the token, and 05 left a
    # deactivated account holding a valid access token for up to
    # JWT_ACCESS_TOKEN_EXPIRES -- this is the same re-check that
    # POST /api/auth/refresh performs, for exactly the same reason.
    if user is None or not user.get("is_active", False):
        raise InactiveAccountError("Authentication required")

    # verify_password, not a comparison and not a re-hash: one code path
    # decides whether a password is correct in this project, and it is the
    # one the login endpoint uses.
    if not verify_password(current_password, user["password_hash"]):
        raise IncorrectPasswordError("Current password is incorrect")

    # A no-op change is refused rather than written. Hashing the same
    # secret again would succeed, bump updated_at, and report a change
    # that did not happen.
    if verify_password(new_password, user["password_hash"]):
        raise ValidationError("New password must be different from the current password")

    try:
        return set_user_password(db, user["_id"], password=new_password)
    except NotFoundError as exc:
        # The account was deleted between the read above and this write.
        # Reported as "authenticate again" rather than as a 404: the
        # answer to "your account no longer exists" is not a
        # missing-resource page, and it keeps this endpoint to the four
        # status codes it documents.
        raise InactiveAccountError("Authentication required") from exc
