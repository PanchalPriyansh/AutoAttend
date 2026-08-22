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

Pure unit tests -- no Flask, no database, no CV library involved.
"""

from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from attendance.validators import (
    DEFAULT_MARKED_BY,
    parse_attendance_date,
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
