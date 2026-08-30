"""Tests for backend/database/schema.py (collection names, validators, indexes).

Spec contract under test (.claude/specs/02-database-setup.md, "Database
changes" + "Definition of done", extended by
.claude/specs/06-face-enrollment.md,
.claude/specs/07-attendance-capture.md,
.claude/specs/08-faculty-attendance-history.md, and
.claude/specs/10-low-attendance-notifications.md):
  - Exactly eleven collections are declared: users, institutes, departments,
    semesters, courses, classes, class_enrollments, face_encodings,
    attendance_sessions, attendance_records, and attendance_notifications --
    no ML-related collections.
  - `face_encodings` joined the list with 06-face-enrollment.md, the two
    attendance collections with 07-attendance-capture.md, and
    `attendance_notifications` with 10-low-attendance-notifications.md, each
    of which owns what it added. Until then those name fragments were
    tripwires here; each was removed because its feature now exists, not to
    make an assertion pass. The ML fragments stay, permanently -- that spec
    is cancelled, not pending.
  - Each collection has a `$jsonSchema`-shaped validator with the required
    fields listed in the spec, and `users.role` is constrained to the
    `admin` | `faculty` | `student` enum.
  - Each collection's declared indexes (unique/non-unique, single/compound)
    match the spec exactly.

This is pure data (schema.py has no pymongo/Flask imports or side effects),
so these are plain assertions against the declared Python data structures --
no MongoDB, live or mocked, is involved.
"""

import pytest
from database import schema

ALL_COLLECTION_NAMES = {
    schema.USERS,
    schema.INSTITUTES,
    schema.DEPARTMENTS,
    schema.SEMESTERS,
    schema.COURSES,
    schema.CLASSES,
    schema.CLASS_ENROLLMENTS,
    schema.FACE_ENCODINGS,
    schema.ATTENDANCE_SESSIONS,
    schema.ATTENDANCE_RECORDS,
    schema.ATTENDANCE_NOTIFICATIONS,
}

# Substrings that would indicate a collection this project should not have.
# "face"/"encoding" were removed when 06-face-enrollment.md landed,
# "attendance" when 07-attendance-capture.md did, and "notification" when
# 10-low-attendance-notifications.md earned `attendance_notifications`.
#
# "ml"/"predict"/"risk" are permanent: AutoAttend records attendance and
# nothing else -- no marks, no grades, no assessments, and no model to feed
# them to (see CLAUDE.md, "Warnings and things to avoid"). Nothing in this
# project can ever earn these back.
OUT_OF_SCOPE_NAME_FRAGMENTS = [
    "ml",
    "predict",
    "risk",
]

EXPECTED_REQUIRED_FIELDS = {
    schema.USERS: {
        "name",
        "email",
        "password_hash",
        "role",
        "is_active",
        "created_at",
        "updated_at",
    },
    schema.INSTITUTES: {"name", "code", "created_at"},
    schema.DEPARTMENTS: {"institute_id", "name", "code", "created_at"},
    schema.SEMESTERS: {"department_id", "name", "start_date", "end_date", "created_at"},
    schema.COURSES: {"semester_id", "name", "code", "created_at"},
    schema.CLASSES: {"course_id", "name", "created_at"},
    schema.CLASS_ENROLLMENTS: {"class_id", "student_id", "enrolled_at"},
    schema.FACE_ENCODINGS: {
        "student_id",
        "encoding",
        "model",
        "source",
        "created_at",
        "created_by",
    },
    schema.ATTENDANCE_SESSIONS: {
        "class_id",
        "date",
        "source",
        "taken_by",
        "created_at",
        "updated_at",
    },
    schema.ATTENDANCE_RECORDS: {
        "session_id",
        "class_id",
        "student_id",
        "status",
        "marked_by",
        "created_at",
    },
    schema.ATTENDANCE_NOTIFICATIONS: {
        "student_id",
        "class_id",
        "email",
        "threshold",
        "percentage",
        "present_count",
        "total_count",
        "sent_at",
    },
}

# (keys, unique) signatures per collection, per spec's "Database changes"
# section. Index *names* are not asserted here (implementation detail);
# only the field/direction shape and uniqueness are part of the contract.
EXPECTED_INDEX_SIGNATURES = {
    schema.USERS: {
        ((("email", 1),), True),
        ((("role", 1),), False),
    },
    schema.INSTITUTES: {
        ((("code", 1),), True),
    },
    schema.DEPARTMENTS: {
        ((("institute_id", 1), ("code", 1)), True),
    },
    schema.SEMESTERS: {
        ((("department_id", 1), ("name", 1)), True),
    },
    schema.COURSES: {
        ((("semester_id", 1), ("code", 1)), True),
    },
    schema.CLASSES: {
        ((("course_id", 1), ("name", 1)), True),
        ((("faculty_id", 1),), False),
    },
    schema.CLASS_ENROLLMENTS: {
        ((("class_id", 1), ("student_id", 1)), True),
        ((("student_id", 1),), False),
    },
    schema.FACE_ENCODINGS: {
        ((("student_id", 1),), False),
    },
    schema.ATTENDANCE_SESSIONS: {
        ((("class_id", 1), ("date", 1)), True),
    },
    schema.ATTENDANCE_RECORDS: {
        ((("session_id", 1), ("student_id", 1)), True),
        ((("student_id", 1), ("class_id", 1)), False),
    },
    # Non-unique on purpose: the same student may be warned about the same
    # class again in a later term. See the comment on the index in
    # schema.py -- a unique key here would silently turn the cooldown into
    # "warned once, ever".
    schema.ATTENDANCE_NOTIFICATIONS: {
        ((("student_id", 1), ("class_id", 1), ("sent_at", -1)), False),
    },
}


