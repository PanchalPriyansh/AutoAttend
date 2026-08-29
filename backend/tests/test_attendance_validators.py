"""Tests for backend/attendance/validators.py.

Spec contract under test (07-attendance-capture.md, "Rules for
implementation" + "Definition of done"):
  - Dates are normalised to UTC midnight and a future date is rejected
    with a 400-mapped ValidationError.
  - `source` is one of "photo"/"video"/"manual"; `status` is one of
    "present"/"absent"; `marked_by` is one of "recognition"/"faculty" and
    defaults to "faculty" when absent.
  - `replace` defaults to False and must be a bool when present.
  - A `records` payload must match the roster exactly: every enrolled
    student exactly once, no duplicates, no student who is not enrolled --
    a partial or contaminated list is a 400, never a partial write.

Also covers 08-faculty-attendance-history.md's additions to this module
("Backend" + "Rules for implementation" + "Definition of done"):
  - `parse_optional_date`: `None` when the filter is omitted, otherwise a
    UTC-midnight `datetime`; unlike `parse_attendance_date`, a future bound
    is accepted (`to=2030-01-01` selects nothing that exists yet, it does
    not invent a lecture).
  - `parse_session_filters`: bundles `from`/`to`/`limit`/`skip`; a `to`
    earlier than `from` is rejected; `limit` defaults to 50 and clamps
    (never rejects) above 200; `skip` defaults to 0; a non-integer or
    negative `limit`/`skip` is rejected.
  - `require_records(..., scope=...)` is one roster-completeness rule with
    two callers: the "stranger" error message names whichever `scope` is
    passed in ("this class" for capture, "this session" for a correction),
    so the two callers cannot silently drift onto different rules.

Also covers 21-attendance-export.md's one addition to this module
("Backend" + "Rules for implementation" 149):
  - `parse_export_filters` is `parse_date_range` and nothing else -- an
    inclusive `from`/`to`, no `limit`, no `skip`, so paging cannot be
    added to it by reflex. `MAX_EXPORT_SESSIONS` itself is exercised at
    the route/service level (test_attendance_routes.py), not here, since
    it is enforced by attendance/service.py::_export_sessions rather than
    by this module.

Pure unit tests -- no Flask, no database, no CV library involved.
"""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from attendance.validators import (
    DEFAULT_MARKED_BY,
    DEFAULT_SESSION_LIMIT,
    MAX_SESSION_LIMIT,
    parse_attendance_date,
    parse_date_range,
    parse_export_filters,
    parse_optional_date,
    parse_session_filters,
    require_marked_by,
    require_records,
    require_replace_flag,
    require_session_source,
    require_status,
)
from common.errors import ValidationError


# --- parse_attendance_date --------------------------------------------------


class TestParseAttendanceDate:
    def test_valid_date_string_is_normalized_to_utc_midnight(self):
        parsed = parse_attendance_date({"date": "2020-01-15"})

        assert parsed == datetime(2020, 1, 15, tzinfo=timezone.utc)
        assert parsed.tzinfo is not None

    def test_a_datetime_with_a_time_component_is_normalized_to_midnight(self):
        parsed = parse_attendance_date({"date": "2020-01-15T14:35:22"})

        assert parsed.hour == 0
        assert parsed.minute == 0
        assert parsed.second == 0
        assert parsed.microsecond == 0
        assert parsed.date() == datetime(2020, 1, 15).date()

    def test_todays_date_is_accepted_not_rejected_as_future(self):
        today = datetime.now(timezone.utc).date().isoformat()

        parsed = parse_attendance_date({"date": today})

        assert parsed.date().isoformat() == today

    def test_a_future_date_is_rejected(self):
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat()

        with pytest.raises(ValidationError):
            parse_attendance_date({"date": tomorrow})

    def test_a_malformed_date_string_is_rejected(self):
        with pytest.raises(ValidationError):
            parse_attendance_date({"date": "not-a-date"})

    def test_a_missing_date_field_is_rejected(self):
        with pytest.raises(ValidationError):
            parse_attendance_date({})

    def test_custom_field_name_is_used_in_lookup(self):
        parsed = parse_attendance_date({"lecture_date": "2020-01-01"}, field_name="lecture_date")

        assert parsed == datetime(2020, 1, 1, tzinfo=timezone.utc)


