"""The project's only PDF writer.

This is the single place `reportlab` may be imported, and the import is
deferred into the functions that need it rather than done at module
scope -- the same shape recognition/encoder.py and recognition/frames.py
use for their optional libraries. Call `is_available()` first and raise
ExportUnavailableError (503) when it returns False.

Nothing here touches MongoDB, Flask, or a clock: resolved rows and an
already-decided generation date go in, bytes come out. The threshold is
passed in for the same reason -- it is request-scoped configuration, and
routes/attendance.py already hands `current_threshold()` to the student
serializers exactly this way.

**The percentages are not computed here.** `attendance_percentage` and
`meets_threshold` are imported from attendance/threshold.py and called
unwrapped, so a figure printed in a report is the same figure the student
sees on their own dashboard and the same one the low-attendance sweep
mails about. A local rounding rule here would be a second source of
truth, and the first thing it would do is disagree.

Nothing in this document is a prediction, a score, or a rank. The `below
N%` marker states that a recorded percentage is under a configured one,
and stops there.

--- What the standard fonts can and cannot set -------------------------

Measured against reportlab 4.5.1 on 2026-08-29, because the answer is not
what it looks like:

  - Latin-1 text sets correctly. `Zoë Müller` is written through
    WinAnsiEncoding as `Zo\\353 M\\374ller` and renders as itself.
  - Text outside Latin-1 does **not** raise, and does not come out blank.
    reportlab silently switches that run to **ZapfDingbats**, where the
    substituted byte `n` is a filled black square -- so a Devanagari,
    CJK, or emoji name renders as a row of ■, per character, with the
    ASCII parts of the same name intact. `stringWidth` returns a plausible
    number, so nothing downstream notices either.

A report naming a student as ■■■■■■ is a wrong report, and one produced
without a word of warning is worse. So `_renderable` below does the
substitution itself, deterministically, to `?`, and the document says at
the foot that it happened and points at the CSV export -- which is UTF-8
and carries those names in full. Bundling a Unicode TTF would fix it
properly and is deliberately out of scope for 21-attendance-export.
"""

import logging
from io import BytesIO
from types import SimpleNamespace
from xml.sax.saxutils import escape as xml_escape

from attendance.errors import ExportUnavailableError
from attendance.serializers import DATE_FORMAT
from attendance.threshold import attendance_percentage, meets_threshold

logger = logging.getLogger(__name__)

# What reportlab's standard (Type 1) fonts actually cover: PDF's
# WinAnsiEncoding is cp1252. Anything this codec rejects is what would
# otherwise be silently redrawn in ZapfDingbats -- see the module
# docstring.
STANDARD_FONT_ENCODING = "cp1252"

# What an unrenderable character becomes. A visible, obviously-wrong
# character beats a black square that looks like a design choice.
SUBSTITUTE_CHARACTER = "?"

# Shown once, at the foot of the document, when any substitution happened.
SUBSTITUTION_NOTE = (
    "Some names contain characters this report's font cannot display and are "
    "shown with '%s'. The CSV export carries them in full."
) % SUBSTITUTE_CHARACTER

# How a date is written in the body of the report. Long form, because a
# printed document is read by people rather than parsed, and the filename
# already carries the sortable form.
LONG_DATE_FORMAT = "%d %b %Y"

# What the attendance column shows for a student with nothing recorded.
# `attendance_percentage` returns None there, and 0% would tell them the
# exact opposite of the truth.
NO_FIGURE = "—"

TITLE = "ATTENDANCE REPORT"
STUDENT_TABLE_HEADER = ("Student", "Present", "Absent", "Total", "Attendance")
LECTURE_TABLE_HEADER = ("Date", "Source", "Present")
MARKER_COLUMN_HEADER = ""
EMPTY_RANGE_NOTE = "No attendance has been recorded for this class in this range."

# What a caller is told when the writer cannot run. Declared here and
# imported by routes/attendance.py, which raises the same error on the
# fast path -- the route checks is_available() before doing a term's
# worth of queries, and _require_reportlab below is the backstop for any
# caller that skipped that check. Two guards, deliberately; two hand-
# synced copies of the sentence, not deliberately.
UNAVAILABLE_MESSAGE = (
    "PDF export is unavailable on this server because its document "
    "library is not installed. The CSV export is unaffected."
)


