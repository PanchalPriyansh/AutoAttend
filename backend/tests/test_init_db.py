"""Tests for backend/database/init_db.py (idempotent collection/index setup).

Spec contract under test (.claude/specs/02-database-setup.md, "Backend" +
"Definition of done"; the collection count rose to eight with
.claude/specs/06-face-enrollment.md, to ten with
.claude/specs/07-attendance-capture.md, to eleven with
.claude/specs/10-low-attendance-notifications.md, and to twelve with
.claude/specs/25-forgot-password.md):
  - `init_database(db)` creates each of the twelve collections declared in
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
from database.schema import (
    ATTENDANCE_NOTIFICATIONS,
    ATTENDANCE_RECORDS,
    ATTENDANCE_SESSIONS,
    COLLECTIONS,
    INSTITUTES,
)


class FakeCollection:
    """Stands in for a pymongo `Collection` -- `index_information`,
    `create_index`, `drop_index` and `aggregate` are what `init_database`
    uses.

    `create_index` registers what it built into the same dict
    `index_information` reads back, so a second run sees what the first
    one did. That is what lets the idempotency tests below assert the
    real contract (a re-run changes nothing) rather than the weaker one
    they could assert while the fake forgot every index immediately.
    """

    def __init__(self, name, call_log, index_information=None, duplicates=None):
        self.name = name
        self._call_log = call_log
        self.created_indexes = []
        self.dropped_indexes = []
        self._index_information = {
            index_name: dict(info) for index_name, info in (index_information or {}).items()
        }
        # Keyed by the sorted tuple of indexed field names, so one test can
        # make a single index's duplicate pre-check come back dirty while
        # every other index in the same collection stays clean.
        self._duplicates = dict(duplicates or {})

    def index_information(self):
        self._call_log.append(("index_information", self.name, None))
        return {index_name: dict(info) for index_name, info in self._index_information.items()}

    def create_index(self, keys, unique=False, name=None):
        entry = {"keys": keys, "unique": unique, "name": name}
        self.created_indexes.append(entry)
        self._call_log.append(("create_index", self.name, entry))

        info = {"v": 2, "key": list(keys)}
        if unique:
            # Mirroring the real driver, which reports `unique` only when
            # it is true rather than as an explicit False.
            info["unique"] = True
        self._index_information[name] = info

        return name

    def drop_index(self, name):
        self.dropped_indexes.append(name)
        self._call_log.append(("drop_index", self.name, name))
        self._index_information.pop(name, None)

    def aggregate(self, pipeline):
        self._call_log.append(("aggregate", self.name, pipeline))
        fields = tuple(sorted(pipeline[0]["$group"]["_id"]))
        return [
            {"_id": key_values, "count": 2}
            for key_values in self._duplicates.get(fields, [])
        ]


class FakeDb:
    """Stands in for a pymongo `Database`. Records every call it receives
    (with ordering, via a shared `call_log`) so tests can assert both
    *what* was called and *in what order*, without depending on how
    `init_database` is internally structured.
    """

    def __init__(self, existing_collections=None, existing_indexes=None, duplicates=None):
        self._existing = set(existing_collections or [])
        self.call_log = []
        self.create_collection_calls = []
        self.command_calls = []
        self._collections = {}
        # What a drifted database already has, per collection name, in the
        # shape `Collection.index_information()` returns.
        self._existing_indexes = existing_indexes or {}
        self._duplicates = duplicates or {}

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
            self._collections[name] = FakeCollection(
                name,
                self.call_log,
                index_information=self._existing_indexes.get(name),
                duplicates=self._duplicates.get(name),
            )
        return self._collections[name]


ALL_COLLECTION_NAMES = [spec["name"] for spec in COLLECTIONS]


class TestInitDatabaseOnEmptyDatabase:
    def test_creates_all_twelve_collections_with_their_validators(self):
        fake_db = FakeDb(existing_collections=set())

        result = init_database(fake_db)

        created_names = {call["name"] for call in fake_db.create_collection_calls}
        assert created_names == set(ALL_COLLECTION_NAMES)
        assert len(fake_db.create_collection_calls) == 12
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
        assert len(fake_db.command_calls) == 12
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
        # assume `create_collection` always succeeds). Since
        # 20-init-db-index-reconcile the index half no longer leans on
        # MongoDB to no-op a duplicate `create_index`: the plan is built
        # from what `index_information()` reports, so a re-run issues
        # nothing at all -- see `test_a_second_run_creates_nothing_and_
        # drops_nothing`.
        fake_db = FakeDb(existing_collections=set())

        first_result = init_database(fake_db)
        second_result = init_database(fake_db)

        assert first_result["collections"] == ALL_COLLECTION_NAMES
        assert second_result["collections"] == ALL_COLLECTION_NAMES

        # First run: all 8 created. Second run: none created (they now
        # exist), all 8 updated via collMod instead.
        assert len(fake_db.create_collection_calls) == 12
        assert len(fake_db.command_calls) == 12

    def test_a_second_run_creates_nothing_and_drops_nothing(self):
        """Per .claude/specs/20-init-db-index-reconcile.md: a re-run
        reports every index as unchanged.

        This assertion is the inverse of the one it replaces, and
        deliberately so. `init_database` used to call `create_index`
        unconditionally and lean on MongoDB to no-op the duplicates --
        which meant the fake could only ever assert "it calls the driver
        again". Now that the plan is built from `index_information()`,
        the second run recognises its own work and issues nothing, which
        is a contract a fake *can* prove.
        """
        fake_db = FakeDb(existing_collections=set())
        expected_total = sum(len(spec["indexes"]) for spec in COLLECTIONS)

        init_database(fake_db)
        init_database(fake_db)

        actual_total = sum(
            len(fake_db[spec["name"]].created_indexes) for spec in COLLECTIONS
        )
        dropped_total = sum(
            len(fake_db[spec["name"]].dropped_indexes) for spec in COLLECTIONS
        )

        assert actual_total == expected_total
        assert dropped_total == 0

    def test_a_second_run_reports_every_declared_index_as_unchanged(self):
        fake_db = FakeDb(existing_collections=set())
        expected_total = sum(len(spec["indexes"]) for spec in COLLECTIONS)

        init_database(fake_db)
        second_result = init_database(fake_db)

        indexes = second_result["indexes"]
        assert len(indexes["unchanged"]) == expected_total
        assert indexes["created"] == []
        assert indexes["recreated"] == []
        assert indexes["blocked"] == []


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


# ---------------------------------------------------------------------------
# .claude/specs/20-init-db-index-reconcile.md
# ---------------------------------------------------------------------------


class TestInitDatabaseReconcilesTheDriftedAttendanceSessionsIndex:
    """Per spec's "Definition of done": against a fake database seeded
    with attendance_sessions.uniq_class_id_date keyed
    {class_id: 1, date: -1} (the real Atlas drift), init_database drops
    that index and recreates it with {class_id: 1, date: 1} -- and this
    is the regression the whole spec exists to close: every later
    collection in COLLECTIONS, including
    attendance_notifications.idx_student_id_class_id_sent_at, still gets
    its index created rather than being abandoned mid-pass.
    """

    def _drifted_db(self):
        return FakeDb(
            existing_collections=set(ALL_COLLECTION_NAMES),
            existing_indexes={
                ATTENDANCE_SESSIONS: {
                    "uniq_class_id_date": {
                        "v": 2,
                        "key": [("class_id", 1), ("date", -1)],
                        "unique": True,
                    }
                }
            },
        )

    def test_the_drifted_index_is_dropped(self):
        fake_db = self._drifted_db()

        init_database(fake_db)

        assert fake_db[ATTENDANCE_SESSIONS].dropped_indexes == ["uniq_class_id_date"]

    def test_the_index_is_recreated_with_the_declared_ascending_date(self):
        fake_db = self._drifted_db()

        init_database(fake_db)

        rebuilt = [
            created
            for created in fake_db[ATTENDANCE_SESSIONS].created_indexes
            if created["name"] == "uniq_class_id_date"
        ]
        assert len(rebuilt) == 1
        assert rebuilt[0]["keys"] == [("class_id", 1), ("date", 1)]
        assert rebuilt[0]["unique"] is True

    def test_drop_happens_before_create_for_the_rebuilt_index(self):
        fake_db = self._drifted_db()

        init_database(fake_db)

        names = [
            entry[0]
            for entry in fake_db.call_log
            if entry[1] == ATTENDANCE_SESSIONS and entry[0] in ("drop_index", "create_index")
        ]
        assert names.index("drop_index") < names.index("create_index")

    def test_every_later_collection_still_gets_its_indexes_created(self):
        """The regression: before this feature, the IndexKeySpecsConflict
        raised while rebuilding attendance_sessions aborted the whole
        run, so attendance_records and attendance_notifications -- both
        declared after attendance_sessions in COLLECTIONS -- never
        received their indexes at all.
        """
        fake_db = self._drifted_db()

        init_database(fake_db)

        notifications_created = {
            created["name"]
            for created in fake_db[ATTENDANCE_NOTIFICATIONS].created_indexes
        }
        assert "idx_student_id_class_id_sent_at" in notifications_created

        records_created = {
            created["name"] for created in fake_db[ATTENDANCE_RECORDS].created_indexes
        }
        assert "uniq_session_id_student_id" in records_created
        assert "idx_student_id_class_id" in records_created

    def test_the_recreated_report_entry_names_both_sides_of_the_conflict(self):
        fake_db = self._drifted_db()

        result = init_database(fake_db)

        recreated = [
            record
            for record in result["indexes"]["recreated"]
            if record["collection"] == ATTENDANCE_SESSIONS
            and record["name"] == "uniq_class_id_date"
        ]
        assert len(recreated) == 1
        reason = recreated[0]["reason"]
        assert "class_id: 1, date: -1" in reason
        assert "class_id: 1, date: 1" in reason


class TestInitDatabaseUniqueIndexDuplicatePrecheck:
    """Per spec's "Rules for implementation": a unique index is never
    dropped, and never first created, over a collection that already
    holds duplicate values for its declared key fields -- that failure
    is reported and the run is not silently allowed to leave the
    collection unconstrained.
    """

    def test_a_rebuild_blocked_by_duplicates_is_not_dropped(self):
        fake_db = FakeDb(
            existing_collections=set(ALL_COLLECTION_NAMES),
            existing_indexes={
                ATTENDANCE_SESSIONS: {
                    "uniq_class_id_date": {
                        "v": 2,
                        "key": [("class_id", 1), ("date", -1)],
                        "unique": True,
                    }
                }
            },
            duplicates={
                ATTENDANCE_SESSIONS: {
                    ("class_id", "date"): [
                        {"class_id": "64abc0000000000000000001", "date": "2024-01-15"}
                    ]
                }
            },
        )

        init_database(fake_db)

        assert fake_db[ATTENDANCE_SESSIONS].dropped_indexes == []
        assert fake_db[ATTENDANCE_SESSIONS].created_indexes == []

    def test_the_blocked_report_names_the_collection_index_and_duplicate_values(self):
        fake_db = FakeDb(
            existing_collections=set(ALL_COLLECTION_NAMES),
            existing_indexes={
                ATTENDANCE_SESSIONS: {
                    "uniq_class_id_date": {
                        "v": 2,
                        "key": [("class_id", 1), ("date", -1)],
                        "unique": True,
                    }
                }
            },
            duplicates={
                ATTENDANCE_SESSIONS: {
                    ("class_id", "date"): [
                        {"class_id": "64abc0000000000000000001", "date": "2024-01-15"}
                    ]
                }
            },
        )

        result = init_database(fake_db)

        blocked = result["indexes"]["blocked"]
        assert len(blocked) == 1
        assert blocked[0]["collection"] == ATTENDANCE_SESSIONS
        assert blocked[0]["name"] == "uniq_class_id_date"
        assert blocked[0]["duplicates"] == [
            {"class_id": "64abc0000000000000000001", "date": "2024-01-15"}
        ]

    def test_a_block_in_one_collection_does_not_stop_indexing_on_the_others(self):
        fake_db = FakeDb(
            existing_collections=set(ALL_COLLECTION_NAMES),
            existing_indexes={
                ATTENDANCE_SESSIONS: {
                    "uniq_class_id_date": {
                        "v": 2,
                        "key": [("class_id", 1), ("date", -1)],
                        "unique": True,
                    }
                }
            },
            duplicates={
                ATTENDANCE_SESSIONS: {
                    ("class_id", "date"): [
                        {"class_id": "64abc0000000000000000001", "date": "2024-01-15"}
                    ]
                }
            },
        )

        init_database(fake_db)

        # attendance_notifications is declared after attendance_sessions --
        # its index must still be created even though the earlier
        # collection was blocked, not merely dropped.
        notifications_created = {
            created["name"]
            for created in fake_db[ATTENDANCE_NOTIFICATIONS].created_indexes
        }
        assert "idx_student_id_class_id_sent_at" in notifications_created

    def test_the_precheck_also_applies_to_a_first_creation_of_a_unique_index(self):
        """Not only a rebuild: creating a unique index for the first time
        over a collection that already holds duplicates must be blocked
        and reported rather than raising mid-pass, since that crash is
        the exact bug this spec exists to remove.
        """
        fake_db = FakeDb(
            existing_collections={INSTITUTES},
            existing_indexes={},
            duplicates={INSTITUTES: {("code",): [{"code": "ENG"}]}},
        )

        result = init_database(fake_db)

        assert fake_db[INSTITUTES].created_indexes == []
        blocked = result["indexes"]["blocked"]
        assert len(blocked) == 1
        assert blocked[0]["collection"] == INSTITUTES
        assert blocked[0]["name"] == "uniq_code"
        assert blocked[0]["duplicates"] == [{"code": "ENG"}]

    def test_the_precheck_is_skipped_for_a_collection_this_run_just_created(self):
        """Skipped only for a collection created moments ago in this same
        run, which is empty by construction -- there is nothing to check.
        """
        fake_db = FakeDb(
            existing_collections=set(),
            duplicates={INSTITUTES: {("code",): [{"code": "ENG"}]}},
        )

        result = init_database(fake_db)

        aggregate_calls = [
            entry for entry in fake_db.call_log if entry[0] == "aggregate" and entry[1] == INSTITUTES
        ]
        assert aggregate_calls == []
        assert result["indexes"]["blocked"] == []
        created_names = {
            created["name"] for created in fake_db[INSTITUTES].created_indexes
        }
        assert "uniq_code" in created_names

    def test_the_precheck_runs_even_on_a_dry_run(self):
        """A preview that could not tell the operator the real run will
        be blocked would not be much of a preview."""
        fake_db = FakeDb(
            existing_collections=set(ALL_COLLECTION_NAMES),
            existing_indexes={
                ATTENDANCE_SESSIONS: {
                    "uniq_class_id_date": {
                        "v": 2,
                        "key": [("class_id", 1), ("date", -1)],
                        "unique": True,
                    }
                }
            },
            duplicates={
                ATTENDANCE_SESSIONS: {
                    ("class_id", "date"): [
                        {"class_id": "64abc0000000000000000001", "date": "2024-01-15"}
                    ]
                }
            },
        )

        result = init_database(fake_db, dry_run=True)

        blocked = result["indexes"]["blocked"]
        assert len(blocked) == 1
        assert blocked[0]["collection"] == ATTENDANCE_SESSIONS


class TestInitDatabaseDryRun:
    def test_a_dry_run_against_an_empty_database_issues_no_write_calls(self):
        fake_db = FakeDb(existing_collections=set())

        init_database(fake_db, dry_run=True)

        assert fake_db.create_collection_calls == []
        assert fake_db.command_calls == []
        for spec in COLLECTIONS:
            collection = fake_db[spec["name"]]
            assert collection.created_indexes == []
            assert collection.dropped_indexes == []

    def test_a_dry_run_against_a_drifted_database_issues_no_write_calls(self):
        fake_db = FakeDb(
            existing_collections=set(ALL_COLLECTION_NAMES),
            existing_indexes={
                ATTENDANCE_SESSIONS: {
                    "uniq_class_id_date": {
                        "v": 2,
                        "key": [("class_id", 1), ("date", -1)],
                        "unique": True,
                    }
                }
            },
        )

        init_database(fake_db, dry_run=True)

        assert fake_db.create_collection_calls == []
        assert fake_db.command_calls == []
        assert fake_db[ATTENDANCE_SESSIONS].dropped_indexes == []
        assert fake_db[ATTENDANCE_SESSIONS].created_indexes == []

    def test_a_dry_run_returns_the_same_plan_a_real_run_would_apply(self):
        def _seeded_db():
            return FakeDb(
                existing_collections=set(ALL_COLLECTION_NAMES),
                existing_indexes={
                    ATTENDANCE_SESSIONS: {
                        "uniq_class_id_date": {
                            "v": 2,
                            "key": [("class_id", 1), ("date", -1)],
                            "unique": True,
                        }
                    }
                },
            )

        dry_run_result = init_database(_seeded_db(), dry_run=True)
        real_run_result = init_database(_seeded_db(), dry_run=False)

        def _plan_shape(result):
            return {
                action: sorted(
                    (record["collection"], record["name"], record["reason"])
                    for record in result["indexes"][action]
                )
                for action in ("created", "recreated", "unchanged", "blocked")
            }

        assert _plan_shape(dry_run_result) == _plan_shape(real_run_result)

    def test_dry_run_true_is_carried_on_the_returned_result(self):
        fake_db = FakeDb(existing_collections=set())

        result = init_database(fake_db, dry_run=True)

        assert result["dry_run"] is True


class TestInitDatabaseIdempotencyAfterReconciliation:
    def test_a_second_run_after_fixing_drift_reports_the_fixed_index_unchanged(self):
        fake_db = FakeDb(
            existing_collections=set(ALL_COLLECTION_NAMES),
            existing_indexes={
                ATTENDANCE_SESSIONS: {
                    "uniq_class_id_date": {
                        "v": 2,
                        "key": [("class_id", 1), ("date", -1)],
                        "unique": True,
                    }
                }
            },
        )

        init_database(fake_db)
        second_result = init_database(fake_db)

        assert fake_db[ATTENDANCE_SESSIONS].dropped_indexes == ["uniq_class_id_date"]

        second_names = {
            record["name"] for record in second_result["indexes"]["unchanged"]
            if record["collection"] == ATTENDANCE_SESSIONS
        }
        assert "uniq_class_id_date" in second_names
        assert second_result["indexes"]["recreated"] == []
        assert second_result["indexes"]["created"] == []
