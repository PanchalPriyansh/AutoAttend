"""Tests for the `flask notify-low-attendance` CLI command (see
backend/app.py).

A new file rather than an addition to test_cli.py, following that file's
own narrow scoping (it covers `init-db` alone) -- one CLI command, one
test module.

Spec contract under test (.claude/specs/10-low-attendance-notifications.md,
"APIs" + "Backend" + "Rules for implementation" + "Definition of done"):
  - `notify-low-attendance` is registered on the app's CLI, with a
    `--dry-run` flag.
  - `--dry-run` loads only the sweep settings (threshold/cooldown), never
    the SMTP credentials, and never constructs a mail transport -- so it
    works on a machine mail has not been configured on yet, and provably
    opens no connection.
  - A normal run loads the SMTP settings too, opens `SmtpTransport` as a
    context manager, and calls `notify_low_attendance` with it.
  - The command reports how many students were emailed/would be emailed
    and how many were skipped, and exits non-zero (without a raw
    traceback) on `MailerNotConfiguredError`, `MailerSendError`, or any
    other failure -- including when the run partially failed
    (`result["failed"] > 0`).
  - No SMTP password appears in the command's output on any path.

`app_module.get_db`, `load_sweep_settings`, `load_smtp_settings`,
`SmtpTransport`, and `notify_low_attendance` -- the names `app.py`
imports directly from the `notifications` package -- are monkeypatched
on `app` itself, exactly as test_cli.py does for `get_db`/`init_database`.
No test here ever touches a real or mocked pymongo client, opens a
socket, or uses a real credential; every "password" is an obviously-fake
placeholder used only to prove it never reaches `result.output`.
"""

import app as app_module
from notifications.errors import MailerNotConfiguredError, MailerSendError
from notifications.settings import SmtpSettings, SweepSettings


def _sweep_settings(threshold=75.0, cooldown_days=7):
    return SweepSettings(threshold=threshold, cooldown_days=cooldown_days)


def _smtp_settings(password="do-not-print-this-password"):
    return SmtpSettings(
        host="smtp.example.test", port=587, username="bot@example.test",
        password=password, sender="noreply@example.test", sender_name="AutoAttend",
        use_tls=True,
    )


def _result(**overrides):
    base = {
        "candidates": 0, "skipped_cooldown": 0, "students_notified": 0,
        "classes_notified": 0, "failed": 0, "recipients": [],
    }
    base.update(overrides)
    return base


def _make_recording_transport_class():
    """A fresh fake `SmtpTransport` replacement per call: a context
    manager whose construction, `__enter__`, and `__exit__` are all
    observable via its own `instances` list, so tests never share mutable
    state through a module-level class.
    """

    class _RecordingTransportContext:
        instances = []

        def __init__(self, settings):
            self.settings = settings
            self.entered = False
            self.exited = False
            type(self).instances.append(self)

        def __enter__(self):
            self.entered = True
            return self

        def __exit__(self, exc_type, exc, tb):
            self.exited = True
            return False

    return _RecordingTransportContext


class TestNotifyLowAttendanceIsRegistered:
    def test_notify_low_attendance_is_registered_as_a_flask_cli_command(self, app_instance):
        assert "notify-low-attendance" in app_instance.cli.commands


