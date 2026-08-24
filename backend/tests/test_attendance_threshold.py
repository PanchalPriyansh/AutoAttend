"""Tests for backend/attendance/threshold.py.

Spec contract under test (.claude/specs/11-student-attendance-threshold.md,
"Backend -- the threshold module" + "Definition of done" ->
"Backend -- the threshold module"):
  - `attendance/threshold.py` imports `Config` directly and never raises.
  - `current_threshold()` returns `75.0` with the default configuration,
    and `None` -- never raising -- when the configured value is missing,
    `True`, a non-numeric string, `0`, negative, or greater than `100`.
  - `meets_threshold(percentage, threshold)` returns `True` when the
    percentage exactly equals the bar, `False` below it, and `None` if
    either argument is `None` -- `None` is not `False`.
  - `lectures_to_reach(present, total, threshold)` returns `0` for a class
    already at or above the bar, and for a class below it the smallest `n`
    for which `attendance_percentage(present + n, total + n)` meets the
    bar.
  - `lectures_to_reach()` returns `None` when `total` is `0`, when
    `threshold` is `None`, and when the answer would exceed
    `MAX_CATCH_UP_LECTURES` -- including the bar-of-100-with-one-absence
    case, which must not produce a five-figure count.
  - The figure it returns never disagrees with the label beside it: no
    input reports a class below the bar with `lectures_to_reach: 0`, or at
    the bar with a positive figure.

`attendance_percentage(present, total)` moved here from
`attendance/serializers.py` (unchanged) and is already covered directly by
`TestAttendancePercentage` in test_attendance_summary.py; it is only
exercised here as the arithmetic `lectures_to_reach` is built on.

No Flask, no pymongo, no live MongoDB, and no CV library is touched on
this path -- `current_threshold()` reads `Config` directly and every other
function here takes plain numbers and returns plain numbers. No real
credentials or production configuration are used anywhere in this file;
`Config.LOW_ATTENDANCE_THRESHOLD` is monkeypatched per test rather than
read from a real environment.
"""

import pytest

from attendance.threshold import (
    MAX_CATCH_UP_LECTURES,
    attendance_percentage,
    current_threshold,
    lectures_to_reach,
    meets_threshold,
)
from config import Config


# --- current_threshold ---------------------------------------------------


class TestCurrentThreshold:
    def test_default_configuration_returns_75(self, monkeypatch):
        """75 is config.py's documented default (`os.environ.get(
        "LOW_ATTENDANCE_THRESHOLD", "75")`); set explicitly here rather
        than relying on a real environment being unset.
        """
        monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 75.0)

        assert current_threshold() == 75.0

    def test_missing_value_returns_none_and_does_not_raise(self, monkeypatch):
        monkeypatch.delattr(Config, "LOW_ATTENDANCE_THRESHOLD", raising=False)

        assert current_threshold() is None

    def test_a_bool_is_rejected_even_though_a_bool_is_an_int_in_python(self, monkeypatch):
        monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", True)

        assert current_threshold() is None

    def test_a_non_numeric_string_returns_none(self, monkeypatch):
        monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", "not-a-number")

        assert current_threshold() is None

    def test_zero_returns_none(self, monkeypatch):
        monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 0)

        assert current_threshold() is None

    def test_a_negative_value_returns_none(self, monkeypatch):
        monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", -5)

        assert current_threshold() is None

    def test_a_value_greater_than_100_returns_none(self, monkeypatch):
        monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 100.1)

        assert current_threshold() is None

    def test_exactly_100_is_accepted(self, monkeypatch):
        """The bar's range is `(0, 100]` -- 100 is a reachable, meaningful
        bar and must not be rejected as "greater than 100".
        """
        monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", 100)

        assert current_threshold() == 100.0

    @pytest.mark.parametrize(
        "unusable_value",
        [None, True, "nonsense", 0, -1, 100.5, [], {}, object()],
    )
    def test_never_raises_for_any_unusable_value(self, monkeypatch, unusable_value):
        monkeypatch.setattr(Config, "LOW_ATTENDANCE_THRESHOLD", unusable_value)

        result = current_threshold()  # must not raise

        assert result is None


# --- meets_threshold -------------------------------------------------------


class TestMeetsThreshold:
    def test_exact_equality_with_the_bar_is_true(self):
        assert meets_threshold(75.0, 75.0) is True

    def test_below_the_bar_is_false(self):
        assert meets_threshold(74.9, 75.0) is False

    def test_above_the_bar_is_true(self):
        assert meets_threshold(90.0, 75.0) is True

    def test_none_percentage_returns_none_not_false(self):
        assert meets_threshold(None, 75.0) is None

    def test_none_threshold_returns_none_not_false(self):
        assert meets_threshold(83.3, None) is None

    def test_both_none_returns_none(self):
        assert meets_threshold(None, None) is None


