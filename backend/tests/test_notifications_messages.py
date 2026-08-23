"""Tests for backend/notifications/messages.py.

Spec contract under test (.claude/specs/10-low-attendance-notifications.md,
"Backend" + "Rules for implementation" + "Definition of done"):
  - `build_subject(count)` is singular/plural but never carries a
    percentage, a class name, or any other figure -- a subject line is
    visible in a lock-screen preview.
  - `build_body(student_name, entries, threshold)` produces a plain-text
    body containing: a greeting using the student's own name, the
    threshold, and -- for every entry -- its course/class heading, its
    percentage, and its present/total counts, spelled out.
  - The body must never mention another student, a roster, a class
    average, a rank, a comparison, or a predicted/risk-scored outcome
    (rules 11-12).
  - `format_percentage(value)` renders the same figure the student
    dashboard shows: rounded to one decimal, with a trailing ".0"
    dropped.

Pure string-construction tests: no database, no SMTP, no Flask object
appears anywhere in this module, matching messages.py itself. No real
student name or address is used -- every fixture is an obviously-fake
placeholder.
"""

import pytest

from notifications.messages import build_body, build_subject, format_percentage


# --- format_percentage -----------------------------------------------------


class TestFormatPercentage:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (75, "75"),
            (75.0, "75"),
            (100, "100"),
            (0, "0"),
            (62.5, "62.5"),
            (33.333, "33.3"),
            (66.66, "66.7"),
        ],
    )
    def test_representative_values(self, value, expected):
        assert format_percentage(value) == expected


# --- build_subject ----------------------------------------------------------


class TestBuildSubject:
    def test_singular_class_count(self):
        subject = build_subject(1)

        assert subject == "AutoAttend: your attendance is low in 1 class"

    @pytest.mark.parametrize("count", [2, 3, 10])
    def test_plural_class_count(self, count):
        subject = build_subject(count)

        assert subject == f"AutoAttend: your attendance is low in {count} classes"

    def test_subject_never_carries_a_percentage_or_figure(self):
        """Kept deliberately free of anything sensitive -- only the count,
        which is not sensitive, may appear.
        """
        subject = build_subject(2)

        assert "%" not in subject


# --- build_body ---------------------------------------------------------------


def _entry(course="Data Structures", class_name="A", percentage=42.5,
           present_count=5, total_count=12):
    return {
        "course": course,
        "class_name": class_name,
        "percentage": percentage,
        "present_count": present_count,
        "total_count": total_count,
    }


class TestBuildBodyGreeting:
    def test_greets_the_student_by_their_own_name(self):
        body = build_body("Alice Test", [_entry()], 75)

        assert body.startswith("Hello Alice Test,")

    def test_falls_back_to_a_generic_greeting_when_no_name_is_given(self):
        body = build_body(None, [_entry()], 75)

        assert body.startswith("Hello,")
        assert "Hello None" not in body


class TestBuildBodyThreshold:
    def test_body_states_the_threshold_in_force(self):
        body = build_body("Alice Test", [_entry()], 75)

        assert "75%" in body

    def test_threshold_is_rendered_through_format_percentage(self):
        body = build_body("Alice Test", [_entry()], 62.5)

        assert "62.5%" in body


class TestBuildBodyPerClassContent:
    def test_body_contains_the_course_and_class_name(self):
        body = build_body(
            "Alice Test", [_entry(course="Operating Systems", class_name="B")], 75
        )

        assert "Operating Systems" in body
        assert "Class B" in body

    def test_body_contains_the_percentage_and_present_over_total_counts(self):
        body = build_body(
            "Alice Test",
            [_entry(percentage=37.5, present_count=3, total_count=8)],
            75,
        )

        assert "37.5%" in body
        assert "3 of 8" in body

    def test_a_missing_course_falls_back_to_unknown_course(self):
        body = build_body("Alice Test", [_entry(course=None)], 75)

        assert "Unknown course" in body

    def test_a_missing_class_name_omits_the_class_qualifier(self):
        entry = _entry(course="Data Structures", class_name=None)

        body = build_body("Alice Test", [entry], 75)

        assert "Data Structures" in body
        assert "Class None" not in body

    def test_a_single_recorded_lecture_uses_the_singular_word(self):
        body = build_body(
            "Alice Test", [_entry(present_count=0, total_count=1)], 75,
        )

        assert "1 recorded lecture" in body
        assert "1 recorded lectures" not in body

    def test_multiple_recorded_lectures_use_the_plural_word(self):
        body = build_body(
            "Alice Test", [_entry(present_count=2, total_count=5)], 75,
        )

        assert "5 recorded lectures" in body


class TestBuildBodyMultipleClasses:
    def test_a_student_short_in_three_classes_sees_all_three_listed(self):
        entries = [
            _entry(course="Course A", class_name="A", percentage=12.5, present_count=1, total_count=8),
            _entry(course="Course B", class_name="B", percentage=25.5, present_count=2, total_count=8),
            _entry(course="Course C", class_name="C", percentage=37.5, present_count=3, total_count=8),
        ]

        body = build_body("Alice Test", entries, 75)

        assert "Course A" in body
        assert "Course B" in body
        assert "Course C" in body
        assert "12.5%" in body
        assert "25.5%" in body
        assert "37.5%" in body

    def test_singular_class_wording_for_exactly_one_short_class(self):
        body = build_body("Alice Test", [_entry()], 75)

        assert "following class:" in body

    def test_plural_classes_wording_for_more_than_one_short_class(self):
        entries = [_entry(class_name="A"), _entry(class_name="B", course="Other Course")]

        body = build_body("Alice Test", entries, 75)

        assert "following classes:" in body


class TestBuildBodyNeverImpliesAcademicConsequenceOrPrediction:
    """Rules 11-12: this is a recorded-percentage-against-a-bar report,
    never a prediction, a verdict, a rank, or a comparison with anyone
    else. A light textual check on the shipped wording -- not a general
    NLP classifier, but enough to catch a rewrite that quietly adds one of
    these back in.
    """

    FORBIDDEN_SUBSTRINGS = [
        "average",
        "rank",
        "predict",
        "risk",
        "grade",
        "penalty",
        "consequence",
        "expelled",
        "fail",
    ]

    def test_body_contains_none_of_the_forbidden_words(self):
        body = build_body(
            "Alice Test",
            [_entry(course="Data Structures", class_name="A", percentage=42.5)],
            75,
        ).lower()

        for word in self.FORBIDDEN_SUBSTRINGS:
            assert word not in body, f"body unexpectedly contains {word!r}"

    def test_body_does_not_mention_that_anybody_else_was_notified(self):
        body = build_body("Alice Test", [_entry()], 75).lower()

        assert "other student" not in body
        assert "classmate" not in body
