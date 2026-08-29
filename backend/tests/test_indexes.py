"""Tests for backend/database/indexes.py (the pure index-reconciliation
planner).

Spec contract under test (.claude/specs/20-init-db-index-reconcile.md,
"Rules for implementation" + "Definition of done"):
  - `plan_indexes(declared_specs, existing_info)` returns one entry per
    *declared* index, with an action of "create" (absent from the live
    database), "keep" (already matches, including key order/direction),
    or "recreate" (a conflicting index exists and must be dropped first).
  - A `recreate` is returned for each of: a differing key direction, a
    differing key order, a differing field set, a differing `unique`, and
    a live index carrying an option the declaration does not
    (`partialFilterExpression`, `collation`, `expireAfterSeconds`,
    `sparse`).
  - A `recreate` is returned naming the *existing* index when a live
    index holds the declared key pattern under a different name.
  - No action is ever returned for `_id_` or any other undeclared index,
    and no undeclared index is ever named in a `drop` list.
  - Every `recreate` entry carries a human-readable reason naming both
    the live definition and the declared one.
  - `background` and `ns` are inert and must never, by themselves,
    trigger a `recreate`.

`plan_indexes` is pure data in, pure data out -- no pymongo import, no
Flask import, no I/O -- so every fixture below is a hand-built dict in
the same shape `Collection.index_information()` returns, per this
module's own docstring and the spec's rationale for keeping it separate
from `init_db.py`.
"""

import database.indexes as indexes_module
from database.indexes import ALLOWED_INFO_KEYS, format_keys, plan_indexes


def _declared(name, keys, unique=False):
    return {"keys": keys, "unique": unique, "name": name}


class TestPlanIndexesImportsNoPymongoOrFlask:
    def test_the_module_does_not_import_pymongo_or_flask(self):
        # A simple, explicit form of "imports neither pymongo nor Flask":
        # the module's own globals must not contain either package's name
        # bound to anything, and the source text never mentions them.
        with open(indexes_module.__file__, encoding="utf-8") as source_file:
            source = source_file.read()
        assert "import pymongo" not in source
        assert "from pymongo" not in source
        assert "import flask" not in source
        assert "from flask" not in source


class TestPlanIndexesCreate:
    def test_a_declared_index_absent_from_index_information_is_create(self):
        declared = [_declared("uniq_email", [("email", 1)], unique=True)]

        plan = plan_indexes(declared, {})

        assert len(plan) == 1
        assert plan[0]["name"] == "uniq_email"
        assert plan[0]["action"] == "create"
        assert plan[0]["drop"] == []
        assert plan[0]["reason"] is None

    def test_an_empty_index_information_produces_create_for_every_declared_index(self):
        declared = [
            _declared("uniq_email", [("email", 1)], unique=True),
            _declared("idx_role", [("role", 1)]),
        ]

        plan = plan_indexes(declared, {})

        assert [entry["action"] for entry in plan] == ["create", "create"]


class TestPlanIndexesKeep:
    def test_an_exact_match_is_kept(self):
        declared = [_declared("uniq_code", [("code", 1)], unique=True)]
        existing = {"uniq_code": {"v": 2, "key": [("code", 1)], "unique": True}}

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "keep"
        assert plan[0]["drop"] == []
        assert plan[0]["reason"] is None

    def test_key_order_and_direction_must_both_match_to_be_kept(self):
        declared = [
            _declared(
                "uniq_class_id_date", [("class_id", 1), ("date", 1)], unique=True
            )
        ]
        existing = {
            "uniq_class_id_date": {
                "v": 2,
                "key": [("class_id", 1), ("date", 1)],
                "unique": True,
            }
        }

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "keep"

    def test_a_non_unique_declared_index_matches_a_live_index_with_no_unique_key(self):
        # index_information() omits "unique" entirely for a non-unique
        # index rather than reporting it as an explicit False.
        declared = [_declared("idx_role", [("role", 1)], unique=False)]
        existing = {"idx_role": {"v": 2, "key": [("role", 1)]}}

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "keep"


