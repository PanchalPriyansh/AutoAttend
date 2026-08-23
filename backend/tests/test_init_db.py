"""Tests for backend/database/init_db.py (idempotent collection/index setup).

Spec contract under test (.claude/specs/02-database-setup.md, "Backend" +
"Definition of done"; the collection count rose to eight with
.claude/specs/06-face-enrollment.md and to ten with
.claude/specs/07-attendance-capture.md):
  - `init_database(db)` creates each of the ten collections declared in
    `schema.COLLECTIONS`, attaching its `$jsonSchema` validator, when the
    collection does not yet exist.
  - For a collection that already exists, `init_database` uses `collMod`
    to (re-)apply the validator instead of `create_collection`.
  - Every declared index is created via `create_index` on the right
    collection with the right keys/uniqueness/name.
  - Running `init_database` repeatedly does not raise and does not
    special-case re-runs incorrectly (actual dedup of collections/indexes
    at the storage level is MongoDB's own guarantee, not this function's;
    see the note on `test_repeated_calls_do_not_error_and_remain_consistent`).
  - Collections are created/updated *before* any index creation is
    attempted (per init_db.py's own documented ordering rationale --
    creating an index against a not-yet-existing collection would
    implicitly create it with no validator attached).

A narrow, hand-written fake standing in for a pymongo `Database`/
`Collection` is used throughout -- no live or mocked pymongo client is
required, matching this project's fully-mocked test convention.
"""

from database.init_db import init_database
from database.schema import ATTENDANCE_SESSIONS, COLLECTIONS


class FakeCollection:
    """Stands in for a pymongo `Collection` -- only `create_index` is used
    by `init_database`."""

    def __init__(self, name, call_log):
        self.name = name
        self._call_log = call_log
        self.created_indexes = []

    def create_index(self, keys, unique=False, name=None):
        entry = {"keys": keys, "unique": unique, "name": name}
        self.created_indexes.append(entry)
        self._call_log.append(("create_index", self.name, entry))
        return name


class FakeDb:
    """Stands in for a pymongo `Database`. Records every call it receives
    (with ordering, via a shared `call_log`) so tests can assert both
    *what* was called and *in what order*, without depending on how
    `init_database` is internally structured.
    """

    def __init__(self, existing_collections=None):
        self._existing = set(existing_collections or [])
        self.call_log = []
        self.create_collection_calls = []
        self.command_calls = []
        self._collections = {}

    def list_collection_names(self):
        return list(self._existing)

    def create_collection(self, name, validator=None, validationLevel=None):
        self.create_collection_calls.append(
            {"name": name, "validator": validator, "validationLevel": validationLevel}
        )
        self.call_log.append(("create_collection", name))
        self._existing.add(name)
        return self[name]

    def command(self, command_name, collection_name, **kwargs):
        self.command_calls.append(
            {"command": command_name, "name": collection_name, **kwargs}
        )
        self.call_log.append(("command", command_name, collection_name))
        return {"ok": 1}

    def __getitem__(self, name):
        if name not in self._collections:
            self._collections[name] = FakeCollection(name, self.call_log)
        return self._collections[name]


ALL_COLLECTION_NAMES = [spec["name"] for spec in COLLECTIONS]


class TestInitDatabaseOnEmptyDatabase:
    def test_creates_all_ten_collections_with_their_validators(self):
        fake_db = FakeDb(existing_collections=set())

        result = init_database(fake_db)

        created_names = {call["name"] for call in fake_db.create_collection_calls}
        assert created_names == set(ALL_COLLECTION_NAMES)
        assert len(fake_db.create_collection_calls) == 10
        assert result["collections"] == ALL_COLLECTION_NAMES

    def test_each_created_collection_receives_its_own_declared_validator(self):
        fake_db = FakeDb(existing_collections=set())

        init_database(fake_db)

        calls_by_name = {call["name"]: call for call in fake_db.create_collection_calls}
        for spec in COLLECTIONS:
            call = calls_by_name[spec["name"]]
            assert call["validator"] == spec["validator"]
            assert call["validationLevel"] == "strict"

    def test_no_collmod_calls_happen_when_nothing_already_exists(self):
        fake_db = FakeDb(existing_collections=set())

        init_database(fake_db)

        assert fake_db.command_calls == []


class TestInitDatabaseWhenAllCollectionsAlreadyExist:
    def test_uses_collmod_instead_of_create_collection_for_every_collection(self):
        fake_db = FakeDb(existing_collections=set(ALL_COLLECTION_NAMES))

        init_database(fake_db)

        assert fake_db.create_collection_calls == []
        assert len(fake_db.command_calls) == 10
        assert {call["name"] for call in fake_db.command_calls} == set(ALL_COLLECTION_NAMES)

    def test_collmod_calls_re_apply_the_correct_validator_with_strict_level(self):
        fake_db = FakeDb(existing_collections=set(ALL_COLLECTION_NAMES))

        init_database(fake_db)

        calls_by_name = {call["name"]: call for call in fake_db.command_calls}
        for spec in COLLECTIONS:
            call = calls_by_name[spec["name"]]
            assert call["command"] == "collMod"
            assert call["validator"] == spec["validator"]
            assert call["validationLevel"] == "strict"