def _reportlab():
    """Import reportlab on first use, or raise ImportError.

    Callers gate on is_available() rather than catching this. Bundled
    into one namespace so the rest of the module reads as ordinary code
    instead of threading a dozen names through every helper.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    return SimpleNamespace(
        colors=colors,
        A4=A4,
        ParagraphStyle=ParagraphStyle,
        mm=mm,
        canvas=canvas,
        Paragraph=Paragraph,
        SimpleDocTemplate=SimpleDocTemplate,
        Spacer=Spacer,
        Table=Table,
        TableStyle=TableStyle,
    )


def is_available():
    """Whether a PDF can be written in this process.

    Checked at request time rather than cached at import, mirroring
    recognition/encoder.py: a machine that gains the dependency should
    not need the Flask process restarted to notice, and Python memoises
    the import itself after the first success.
    """
    try:
        _reportlab()
    except ImportError:
        logger.warning(
            "reportlab is not importable; the PDF export endpoint will "
            "report 503 until it is installed"
        )
        return False

    return True


def _require_reportlab():
    try:
        return _reportlab()
    except ImportError as exc:
        raise ExportUnavailableError(UNAVAILABLE_MESSAGE) from exc


# --- Text the standard fonts can actually set --------------------------


def _renderable(value):
    """`(text, substituted)` -- the text this document can set, and
    whether anything had to be replaced to get there.

    Per character rather than per string, so `संरचना Desai` keeps its
    surname instead of losing the whole cell.
    """
    text = "" if value is None else str(value)

    try:
        text.encode(STANDARD_FONT_ENCODING)
    except UnicodeEncodeError:
        pass
    else:
        return text, False

    safe = []
    for character in text:
        try:
            character.encode(STANDARD_FONT_ENCODING)
        except UnicodeEncodeError:
            safe.append(SUBSTITUTE_CHARACTER)
        else:
            safe.append(character)

    return "".join(safe), True


class _Text:
    """Collects whether any cell in the document needed substituting.

    A flag threaded through every helper would be noise; this is the one
    piece of state the builders below share.
    """

    def __init__(self):
        self.substituted = False

    def __call__(self, value):
        text, substituted = _renderable(value)
        self.substituted = self.substituted or substituted

        return text


# --- Page furniture ----------------------------------------------------


def _numbered_canvas(rl):
    """A canvas that can print "Page N of M".

    reportlab does not know M while it is laying out page 1, so pages are
    buffered and the footer stamped on the way out. Built here rather
    than at module scope because the base class only exists once the
    lazy import has run.
    """

    class NumberedCanvas(rl.canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._draw_page_number(total)
                super().showPage()
            super().save()

        def _draw_page_number(self, total):
            self.setFont("Helvetica", 8)
            self.setFillColor(rl.colors.HexColor("#666666"))
            self.drawRightString(
                self._pagesize[0] - 18 * rl.mm,
                12 * rl.mm,
                f"Page {self._pageNumber} of {total}",
            )

    return NumberedCanvas


def _paragraph(rl, value, style):
    """Build a Paragraph from text that must never be read as markup.

    **This escaping is a security control, not formatting.** A Paragraph
    parses a small HTML-like dialect, and several of the strings that
    reach one here are typed by an admin: the class name, the course
    name, the three hierarchy names, and the faculty member's own name.
    Measured against reportlab 4.5.1 on 2026-08-29, unescaped:

      - `<img src="http://host/path"/>` in any of them makes *this
        process* fetch that URL while rendering the page. That is a
        server-side request forgery reachable from a field that looks
        like an ordinary name.
      - `<img src="file:///path"/>` makes it open that path on the
        server's disk.
      - A stray `<` -- `Section <A>` is a plausible class name -- raises
        ValueError inside the parser. No blueprint handler covers that,
        so it is a 500 rather than this feature's JSON error contract,
        and it recurs on every export of that class until somebody edits
        the name.
      - `<b>...</b>` silently reformats a document meant to be printed
        and signed.

    Escaping happens **here, at the Paragraph boundary, and deliberately
    not inside `_renderable`.** Every table cell goes through that
    function too, and a Table cell holding a plain string is drawn as
    literal text rather than parsed -- so escaping there would print a
    student called `Smith & Sons` as `Smith &amp; Sons`, and would fix
    nothing, because the table was never the vector.
    """
    return rl.Paragraph(xml_escape(value), style)


def _styles(rl):
    base = rl.ParagraphStyle(
        "body", fontName="Helvetica", fontSize=9.5, leading=13, spaceAfter=0
    )

    return SimpleNamespace(
        title=rl.ParagraphStyle(
            "title", parent=base, fontName="Helvetica-Bold", fontSize=15, leading=19
        ),
        context=rl.ParagraphStyle(
            "context",
            parent=base,
            fontSize=10,
            leading=14,
            textColor=rl.colors.HexColor("#333333"),
        ),
        meta=rl.ParagraphStyle(
            "meta",
            parent=base,
            fontSize=8.5,
            leading=12,
            textColor=rl.colors.HexColor("#666666"),
        ),
        section=rl.ParagraphStyle(
            "section",
            parent=base,
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=14,
            spaceBefore=6,
        ),
        note=base,
    )


# --- Title block -------------------------------------------------------


def _hierarchy_line(context, text):
    """Institute, department and semester, skipping any the hierarchy
    could not resolve rather than printing "None".
    """
    names = [context.get(level) for level in ("institute", "department", "semester")]

    return " · ".join(text(name) for name in names if name)


def _range_line(date_from, date_to, lecture_count, text):
    if date_from is None and date_to is None:
        covered = "All recorded attendance"
    elif date_from is not None and date_to is not None:
        covered = (
            f"{date_from.strftime(LONG_DATE_FORMAT)} – "
            f"{date_to.strftime(LONG_DATE_FORMAT)}"
        )
    elif date_from is not None:
        covered = f"From {date_from.strftime(LONG_DATE_FORMAT)}"
    else:
        covered = f"Up to {date_to.strftime(LONG_DATE_FORMAT)}"

    plural = "" if lecture_count == 1 else "s"

    return text(f"{covered} · {lecture_count} lecture{plural} recorded")


def _title_block(rl, styles, *, class_name, course, context, prepared_by,
                 generated_on, date_from, date_to, lecture_count, threshold, text):
    flowables = [_paragraph(rl, TITLE, styles.title)]

    hierarchy = _hierarchy_line(context, text)
    if hierarchy:
        flowables.append(_paragraph(rl, hierarchy, styles.context))

    heading = " — ".join(
        text(part) for part in (course, class_name) if part
    )
    flowables.append(_paragraph(rl, heading, styles.context))
    flowables.append(
        _paragraph(
            rl, _range_line(date_from, date_to, lecture_count, text), styles.meta
        )
    )

    provenance = f"Generated {generated_on.strftime(LONG_DATE_FORMAT)}"
    if prepared_by:
        provenance = f"Prepared by {text(prepared_by)} · {provenance}"
    if threshold is not None:
        provenance += f" · required attendance {_percentage(threshold)}"
    flowables.append(_paragraph(rl, provenance, styles.meta))

    flowables.append(rl.Spacer(1, 10))

    return flowables


# --- Tables ------------------------------------------------------------


def _percentage(value):
    """A percentage as it is printed. `%g` so a whole bar reads "75%"
    rather than "75.0%", while 92.9 keeps its decimal.
    """
    return f"{value:g}%"


def _student_rows(standings, threshold, text):
    """One row per student, plus whether any marker was actually set.

    `standings` is `(student, present, total)` per student, already in
    the order the report should list them. `total` is that student's own
    record count -- never the lecture count -- so two rows may legitimately
    show different totals.
    """
    rows = []
    marked = False

    for student, present, total in standings:
        percentage = attendance_percentage(present, total)
        below = meets_threshold(percentage, threshold) is False
        marked = marked or below

        rows.append(
            [
                text(student.get("name")),
                str(present),
                str(total - present),
                str(total),
                NO_FIGURE if percentage is None else _percentage(percentage),
                f"below {_percentage(threshold)}" if below else "",
            ]
        )

    return rows, marked


def _table_style(rl, *, align_from_column):
    return rl.TableStyle(
        [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (-1, 0), rl.colors.HexColor("#222222")),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, rl.colors.HexColor("#999999")),
            ("LINEBELOW", (0, 1), (-1, -2), 0.25, rl.colors.HexColor("#DDDDDD")),
            ("ALIGN", (align_from_column, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]
    )


def _student_table(rl, standings, threshold, text, usable_width):
    rows, marked = _student_rows(standings, threshold, text)

    # The marker column is dropped entirely when no bar is configured, or
    # when every student clears the one that is -- an empty column with a
    # blank header is furniture nobody asked for.
    keep_marker = threshold is not None and marked
    header = list(STUDENT_TABLE_HEADER) + ([MARKER_COLUMN_HEADER] if keep_marker else [])
    body = [row if keep_marker else row[:-1] for row in rows]

    numeric = [20 * rl.mm, 20 * rl.mm, 18 * rl.mm, 26 * rl.mm]
    marker = [26 * rl.mm] if keep_marker else []
    name_width = usable_width - sum(numeric) - sum(marker)

    table = rl.Table(
        [header] + body,
        colWidths=[name_width] + numeric + marker,
        repeatRows=1,
    )
    table.setStyle(_table_style(rl, align_from_column=1))
    if keep_marker:
        table.setStyle(
            rl.TableStyle([("ALIGN", (-1, 0), (-1, -1), "LEFT"),
                           ("LEFTPADDING", (-1, 0), (-1, -1), 8)])
        )

    return table


def _lecture_table(rl, lectures, text, usable_width):
    rows = []
    for session, counts in lectures:
        date = session.get("date")
        total = counts.get("total", 0)
        rows.append(
            [
                text(date.strftime(DATE_FORMAT) if date is not None else ""),
                text(session.get("source")),
                f"{counts.get('present', 0)} / {total}",
            ]
        )

    widths = [34 * rl.mm, 30 * rl.mm, 30 * rl.mm]
    table = rl.Table(
        [list(LECTURE_TABLE_HEADER)] + rows,
        colWidths=widths + [usable_width - sum(widths)],
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(_table_style(rl, align_from_column=2))

    return table


# --- The document ------------------------------------------------------


def render_report(
    *,
    class_name,
    course,
    context,
    prepared_by,
    generated_on,
    date_from,
    date_to,
    lectures,
    standings,
    threshold,
):
    """The attendance report for one class over one range, as bytes.

    `lectures` is `(session, counts)` per recorded lecture, oldest first;
    `standings` is `(student, present, total)` per student, by name. Both
    are already resolved, ordered, and authorized by
    attendance/service.py::export_summary -- this module decides only how
    they are set on a page.

    An empty range still produces a complete document: the title block,
    and a sentence saying nothing was recorded. "Nothing" is an answer,
    and a faculty member who prints it has evidence of it.
    """
    rl = _require_reportlab()
    styles = _styles(rl)
    text = _Text()

    margin = 18 * rl.mm
    usable_width = rl.A4[0] - 2 * margin

    story = _title_block(
        rl,
        styles,
        class_name=class_name,
        course=course,
        context=context,
        prepared_by=prepared_by,
        generated_on=generated_on,
        date_from=date_from,
        date_to=date_to,
        lecture_count=len(lectures),
        threshold=threshold,
        text=text,
    )

    if not lectures and not standings:
        story.append(_paragraph(rl, EMPTY_RANGE_NOTE, styles.note))
    else:
        story.append(_paragraph(rl, "Students", styles.section))
        story.append(rl.Spacer(1, 4))
        story.append(_student_table(rl, standings, threshold, text, usable_width))

        story.append(rl.Spacer(1, 16))
        story.append(_paragraph(rl, "Lecture index", styles.section))
        story.append(rl.Spacer(1, 4))
        if lectures:
            story.append(_lecture_table(rl, lectures, text, usable_width))
        else:
            story.append(_paragraph(rl, EMPTY_RANGE_NOTE, styles.note))

    # Appended last because only now is it known whether anything was
    # substituted -- every cell has been through `text` by this point.
    if text.substituted:
        story.append(rl.Spacer(1, 14))
        story.append(_paragraph(rl, SUBSTITUTION_NOTE, styles.meta))

    buffer = BytesIO()
    document = rl.SimpleDocTemplate(
        buffer,
        pagesize=rl.A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=20 * rl.mm,
        # `_renderable` directly, not the shared `text` callable, and not
        # `_paragraph` either. This is PDF metadata rather than a drawn
        # cell: reportlab escapes it for PDF string syntax itself, so it
        # needs no markup escaping, and routing it through `text` would
        # let the document *title* alone trip the substitution note at
        # the foot of a report whose visible content was all fine.
        title=f"Attendance report - {_renderable(class_name)[0]}",
        author="AutoAttend",
    )
    document.build(story, canvasmaker=_numbered_canvas(rl))

    return buffer.getvalue()