class TestPlanIndexesRecreateSameNameDifferentDefinition:
    def test_differing_key_direction_is_recreated(self):
        """The live incident this spec exists for: attendance_sessions'
        uniq_class_id_date was keyed {class_id: 1, date: -1} while
        schema.py declares {class_id: 1, date: 1}."""
        declared = [
            _declared(
                "uniq_class_id_date", [("class_id", 1), ("date", 1)], unique=True
            )
        ]
        existing = {
            "uniq_class_id_date": {
                "v": 2,
                "key": [("class_id", 1), ("date", -1)],
                "unique": True,
            }
        }

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "recreate"
        assert plan[0]["drop"] == ["uniq_class_id_date"]

    def test_differing_key_order_is_recreated(self):
        declared = [_declared("uniq_pair", [("a", 1), ("b", 1)], unique=True)]
        existing = {
            "uniq_pair": {"v": 2, "key": [("b", 1), ("a", 1)], "unique": True}
        }

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "recreate"
        assert plan[0]["drop"] == ["uniq_pair"]

    def test_differing_field_set_is_recreated(self):
        declared = [_declared("idx_a", [("a", 1)])]
        existing = {"idx_a": {"v": 2, "key": [("a", 1), ("b", 1)]}}

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "recreate"
        assert plan[0]["drop"] == ["idx_a"]

    def test_differing_unique_is_recreated(self):
        declared = [_declared("idx_student_id", [("student_id", 1)], unique=True)]
        existing = {"idx_student_id": {"v": 2, "key": [("student_id", 1)]}}

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "recreate"
        assert plan[0]["drop"] == ["idx_student_id"]

    def test_declared_unique_but_live_non_unique_is_recreated_the_other_direction(self):
        declared = [_declared("idx_student_id", [("student_id", 1)], unique=False)]
        existing = {
            "idx_student_id": {"v": 2, "key": [("student_id", 1)], "unique": True}
        }

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "recreate"
        assert plan[0]["drop"] == ["idx_student_id"]

    def test_extra_option_not_in_the_declaration_is_recreated(self):
        # Parametrized by hand (not pytest.mark.parametrize) so each
        # option is a clearly-named case in output, and each is checked
        # against a bare matching declaration -- the only difference is
        # the presence of the option itself.
        extra_options = {
            "partialFilterExpression": {"is_active": True},
            "collation": {"locale": "en", "strength": 2},
            "expireAfterSeconds": 3600,
            "sparse": True,
        }

        for option_name, option_value in extra_options.items():
            declared = [_declared("idx_thing", [("field", 1)])]
            existing = {
                "idx_thing": {
                    "v": 2,
                    "key": [("field", 1)],
                    option_name: option_value,
                }
            }

            plan = plan_indexes(declared, existing)

            assert plan[0]["action"] == "recreate", (
                f"expected a recreate when the live index carries "
                f"{option_name!r}, which the declaration does not"
            )
            assert plan[0]["drop"] == ["idx_thing"]


class TestPlanIndexesRecreateDifferentNameSameKeyPattern:
    def test_a_live_index_with_the_declared_key_pattern_under_another_name_is_recreated(
        self,
    ):
        declared = [_declared("uniq_email", [("email", 1)], unique=True)]
        existing = {"email_1": {"v": 2, "key": [("email", 1)], "unique": True}}

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "recreate"
        assert plan[0]["drop"] == ["email_1"]

    def test_the_reason_names_the_conflicting_existing_index_by_name(self):
        declared = [_declared("uniq_email", [("email", 1)], unique=True)]
        existing = {"email_1": {"v": 2, "key": [("email", 1)], "unique": True}}

        plan = plan_indexes(declared, existing)

        assert "email_1" in plan[0]["reason"]

    def test_both_a_same_name_conflict_and_a_different_name_conflict_are_both_dropped(self):
        # The declared name exists under a wrong definition *and* a third
        # index already holds the declared key pattern -- both have to go
        # before the declared index can be built.
        declared = [
            _declared("uniq_pair", [("a", 1), ("b", 1)], unique=True)
        ]
        existing = {
            "uniq_pair": {"v": 2, "key": [("a", 1), ("b", -1)], "unique": True},
            "a_1_b_1": {"v": 2, "key": [("a", 1), ("b", 1)], "unique": True},
        }

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "recreate"
        assert set(plan[0]["drop"]) == {"uniq_pair", "a_1_b_1"}


