"""Business logic for admin-managed user accounts.

No Flask request/response objects appear in any signature here -- HTTP
concerns belong to routes/users.py, which maps the domain exceptions from
common/errors.py onto status codes.

Account creation deliberately delegates to auth.service.create_user
rather than re-implementing email normalisation, hashing, and insertion:
accounts created here must be byte-for-byte the same shape as the one
`flask create-admin` bootstraps, or the login path would treat them
differently.

Known limitation: resetting a password does not invalidate an access
token already issued to that user, which stays valid for up to
JWT_ACCESS_TOKEN_EXPIRES (15 minutes). Deactivation is bounded the same
way, since POST /api/auth/refresh re-checks `is_active` before minting a
new access token. Closing that window needs a token blocklist or a
token-version claim, which is a change of its own and deliberately out of
scope here.
"""

import re
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from auth.passwords import hash_password
from auth.service import DuplicateEmailError, create_user, normalize_email
from common.errors import ConflictError, DuplicateError, NotFoundError
from database.schema import INSTITUTES, USERS


def _require_institute(db, institute_id):
    if institute_id is None:
        return
    if db[INSTITUTES].count_documents({"_id": institute_id}, limit=1) == 0:
        raise NotFoundError("Institute not found")


def _now():
    return datetime.now(timezone.utc)


def list_users(db, *, role=None, institute_id=None, q=None):
    query = {}

    if role is not None:
        query["role"] = role
    if institute_id is not None:
        query["institute_id"] = institute_id
    if q:
        # Escaped: an unescaped search term would let a caller inject
        # regex syntax -- at best matching far more than they typed, at
        # worst pinning a CPU with a catastrophically backtracking pattern.
        pattern = {"$regex": re.escape(q), "$options": "i"}
        query["$or"] = [{"name": pattern}, {"email": pattern}]

    return list(db[USERS].find(query).sort("name", 1))


def get_user(db, user_id):
    user = db[USERS].find_one({"_id": user_id})
    if user is None:
        raise NotFoundError("User not found")
    return user


def create_managed_user(db, *, name, email, password, role, institute_id=None):
    # Checked before the insert so a bad institute id cannot leave a
    # usable account behind after the request has already failed.
    _require_institute(db, institute_id)

    try:
        return create_user(
            db,
            name=name,
            email=email,
            password=password,
            role=role,
            institute_id=institute_id,
        )
    except DuplicateEmailError as exc:
        raise DuplicateError(str(exc)) from exc


def update_user(db, user_id, *, name, email, institute_id=None):
    """Updates a user's own profile fields only.

    `role` is never part of the `$set`, so it cannot be changed by this
    endpoint no matter what the request body contains -- immutability is
    structural rather than a guard that could be edited away.
    """
    _require_institute(db, institute_id)

    try:
        user = db[USERS].find_one_and_update(
            {"_id": user_id},
            {
                "$set": {
                    "name": name,
                    "email": normalize_email(email),
                    "institute_id": institute_id,
                    "updated_at": _now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise DuplicateError("A user with this email already exists") from exc

    if user is None:
        raise NotFoundError("User not found")

    return user


def _require_status_change_is_safe(db, user, *, acting_user_id):
    """Guards that stop an admin locking everyone, including themselves,
    out of the system. Enforced here rather than in the UI, which an
    authenticated admin can bypass by calling the API directly.
    """
    if user["_id"] == acting_user_id:
        raise ConflictError("You cannot deactivate your own account")

    if user.get("role") != "admin" or not user.get("is_active", False):
        return

    # limit=2 because the only question is "is there more than one?".
    # Known limitation: this count-then-write is not atomic. Two concurrent
    # deactivation requests against two different admins could both read a
    # count of 2 before either write lands, and both would then succeed,
    # leaving zero active admins. Accepted for now because this is an
    # admin-only, low-concurrency screen (mirroring the same accepted
    # trade-off already noted for delete_item's child check in
    # academic/service.py); a transaction would need a replica set.
    # Revisit if concurrent admin usage ever becomes realistic.
    active_admins = db[USERS].count_documents(
        {"role": "admin", "is_active": True}, limit=2
    )
    if active_admins <= 1:
        raise ConflictError("Cannot deactivate the last active admin")


def set_user_status(db, user_id, *, is_active, acting_user_id):
    user = get_user(db, user_id)

    if not is_active:
        _require_status_change_is_safe(db, user, acting_user_id=acting_user_id)

    return db[USERS].find_one_and_update(
        {"_id": user_id},
        {"$set": {"is_active": is_active, "updated_at": _now()}},
        return_document=ReturnDocument.AFTER,
    )


def set_user_password(db, user_id, *, password):
    user = db[USERS].find_one_and_update(
        {"_id": user_id},
        {"$set": {"password_hash": hash_password(password), "updated_at": _now()}},
        return_document=ReturnDocument.AFTER,
    )

    if user is None:
        raise NotFoundError("User not found")

    return user
