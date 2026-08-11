"""Authentication business logic: user lookup, credential verification,
and user creation against the `users` collection.

No Flask request/response objects appear in any signature here -- HTTP
concerns (status codes, cookies, JSON bodies) belong to routes/auth.py.
"""

from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.errors import DuplicateKeyError

from auth.passwords import hash_password, verify_password
from database.schema import USERS

# Precomputed so the "unknown email" path in authenticate_user() burns
# roughly the same time as a real password check, narrowing (not
# eliminating) the timing gap that would otherwise hint whether an email
# is registered.
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-password-used-only-for-timing")


class DuplicateEmailError(Exception):
    """Raised when creating a user with an email that already exists."""


def normalize_email(email):
    return email.strip().lower()


def authenticate_user(db, email, password):
    """Return the user document on success, else None.

    Unknown email, wrong password, and a deactivated (`is_active: False`)
    account are all rejected identically so a caller cannot distinguish
    them -- this prevents account enumeration via the login endpoint.
    """
    user = db[USERS].find_one({"email": normalize_email(email)})

    if user is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None

    if not verify_password(password, user["password_hash"]):
        return None

    if not user.get("is_active", False):
        return None

    return user


def get_user_by_id(db, user_id):
    try:
        object_id = ObjectId(user_id)
    except (InvalidId, TypeError):
        return None
    return db[USERS].find_one({"_id": object_id})


def create_user(db, name, email, password, role, institute_id=None):
    normalized_email = normalize_email(email)
    now = datetime.now(timezone.utc)

    document = {
        "name": name,
        "email": normalized_email,
        "password_hash": hash_password(password),
        "role": role,
        "institute_id": ObjectId(institute_id) if institute_id else None,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }

    try:
        result = db[USERS].insert_one(document)
    except DuplicateKeyError as exc:
        raise DuplicateEmailError(f"A user with email '{normalized_email}' already exists") from exc

    document["_id"] = result.inserted_id
    return document


def to_safe_profile(user):
    return {
        "id": str(user["_id"]),
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "institute_id": str(user["institute_id"]) if user.get("institute_id") else None,
    }
