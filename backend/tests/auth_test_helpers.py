"""Shared, in-memory fakes for authentication tests.

Provides a minimal stand-in for the `users` MongoDB collection so
`auth/service.py` and `routes/auth.py` can be exercised without a live
MongoDB connection, following the same "fake the pymongo driver at the
point of use" pattern already used in test_database.py and test_health.py.
No real credentials or biometric data are involved -- every password used
by tests importing this module is an obviously-fake, hardcoded test value.

Not a test module itself (no `test_` prefix), so pytest does not collect
it; it is imported directly by the auth test modules that need it.
"""

from datetime import datetime, timezone

from auth.passwords import hash_password
from bson import ObjectId
from database.schema import USERS
from pymongo.errors import DuplicateKeyError


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeUsersCollection:
    """Stands in for `db[USERS]`; supports only what auth/service.py uses:
    `find_one({"email": ...})`, `find_one({"_id": ...})`, and `insert_one`.
    """

    def __init__(self, users=None):
        self._users = list(users or [])

    def find_one(self, query):
        for user in self._users:
            if "_id" in query and user.get("_id") == query["_id"]:
                return user
            if "email" in query and user.get("email") == query["email"]:
                return user
        return None

    def insert_one(self, document):
        if any(u.get("email") == document.get("email") for u in self._users):
            raise DuplicateKeyError("E11000 duplicate key error collection: users index: uniq_email")
        stored = dict(document)
        stored.setdefault("_id", ObjectId())
        self._users.append(stored)
        return _InsertOneResult(stored["_id"])


def make_fake_db(users=None):
    """A minimal fake `db`: auth code only ever does `db[USERS]`, so a
    plain dict keyed by the real collection-name constant is sufficient.
    """
    return {USERS: FakeUsersCollection(users)}


def make_user(
    *,
    name="Test User",
    email="user@example.test",
    password="a-fake-test-password",
    role="student",
    is_active=True,
    institute_id=None,
):
    """Builds a raw `users` document as it would be stored in MongoDB.

    `password` is hashed here (never stored in plaintext), matching the
    schema.py note that dates must be real BSON datetimes.
    """
    now = datetime.now(timezone.utc)
    return {
        "_id": ObjectId(),
        "name": name,
        "email": email,
        "password_hash": hash_password(password),
        "role": role,
        "institute_id": institute_id,
        "is_active": is_active,
        "created_at": now,
        "updated_at": now,
    }
