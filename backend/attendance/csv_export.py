"""Writing the attendance register as CSV.

Pure -- no Flask, no pymongo, no database, and no clock. It is handed
rows that have already been resolved and authorized, and it hands back
bytes. Mirrors the way notifications/reporting.py and
database/reporting.py keep formatting out of the module that fetches.

It reads stored documents directly, the way attendance/serializers.py
does, and for the same reason: the field names of a record and a student
are one thing this project already knows in one place per feature, and a
service that pre-flattened rows into the CSV's column order would be a
service that knows about the CSV.

**The shape is the register, not a summary**: one row per student per
lecture, absences explicitly written rather than left out. A student
missing from a lecture's rows would be indistinguishable from a student
nobody considered, which is the one thing an attendance record must never
be ambiguous about.

Two things here are defences rather than formatting, and both are easy to
"tidy" into a bug:

  - **The apostrophe in `_neutralise`.** A spreadsheet executes a cell
    beginning `=`, `+`, `-`, or `@` as a formula on open. A student name
    is admin-provisioned, but it is still text a person typed, and a CSV
    is a file somebody else double-clicks.
  - **The `utf-8-sig` encoding.** Excel on Windows reads a plain UTF-8
    file as the system code page and mangles every non-ASCII name in it.
    The BOM is what stops that, and it is why this returns bytes rather
    than a string -- an encoding decision made here cannot be undone by a
    caller that just wanted text.
"""

import csv
import io

from attendance.serializers import DATE_FORMAT

# The column order, and the header row. One tuple so the two cannot
# drift: a column added to the header without a value written under it
# would shift every field after it, silently, on every row.
HEADER = ("date", "student_name", "student_email", "status", "marked_by", "source")

# What a spreadsheet reads as the start of a formula rather than as text.
# The two whitespace characters are here because a leading tab or CR is
# stripped before the cell is parsed, which puts whatever follows them
# back in first position.
FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")

# Prefixed to a formula-shaped cell. A spreadsheet consumes it and shows
# the text; a plain CSV reader sees one extra character, which is the
# right trade against executing the cell.
FORMULA_GUARD = "'"

# RFC 4180 says CRLF, and Excel agrees. csv.writer defaults to "\r\n"
# already, but it is stated rather than inherited -- it is asserted in
# the tests, and a default is a poor place for something asserted.
LINE_TERMINATOR = "\r\n"

# Carries the byte-order mark Excel needs. See the module docstring.
ENCODING = "utf-8-sig"


def _neutralise(value):
    """Render one cell as text a spreadsheet will not execute.

    Applied to every cell rather than to the two that can realistically
    trigger it. A hand-picked subset is exactly what a column added later
    falls outside of, and the cost of the general rule is one comparison
    per cell.
    """
    if value is None:
        return ""

    text = str(value)
    if text.startswith(FORMULA_LEADS):
        return f"{FORMULA_GUARD}{text}"

    return text


def _row(session, record, student):
    """One register line, built from a literal allow-list.

    Never by copying a document and dropping keys: a new field on `users`
    or on `attendance_records` must not be able to reach a file by simply
    existing.
    """
    date = session.get("date")

    return (
        date.strftime(DATE_FORMAT) if date is not None else "",
        student.get("name"),
        student.get("email"),
        record.get("status"),
        record.get("marked_by"),
        session.get("source"),
    )


def render_register(triples):
    """The CSV for one class over one range, as bytes.

    `triples` is an iterable of `(session, record, student)` in the order
    the rows should appear -- attendance/service.py::export_records sorts
    them, because it is the module that knows the register runs forwards
    while the screen runs backwards.

    An empty `triples` yields the header row and nothing else, which is a
    complete answer: it says no attendance was recorded in that range,
    and it is a file a faculty member can keep as evidence of that.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator=LINE_TERMINATOR)

    writer.writerow(HEADER)
    for session, record, student in triples:
        writer.writerow([_neutralise(cell) for cell in _row(session, record, student)])

    return buffer.getvalue().encode(ENCODING)