# --- enums -------------------------------------------------------------------


class TestRequireSessionSource:
    @pytest.mark.parametrize("value", ["photo", "video", "manual"])
    def test_accepts_each_documented_source(self, value):
        assert require_session_source(value) == value

    @pytest.mark.parametrize("value", [None, "", "webcam", "PHOTO"])
    def test_rejects_missing_or_unknown_values(self, value):
        with pytest.raises(ValidationError):
            require_session_source(value)


class TestRequireStatus:
    @pytest.mark.parametrize("value", ["present", "absent"])
    def test_accepts_each_documented_status(self, value):
        assert require_status(value) == value

    @pytest.mark.parametrize("value", [None, "", "late", "Present"])
    def test_rejects_missing_or_unknown_values(self, value):
        with pytest.raises(ValidationError):
            require_status(value)


class TestRequireMarkedBy:
    @pytest.mark.parametrize("value", ["recognition", "faculty"])
    def test_accepts_each_documented_value(self, value):
        assert require_marked_by(value) == value

    @pytest.mark.parametrize("value", [None, ""])
    def test_missing_defaults_to_faculty(self, value):
        assert require_marked_by(value) == DEFAULT_MARKED_BY == "faculty"

    def test_rejects_an_unknown_value(self):
        with pytest.raises(ValidationError):
            require_marked_by("robot")


class TestRequireReplaceFlag:
    def test_missing_defaults_to_false(self):
        assert require_replace_flag(None) is False

    @pytest.mark.parametrize("value", [True, False])
    def test_bool_values_pass_through_unchanged(self, value):
        assert require_replace_flag(value) is value

    @pytest.mark.parametrize("value", ["true", 1, 0, "false"])
    def test_non_bool_values_are_rejected(self, value):
        with pytest.raises(ValidationError):
            require_replace_flag(value)


# --- require_records ----------------------------------------------------------


class TestRequireRecords:
    def test_a_complete_roster_is_accepted_and_normalized(self):
        student_a, student_b = ObjectId(), ObjectId()
        roster_ids = [student_a, student_b]
        records = [
            {"student_id": str(student_b), "status": "absent", "marked_by": "faculty"},
            {"student_id": str(student_a), "status": "present", "marked_by": "recognition"},
        ]

        normalized = require_records(records, roster_ids)

        assert [entry["student_id"] for entry in normalized] == roster_ids

    def test_result_order_follows_roster_order_not_payload_order(self):
        student_a, student_b, student_c = ObjectId(), ObjectId(), ObjectId()
        roster_ids = [student_a, student_b, student_c]
        # Deliberately submitted out of roster order.
        records = [
            {"student_id": str(student_c), "status": "present"},
            {"student_id": str(student_a), "status": "absent"},
            {"student_id": str(student_b), "status": "present"},
        ]

        normalized = require_records(records, roster_ids)

        assert [entry["student_id"] for entry in normalized] == [student_a, student_b, student_c]

    def test_marked_by_defaults_to_faculty_when_omitted(self):
        student = ObjectId()
        normalized = require_records([{"student_id": str(student), "status": "present"}], [student])

        assert normalized[0]["marked_by"] == "faculty"

    def test_missing_a_roster_student_is_rejected(self):
        student_a, student_b = ObjectId(), ObjectId()
        records = [{"student_id": str(student_a), "status": "present"}]

        with pytest.raises(ValidationError):
            require_records(records, [student_a, student_b])

    def test_duplicate_student_entries_are_rejected(self):
        student = ObjectId()
        records = [
            {"student_id": str(student), "status": "present"},
            {"student_id": str(student), "status": "absent"},
        ]

        with pytest.raises(ValidationError):
            require_records(records, [student])

    def test_a_student_not_on_the_roster_is_rejected(self):
        roster_student = ObjectId()
        stranger = ObjectId()
        records = [
            {"student_id": str(roster_student), "status": "present"},
            {"student_id": str(stranger), "status": "present"},
        ]

        with pytest.raises(ValidationError):
            require_records(records, [roster_student])

    def test_empty_records_list_is_rejected(self):
        with pytest.raises(ValidationError):
            require_records([], [ObjectId()])

    def test_non_list_records_is_rejected(self):
        with pytest.raises(ValidationError):
            require_records({"student_id": "x"}, [ObjectId()])

    def test_a_non_dict_record_entry_is_rejected(self):
        student = ObjectId()
        with pytest.raises(ValidationError):
            require_records(["not-a-dict"], [student])

    def test_an_invalid_status_inside_a_record_is_rejected(self):
        student = ObjectId()
        records = [{"student_id": str(student), "status": "late"}]

        with pytest.raises(ValidationError):
            require_records(records, [student])

    def test_a_malformed_student_id_inside_a_record_is_rejected(self):
        records = [{"student_id": "not-a-valid-id", "status": "present"}]

        with pytest.raises(ValidationError):
            require_records(records, [ObjectId()])

    # -- scope (08-faculty-attendance-history.md) ---------------------------

    def test_scope_defaults_to_this_class(self):
        roster_student, stranger = ObjectId(), ObjectId()
        records = [
            {"student_id": str(roster_student), "status": "present"},
            {"student_id": str(stranger), "status": "present"},
        ]

        with pytest.raises(ValidationError, match="this class"):
            require_records(records, [roster_student])

    def test_scope_this_session_names_the_session_in_the_stranger_message(self):
        session_student, stranger = ObjectId(), ObjectId()
        records = [
            {"student_id": str(session_student), "status": "present"},
            {"student_id": str(stranger), "status": "present"},
        ]

        with pytest.raises(ValidationError, match="this session"):
            require_records(records, [session_student], scope="this session")

    def test_scope_this_session_names_the_session_in_the_missing_message(self):
        first, second = ObjectId(), ObjectId()
        records = [{"student_id": str(first), "status": "present"}]

        with pytest.raises(ValidationError, match="this session"):
            require_records(records, [first, second], scope="this session")

    def test_a_complete_session_roster_is_accepted_regardless_of_scope_label(self):
        student_a, student_b = ObjectId(), ObjectId()
        records = [
            {"student_id": str(student_a), "status": "present"},
            {"student_id": str(student_b), "status": "absent"},
        ]

        normalized = require_records(records, [student_a, student_b], scope="this session")

        assert [entry["student_id"] for entry in normalized] == [student_a, student_b]


