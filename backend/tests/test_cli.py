"""Tests for the `flask init-db` CLI command (see backend/app.py).

Spec contract under test (.claude/specs/02-database-setup.md, "Backend" +
"Definition of done"):
  - An `init-db` command is registered on the Flask app's CLI
    (`app.cli.command("init-db")`) that calls `init_database(get_db())`.
  - On success, it reports which collections were initialized and exits 0.
  - On failure, it fails gracefully: a clear message (no raw traceback
    dumped to the user) and a non-zero exit code -- never an unhandled
    exception propagating out of the CLI invocation.
  - `init_database`/`get_db` are wired up *only* behind this explicit CLI
    command -- normal `create_app()` / `python app.py` startup, and
    `GET /api/health`, must never trigger a MongoDB connection or
    initialization attempt as a side effect.

`app.get_db` / `app.init_database` (the names imported into app.py's own
module namespace) are monkeypatched directly, so these tests never touch
a real or even mocked pymongo client -- they only exercise the CLI
wiring and error-handling contract.
"""

import app as app_module


class TestInitDbCommandIsRegistered:
    def test_init_db_is_registered_as_a_flask_cli_command(self, app_instance):
        assert "init-db" in app_instance.cli.commands


class TestInitDbCommandSuccessPath:
    def test_reports_initialized_collections_and_exits_zero(self, app_instance, monkeypatch):
        fake_db = object()
        captured = {}

        def fake_get_db():
            return fake_db

        def fake_init_database(db, dry_run=False):
            captured["db"] = db
            captured["dry_run"] = dry_run
            return {
                "collections": ["users", "institutes", "class_enrollments"],
                "dry_run": dry_run,
                "indexes": {
                    "created": [],
                    "recreated": [],
                    "unchanged": [],
                    "blocked": [],
                },
            }

        monkeypatch.setattr(app_module, "get_db", fake_get_db)
        monkeypatch.setattr(app_module, "init_database", fake_init_database)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert result.exit_code == 0
        assert captured["db"] is fake_db
        assert "users" in result.output
        assert "institutes" in result.output
        assert "class_enrollments" in result.output


def _index_result(
    collections=None,
    dry_run=False,
    created=None,
    recreated=None,
    unchanged=None,
    blocked=None,
):
    """A result shaped like `init_database`'s real return value, per
    .claude/specs/20-init-db-index-reconcile.md. Used to monkeypatch
    `app_module.init_database` so these tests exercise the *real*
    `format_index_report` the command calls, and only fake the database
    layer underneath it -- matching this file's existing convention of
    monkeypatching `get_db`/`init_database` directly rather than a fake
    pymongo client.
    """
    return {
        "collections": collections or ["users", "institutes"],
        "dry_run": dry_run,
        "indexes": {
            "created": created or [],
            "recreated": recreated or [],
            "unchanged": unchanged or [],
            "blocked": blocked or [],
        },
    }


def _index_record(collection, name, reason=None, dropped=None, unique=False, **extra):
    return {
        "collection": collection,
        "name": name,
        "keys": [("field", 1)],
        "unique": unique,
        "reason": reason,
        "dropped": dropped or [],
        **extra,
    }