class TestPlanIndexesIgnoresUndeclaredIndexes:
    def test_id_index_produces_no_plan_entry_and_is_never_named_in_a_drop_list(self):
        declared = [_declared("uniq_email", [("email", 1)], unique=True)]
        existing = {
            "_id_": {"v": 2, "key": [("_id", 1)]},
            "uniq_email": {"v": 2, "key": [("email", 1)], "unique": True},
        }

        plan = plan_indexes(declared, existing)

        assert all(entry["name"] != "_id_" for entry in plan)
        assert all("_id_" not in entry["drop"] for entry in plan)

    def test_an_ad_hoc_index_on_an_undeclared_field_is_never_named_to_drop(self):
        declared = [_declared("uniq_email", [("email", 1)], unique=True)]
        existing = {
            "uniq_email": {"v": 2, "key": [("email", 1)], "unique": True},
            "operators_manual_index": {"v": 2, "key": [("some_unrelated_field", 1)]},
        }

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "keep"
        assert all(
            "operators_manual_index" not in entry["drop"] for entry in plan
        )

    def test_an_undeclared_index_never_produces_its_own_plan_entry(self):
        # plan_indexes iterates declared_specs, never existing_info, so an
        # index nobody declared can never surface as an entry of its own
        # regardless of what it looks like.
        declared = []
        existing = {
            "_id_": {"v": 2, "key": [("_id", 1)]},
            "some_operator_index": {"v": 2, "key": [("whatever", 1)]},
        }

        plan = plan_indexes(declared, existing)

        assert plan == []


class TestPlanIndexesRecreateReasonText:
    def test_the_reason_names_both_the_live_and_the_declared_key_spec(self):
        declared = [
            _declared(
                "uniq_class_id_date", [("class_id", 1), ("date", 1)], unique=True
            )
        ]
        existing = {
            "uniq_class_id_date": {
                "v": 2,
                "key": [("class_id", 1), ("date", -1)],
                "unique": True,
            }
        }

        plan = plan_indexes(declared, existing)

        reason = plan[0]["reason"]
        assert "class_id: 1, date: -1" in reason
        assert "class_id: 1, date: 1" in reason

    def test_the_reason_for_a_unique_mismatch_states_both_sides(self):
        declared = [_declared("idx_student_id", [("student_id", 1)], unique=True)]
        existing = {"idx_student_id": {"v": 2, "key": [("student_id", 1)]}}

        plan = plan_indexes(declared, existing)

        reason = plan[0]["reason"]
        assert "false" in reason
        assert "true" in reason

    def test_a_kept_index_carries_no_reason(self):
        declared = [_declared("uniq_code", [("code", 1)], unique=True)]
        existing = {"uniq_code": {"v": 2, "key": [("code", 1)], "unique": True}}

        plan = plan_indexes(declared, existing)

        assert plan[0]["reason"] is None

    def test_a_created_index_carries_no_reason(self):
        declared = [_declared("uniq_code", [("code", 1)], unique=True)]

        plan = plan_indexes(declared, {})

        assert plan[0]["reason"] is None


class TestPlanIndexesInertOptionsDoNotTriggerRecreate:
    def test_background_alone_does_not_trigger_a_recreate(self):
        declared = [_declared("idx_role", [("role", 1)])]
        existing = {"idx_role": {"v": 2, "key": [("role", 1)], "background": True}}

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "keep"

    def test_ns_alone_does_not_trigger_a_recreate(self):
        declared = [_declared("idx_role", [("role", 1)])]
        existing = {
            "idx_role": {"v": 2, "key": [("role", 1)], "ns": "autoattend.users"}
        }

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "keep"

    def test_background_and_ns_together_still_keep(self):
        declared = [_declared("idx_role", [("role", 1)])]
        existing = {
            "idx_role": {
                "v": 2,
                "key": [("role", 1)],
                "background": True,
                "ns": "autoattend.users",
            }
        }

        plan = plan_indexes(declared, existing)

        assert plan[0]["action"] == "keep"

    def test_allowed_info_keys_names_exactly_the_inert_set(self):
        assert ALLOWED_INFO_KEYS == {"v", "key", "unique", "name", "ns", "background"}


class TestFormatKeys:
    def test_renders_a_single_ascending_field_like_the_mongo_shell(self):
        assert format_keys([("email", 1)]) == "{email: 1}"

    def test_renders_a_compound_key_with_mixed_directions(self):
        assert (
            format_keys([("class_id", 1), ("date", -1)])
            == "{class_id: 1, date: -1}"
        )

    def test_renders_an_empty_key_list_as_empty_braces(self):
        assert format_keys([]) == "{}"
