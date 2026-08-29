"""Tests for backend/attendance/pdf_export.py.

Spec contract under test (.claude/specs/21-attendance-export.md, "PDF
response", "Rules for implementation" 9-11 + 16 + 17 + 19, "Definition of
done" -> "Backend -- PDF" 17-28):
  - `render_report` returns bytes beginning `%PDF-`.
  - The title block carries the hierarchy names, the course and class, the
    effective range, the lecture count, who prepared it, and when.
  - Each student appears once with present, absent, total and percentage;
    `total` is that student's own record count, so two students in one
    class may legitimately differ.
  - Every percentage equals `attendance_percentage(present, total)`, and a
    student with nothing recorded prints an em dash rather than `0%`.
  - With a usable bar, exactly the students `meets_threshold` reports
    `False` for carry the marker; with no bar the report renders complete
    with no marker and no stated requirement.
  - The lecture index lists every session with its date, source, and
    present/total.
  - A report spanning pages repeats the student table's header and prints
    `Page N of M` on every page.
  - An empty range still renders a complete document saying so.
  - A name outside Latin-1 is substituted visibly and the document says
    it happened -- reportlab's own behaviour is to silently redraw such a
    run in ZapfDingbats (black squares), which is what `_renderable`
    exists to prevent. See the module docstring for the measurement.
  - `is_available()` is False and `render_report` raises
    ExportUnavailableError when `reportlab` cannot be imported.

No Flask, no pymongo, no database, and no clock: every test passes plain
dicts and an explicit `generated_on`. No real institutional data is used
-- every name and address is obviously synthetic.
"""

import base64
import builtins
import re
import zlib
from datetime import datetime, timezone

import pytest

from attendance.errors import ExportUnavailableError
from attendance.pdf_export import (
    NO_FIGURE,
    SUBSTITUTE_CHARACTER,
    is_available,
    render_report,
)
from attendance.threshold import attendance_percentage

GENERATED_ON = datetime(2026, 8, 29, tzinfo=timezone.utc)
JULY_1 = datetime(2026, 7, 1, tzinfo=timezone.utc)
AUGUST_29 = datetime(2026, 8, 29, tzinfo=timezone.utc)

CONTEXT = {
    "institute": "Sri Institute",
    "department": "Computer Engineering",
    "semester": "Semester 3",
}


# --- Reading a rendered document -------------------------------------
#
# reportlab writes each page's content stream Flate-compressed and then
# ASCII85-encoded, so both layers come off before the drawn strings are
# readable. Done here rather than by adding a PDF-reading dependency for
# the tests: three stdlib calls is a smaller price than a package.

# PDF string escapes: an octal byte, or one of the named single-character
# ones. Undoing these is what turns `Zo\353` back into `Zoë` -- without it
# every non-ASCII assertion below would be checking the escape sequence
# instead of the character the document actually shows.
_ESCAPE = re.compile(rb"\\(?:([0-7]{1,3})|(.))", re.S)
_NAMED_ESCAPES = {
    b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
}


def _unescape(raw):
    def replace(match):
        octal, character = match.groups()
        if octal is not None:
            return bytes([int(octal, 8) & 0xFF])
        return _NAMED_ESCAPES.get(character, character)

    return _ESCAPE.sub(replace, raw)


def pdf_text(payload):
    """Every string drawn in the document, joined, in page order."""
    chunks = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", payload, re.S):
        raw = match.group(1).strip()
        try:
            raw = base64.a85decode(raw, adobe=True)
        except ValueError:
            pass
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        chunks.extend(
            _unescape(token[1:-1])
            for token in re.findall(rb"\((?:\\.|[^\\()])*\)", raw)
        )

    # cp1252 is the encoding the standard fonts are written through, so
    # decoding back through it is what turns \267 into the middle dot the
    # document actually shows.
    return " ".join(chunk.decode("cp1252", "replace") for chunk in chunks)


def student(name, email="student@example.test"):
    return {"name": name, "email": email}


def lecture(day, present, total, source="photo"):
    return (
        {"date": datetime(2026, 7, day, tzinfo=timezone.utc), "source": source},
        {"present": present, "total": total},
    )


