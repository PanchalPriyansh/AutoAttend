"""Tests for backend/notifications/reporting.py.

Added after the quality review of
.claude/specs/10-low-attendance-notifications.md, which moved this
formatting out of `app.py` so the CLI command stays the thin wiring that
`init-db` and `create-admin` are.

Spec contract under test ("Rules for implementation" 19 + the CLI
Definition-of-done items):
  - A finished sweep is reported as counts: the threshold and cooldown in
    force, how many classes were below the bar, how many were skipped for
    having been notified recently, and how many students/classes were (or
    would be) notified.
  - A dry run says so, and is phrased as "would notify" rather than
    "notified".
  - Recipient addresses appear only when the result carries them, which
    `notify_low_attendance` arranges to happen on the dry-run path alone.
  - A partial failure is reported separately, so the caller can route it
    to stderr rather than stdout.
  - Message bodies are never printed.

Pure string assembly, so these tests need no CliRunner, no Flask app, and
no database -- which is the point of the module existing separately. No
real email address appears anywhere.
"""

import pytest
from notifications.reporting import format_run_report
from notifications_test_helpers import make_sweep_settings


def _result(**overrides):
    base = {
        "candidates": 0,
        "skipped_cooldown": 0,
        "students_notified": 0,
        "classes_notified": 0,
        "failed": 0,
        "recipients": [],
    }
    base.update(overrides)
    return base


def _recipient(**overrides):
    base = {
        "name": "Test Student",
        "email": "student@example.test",
        "classes": [
            {
                "course": "Data Structures",
                "class_name": "A",
                "percentage": 62.5,
                "present_count": 15,
                "total_count": 24,
            }
        ],
    }
    base.update(overrides)
    return base


class TestTheHeadlineCounts:
    def test_the_threshold_and_cooldown_in_force_are_reported(self):
        lines, _ = format_run_report(
            _result(),
            make_sweep_settings(threshold=75.0, cooldown_days=7),
            dry_run=False,
        )

        assert any("75%" in line and "7 day(s)" in line for line in lines)

    def test_a_whole_number_threshold_is_not_shown_with_a_trailing_zero(self):
        lines, _ = format_run_report(
            _result(), make_sweep_settings(threshold=75.0), dry_run=False
        )

        assert not any("75.0%" in line for line in lines)

    def test_a_fractional_threshold_keeps_its_decimal(self):
        lines, _ = format_run_report(
            _result(), make_sweep_settings(threshold=72.5), dry_run=False
        )

        assert any("72.5%" in line for line in lines)

    def test_a_disabled_cooldown_is_reported_as_none_rather_than_zero_days(self):
        lines, _ = format_run_report(
            _result(), make_sweep_settings(cooldown_days=0), dry_run=False
        )

        assert any("none" in line for line in lines)
        assert not any("0 day(s)" in line for line in lines)

    def test_the_candidate_and_skipped_counts_are_reported(self):
        lines, _ = format_run_report(
            _result(candidates=5, skipped_cooldown=3),
            make_sweep_settings(),
            dry_run=False,
        )

        text = "\n".join(lines)
        assert "5 class(es)" in text
        assert "3" in text

    def test_the_notified_counts_are_reported(self):
        lines, _ = format_run_report(
            _result(students_notified=2, classes_notified=4),
            make_sweep_settings(),
            dry_run=False,
        )

        text = "\n".join(lines)
        assert "2 student(s)" in text
        assert "4 class(es)" in text


class TestDryRunPhrasing:
    def test_a_dry_run_says_nothing_was_sent_or_recorded(self):
        lines, _ = format_run_report(_result(), make_sweep_settings(), dry_run=True)

        assert "Dry run" in lines[0]
        assert "nothing was sent" in lines[0]

    def test_a_dry_run_is_phrased_as_would_notify(self):
        lines, _ = format_run_report(_result(), make_sweep_settings(), dry_run=True)

        text = "\n".join(lines)
        assert "Would notify" in text
        assert "Notified:" not in text

    def test_a_real_run_is_phrased_as_notified_and_carries_no_dry_run_banner(self):
        lines, _ = format_run_report(_result(), make_sweep_settings(), dry_run=False)

        text = "\n".join(lines)
        assert "Notified:" in text
        assert "Dry run" not in text


class TestRecipientListing:
    def test_a_recipient_is_listed_with_their_name_and_address(self):
        lines, _ = format_run_report(
            _result(recipients=[_recipient()]), make_sweep_settings(), dry_run=True
        )

        assert any("Test Student <student@example.test>" in line for line in lines)

    def test_each_short_class_is_listed_with_its_figures(self):
        lines, _ = format_run_report(
            _result(recipients=[_recipient()]), make_sweep_settings(), dry_run=True
        )

        text = "\n".join(lines)
        assert "Data Structures (Class A)" in text
        assert "62.5%" in text
        assert "(15/24)" in text

    def test_a_class_with_no_resolvable_course_name_still_renders(self):
        recipient = _recipient(
            classes=[
                {
                    "course": None,
                    "class_name": "A",
                    "percentage": 40.0,
                    "present_count": 2,
                    "total_count": 5,
                }
            ]
        )

        lines, _ = format_run_report(
            _result(recipients=[recipient]), make_sweep_settings(), dry_run=True
        )

        assert any("Unknown course" in line for line in lines)

    def test_an_empty_recipient_list_lists_nobody(self):
        lines, _ = format_run_report(_result(), make_sweep_settings(), dry_run=True)

        assert not any("<" in line and ">" in line for line in lines)

    def test_a_real_run_result_carries_no_recipients_so_none_are_printed(self):
        """Not a property of this module but of what it is handed:
        `notify_low_attendance` populates `recipients` on the dry-run path
        only, so there is nothing here to print on a real run.
        """
        lines, _ = format_run_report(_result(), make_sweep_settings(), dry_run=False)

        assert not any("@" in line for line in lines)


class TestPartialFailureIsReportedSeparately:
    def test_a_failure_count_is_returned_on_the_error_channel(self):
        lines, error_lines = format_run_report(
            _result(students_notified=1, failed=2),
            make_sweep_settings(),
            dry_run=False,
        )

        assert len(error_lines) == 1
        assert "2 student(s)" in error_lines[0]
        assert not any("Failed to notify" in line for line in lines)

    def test_no_failures_produces_no_error_lines(self):
        _, error_lines = format_run_report(
            _result(students_notified=1), make_sweep_settings(), dry_run=False
        )

        assert error_lines == []

    def test_a_failure_line_points_at_the_logs_rather_than_naming_anyone(self):
        _, error_lines = format_run_report(
            _result(failed=1), make_sweep_settings(), dry_run=False
        )

        assert "See server logs" in error_lines[0]
        assert "@" not in error_lines[0]


class TestNothingSensitiveIsEverRendered:
    @pytest.mark.parametrize("dry_run", [True, False])
    def test_no_message_body_text_appears_in_the_report(self, dry_run):
        lines, error_lines = format_run_report(
            _result(recipients=[_recipient()], students_notified=1, failed=1),
            make_sweep_settings(),
            dry_run=dry_run,
        )

        text = "\n".join(lines + error_lines)
        # Phrases that only ever occur in an actual email body.
        assert "Hello" not in text
        assert "do not reply" not in text
        assert "automatic notice" not in text