class TestInitDbCommandReportsIndexChanges:
    """Per spec's "Definition of done": `flask init-db` output names every
    index created or recreated (with the reason for a recreate) plus a
    count of unchanged indexes.
    """

    def test_output_names_a_created_index_by_collection_and_name(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(
            app_module,
            "init_database",
            lambda db, dry_run=False: _index_result(
                created=[_index_record("users", "uniq_email", unique=True)]
            ),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert result.exit_code == 0
        assert "users.uniq_email" in result.output

    def test_output_names_a_recreated_index_with_its_reason(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(
            app_module,
            "init_database",
            lambda db, dry_run=False: _index_result(
                recreated=[
                    _index_record(
                        "attendance_sessions",
                        "uniq_class_id_date",
                        reason="key spec was {class_id: 1, date: -1}, "
                        "declared {class_id: 1, date: 1}",
                        unique=True,
                    )
                ]
            ),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert result.exit_code == 0
        assert "attendance_sessions.uniq_class_id_date" in result.output
        assert "class_id: 1, date: -1" in result.output
        assert "class_id: 1, date: 1" in result.output

    def test_output_reports_a_count_of_unchanged_indexes(self, app_instance, monkeypatch):
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(
            app_module,
            "init_database",
            lambda db, dry_run=False: _index_result(
                unchanged=[
                    _index_record("users", "idx_role"),
                    _index_record("users", "uniq_email", unique=True),
                ]
            ),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert result.exit_code == 0
        assert "2 indexes already match the schema." in result.output


class TestInitDbCommandDryRunFlag:
    """Per spec's "Definition of done": `--dry-run` prints the plan and
    exits zero; a real run applies it.
    """

    def test_dry_run_flag_is_passed_through_to_init_database(self, app_instance, monkeypatch):
        captured = {}

        monkeypatch.setattr(app_module, "get_db", lambda: object())

        def fake_init_database(db, dry_run=False):
            captured["dry_run"] = dry_run
            return _index_result(dry_run=dry_run)

        monkeypatch.setattr(app_module, "init_database", fake_init_database)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db", "--dry-run"])

        assert result.exit_code == 0
        assert captured["dry_run"] is True

    def test_without_the_flag_dry_run_defaults_to_false(self, app_instance, monkeypatch):
        captured = {}

        monkeypatch.setattr(app_module, "get_db", lambda: object())

        def fake_init_database(db, dry_run=False):
            captured["dry_run"] = dry_run
            return _index_result(dry_run=dry_run)

        monkeypatch.setattr(app_module, "init_database", fake_init_database)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert result.exit_code == 0
        assert captured["dry_run"] is False

    def test_dry_run_output_uses_would_create_and_would_rebuild_wording(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(
            app_module,
            "init_database",
            lambda db, dry_run=False: _index_result(
                dry_run=True,
                created=[_index_record("institutes", "uniq_code", unique=True)],
                recreated=[
                    _index_record(
                        "attendance_sessions",
                        "uniq_class_id_date",
                        reason="key spec differs",
                        unique=True,
                    )
                ],
            ),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db", "--dry-run"])

        assert result.exit_code == 0
        assert "Would create index institutes.uniq_code" in result.output
        assert "Would rebuild index attendance_sessions.uniq_class_id_date" in result.output
        assert "Created index" not in result.output
        assert "Rebuilt index" not in result.output


class TestInitDbCommandBlockedRunExitCode:
    """Per spec's "Definition of done": a real run blocked by the
    duplicate pre-check exits non-zero; a dry run that reports the same
    block does not, since it changed nothing to fail at.
    """

    def _blocked_result(self, dry_run):
        return _index_result(
            dry_run=dry_run,
            blocked=[
                {
                    **_index_record(
                        "attendance_sessions", "uniq_class_id_date", unique=True
                    ),
                    "duplicates": [
                        {"class_id": "64abc0000000000000000001", "date": "2024-01-15"}
                    ],
                }
            ],
        )

    def test_a_real_run_blocked_by_duplicates_exits_non_zero(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(
            app_module,
            "init_database",
            lambda db, dry_run=False: self._blocked_result(dry_run=False),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert result.exit_code != 0
        assert "attendance_sessions" in result.output
        assert "uniq_class_id_date" in result.output

    def test_a_dry_run_reporting_the_same_block_exits_zero(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(
            app_module,
            "init_database",
            lambda db, dry_run=False: self._blocked_result(dry_run=True),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db", "--dry-run"])

        assert result.exit_code == 0

    def test_a_blocked_real_run_names_the_duplicate_key_values(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(
            app_module,
            "init_database",
            lambda db, dry_run=False: self._blocked_result(dry_run=False),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert "class_id=64abc0000000000000000001" in result.output
        assert "date=2024-01-15" in result.output


class TestInitDbCommandFailurePath:
    def test_a_failure_getting_the_database_is_reported_clearly_and_exits_nonzero(
        self, app_instance, monkeypatch
    ):
        def fake_get_db():
            raise RuntimeError("MONGODB_URI is not configured")

        monkeypatch.setattr(app_module, "get_db", fake_get_db)
        monkeypatch.setattr(
            app_module,
            "init_database",
            lambda db, dry_run=False: {"collections": []},
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert result.exit_code != 0
        assert "Database initialization failed" in result.output
        assert "Traceback" not in result.output

    def test_a_failure_during_initialization_itself_is_reported_clearly_and_exits_nonzero(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "get_db", lambda: object())

        def fake_init_database(db, dry_run=False):
            raise RuntimeError("simulated collMod/create_index failure")

        monkeypatch.setattr(app_module, "init_database", fake_init_database)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert result.exit_code != 0
        assert "Database initialization failed" in result.output
        assert "Traceback" not in result.output

    def test_does_not_leak_a_raw_python_exception_object_as_the_only_message(
        self, app_instance, monkeypatch
    ):
        """A bare, unformatted exception dump is not a 'clear message' --
        the command must at minimum wrap it with context (per spec's
        'handles failures gracefully')."""
        monkeypatch.setattr(app_module, "get_db", lambda: object())

        def fake_init_database(db, dry_run=False):
            raise RuntimeError("simulated failure")

        monkeypatch.setattr(app_module, "init_database", fake_init_database)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert "failed" in result.output.lower()


class TestNormalAppStartupNeverTouchesMongoForThisFeature:
    def test_create_app_alone_never_calls_get_db_or_init_database(self, monkeypatch):
        def _fail_get_db(*args, **kwargs):
            raise AssertionError("get_db must not be called by create_app() alone")

        def _fail_init_database(*args, **kwargs):
            raise AssertionError("init_database must not be called by create_app() alone")

        monkeypatch.setattr(app_module, "get_db", _fail_get_db)
        monkeypatch.setattr(app_module, "init_database", _fail_init_database)

        # create_app() itself must not invoke either -- if it did, this
        # call would raise before we even get to making a request.
        application = app_module.create_app()

        assert application is not None

    def test_health_endpoint_still_works_without_init_db_ever_running(self, monkeypatch):
        def _fail_get_db(*args, **kwargs):
            raise AssertionError("get_db must not be called by a normal /api/health request")

        def _fail_init_database(*args, **kwargs):
            raise AssertionError(
                "init_database must not be called by a normal /api/health request"
            )

        monkeypatch.setattr(app_module, "get_db", _fail_get_db)
        monkeypatch.setattr(app_module, "init_database", _fail_init_database)
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})

        application = app_module.create_app()
        with application.test_client() as client:
            response = client.get("/api/health")

        assert response.status_code == 200

    def test_init_db_cli_command_exists_only_as_an_explicit_command_not_auto_run(
        self, app_instance, monkeypatch
    ):
        """Registering the command on app.cli must not itself execute it --
        only an explicit `flask init-db` invocation should call
        get_db()/init_database()."""

        def _fail_get_db(*args, **kwargs):
            raise AssertionError("get_db must not run just from CLI registration")

        def _fail_init_database(*args, **kwargs):
            raise AssertionError("init_database must not run just from CLI registration")

        monkeypatch.setattr(app_module, "get_db", _fail_get_db)
        monkeypatch.setattr(app_module, "init_database", _fail_init_database)

        # Merely creating the app (which registers the CLI command) and
        # reading its registered commands must not trigger the command body.
        application = app_module.create_app()
        assert "init-db" in application.cli.commands