# --- parse_optional_date (08-faculty-attendance-history.md) ------------------


class TestParseOptionalDate:
    def test_returns_none_when_the_field_is_absent(self):
        assert parse_optional_date({}, "from") is None

    def test_returns_none_when_the_field_is_an_empty_string(self):
        assert parse_optional_date({"from": ""}, "from") is None

    def test_a_valid_date_is_normalized_to_utc_midnight(self):
        parsed = parse_optional_date({"from": "2020-06-01"}, "from")

        assert parsed == datetime(2020, 6, 1, tzinfo=timezone.utc)

    def test_a_future_date_is_accepted_unlike_parse_attendance_date(self):
        future = (datetime.now(timezone.utc) + timedelta(days=3650)).date().isoformat()

        parsed = parse_optional_date({"to": future}, "to")

        assert parsed.date().isoformat() == future

    def test_a_malformed_date_is_rejected(self):
        with pytest.raises(ValidationError):
            parse_optional_date({"from": "not-a-date"}, "from")

    def test_reads_the_named_field_only(self):
        assert parse_optional_date({"to": "2020-01-01"}, "from") is None


# --- parse_session_filters (08-faculty-attendance-history.md) ----------------


class TestParseSessionFilters:
    def test_defaults_when_nothing_is_supplied(self):
        filters = parse_session_filters({})

        assert filters == {
            "date_from": None,
            "date_to": None,
            "limit": DEFAULT_SESSION_LIMIT,
            "skip": 0,
        }

    def test_from_and_to_are_normalized_to_utc_midnight(self):
        filters = parse_session_filters({"from": "2020-01-01", "to": "2020-01-31"})

        assert filters["date_from"] == datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert filters["date_to"] == datetime(2020, 1, 31, tzinfo=timezone.utc)

    def test_to_equal_to_from_is_accepted_a_single_day_range(self):
        filters = parse_session_filters({"from": "2020-01-01", "to": "2020-01-01"})

        assert filters["date_from"] == filters["date_to"]

    def test_to_earlier_than_from_is_rejected(self):
        with pytest.raises(ValidationError):
            parse_session_filters({"from": "2020-02-01", "to": "2020-01-01"})

    def test_a_malformed_from_is_rejected(self):
        with pytest.raises(ValidationError):
            parse_session_filters({"from": "not-a-date"})

    def test_a_malformed_to_is_rejected(self):
        with pytest.raises(ValidationError):
            parse_session_filters({"to": "not-a-date"})

    def test_limit_defaults_to_50(self):
        assert parse_session_filters({})["limit"] == 50 == DEFAULT_SESSION_LIMIT

    def test_limit_above_the_maximum_is_clamped_not_rejected(self):
        filters = parse_session_filters({"limit": "5000"})

        assert filters["limit"] == MAX_SESSION_LIMIT == 200

    def test_limit_at_the_maximum_is_accepted_unclamped(self):
        filters = parse_session_filters({"limit": str(MAX_SESSION_LIMIT)})

        assert filters["limit"] == MAX_SESSION_LIMIT

    @pytest.mark.parametrize("value", ["0", "-1", "not-a-number", "1.5"])
    def test_an_invalid_limit_is_rejected(self, value):
        with pytest.raises(ValidationError):
            parse_session_filters({"limit": value})

    def test_skip_defaults_to_zero(self):
        assert parse_session_filters({})["skip"] == 0

    def test_skip_zero_is_accepted(self):
        assert parse_session_filters({"skip": "0"})["skip"] == 0

    @pytest.mark.parametrize("value", ["-1", "not-a-number", "1.5"])
    def test_an_invalid_skip_is_rejected(self, value):
        with pytest.raises(ValidationError):
            parse_session_filters({"skip": value})

    def test_a_large_skip_is_accepted_unclamped(self):
        assert parse_session_filters({"skip": "10000"})["skip"] == 10000


