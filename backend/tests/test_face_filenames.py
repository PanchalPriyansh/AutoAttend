"""Tests for backend/recognition/filenames.py.

Spec contract under test (22-bulk-face-enrollment-import.md, "Matching a
file to a student" + "Rules for implementation" + "Definition of done"
items 7-12):

  - A stem matches a roster student when it equals, case-insensitively,
    that student's **ID** -- the local part of their email, which is the
    roll number a photo is named by (`24DCS001.jpg`). The full email is
    also accepted, as the fallback for the ambiguous case below.
  - Surrounding whitespace in the stem is ignored.
  - An ID stem matching two roster students is `ambiguous` --
    `match_count` is 2 and no student is returned -- and is never guessed
    at, even when a full-email stem naming one of those same two students
    resolves cleanly elsewhere in the same batch.
  - Nothing else is parsed: no numeric suffix is stripped
    (`24DCS001-2.jpg` does not match `24DCS001`), and a file named after
    a student's *name* rather than their ID is a no-match.
  - Matching is scoped to the roster handed in -- a student not present
    in that list can never be resolved, regardless of their real email.
  - One `Resolution` is returned per input filename, in the order given.

This module is pure by design (no Flask, no pymongo, no CV library, no
database, no I/O), so every test below calls `resolve_roster` directly
against plain dicts -- no `app_instance` fixture, no fake db, and no
`conftest.py` fixtures of any kind are used anywhere in this file.
"""

import os
import re

from recognition.filenames import resolve_roster

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILENAMES_PATH = os.path.join(BACKEND_DIR, "recognition", "filenames.py")


def _student(student_id, email, name=None):
    """A roster entry as `resolve_roster` needs it -- resolve_roster only
    ever reads `email` (to match) and returns the dict verbatim, so a
    plain dict with `_id`/`email`/`name` is a faithful stand-in for a
    real `users` document without needing bson, pymongo, or a database.
    """
    return {"_id": student_id, "email": email, "name": name or email}


# --- Student-ID and full-email matching --------------------------------------


class TestStudentIdAndFullEmailMatching:
    def test_full_email_stem_matches_the_student(self):
        aarti = _student(1, "aarti.desai@example.edu")

        [resolution] = resolve_roster(["aarti.desai@example.edu.jpg"], [aarti])

        assert resolution.student == aarti
        assert resolution.match_count == 1

    def test_local_part_stem_matches_the_student(self):
        aarti = _student(1, "aarti.desai@example.edu")

        [resolution] = resolve_roster(["aarti.desai.jpg"], [aarti])

        assert resolution.student == aarti
        assert resolution.match_count == 1

    def test_an_uppercase_student_id_matches_the_roster_email_local_part(self):
        """The convention as it is actually used: a college names photo
        folders by roll number, in caps, with no domain anywhere in
        sight. `users` has no roll-number field -- the ID is the local
        part of the address -- so this is the case the whole feature
        turns on.
        """
        roster = [
            _student(1, "24dcs001@charusat.edu.in", "Aarav"),
            _student(2, "24dcs002@charusat.edu.in", "Bhavya"),
        ]

        resolutions = resolve_roster(["24DCS001.jpg", "24DCS002.JPG"], roster)

        assert [r.student["name"] for r in resolutions] == ["Aarav", "Bhavya"]
        assert [r.match_count for r in resolutions] == [1, 1]

    def test_matching_is_case_insensitive_on_full_email_and_local_part(self):
        aarti = _student(1, "aarti.desai@example.edu")

        full_email, local_part = resolve_roster(
            ["AARTI.DESAI@EXAMPLE.EDU.JPG", "Aarti.Desai.JPG"], [aarti]
        )

        assert full_email.student == aarti
        assert local_part.student == aarti

    def test_surrounding_whitespace_in_the_stem_is_ignored(self):
        aarti = _student(1, "aarti.desai@example.edu")

        [resolution] = resolve_roster(["  aarti.desai@example.edu.jpg  "], [aarti])

        assert resolution.student == aarti
        assert resolution.match_count == 1

    def test_full_email_match_wins_over_local_part_match(self):
        # Two students share nothing but the fact that "rohit" is the
        # local part of both addresses -- a filename using the *full*
        # address of one of them must resolve to exactly that student.
        rohit_abc = _student(1, "rohit@abc.edu")
        rohit_xyz = _student(2, "rohit@xyz.edu")

        [resolution] = resolve_roster(["rohit@abc.edu.jpg"], [rohit_abc, rohit_xyz])

        assert resolution.student == rohit_abc
        assert resolution.match_count == 1

    def test_local_part_matching_two_roster_students_is_ambiguous_with_its_count(self):
        rohit_abc = _student(1, "rohit@abc.edu")
        rohit_xyz = _student(2, "rohit@xyz.edu")

        [resolution] = resolve_roster(["rohit.jpg"], [rohit_abc, rohit_xyz])

        assert resolution.student is None
        assert resolution.match_count == 2

    def test_full_email_stem_still_registers_when_the_local_part_is_ambiguous(self):
        rohit_abc = _student(1, "rohit@abc.edu")
        rohit_xyz = _student(2, "rohit@xyz.edu")

        ambiguous, disambiguated = resolve_roster(
            ["rohit.jpg", "rohit@abc.edu.jpg"], [rohit_abc, rohit_xyz]
        )

        assert ambiguous.student is None
        assert ambiguous.match_count == 2
        assert disambiguated.student == rohit_abc
        assert disambiguated.match_count == 1