def _spec_for(collection_name):
    matches = [spec for spec in schema.COLLECTIONS if spec["name"] == collection_name]
    assert len(matches) == 1, f"expected exactly one COLLECTIONS entry for {collection_name!r}"
    return matches[0]


class TestCollectionNameConstants:
    def test_all_eleven_collection_name_constants_are_defined(self):
        assert schema.USERS == "users"
        assert schema.INSTITUTES == "institutes"
        assert schema.DEPARTMENTS == "departments"
        assert schema.SEMESTERS == "semesters"
        assert schema.COURSES == "courses"
        assert schema.CLASSES == "classes"
        assert schema.CLASS_ENROLLMENTS == "class_enrollments"
        assert schema.FACE_ENCODINGS == "face_encodings"
        assert schema.ATTENDANCE_SESSIONS == "attendance_sessions"
        assert schema.ATTENDANCE_RECORDS == "attendance_records"
        assert schema.ATTENDANCE_NOTIFICATIONS == "attendance_notifications"


class TestCollectionsRegistry:
    def test_collections_declares_exactly_the_eleven_expected_names(self):
        declared_names = {spec["name"] for spec in schema.COLLECTIONS}

        assert declared_names == ALL_COLLECTION_NAMES
        # Hardcoded rather than derived from COLLECTIONS: the point is to
        # notice an unplanned collection being added, which a derived count
        # would silently accept.
        assert len(schema.COLLECTIONS) == 11

    def test_no_out_of_scope_collection_names_are_declared(self):
        for spec in schema.COLLECTIONS:
            lowered = spec["name"].lower()
            for fragment in OUT_OF_SCOPE_NAME_FRAGMENTS:
                assert fragment not in lowered, (
                    f"Collection '{spec['name']}' looks out of scope for the "
                    "features implemented so far (ML/notification collections "
                    "belong to their own later specs)."
                )


class TestValidatorsAreJsonSchemaShaped:
    @pytest.mark.parametrize("collection_name", sorted(ALL_COLLECTION_NAMES))
    def test_validator_is_wrapped_in_json_schema_with_object_bson_type(self, collection_name):
        spec = _spec_for(collection_name)
        validator = spec["validator"]

        assert "$jsonSchema" in validator
        assert validator["$jsonSchema"]["bsonType"] == "object"

    @pytest.mark.parametrize("collection_name", sorted(ALL_COLLECTION_NAMES))
    def test_required_fields_match_the_spec(self, collection_name):
        spec = _spec_for(collection_name)
        required = set(spec["validator"]["$jsonSchema"].get("required", []))

        assert required == EXPECTED_REQUIRED_FIELDS[collection_name]


class TestUsersValidatorSpecificContract:
    def test_role_enum_is_exactly_the_three_platform_roles(self):
        role_property = schema.USERS_VALIDATOR["$jsonSchema"]["properties"]["role"]

        assert role_property["enum"] == ["admin", "faculty", "student"]

    def test_institute_id_is_nullable_for_a_not_yet_scoped_admin(self):
        institute_id_property = schema.USERS_VALIDATOR["$jsonSchema"]["properties"]["institute_id"]

        assert "objectId" in institute_id_property["bsonType"]
        assert "null" in institute_id_property["bsonType"]

    def test_is_active_is_not_part_of_role_or_password_fields(self):
        properties = schema.USERS_VALIDATOR["$jsonSchema"]["properties"]

        assert properties["is_active"]["bsonType"] == "bool"
        assert properties["password_hash"]["bsonType"] == "string"

    def test_token_version_is_a_declared_int_property(self):
        """24-invalidate-tokens-on-password-change. "int" is BSON int32,
        which is what both `$inc` and an explicit 0 produce -- verified
        against a live server, where a double is rejected."""
        properties = schema.USERS_VALIDATOR["$jsonSchema"]["properties"]

        assert properties["token_version"]["bsonType"] == "int"

    def test_token_version_is_not_required(self):
        """The whole migration story, and it must not be "tightened" by a
        later edit.

        MongoDB validates the entire document on update, so requiring
        this would make every user document written before 24 unwritable
        -- PUT /api/users/<id> and .../status would start failing against
        the live database until somebody ran a backfill. Optional, plus
        absent-means-zero in auth/tokens.py, means there is no backfill
        and no ordering dependency between deploying the code and
        running `flask init-db`.
        """
        required = schema.USERS_VALIDATOR["$jsonSchema"]["required"]

        assert "token_version" not in required


