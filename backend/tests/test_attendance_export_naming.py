"""Tests for backend/attendance/export_naming.py.

Spec contract under test (.claude/specs/21-attendance-export.md, "Rules
for implementation" 12 + 19, "Definition of done" -> "Backend -- shared"
7 and "Backend -- CSV" 16):
  - The slug keeps only `[a-z0-9-]`; every other character, including the
    ones that would break a `Content-Disposition` header (CR, LF, `"`,
    `;`) and every non-ASCII character, becomes a dash.
  - A name that slugs to nothing falls back to `attendance`, so no file
    is ever named `-.csv`.
  - Both bounds given produces `attendance-<slug>-<from>-to-<to>.<ext>`;
    anything else produces `attendance-<slug>-<generated_on>.<ext>`.
  - The module takes the generation date as an argument and never reads a
    clock, so a filename is a function of its inputs.
  - No Flask and no pymongo import -- asserted structurally in
    test_no_secrets_and_scope.py, and demonstrated here by this file
    needing no app context, no database, and no fixtures.

The header-injection cases below are the point of the module rather than
an edge case of it: `class_name` is text an admin typed and it reaches an
HTTP response header. No real institutional data is used -- every class
name here is obviously synthetic.
"""

from datetime import datetime, timezone

from attendance.export_naming import (
    FALLBACK_SLUG,
    MAX_SLUG_LENGTH,
    export_filename,
    slugify,
)

GENERATED_ON = datetime(2026, 8, 29, 14, 30, tzinfo=timezone.utc)
JULY_1 = datetime(2026, 7, 1, tzinfo=timezone.utc)
AUGUST_29 = datetime(2026, 8, 29, tzinfo=timezone.utc)

# Everything outside [a-z0-9-] that a filename or a header must not carry.
ILLEGAL_IN_A_FILENAME_OR_HEADER = '\r\n";\\/:*?<>|'


class TestSlugify:
    def test_lowercases_and_dashes_an_ordinary_class_name(self):
        assert slugify("Data Structures - CS-3A") == "data-structures-cs-3a"

    def test_collapses_a_run_of_disallowed_characters_to_one_dash(self):
        assert slugify("CS   ///   3A") == "cs-3a"

    def test_trims_leading_and_trailing_dashes(self):
        assert slugify("   -- CS-3A --   ") == "cs-3a"

    def test_drops_every_character_that_would_break_a_header(self):
        slug = slugify(f"CS{ILLEGAL_IN_A_FILENAME_OR_HEADER}3A")

        for character in ILLEGAL_IN_A_FILENAME_OR_HEADER:
            assert character not in slug

    def test_drops_non_ascii_characters(self):
        assert slugify("संरचना CS-3A") == "cs-3a"

    def test_falls_back_when_nothing_survives_the_whitelist(self):
        assert slugify("***") == FALLBACK_SLUG
        assert slugify("") == FALLBACK_SLUG
        assert slugify(None) == FALLBACK_SLUG

    def test_caps_the_length(self):
        assert len(slugify("a" * (MAX_SLUG_LENGTH * 2))) == MAX_SLUG_LENGTH

    def test_a_name_cut_by_the_cap_does_not_end_on_a_dash(self):
        # The cap lands mid-run, so the trim has to happen after it too.
        name = ("a" * (MAX_SLUG_LENGTH - 1)) + "   tail"

        assert not slugify(name).endswith("-")

    def test_produces_only_whitelisted_characters(self):
        slug = slugify("Data Structures — CS/3A (2026)\r\nsecond line")

        assert all(character.isalnum() or character == "-" for character in slug)
        assert slug.islower()


class TestExportFilename:
    def test_names_both_bounds_when_both_are_given(self):
        assert (
            export_filename(
                "Data Structures - CS-3A",
                date_from=JULY_1,
                date_to=AUGUST_29,
                generated_on=GENERATED_ON,
                extension="csv",
            )
            == "attendance-data-structures-cs-3a-2026-07-01-to-2026-08-29.csv"
        )

    def test_names_the_generation_date_when_neither_bound_is_given(self):
        assert (
            export_filename(
                "CS-3A",
                date_from=None,
                date_to=None,
                generated_on=GENERATED_ON,
                extension="pdf",
            )
            == "attendance-cs-3a-2026-08-29.pdf"
        )

    def test_names_the_generation_date_when_only_one_bound_is_given(self):
        # Half a range in a filename reads like a whole one, and two
        # exports a week apart would otherwise collide in a downloads
        # folder.
        for bounds in ({"date_from": JULY_1, "date_to": None},
                       {"date_from": None, "date_to": AUGUST_29}):
            assert export_filename(
                "CS-3A",
                generated_on=GENERATED_ON,
                extension="csv",
                **bounds,
            ) == "attendance-cs-3a-2026-08-29.csv"

    def test_carries_the_requested_extension(self):
        for extension in ("csv", "pdf"):
            name = export_filename(
                "CS-3A",
                date_from=None,
                date_to=None,
                generated_on=GENERATED_ON,
                extension=extension,
            )

            assert name.endswith(f".{extension}")

    def test_a_hostile_class_name_cannot_reach_the_header(self):
        name = export_filename(
            'CS-3A"; rm -rf /\r\nX-Injected: yes',
            date_from=None,
            date_to=None,
            generated_on=GENERATED_ON,
            extension="csv",
        )

        for character in ILLEGAL_IN_A_FILENAME_OR_HEADER:
            assert character not in name

    def test_is_a_function_of_its_arguments_and_reads_no_clock(self):
        first = export_filename(
            "CS-3A",
            date_from=None,
            date_to=None,
            generated_on=GENERATED_ON,
            extension="csv",
        )
        second = export_filename(
            "CS-3A",
            date_from=None,
            date_to=None,
            generated_on=GENERATED_ON,
            extension="csv",
        )

        assert first == second == "attendance-cs-3a-2026-08-29.csv"
