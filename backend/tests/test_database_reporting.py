"""Tests for backend/database/reporting.py.

Spec contract under test (.claude/specs/20-init-db-index-reconcile.md,
"Backend" + "Definition of done"):
  - `format_index_report(result, dry_run=False)` returns
    `(stdout lines, stderr lines)` and prints nothing itself -- the
    choice of stdout/stderr stays with the CLI command, not this module.
  - Wording is conditional on `dry_run`: "Would create"/"Would rebuild"/
    "Would initialize" on a dry run, versus "Created"/"Rebuilt"/
    "Initialized" on a real one.
  - Every created or recreated index is named as `<collection>.<name>`;
    a recreate additionally carries its reason.
  - A count of unchanged indexes is always reported.
  - A blocked entry goes to the *error* line list, naming the collection,
    the index, and the duplicate key values, and states that nothing was
    dropped.
  - No document content beyond the indexed fields' own values ever
    appears -- the duplicate report is confined to what was passed in.

Pure string assembly, mirroring the convention in
test_notifications_reporting.py: no CliRunner, no Flask app, and no
database anywhere in this file.
"""

from database.reporting import format_index_report


def _record(collection, name, reason=None, dropped=None, **extra):
    return {
        "collection": collection,
        "name": name,
        "keys": [("field", 1)],
        "unique": False,
        "reason": reason,
        "dropped": dropped or [],
        **extra,
    }


def _result(created=None, recreated=None, unchanged=None, blocked=None, collections=None):
    return {
        "collections": collections or ["users", "institutes"],
        "dry_run": False,
        "indexes": {
            "created": created or [],
            "recreated": recreated or [],
            "unchanged": unchanged or [],
            "blocked": blocked or [],
        },
    }


class TestFormatIndexReportReturnsLinesRatherThanPrinting:
    def test_returns_a_tuple_of_two_lists(self):
        lines, error_lines = format_index_report(_result())

        assert isinstance(lines, list)
        assert isinstance(error_lines, list)

    def test_prints_nothing_itself(self, capsys):
        format_index_report(
            _result(
                created=[_record("users", "uniq_email")],
                blocked=[
                    {
                        **_record("attendance_sessions", "uniq_class_id_date"),
                        "duplicates": [{"class_id": "abc", "date": "2024-01-01"}],
                    }
                ],
            )
        )

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""


class TestCollectionsLineWording:
    def test_a_real_run_says_initialized(self):
        lines, _ = format_index_report(_result(), dry_run=False)

        assert any(line.startswith("Initialized collections:") for line in lines)

    def test_a_dry_run_says_would_initialize(self):
        lines, _ = format_index_report(_result(), dry_run=True)

        assert any(line.startswith("Would initialize collections:") for line in lines)
        assert not any(line.startswith("Initialized collections:") for line in lines)

    def test_the_collections_line_names_every_collection_in_order(self):
        lines, _ = format_index_report(
            _result(collections=["users", "institutes", "departments"])
        )

        assert any(
            "users, institutes, departments" in line for line in lines
        )


class TestCreatedIndexLines:
    def test_a_created_index_is_named_by_collection_and_index_name(self):
        lines, _ = format_index_report(
            _result(created=[_record("users", "uniq_email")]), dry_run=False
        )

        assert any(
            "Created index users.uniq_email" in line for line in lines
        )

    def test_a_dry_run_created_index_says_would_create(self):
        lines, _ = format_index_report(
            _result(created=[_record("users", "uniq_email")]), dry_run=True
        )

        assert any(
            "Would create index users.uniq_email" in line for line in lines
        )
        assert not any(line.startswith("Created index") for line in lines)


class TestRecreatedIndexLines:
    def test_a_recreated_index_is_named_with_its_reason(self):
        lines, _ = format_index_report(
            _result(
                recreated=[
                    _record(
                        "attendance_sessions",
                        "uniq_class_id_date",
                        reason="key spec was {class_id: 1, date: -1}, "
                        "declared {class_id: 1, date: 1}",
                    )
                ]
            ),
            dry_run=False,
        )

        text = "\n".join(lines)
        assert "Rebuilt index attendance_sessions.uniq_class_id_date" in text
        assert "class_id: 1, date: -1" in text
        assert "class_id: 1, date: 1" in text

    def test_a_dry_run_recreated_index_says_would_rebuild(self):
        lines, _ = format_index_report(
            _result(
                recreated=[
                    _record(
                        "attendance_sessions",
                        "uniq_class_id_date",
                        reason="key spec differs",
                    )
                ]
            ),
            dry_run=True,
        )

        assert any(
            "Would rebuild index attendance_sessions.uniq_class_id_date" in line
            for line in lines
        )
        assert not any(line.startswith("Rebuilt index") for line in lines)