class TestObjectIdReferenceFields:
    """Per spec/CLAUDE.md: hierarchy levels reference their parent via
    ObjectId fields, never embedded documents."""

    @pytest.mark.parametrize(
        "collection_name, ref_field",
        [
            (schema.DEPARTMENTS, "institute_id"),
            (schema.SEMESTERS, "department_id"),
            (schema.COURSES, "semester_id"),
            (schema.CLASSES, "course_id"),
            (schema.CLASS_ENROLLMENTS, "class_id"),
            (schema.CLASS_ENROLLMENTS, "student_id"),
            (schema.FACE_ENCODINGS, "student_id"),
            (schema.FACE_ENCODINGS, "created_by"),
        ],
    )
    def test_parent_reference_field_is_typed_as_object_id(self, collection_name, ref_field):
        spec = _spec_for(collection_name)
        properties = spec["validator"]["$jsonSchema"]["properties"]

        assert properties[ref_field]["bsonType"] == "objectId"


class TestFaceEncodingsValidatorSpecificContract:
    """Per .claude/specs/06-face-enrollment.md: only the derived encoding is
    stored, and it is pinned to the descriptor length the encoder produces.
    """

    def test_encoding_is_an_array_pinned_to_the_descriptor_length(self):
        encoding_property = schema.FACE_ENCODINGS_VALIDATOR["$jsonSchema"]["properties"][
            "encoding"
        ]

        assert encoding_property["bsonType"] == "array"
        assert encoding_property["minItems"] == 128
        assert encoding_property["maxItems"] == 128
        assert encoding_property["items"]["bsonType"] == "double"

    def test_source_is_constrained_to_the_two_capture_paths(self):
        source_property = schema.FACE_ENCODINGS_VALIDATOR["$jsonSchema"]["properties"][
            "source"
        ]

        assert source_property["enum"] == ["upload", "camera"]

    def test_no_field_exists_for_storing_the_source_image(self):
        """The photograph is processed in memory and discarded; the schema
        must not offer anywhere to put it, in any form.
        """
        properties = schema.FACE_ENCODINGS_VALIDATOR["$jsonSchema"]["properties"]

        for forbidden in ("image", "image_data", "photo", "thumbnail", "file_path", "url"):
            assert forbidden not in properties, (
                f"'{forbidden}' would persist biometric source material; only "
                "the derived encoding may be stored."
            )


class TestAttendanceSessionsValidatorSpecificContract:
    """Per .claude/specs/08-faculty-attendance-history.md, `updated_by`
    records who last corrected a session. It must be a real field a
    correction can populate, and it must stay optional: every session
    07-attendance-capture.md ever wrote has no such field, and the
    validator must not start rejecting them the moment 08's `collMod`
    (database/init_db.py) re-applies it.

    This only proves the declared shape is right -- that a document
    missing an optional $jsonSchema property still satisfies "strict"
    validation is MongoDB's own documented behavior for an omitted,
    non-required property, exercised manually against a live cluster per
    this feature's scope note, the same boundary test_init_db.py draws
    around create_index's server-side dedup.
    """

    def test_updated_by_exists_and_is_typed_as_an_object_id(self):
        properties = schema.ATTENDANCE_SESSIONS_VALIDATOR["$jsonSchema"]["properties"]

        assert properties["updated_by"]["bsonType"] == "objectId"

    def test_updated_by_is_not_required(self):
        """Redundant with test_required_fields_match_the_spec above, kept
        here as a standalone, locally-obvious guarantee: this is the one
        field in the collection a document is allowed to be missing.
        """
        required = schema.ATTENDANCE_SESSIONS_VALIDATOR["$jsonSchema"]["required"]

        assert "updated_by" not in required


class TestIndexDefinitionsPerCollection:
    @pytest.mark.parametrize("collection_name", sorted(ALL_COLLECTION_NAMES))
    def test_declared_indexes_match_the_spec_exactly(self, collection_name):
        spec = _spec_for(collection_name)
        actual_signatures = {
            (tuple(idx["keys"]), idx["unique"]) for idx in spec["indexes"]
        }

        assert actual_signatures == EXPECTED_INDEX_SIGNATURES[collection_name]

    def test_every_declared_index_has_a_non_empty_name(self):
        for spec in schema.COLLECTIONS:
            for index_spec in spec["indexes"]:
                assert isinstance(index_spec["name"], str)
                assert index_spec["name"]

    def test_index_names_are_unique_within_each_collection(self):
        for spec in schema.COLLECTIONS:
            names = [index_spec["name"] for index_spec in spec["indexes"]]
            assert len(names) == len(set(names)), (
                f"Duplicate index names declared for collection {spec['name']!r}"
            )
