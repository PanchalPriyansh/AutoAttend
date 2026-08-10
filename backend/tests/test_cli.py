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

        def fake_init_database(db):
            captured["db"] = db
            return {"collections": ["users", "institutes", "class_enrollments"]}

        monkeypatch.setattr(app_module, "get_db", fake_get_db)
        monkeypatch.setattr(app_module, "init_database", fake_init_database)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert result.exit_code == 0
        assert captured["db"] is fake_db
        assert "users" in result.output
        assert "institutes" in result.output
        assert "class_enrollments" in result.output


class TestInitDbCommandFailurePath:
    def test_a_failure_getting_the_database_is_reported_clearly_and_exits_nonzero(
        self, app_instance, monkeypatch
    ):
        def fake_get_db():
            raise RuntimeError("MONGODB_URI is not configured")

        monkeypatch.setattr(app_module, "get_db", fake_get_db)
        monkeypatch.setattr(app_module, "init_database", lambda db: {"collections": []})

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["init-db"])

        assert result.exit_code != 0
        assert "Database initialization failed" in result.output
        assert "Traceback" not in result.output

    def test_a_failure_during_initialization_itself_is_reported_clearly_and_exits_nonzero(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "get_db", lambda: object())

        def fake_init_database(db):
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

        def fake_init_database(db):
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