# --- lectures_to_reach -----------------------------------------------------


class TestLecturesToReach:
    def test_already_exactly_at_the_bar_returns_zero(self):
        assert lectures_to_reach(3, 4, 75.0) == 0  # 75.0%

    def test_already_above_the_bar_returns_zero(self):
        assert lectures_to_reach(9, 10, 75.0) == 0  # 90.0%

    @pytest.mark.parametrize(
        "present, total, threshold, expected",
        [
            # 15/24 = 62.5%; (15+n)/(24+n) first meets 75.0% at n=12
            # (27/36 = 75.0% exactly); n=11 gives 26/35 = 74.3%, still short.
            (15, 24, 75.0, 12),
            # 0/1 = 0%; (0+n)/(1+n) first meets 75.0% at n=3 (3/4 = 75.0%).
            (0, 1, 75.0, 3),
            # 0/5 = 0%; (0+n)/(5+n) first meets 50.0% at n=5 (5/10 = 50.0%).
            (0, 5, 50.0, 5),
        ],
    )
    def test_below_the_bar_returns_the_smallest_n_that_reaches_it(
        self, present, total, threshold, expected
    ):
        result = lectures_to_reach(present, total, threshold)

        assert result == expected
        assert result <= MAX_CATCH_UP_LECTURES
        # The definition itself: attending `result` more lectures in a row
        # must bring the rounded percentage to the bar.
        assert meets_threshold(
            attendance_percentage(present + result, total + result), threshold
        )
        # One lecture short of that must still fall short, or `result`
        # would not be the *smallest* n.
        assert not meets_threshold(
            attendance_percentage(present + result - 1, total + result - 1), threshold
        )

    def test_zero_total_returns_none(self):
        assert lectures_to_reach(0, 0, 75.0) is None

    def test_none_threshold_returns_none(self):
        assert lectures_to_reach(1, 4, None) is None

    def test_bar_of_100_with_one_absence_does_not_return_an_absurd_count(self):
        """DoD, named explicitly: a single absence against a bar of 100 can
        never be made up -- the ratio approaches but never reaches 100%
        however many further lectures are attended -- so the honest answer
        is "no answer", not a five-figure (or larger) number of lectures.
        """
        result = lectures_to_reach(9, 10, 100.0)

        assert result is None

    def test_the_cap_is_respected_generally_not_only_at_a_bar_of_100(self):
        """A class far enough below a high bar that the true answer exceeds
        MAX_CATCH_UP_LECTURES must also degrade to None rather than an
        enormous number.
        """
        result = lectures_to_reach(1, 1000, 99.0)

        assert result is None


# --- The figure and the label beside it never disagree ---------------------


class TestTheFigureAgreesWithTheLabel:
    """DoD: "there is no input for which a class is reported below the bar
    with lectures_to_reach: 0, or at the bar with a positive figure."
    """

    @pytest.mark.parametrize(
        "present, total, threshold",
        [
            (0, 0, 75.0),          # nothing recorded
            (0, 5, 75.0),          # 0%, below
            (3, 5, 75.0),          # 60%, below
            (3, 4, 75.0),          # 75%, exactly at the bar
            (4, 4, 75.0),          # 100%, above
            (1, 10, 75.0),         # 10%, below
            (9, 10, 100.0),        # below a bar of 100, beyond the cap
            (10, 10, 100.0),       # exactly at a bar of 100
            (1, 3, 66.7),          # 33.3%, below a fractional bar
            (2, 3, 66.6),          # 66.7%, at/above a fractional bar
            (0, 3, None),          # threshold unusable
        ],
    )
    def test_zero_and_positive_never_disagree_with_meets_threshold(
        self, present, total, threshold
    ):
        percentage = attendance_percentage(present, total)
        met = meets_threshold(percentage, threshold)
        catch_up = lectures_to_reach(present, total, threshold)

        if met is True:
            assert catch_up == 0, (
                f"met=True but lectures_to_reach={catch_up} for "
                f"present={present}, total={total}, threshold={threshold}"
            )
        elif met is False:
            assert catch_up is None or catch_up > 0, (
                f"met=False but lectures_to_reach={catch_up} for "
                f"present={present}, total={total}, threshold={threshold}"
            )
        else:
            assert catch_up is None, (
                f"met=None but lectures_to_reach={catch_up} for "
                f"present={present}, total={total}, threshold={threshold}"
            )

        if catch_up == 0:
            assert met is True