class TestDryRunNeverTouchesSmtp:
    def test_dry_run_never_loads_smtp_settings_or_constructs_a_transport(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "load_sweep_settings", lambda config: _sweep_settings())
        monkeypatch.setattr(app_module, "get_db", lambda: object())

        def _fail_load_smtp_settings(config):
            raise AssertionError("load_smtp_settings must not be called on a dry run")

        monkeypatch.setattr(app_module, "load_smtp_settings", _fail_load_smtp_settings)

        def _fail_smtp_transport(settings):
            raise AssertionError("SmtpTransport must not be constructed on a dry run")

        monkeypatch.setattr(app_module, "SmtpTransport", _fail_smtp_transport)

        captured = {}

        def fake_notify(db, sweep_settings, *args, dry_run=False, **kwargs):
            captured["dry_run"] = dry_run
            captured["args"] = args
            return _result(
                candidates=2, students_notified=1, classes_notified=2,
                recipients=[
                    {
                        "name": "Alice Test",
                        "email": "alice@example.test",
                        "classes": [
                            {"course": "Data Structures", "class_name": "A",
                             "percentage": 42.5, "present_count": 5, "total_count": 12},
                        ],
                    }
                ],
            )

        monkeypatch.setattr(app_module, "notify_low_attendance", fake_notify)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance", "--dry-run"])

        assert result.exit_code == 0
        assert captured["dry_run"] is True
        assert captured["args"] == ()  # no smtp_settings/transport were passed through

    def test_dry_run_reports_the_would_be_recipients_and_counts(self, app_instance, monkeypatch):
        monkeypatch.setattr(
            app_module, "load_sweep_settings",
            lambda config: _sweep_settings(threshold=75.0, cooldown_days=7),
        )
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(
            app_module, "notify_low_attendance",
            lambda db, sweep_settings, *a, dry_run=False, **kw: _result(
                candidates=2, skipped_cooldown=0, students_notified=1, classes_notified=2,
                recipients=[
                    {
                        "name": "Alice Test",
                        "email": "alice@example.test",
                        "classes": [
                            {"course": "Data Structures", "class_name": "A",
                             "percentage": 42.5, "present_count": 5, "total_count": 12},
                        ],
                    }
                ],
            ),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance", "--dry-run"])

        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "75%" in result.output
        assert "7 day(s)" in result.output
        assert "Would notify: 1 student(s) about 2 class(es)" in result.output
        assert "alice@example.test" in result.output


class TestRealRunOpensTransportAndReportsCounts:
    def test_a_real_run_constructs_smtp_settings_and_the_transport_and_reports_counts(
        self, app_instance, monkeypatch
    ):
        transport_class = _make_recording_transport_class()
        monkeypatch.setattr(app_module, "load_sweep_settings", lambda config: _sweep_settings())
        monkeypatch.setattr(app_module, "load_smtp_settings", lambda config: _smtp_settings())
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(app_module, "SmtpTransport", transport_class)

        captured = {}

        def fake_notify(db, sweep_settings, smtp_settings, transport, *, dry_run=False, now=None):
            captured["smtp_settings"] = smtp_settings
            captured["transport"] = transport
            return _result(candidates=4, skipped_cooldown=1, students_notified=2, classes_notified=3)

        monkeypatch.setattr(app_module, "notify_low_attendance", fake_notify)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance"])

        assert result.exit_code == 0
        assert len(transport_class.instances) == 1
        transport_instance = transport_class.instances[0]
        assert transport_instance.entered is True
        assert transport_instance.exited is True
        assert captured["transport"] is transport_instance
        assert "Notified: 2 student(s) about 3 class(es)" in result.output
        assert "Below the threshold: 4 class(es)" in result.output
        assert "Skipped (notified recently): 1" in result.output

    def test_a_real_run_with_no_failures_prints_no_recipient_addresses(
        self, app_instance, monkeypatch
    ):
        """A real send only ever reports counts -- `recipients` is only
        ever populated on the dry-run branch of `notify_low_attendance`,
        so nothing here could print an address even by accident.
        """
        monkeypatch.setattr(app_module, "load_sweep_settings", lambda config: _sweep_settings())
        monkeypatch.setattr(app_module, "load_smtp_settings", lambda config: _smtp_settings())
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(app_module, "SmtpTransport", _make_recording_transport_class())
        monkeypatch.setattr(
            app_module, "notify_low_attendance",
            lambda db, sweep_settings, smtp_settings, transport, **kw: _result(
                students_notified=1, classes_notified=1,
            ),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance"])

        assert result.exit_code == 0
        assert "@" not in result.output


class TestFailedSendsExitNonZero:
    def test_a_run_with_failed_sends_exits_nonzero_even_though_others_succeeded(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "load_sweep_settings", lambda config: _sweep_settings())
        monkeypatch.setattr(app_module, "load_smtp_settings", lambda config: _smtp_settings())
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(app_module, "SmtpTransport", _make_recording_transport_class())
        monkeypatch.setattr(
            app_module, "notify_low_attendance",
            lambda db, sweep_settings, smtp_settings, transport, **kw: _result(
                students_notified=1, classes_notified=1, failed=1,
            ),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance"])

        assert result.exit_code != 0
        assert "Failed to notify: 1 student(s)" in result.output
        assert "Traceback" not in result.output


class TestConfigurationErrorsAreReportedClearly:
    def test_a_missing_sweep_setting_is_reported_clearly_with_nonzero_exit_and_no_traceback(
        self, app_instance, monkeypatch
    ):
        def _raise(config):
            raise MailerNotConfiguredError("LOW_ATTENDANCE_THRESHOLD is not set")

        monkeypatch.setattr(app_module, "load_sweep_settings", _raise)

        def _fail_get_db():
            raise AssertionError("get_db must not run once sweep settings fail to load")

        monkeypatch.setattr(app_module, "get_db", _fail_get_db)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance", "--dry-run"])

        assert result.exit_code != 0
        assert "LOW_ATTENDANCE_THRESHOLD is not set" in result.output
        assert "Traceback" not in result.output

    def test_missing_smtp_settings_on_a_real_run_are_reported_clearly(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "load_sweep_settings", lambda config: _sweep_settings())
        monkeypatch.setattr(app_module, "get_db", lambda: object())

        def _raise(config):
            raise MailerNotConfiguredError("SMTP_HOST is not set")

        monkeypatch.setattr(app_module, "load_smtp_settings", _raise)

        def _fail_smtp_transport(settings):
            raise AssertionError("SmtpTransport must not be constructed when settings fail to load")

        monkeypatch.setattr(app_module, "SmtpTransport", _fail_smtp_transport)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance"])

        assert result.exit_code != 0
        assert "SMTP_HOST is not set" in result.output
        assert "Traceback" not in result.output

    def test_a_transport_connection_failure_is_reported_clearly(self, app_instance, monkeypatch):
        monkeypatch.setattr(app_module, "load_sweep_settings", lambda config: _sweep_settings())
        monkeypatch.setattr(app_module, "load_smtp_settings", lambda config: _smtp_settings())
        monkeypatch.setattr(app_module, "get_db", lambda: object())

        class _RefusingTransport:
            def __init__(self, settings):
                pass

            def __enter__(self):
                raise MailerSendError(
                    "Could not connect to the mail server. See server logs for details."
                )

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(app_module, "SmtpTransport", _RefusingTransport)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance"])

        assert result.exit_code != 0
        assert "Could not connect to the mail server" in result.output
        assert "Traceback" not in result.output

    def test_a_database_failure_is_reported_clearly_with_nonzero_exit_and_no_traceback(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "load_sweep_settings", lambda config: _sweep_settings())

        def _fail_get_db():
            raise RuntimeError("MONGODB_URI is not configured")

        monkeypatch.setattr(app_module, "get_db", _fail_get_db)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance", "--dry-run"])

        assert result.exit_code != 0
        assert "failed" in result.output.lower()
        assert "Traceback" not in result.output


class TestNoSmtpPasswordEverAppearsInOutput:
    def test_no_password_appears_in_output_on_a_successful_dry_run(self, app_instance, monkeypatch):
        monkeypatch.setattr(app_module, "load_sweep_settings", lambda config: _sweep_settings())
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(
            app_module, "notify_low_attendance",
            lambda db, sweep_settings, *a, dry_run=False, **kw: _result(
                students_notified=1, classes_notified=1,
                recipients=[
                    {"name": "Alice Test", "email": "alice@example.test",
                     "classes": [{"course": "C", "class_name": "A", "percentage": 10.0,
                                  "present_count": 1, "total_count": 10}]},
                ],
            ),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance", "--dry-run"])

        assert "do-not-print-this-password" not in result.output

    def test_no_password_appears_in_output_on_a_successful_real_run(self, app_instance, monkeypatch):
        monkeypatch.setattr(app_module, "load_sweep_settings", lambda config: _sweep_settings())
        monkeypatch.setattr(app_module, "load_smtp_settings", lambda config: _smtp_settings())
        monkeypatch.setattr(app_module, "get_db", lambda: object())
        monkeypatch.setattr(app_module, "SmtpTransport", _make_recording_transport_class())
        monkeypatch.setattr(
            app_module, "notify_low_attendance",
            lambda db, sweep_settings, smtp_settings, transport, **kw: _result(
                students_notified=1, classes_notified=1,
            ),
        )

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance"])

        assert "do-not-print-this-password" not in result.output

    def test_no_password_appears_in_output_when_the_connection_itself_fails(
        self, app_instance, monkeypatch
    ):
        monkeypatch.setattr(app_module, "load_sweep_settings", lambda config: _sweep_settings())
        monkeypatch.setattr(app_module, "load_smtp_settings", lambda config: _smtp_settings())
        monkeypatch.setattr(app_module, "get_db", lambda: object())

        class _RefusingTransport:
            def __init__(self, settings):
                self.settings = settings

            def __enter__(self):
                # A real failure message never carries the password (see
                # notifications/mailer.py), but this pins that no path
                # through the CLI command could reintroduce one even by
                # printing `settings` directly.
                raise MailerSendError("Could not connect to the mail server.")

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(app_module, "SmtpTransport", _RefusingTransport)

        runner = app_instance.test_cli_runner()
        result = runner.invoke(args=["notify-low-attendance"])

        assert "do-not-print-this-password" not in result.output