def render(**overrides):
    kwargs = {
        "class_name": "CS-3A",
        "course": "Data Structures",
        "context": CONTEXT,
        "prepared_by": "Dr A. Mehta",
        "generated_on": GENERATED_ON,
        "date_from": JULY_1,
        "date_to": AUGUST_29,
        "lectures": [lecture(1, 38, 42), lecture(3, 40, 42)],
        "standings": [(student("Aarti Desai"), 39, 42)],
        "threshold": 75.0,
    }
    kwargs.update(overrides)

    return render_report(**kwargs)


class TestDocument:
    def test_returns_pdf_bytes(self):
        payload = render()

        assert isinstance(payload, bytes)
        assert payload.startswith(b"%PDF-")

    def test_title_block_names_the_class_and_its_hierarchy(self):
        text = pdf_text(render())

        assert "ATTENDANCE REPORT" in text
        for name in CONTEXT.values():
            assert name in text
        assert "Data Structures" in text
        assert "CS-3A" in text

    def test_title_block_carries_the_range_and_lecture_count(self):
        text = pdf_text(render())

        assert "01 Jul 2026" in text
        assert "29 Aug 2026" in text
        assert "2 lectures recorded" in text

    def test_title_block_carries_its_provenance(self):
        text = pdf_text(render())

        assert "Prepared by Dr A. Mehta" in text
        assert "Generated 29 Aug 2026" in text

    def test_an_unbounded_range_says_so_rather_than_naming_half_of_one(self):
        text = pdf_text(render(date_from=None, date_to=None))

        assert "All recorded attendance" in text

    def test_one_lecture_is_not_pluralised(self):
        text = pdf_text(render(lectures=[lecture(1, 38, 42)]))

        assert "1 lecture recorded" in text

    def test_a_missing_hierarchy_name_is_skipped_rather_than_printed_as_none(self):
        text = pdf_text(render(context={"institute": "Sri Institute"}))

        assert "Sri Institute" in text
        assert "None" not in text


class TestStudentTable:
    def test_lists_each_student_once_with_their_own_figures(self):
        text = pdf_text(
            render(
                standings=[
                    (student("Aarti Desai"), 39, 42),
                    (student("Rohit Nair"), 28, 42),
                ]
            )
        )

        assert "Aarti Desai 39 3 42 92.9%" in text
        assert "Rohit Nair 28 14 42 66.7%" in text

    def test_totals_are_per_student_not_the_lecture_count(self):
        # A student enrolled late has fewer records, and dividing by the
        # class's session count would mark them down for lectures held
        # before they were on the roster.
        text = pdf_text(
            render(
                standings=[
                    (student("Aarti Desai"), 39, 42),
                    (student("Late Joiner"), 9, 10),
                ]
            )
        )

        assert "Aarti Desai 39 3 42" in text
        assert "Late Joiner 9 1 10 90%" in text

    def test_every_percentage_matches_the_shared_helper(self):
        for present, total in ((39, 42), (28, 42), (0, 5), (5, 5), (9, 10)):
            expected = attendance_percentage(present, total)
            text = pdf_text(render(standings=[(student("Solo Student"), present, total)]))

            assert f"{expected:g}%" in text

    def test_a_student_with_nothing_recorded_shows_a_dash_not_zero_percent(self):
        text = pdf_text(render(standings=[(student("New Student"), 0, 0)]))

        assert f"New Student 0 0 0 {NO_FIGURE}" in text
        assert "0%" not in text

    def test_the_header_row_is_present(self):
        text = pdf_text(render())

        assert "Student Present Absent Total Attendance" in text