class TestUnchangedCount:
    def test_zero_unchanged_reports_zero_with_the_plural_form(self):
        lines, _ = format_index_report(_result(unchanged=[]))

        assert any("0 indexes already match the schema." in line for line in lines)

    def test_exactly_one_unchanged_uses_the_singular_form(self):
        lines, _ = format_index_report(
            _result(unchanged=[_record("users", "idx_role")])
        )

        assert any("1 index already matches the schema." in line for line in lines)

    def test_several_unchanged_uses_the_plural_form(self):
        lines, _ = format_index_report(
            _result(
                unchanged=[
                    _record("users", "idx_role"),
                    _record("users", "uniq_email"),
                    _record("institutes", "uniq_code"),
                ]
            )
        )

        assert any("3 indexes already match the schema." in line for line in lines)


class TestBlockedEntriesGoToTheErrorChannel:
    def _blocked_result(self, duplicates=None):
        return _result(
            blocked=[
                {
                    **_record("attendance_sessions", "uniq_class_id_date"),
                    "duplicates": duplicates
                    if duplicates is not None
                    else [{"class_id": "abc123", "date": "2024-01-15"}],
                }
            ]
        )

    def test_a_blocked_entry_does_not_appear_in_the_stdout_lines(self):
        lines, _ = format_index_report(self._blocked_result())

        text = "\n".join(lines)
        assert "attendance_sessions.uniq_class_id_date" not in text

    def test_a_blocked_entry_appears_in_the_error_lines(self):
        _, error_lines = format_index_report(self._blocked_result())

        text = "\n".join(error_lines)
        assert "attendance_sessions.uniq_class_id_date" in text

    def test_the_block_reason_names_the_collection_and_the_index(self):
        _, error_lines = format_index_report(self._blocked_result())

        text = "\n".join(error_lines)
        assert "attendance_sessions" in text
        assert "uniq_class_id_date" in text

    def test_the_block_reason_states_nothing_was_dropped(self):
        _, error_lines = format_index_report(self._blocked_result())

        text = "\n".join(error_lines)
        assert "Nothing was dropped" in text

    def test_the_duplicate_key_values_are_listed(self):
        _, error_lines = format_index_report(
            self._blocked_result(
                duplicates=[{"class_id": "abc123", "date": "2024-01-15"}]
            )
        )

        text = "\n".join(error_lines)
        assert "class_id=abc123" in text
        assert "date=2024-01-15" in text

    def test_multiple_duplicate_groups_are_each_listed(self):
        _, error_lines = format_index_report(
            self._blocked_result(
                duplicates=[
                    {"class_id": "aaa", "date": "2024-01-01"},
                    {"class_id": "bbb", "date": "2024-02-02"},
                ]
            )
        )

        text = "\n".join(error_lines)
        assert "class_id=aaa" in text
        assert "class_id=bbb" in text

    def test_no_blocked_entries_produces_no_error_lines(self):
        _, error_lines = format_index_report(_result())

        assert error_lines == []

    def test_a_blocked_entry_adds_a_hint_to_resolve_and_rerun(self):
        _, error_lines = format_index_report(self._blocked_result())

        text = "\n".join(error_lines)
        assert "init-db" in text or "run" in text.lower()


class TestNoDocumentContentBeyondIndexedFieldValues:
    def test_only_the_declared_duplicate_key_fields_are_rendered_not_a_whole_document(self):
        result = _result(
            blocked=[
                {
                    **_record("attendance_sessions", "uniq_class_id_date"),
                    "duplicates": [{"class_id": "abc123", "date": "2024-01-15"}],
                }
            ]
        )

        _, error_lines = format_index_report(result)

        text = "\n".join(error_lines)
        # Only what was actually passed as duplicate key values appears --
        # nothing about a password hash, an encoding, or any other field
        # that was never handed to the formatter in the first place.
        assert "password_hash" not in text
        assert "encoding" not in text