# --- Nothing else is parsed ---------------------------------------------------


class TestNothingElseIsParsed:
    def test_a_numeric_suffix_is_not_stripped(self):
        aarti = _student(1, "aarti@example.edu")

        [resolution] = resolve_roster(["aarti-2.jpg"], [aarti])

        assert resolution.student is None
        assert resolution.match_count == 0

    def test_a_file_named_after_the_students_name_rather_than_their_email_is_no_match(self):
        aarti = _student(1, "aarti.desai@example.edu", name="Aarti Desai")

        [resolution] = resolve_roster(["Aarti Desai.jpg"], [aarti])

        assert resolution.student is None
        assert resolution.match_count == 0


# --- Matching is scoped to the roster handed in -------------------------------


class TestMatchingIsScopedToTheGivenRoster:
    def test_a_student_absent_from_the_passed_in_roster_is_no_match(self):
        # resolve_roster only ever sees the roster it is handed -- a real
        # email that would match a student in another class resolves to
        # nothing when that student's document is not among `students`.
        [resolution] = resolve_roster(["aarti.desai@example.edu.jpg"], [])

        assert resolution.student is None
        assert resolution.match_count == 0


# --- One Resolution per filename, in submitted order --------------------------


class TestResolutionOrdering:
    def test_one_resolution_is_returned_per_filename_in_the_order_given(self):
        aarti = _student(1, "aarti@example.edu")
        sneha = _student(2, "sneha@example.edu")

        resolutions = resolve_roster(
            ["sneha@example.edu.jpg", "unknown.jpg", "aarti@example.edu.jpg"],
            [aarti, sneha],
        )

        assert len(resolutions) == 3
        assert resolutions[0].student == sneha
        assert resolutions[1].student is None
        assert resolutions[2].student == aarti


# --- Module import isolation ---------------------------------------------------


class TestModuleImportIsolation:
    """Definition of done #12: "filenames.py imports neither Flask, nor
    pymongo, nor any CV library, verified by reading its imports, and its
    tests run with no app context and no database."

    Checked by scanning the module's own source text for a forbidden
    import line -- not by importing it and inspecting `sys.modules`, so
    the assertion holds even on a machine that has never installed any of
    these packages (which is also why every other test in this file
    imports nothing beyond `recognition.filenames` itself and stdlib).
    """

    FORBIDDEN_IMPORT = re.compile(
        r"^\s*(import|from)\s+(flask|pymongo|cv2|face_recognition|numpy)\b",
        re.IGNORECASE,
    )

    def test_filenames_module_imports_no_flask_pymongo_or_cv_library(self):
        with open(FILENAMES_PATH, "r", encoding="utf-8") as handle:
            offenders = [
                f"{line_number}: {line.strip()}"
                for line_number, line in enumerate(handle, start=1)
                if self.FORBIDDEN_IMPORT.match(line)
            ]

        assert not offenders, f"recognition/filenames.py imports a forbidden module: {offenders}"