class TestThresholdMarker:
    def test_marks_only_the_students_below_the_bar(self):
        text = pdf_text(
            render(
                standings=[
                    (student("Above Bar"), 39, 42),
                    (student("Below Bar"), 28, 42),
                ]
            )
        )

        assert "Below Bar 28 14 42 66.7% below 75%" in text
        assert "Above Bar 39 3 42 92.9% below" not in text

    def test_a_student_exactly_on_the_bar_is_not_marked(self):
        # meets_threshold is "at or above", the same comparison the sweep
        # makes, so the screen, the email and this report cannot disagree
        # about the student sitting exactly on it.
        text = pdf_text(render(standings=[(student("Exactly On"), 3, 4)]))

        assert "Exactly On 3 1 4 75%" in text
        assert "below" not in text

    def test_a_student_with_nothing_recorded_is_not_marked(self):
        # attendance_percentage is None there, and meets_threshold(None)
        # is None -- which is not False.
        text = pdf_text(render(standings=[(student("New Student"), 0, 0)]))

        assert "below" not in text

    def test_states_the_bar_in_the_title_block(self):
        assert "required attendance 75%" in pdf_text(render())

    def test_no_bar_configured_renders_a_complete_report_without_one(self):
        text = pdf_text(
            render(
                threshold=None,
                standings=[
                    (student("Aarti Desai"), 39, 42),
                    (student("Rohit Nair"), 28, 42),
                ],
            )
        )

        assert "Aarti Desai 39 3 42 92.9%" in text
        assert "Rohit Nair 28 14 42 66.7%" in text
        assert "below" not in text
        assert "required attendance" not in text


class TestLectureIndex:
    def test_lists_every_lecture_with_its_source_and_counts(self):
        text = pdf_text(
            render(lectures=[lecture(1, 38, 42), lecture(3, 40, 42, source="video")])
        )

        assert "Lecture index" in text
        assert "2026-07-01 photo 38 / 42" in text
        assert "2026-07-03 video 40 / 42" in text


class TestEmptyRange:
    def test_renders_a_complete_document_saying_nothing_was_recorded(self):
        payload = render(lectures=[], standings=[])
        text = pdf_text(payload)

        assert payload.startswith(b"%PDF-")
        assert "ATTENDANCE REPORT" in text
        assert "No attendance has been recorded" in text
        assert "0 lectures recorded" in text


class TestPagination:
    def test_repeats_the_header_and_numbers_every_page(self):
        standings = [
            (student(f"Filler Student {index:02d}"), 30, 42) for index in range(60)
        ]
        text = pdf_text(render(standings=standings))

        pages = re.findall(r"Page (\d+) of (\d+)", text)
        assert len(pages) > 1, "expected a document spanning several pages"

        total = pages[0][1]
        assert [number for number, _ in pages] == [
            str(index + 1) for index in range(len(pages))
        ]
        assert all(reported == total for _, reported in pages)
        assert int(total) == len(pages)

        # repeatRows=1 repeats the header on every page the *table* spans,
        # which is not every page of the document -- the lecture index
        # gets one of its own here. So the property is per student page.
        student_pages = [
            page
            for page in re.split(r"Page \d+ of \d+", text)
            if "Filler Student" in page
        ]
        assert len(student_pages) > 1, "expected the table itself to span pages"
        assert all(
            "Student Present Absent Total Attendance" in page
            for page in student_pages
        )


class TestUnrenderableNames:
    """reportlab does not raise on text outside Latin-1 -- it silently
    redraws that run in ZapfDingbats, where every substituted character
    is a filled black square. A report naming a student as a row of
    squares is wrong, and one produced without warning is worse.
    """

    DEVANAGARI = "संरचना Kulkarni"

    def test_substitutes_visibly_rather_than_drawing_black_squares(self):
        text = pdf_text(render(standings=[(student(self.DEVANAGARI), 39, 42)]))

        assert SUBSTITUTE_CHARACTER * 6 in text
        # The ASCII part of the same name survives: the substitution is
        # per character, not per cell.
        assert "Kulkarni" in text

    def test_says_that_it_happened_and_points_at_the_csv(self):
        text = pdf_text(render(standings=[(student(self.DEVANAGARI), 39, 42)]))

        assert "cannot display" in text
        assert "CSV export" in text

    def test_a_latin1_name_is_set_as_itself_and_triggers_no_note(self):
        text = pdf_text(render(standings=[(student("Zoë Müller"), 39, 42)]))

        assert "Zoë Müller" in text
        assert SUBSTITUTE_CHARACTER not in text
        assert "cannot display" not in text

    def test_an_ordinary_report_carries_no_note(self):
        assert "cannot display" not in pdf_text(render())


