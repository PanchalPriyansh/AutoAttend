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

import re
from datetime import datetime, timezone

from auth.passwords import hash_password
from bson import ObjectId
from database.schema import USERS
from pymongo.errors import DuplicateKeyError


class _InsertOneResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


def _field_matches(value, condition):
    """A single query-field comparison, either plain equality or one of
    the small set of operators the test suite needs: `$in` (student-id
    lookup for a roster), `$ne` (06-face-enrollment.md's cross-student
    duplicate-face scan, "every *other* student's encodings"), and
    `$regex`/`$options` (the `q=` name/email search).
    """
    if isinstance(condition, dict):
        if "$in" in condition:
            return value in condition["$in"]
        if "$ne" in condition:
            return value != condition["$ne"]
        if "$regex" in condition:
            flags = re.IGNORECASE if condition.get("$options") == "i" else 0
            return bool(re.search(condition["$regex"], value or "", flags))
        return False
    return value == condition


def _matches(document, query):
    for key, condition in (query or {}).items():
        if key == "$or":
            if not any(_matches(document, sub) for sub in condition):
                return False
        elif key == "$and":
            if not all(_matches(document, sub) for sub in condition):
                return False
        elif not _field_matches(document.get(key), condition):
            return False
    return True


class FakeUsersCursor:
    """Stands in for the pymongo `Cursor` returned by `find(...)`; only
    `.sort(field, direction)` followed by iteration/`list(...)` is used by
    `users/service.py`.
    """

    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, field_name, direction=1):
        self._docs.sort(key=lambda d: (d.get(field_name) or "").lower(), reverse=(direction < 0))
        return self

    def __iter__(self):
        return iter(self._docs)

    def __len__(self):
        return len(self._docs)


class FakeUsersCollection:
    """Stands in for `db[USERS]`. Originally supported only what
    `auth/service.py` uses (`find_one`, `insert_one`); extended for
    `05-admin-user-management` (`users/service.py`, `users/assignments.py`)
    with `find`, `count_documents`, and `find_one_and_update`, plus the
    `$in`/`$regex`/`$or` query-operator support those need.
    """

    def __init__(self, users=None):
        self._users = list(users or [])

    def find_one(self, query):
        for user in self._users:
            if _matches(user, query):
                return user
        return None

    def find(self, query=None):
        return FakeUsersCursor(u for u in self._users if _matches(u, query or {}))

    def count_documents(self, query, limit=None):
        count = sum(1 for u in self._users if _matches(u, query))
        return min(count, limit) if limit else count

    def insert_one(self, document):
        if any(u.get("email") == document.get("email") for u in self._users):
            raise DuplicateKeyError("E11000 duplicate key error collection: users index: uniq_email")
        stored = dict(document)
        stored.setdefault("_id", ObjectId())
        self._users.append(stored)
        return _InsertOneResult(stored["_id"])

    def find_one_and_update(self, query, update, return_document=None):
        """Applies `update["$set"]` to the first matching document.

        Only `$set` is modelled -- nothing in `users/service.py` uses any
        other update operator. Enforces the `uniq_email` index the same
        way `insert_one` does, so a duplicate-email `PUT` raises
        `DuplicateKeyError` for the caller to translate into a 409.
        """
        set_fields = (update or {}).get("$set", {})
        for user in self._users:
            if not _matches(user, query):
                continue
            if "email" in set_fields:
                new_email = set_fields["email"]
                if any(
                    other is not user and other.get("email") == new_email
                    for other in self._users
                ):
                    raise DuplicateKeyError(
                        "E11000 duplicate key error collection: users index: uniq_email"
                    )
            user.update(set_fields)
            return dict(user)
        return None


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
