"""The wording of a low-attendance email.

Pure string construction. No database, no SMTP, no Flask, and no Mongo
document reaches this module -- callers hand it a student's name and a
list of plain dicts. That is deliberate: a function that never sees a
document cannot leak a field off one, so "the body contains only this
student's own data" is a property of the interface rather than a rule the
caller has to keep.

It is also what makes the wording testable. The body is assertable
without a transport, a socket, or a credential.

Two things this text must never become:

  - **A prediction.** It reports a recorded percentage against a
    configured bar. It does not estimate, forecast, score, or rank, and
    it must not imply that a model produced it -- there is no model (see
    CLAUDE.md).
  - **A verdict.** It does not threaten a consequence, name a penalty, or
    tell the student what will happen to them. Attendance policy belongs
    to the institute, not to an automated mailer that only knows one
    number.
"""

__all__ = ["build_subject", "build_body"]

# Kept out of the subject line on purpose: a subject is visible in a lock
# screen preview and over the shoulder of whoever is next to the student.
# The count is not sensitive; the figures and the class names are.
SUBJECT_PREFIX = "AutoAttend"


def format_percentage(value):
    """Render a percentage the way the student dashboard does.

    Trailing ".0" is dropped so a threshold of 75 reads as "75%" rather
    than "75.0%", while a real 62.5 keeps its half. The rounding itself
    happens upstream (attendance/threshold.py::attendance_percentage) so
    that the figure emailed and the figure on screen are the same number,
    not two roundings of one.
    """
    rounded = round(float(value), 1)

    if rounded == int(rounded):
        return str(int(rounded))

    return str(rounded)


def build_subject(short_class_count):
    """One subject line, singular or plural, carrying no figures."""
    if short_class_count == 1:
        return f"{SUBJECT_PREFIX}: your attendance is low in 1 class"

    return f"{SUBJECT_PREFIX}: your attendance is low in {short_class_count} classes"


def _describe(entry):
    """One class, as two indented lines.

    `entry` is a plain dict: `course`, `class_name`, `percentage`,
    `present_count`, `total_count`.

    The counts are spelled out beside the percentage rather than left
    implicit. "62.5%" invites the question "out of how many?", and a
    student who has attended 5 of 8 lectures is in a very different
    position from one who has attended 50 of 80 -- the raw pair is what
    lets them judge whether the figure can still be recovered.
    """
    course = entry.get("course") or "Unknown course"
    class_name = entry.get("class_name")
    # "Data Structures (Class A)" rather than "Data Structures (A)": a
    # bare letter in brackets reads as a grade, which is the one thing
    # this project must never appear to be sending.
    heading = f"{course} (Class {class_name})" if class_name else course

    percentage = format_percentage(entry["percentage"])
    present = entry["present_count"]
    total = entry["total_count"]
    lectures = "lecture" if total == 1 else "lectures"

    return (
        f"  - {heading}\n"
        f"    {percentage}% - you were present for {present} of {total} "
        f"recorded {lectures}"
    )


def build_body(student_name, entries, threshold):
    """The full plain-text body for one student.

    `entries` are that student's below-threshold classes, and nothing
    else: no roster, no class average, no rank, no comparison with
    another student, and no mention that anybody else was mailed.
    """
    greeting = f"Hello {student_name}," if student_name else "Hello,"
    bar = format_percentage(threshold)
    classes = "class" if len(entries) == 1 else "classes"

    lines = [
        greeting,
        "",
        "This is an automatic notice from AutoAttend about your recorded",
        "attendance.",
        "",
        f"Your attendance is below the required {bar}% in the following {classes}:",
        "",
    ]
    lines.extend(_describe(entry) for entry in entries)
    lines.extend(
        [
            "",
            "These figures count only the lectures that have been recorded for",
            "you in each class so far. You can see the full lecture-by-lecture",
            "record on your AutoAttend student dashboard.",
            "",
            "If you think a lecture has been recorded incorrectly, please speak",
            "to the faculty member for that class -- they can review and correct",
            "the register.",
            "",
            "This message was sent automatically. Please do not reply to it.",
        ]
    )

    return "\n".join(lines) + "\n"