# --- parse_export_filters (21-attendance-export.md) ---------------------
#
# Spec contract under test (21-attendance-export.md, "APIs" + "Backend" +
# "Rules for implementation" 149 + Definition of done 3-4):
#   - `parse_export_filters` is `parse_date_range` and nothing else -- an
#     inclusive `from`/`to`, no `limit`, no `skip`. It exists as its own
#     function only so the export routes read like their neighbours and so
#     paging cannot be added to it by reflex later.


class TestParseExportFilters:
    def test_defaults_when_nothing_is_supplied(self):
        assert parse_export_filters({}) == {"date_from": None, "date_to": None}

    def test_from_and_to_are_normalized_to_utc_midnight(self):
        filters = parse_export_filters({"from": "2020-01-01", "to": "2020-01-31"})

        assert filters["date_from"] == datetime(2020, 1, 1, tzinfo=timezone.utc)
        assert filters["date_to"] == datetime(2020, 1, 31, tzinfo=timezone.utc)

    def test_to_equal_to_from_is_accepted_a_single_day_range(self):
        filters = parse_export_filters({"from": "2020-01-01", "to": "2020-01-01"})

        assert filters["date_from"] == filters["date_to"]

    def test_to_earlier_than_from_is_rejected(self):
        with pytest.raises(ValidationError):
            parse_export_filters({"from": "2020-02-01", "to": "2020-01-01"})

    def test_a_malformed_from_is_rejected(self):
        with pytest.raises(ValidationError):
            parse_export_filters({"from": "not-a-date"})

    def test_a_malformed_to_is_rejected(self):
        with pytest.raises(ValidationError):
            parse_export_filters({"to": "not-a-date"})

    def test_a_future_to_is_accepted_unlike_a_lecture_date(self):
        """Unlike parse_attendance_date, a future bound selects nothing
        that exists yet rather than inventing a lecture -- the same rule
        parse_optional_date documents.
        """
        future = (datetime.now(timezone.utc) + timedelta(days=3650)).date().isoformat()

        filters = parse_export_filters({"to": future})

        assert filters["date_to"].date().isoformat() == future

    def test_reads_only_from_and_to_never_limit_or_skip(self):
        filters = parse_export_filters({"from": "2020-01-01", "limit": "5", "skip": "2"})

        assert set(filters.keys()) == {"date_from", "date_to"}

    def test_is_exactly_parse_date_range_and_nothing_else(self):
        args = {"from": "2020-01-01", "to": "2020-01-31"}

        assert parse_export_filters(args) == parse_date_range(args)
