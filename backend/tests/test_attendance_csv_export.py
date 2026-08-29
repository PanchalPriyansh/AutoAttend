"""Tests for backend/attendance/csv_export.py.

Spec contract under test (.claude/specs/21-attendance-export.md, "CSV
response", "Rules for implementation" 14 + 15 + 19, "Definition of done"
-> "Backend -- CSV" 11-16):
  - The header row is exactly
    `date,student_name,student_email,status,marked_by,source`.
  - One row per `(session, record, student)` triple, in the order given;
    absences are written as their own rows, never omitted.
  - Every row is built from an allow-list, so a field added to `users` or
    to `attendance_records` cannot reach the file by existing.
  - A cell beginning `=`, `+`, `-`, `@`, a tab, or a CR is written with a
    leading apostrophe, so a spreadsheet renders it as text rather than
    executing it.
  - Lines end `\r\n` and the bytes carry the UTF-8 BOM, so Excel on
    Windows reads a non-ASCII roster correctly.
  - No triples yields the header row alone -- a complete answer, not an
    error.
  - No Flask, no pymongo, no database, and no clock: every test below
    passes plain dicts and needs no fixtures.

The formula-injection cases are a security control, not a formatting
nicety; see the module docstring. No real institutional or biometric data
is used -- every name and address here is obviously synthetic.
"""

from datetime import datetime, timezone

from attendance.csv_export import (
    ENCODING,
    FORMULA_GUARD,
    FORMULA_LEADS,
    HEADER,
    LINE_TERMINATOR,
    render_register,
)

JULY_1 = datetime(2026, 7, 1, tzinfo=timezone.utc)
JULY_3 = datetime(2026, 7, 3, tzinfo=timezone.utc)

EXPECTED_HEADER_LINE = "date,student_name,student_email,status,marked_by,source"


def make_session(date=JULY_1, source="photo"):
    return {"date": date, "source": source}


def make_record(status="present", marked_by="recognition"):
    return {"status": status, "marked_by": marked_by}


def make_student(name="Aarti Desai", email="aarti.desai@example.test"):
    return {"name": name, "email": email}


def decoded_lines(payload):
    """The rendered file as text lines, BOM and trailing blank removed."""
    return payload.decode(ENCODING).rstrip(LINE_TERMINATOR).split(LINE_TERMINATOR)


class TestHeader:
    def test_header_is_the_documented_column_order(self):
        assert decoded_lines(render_register([]))[0] == EXPECTED_HEADER_LINE

    def test_header_constant_and_written_header_cannot_drift(self):
        assert ",".join(HEADER) == EXPECTED_HEADER_LINE

    def test_no_triples_yields_the_header_alone(self):
        assert decoded_lines(render_register([])) == [EXPECTED_HEADER_LINE]


class TestRows:
    def test_writes_one_row_per_triple_in_the_order_given(self):
        triples = [
            (make_session(JULY_1), make_record(), make_student("Aarti Desai")),
            (make_session(JULY_3), make_record(), make_student("Rohit Nair")),
        ]

        rows = decoded_lines(render_register(triples))[1:]

        assert rows[0].startswith("2026-07-01,Aarti Desai,")
        assert rows[1].startswith("2026-07-03,Rohit Nair,")

    def test_writes_an_absence_as_its_own_row(self):
        # An absence left out would be indistinguishable from a student
        # nobody considered.
        triples = [
            (
                make_session(),
                make_record(status="absent", marked_by="faculty"),
                make_student("Rohit Nair", "rohit.nair@example.test"),
            )
        ]

        assert decoded_lines(render_register(triples))[1] == (
            "2026-07-01,Rohit Nair,rohit.nair@example.test,absent,faculty,photo"
        )

    def test_carries_the_sessions_source_on_every_row_of_that_lecture(self):
        session = make_session(source="video")
        triples = [
            (session, make_record(), make_student("Aarti Desai")),
            (session, make_record(), make_student("Rohit Nair")),
        ]

        for row in decoded_lines(render_register(triples))[1:]:
            assert row.endswith(",video")

    def test_ignores_fields_outside_the_allow_list(self):
        # A new field on either document must not reach the file.
        session = {**make_session(), "taken_by": "should-not-appear"}
        record = {**make_record(), "_id": "should-not-appear"}
        student = {**make_student(), "password_hash": "should-not-appear"}

        payload = render_register([(session, record, student)]).decode(ENCODING)

        assert "should-not-appear" not in payload

    def test_writes_an_empty_cell_for_a_missing_value(self):
        triples = [({"date": None, "source": None}, {}, {})]

        assert decoded_lines(render_register(triples))[1] == ",,,,,"


class TestFormulaNeutralisation:
    def test_every_formula_lead_is_guarded(self):
        for lead in FORMULA_LEADS:
            triples = [(make_session(), make_record(), make_student(f"{lead}CMD"))]

            payload = render_register(triples).decode(ENCODING)

            assert f"{FORMULA_GUARD}{lead}CMD" in payload

    def test_a_formula_shaped_name_is_not_left_executable(self):
        name = '=HYPERLINK("http://example.test","click")'
        triples = [(make_session(), make_record(), make_student(name))]

        payload = render_register(triples).decode(ENCODING)

        # Quoted by csv.writer because it contains commas and quotes, so
        # the guard is what is checked, not the surrounding quoting.
        assert f'"{FORMULA_GUARD}=HYPERLINK' in payload

    def test_a_formula_shaped_email_is_guarded_too(self):
        # The rule is per cell, not per column: a subset is what a column
        # added later falls outside of.
        student = make_student(email="@example.test")
        triples = [(make_session(), make_record(), student)]

        assert f"{FORMULA_GUARD}@example.test" in render_register(triples).decode(
            ENCODING
        )

    def test_an_ordinary_name_is_left_alone(self):
        triples = [(make_session(), make_record(), make_student("Aarti Desai"))]

        payload = render_register(triples).decode(ENCODING)

        assert FORMULA_GUARD not in payload


class TestEncodingAndLineEndings:
    def test_returns_bytes_carrying_the_utf8_bom(self):
        payload = render_register([])

        assert isinstance(payload, bytes)
        assert payload.startswith(b"\xef\xbb\xbf")

    def test_lines_end_with_crlf(self):
        triples = [(make_session(), make_record(), make_student())]

        payload = render_register(triples)

        assert payload.endswith(b"\r\n")
        assert payload.count(b"\r\n") == 2

    def test_a_non_ascii_name_survives_the_round_trip(self):
        student = make_student("संरचना Desai")
        triples = [(make_session(), make_record(), student)]

        assert "संरचना Desai" in render_register(triples).decode(ENCODING)