class TestMarkupIsNeverInterpreted:
    """reportlab's Paragraph parses a small HTML-like dialect, and the
    title block is built from admin-typed text: the class name, the
    course, the three hierarchy names, and the faculty member's name.

    Measured unescaped against reportlab 4.5.1 on 2026-08-29, an `<img>`
    in any of them made the rendering process fetch an http:// URL or
    open a file:// path, and a stray `<` raised ValueError out of the
    parser. `_paragraph` escapes at the Paragraph boundary; these tests
    are what stop that escaping being removed as decoration.

    The student table is checked too, for the opposite reason: its cells
    are plain strings in a Table, which reportlab draws literally rather
    than parsing, so a name there must keep its angle brackets. Escaping
    it would be a bug, not extra safety.
    """

    # A local address, never fetched -- if the escaping regresses, this
    # test fails rather than a request being attempted. Port 9 is discard.
    SSRF = '<img src="http://127.0.0.1:9/probe"/>'
    LOCAL_FILE = '<img src="file:///etc/passwd"/>'
    PARAGRAPH_FIELDS = ("class_name", "course", "prepared_by")

    @staticmethod
    def _drawn(payload, text):
        """Whether `payload` was drawn, ignoring where it was split.

        An escaped entity becomes its own text run, so `<img …/>` reaches
        the content stream as three runs and `pdf_text` joins them with
        spaces. Comparing with all whitespace removed asserts the
        characters without asserting reportlab's line-breaking.
        """
        squeeze = lambda value: "".join(value.split())

        return squeeze(payload) in squeeze(text)

    @pytest.mark.parametrize(
        "payload",
        [SSRF, LOCAL_FILE, "<b>BOLD</b>", "<script>x</script>", "Section <A>",
         "<b>unclosed", "Smith & Sons"],
    )
    @pytest.mark.parametrize("field", PARAGRAPH_FIELDS)
    def test_a_title_block_field_is_rendered_literally(self, field, payload):
        # No exception escapes, and the text is drawn as typed rather
        # than consumed as a tag.
        assert self._drawn(payload, pdf_text(render(**{field: payload})))

    @pytest.mark.parametrize("payload", [SSRF, LOCAL_FILE, "Section <A>"])
    def test_a_hierarchy_name_is_rendered_literally(self, payload):
        text = pdf_text(
            render(context={"institute": payload, "department": "D", "semester": "S"})
        )

        assert self._drawn(payload, text)

    def test_a_student_name_keeps_its_angle_brackets(self):
        # The table is not a markup context, so this cell must NOT be
        # escaped -- `&lt;` printed on a register would be the bug.
        text = pdf_text(render(standings=[(student("Ada <A> & Co"), 39, 42)]))

        assert "Ada <A> & Co" in text
        assert "&lt;" not in text
        assert "&amp;" not in text

    def test_markup_does_not_change_how_the_document_is_drawn(self):
        # `<b>` must not reach the parser as emphasis: a report that is
        # printed and signed should not be reformattable through a name.
        plain = render(class_name="CS-3A")
        marked = render(class_name="<b>CS-3A</b>")

        assert self._drawn("<b>CS-3A</b>", pdf_text(marked))
        # Same fonts in use either way -- no bold face pulled in.
        assert marked.count(b"/BaseFont") == plain.count(b"/BaseFont")


class TestLibraryAvailability:
    @staticmethod
    def _hide_reportlab(monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "reportlab" or name.startswith("reportlab."):
                raise ImportError("No module named 'reportlab'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

    def test_is_available_is_true_when_the_library_imports(self):
        assert is_available() is True

    def test_is_available_is_false_when_the_library_is_missing(self, monkeypatch):
        self._hide_reportlab(monkeypatch)

        assert is_available() is False

    def test_render_raises_export_unavailable_when_the_library_is_missing(
        self, monkeypatch
    ):
        self._hide_reportlab(monkeypatch)

        with pytest.raises(ExportUnavailableError):
            render()