class TestInitDatabaseIndexCreation:
    def test_every_declared_index_spec_produces_exactly_one_matching_create_index_call(self):
        fake_db = FakeDb(existing_collections=set())

        init_database(fake_db)

        for spec in COLLECTIONS:
            collection = fake_db[spec["name"]]
            for index_spec in spec["indexes"]:
                matches = [
                    created
                    for created in collection.created_indexes
                    if created["keys"] == index_spec["keys"]
                    and created["unique"] == index_spec["unique"]
                    and created["name"] == index_spec["name"]
                ]
                assert len(matches) == 1, (
                    f"expected exactly one create_index call for "
                    f"{spec['name']}.{index_spec['name']}, found {len(matches)}"
                )

    def test_total_create_index_call_count_matches_total_declared_indexes(self):
        fake_db = FakeDb(existing_collections=set())

        init_database(fake_db)

        expected_total = sum(len(spec["indexes"]) for spec in COLLECTIONS)
        actual_total = sum(
            len(fake_db[spec["name"]].created_indexes) for spec in COLLECTIONS
        )
        assert actual_total == expected_total


class TestInitDatabaseOrdering:
    def test_collection_setup_happens_before_any_index_creation(self):
        fake_db = FakeDb(existing_collections=set())

        init_database(fake_db)

        setup_indices = [
            position
            for position, entry in enumerate(fake_db.call_log)
            if entry[0] in ("create_collection", "command")
        ]
        index_indices = [
            position
            for position, entry in enumerate(fake_db.call_log)
            if entry[0] == "create_index"
        ]

        assert setup_indices, "expected at least one collection setup call"
        assert index_indices, "expected at least one index creation call"
        assert max(setup_indices) < min(index_indices), (
            "every create_collection/collMod call must happen before any "
            "create_index call, since indexing a not-yet-existing "
            "collection would implicitly create it without a validator"
        )


class TestInitDatabaseIdempotency:
    def test_repeated_calls_do_not_error_and_remain_consistent(self):
        # NOTE: FakeDb.create_collection mutates its own `_existing` set,
        # mirroring how a *second* call against the same real MongoDB
        # database would see those collections as already present. This
        # proves init_database's own code path doesn't special-case,
        # error on, or behave differently on a re-run (e.g. it doesn't
        # assume `create_collection` always succeeds, and unconditionally
        # calls create_index every time). It intentionally does NOT prove
        # that a real `create_index` call is a no-op for an
        # already-identical index -- that dedup behavior is MongoDB's own
        # server-side guarantee, exercised manually against a live cluster
        # per this feature's scope note, not something a fake can verify.
        fake_db = FakeDb(existing_collections=set())

        first_result = init_database(fake_db)
        second_result = init_database(fake_db)

        assert first_result["collections"] == ALL_COLLECTION_NAMES
        assert second_result["collections"] == ALL_COLLECTION_NAMES

        # First run: all 8 created. Second run: none created (they now
        # exist), all 8 updated via collMod instead.
        assert len(fake_db.create_collection_calls) == 10
        assert len(fake_db.command_calls) == 10

    def test_repeated_calls_keep_issuing_index_creation_for_every_declared_index(self):
        fake_db = FakeDb(existing_collections=set())
        expected_total = sum(len(spec["indexes"]) for spec in COLLECTIONS)

        init_database(fake_db)
        init_database(fake_db)

        actual_total = sum(
            len(fake_db[spec["name"]].created_indexes) for spec in COLLECTIONS
        )
        # Each of the two runs issues one create_index call per declared
        # index against our fake (a real create_index call the second time
        # around is a no-op server-side, but our code still legitimately
        # calls the driver method both times).
        assert actual_total == expected_total * 2


class TestInitDatabaseUpgradesAnExistingSevenEraDatabase:
    """Per .claude/specs/08-faculty-attendance-history.md: a database that
    already has `attendance_sessions` from 07-attendance-capture.md, full of
    sessions with no `updated_by`, must upgrade cleanly -- collMod is what
    applies 08's widened validator to a collection that already exists, and
    that validator must still describe those old documents as valid.

    A fake collMod call can't prove a real MongoDB accepts a document
    missing an optional property (that's MongoDB's own $jsonSchema
    behavior, exercised manually per this suite's usual live-cluster
    boundary), but it can prove init_database hands MongoDB the validator
    that makes that true, rather than one that was mutated or truncated
    somewhere on the way from schema.py.
    """

    def test_collmod_for_an_existing_attendance_sessions_collection_allows_updated_by_to_be_absent(
        self,
    ):
        fake_db = FakeDb(existing_collections={ATTENDANCE_SESSIONS})

        init_database(fake_db)

        collmod_call = next(
            call for call in fake_db.command_calls if call["name"] == ATTENDANCE_SESSIONS
        )
        json_schema = collmod_call["validator"]["$jsonSchema"]
        assert "updated_by" in json_schema["properties"]
        assert json_schema["properties"]["updated_by"]["bsonType"] == "objectId"
        assert "updated_by" not in json_schema["required"]

    def test_a_second_init_db_run_keeps_updated_by_optional(self):
        """The upgrade is not a one-time migration step -- every future
        `flask init-db` run must keep re-describing old sessions as valid,
        not just the first one after 08 ships.
        """
        fake_db = FakeDb(existing_collections={ATTENDANCE_SESSIONS})

        init_database(fake_db)
        init_database(fake_db)

        collmod_calls = [
            call for call in fake_db.command_calls if call["name"] == ATTENDANCE_SESSIONS
        ]
        assert len(collmod_calls) == 2
        for call in collmod_calls:
            json_schema = call["validator"]["$jsonSchema"]
            assert "updated_by" not in json_schema["required"]
