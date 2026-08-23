import logging

import click
from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from werkzeug.exceptions import RequestEntityTooLarge

from auth.service import DuplicateEmailError, create_user
from common.errors import ValidationError
from config import Config
from database.db import get_db
from database.init_db import init_database
from notifications.errors import MailerNotConfiguredError, MailerSendError
from notifications.mailer import SmtpTransport
from notifications.reporting import format_run_report
from notifications.service import notify_low_attendance
from notifications.settings import load_smtp_settings, load_sweep_settings
from recognition.validators import MAX_REQUEST_BYTES
from routes.academic import academic_bp
from routes.attendance import attendance_bp
from routes.auth import auth_bp
from routes.faces import faces_bp
from routes.health import health_bp
from routes.users import users_bp
from users.validators import validate_password_length

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if not app.config.get("JWT_SECRET_KEY"):
        raise RuntimeError("JWT_SECRET_KEY environment variable must be set")

    # CORS_ORIGINS is a single origin string today. If multiple origins are
    # ever needed, split it into a list here rather than loosening this to "*".
    # supports_credentials=True is required for the browser to send/accept
    # the HttpOnly JWT cookies cross-origin; this is incompatible with a
    # wildcard origin, so CORS_ORIGINS must stay a specific origin string.
    CORS(app, origins=Config.CORS_ORIGINS, supports_credentials=True)

    # Werkzeug refuses a larger request body before it is buffered into
    # memory, so an outsized upload costs nothing to reject. The image
    # itself is held to the tighter MAX_IMAGE_BYTES in
    # recognition/validators.py, which is what produces the 400.
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

    jwt = JWTManager(app)

    @jwt.unauthorized_loader
    def _unauthorized(reason):
        return jsonify({"error": "Authentication required"}), 401

    @jwt.invalid_token_loader
    def _invalid_token(reason):
        return jsonify({"error": "Invalid authentication token"}), 422

    @jwt.expired_token_loader
    def _expired_token(jwt_header, jwt_payload):
        return jsonify({"error": "Authentication token has expired"}), 401

    @app.errorhandler(RequestEntityTooLarge)
    def _request_too_large(exc):
        """Registered app-wide rather than on the faces blueprint: Werkzeug
        rejects the body during request parsing, before routing has decided
        which blueprint the request belongs to, so a blueprint-level handler
        would never run and the client would get Flask's HTML error page
        instead of the JSON error contract.
        """
        return jsonify({"error": "The uploaded file is too large"}), 413

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(academic_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(faces_bp)
    app.register_blueprint(attendance_bp)

    @app.cli.command("init-db")
    def init_db_command():
        """Idempotently create AutoAttend's MongoDB collections, validators, and indexes."""
        try:
            result = init_database(get_db())
            click.echo(f"Initialized collections: {', '.join(result['collections'])}")
        except Exception:
            logger.exception("Database initialization failed")
            click.echo("Database initialization failed. See server logs for details.", err=True)
            raise SystemExit(1)

    @app.cli.command("create-admin")
    def create_admin_command():
        """Interactively create the first admin user (no public registration exists)."""
        name = click.prompt("Name")
        email = click.prompt("Email")
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)

        # The same rule POST /api/users enforces, so the bootstrap admin
        # cannot be created with a password the API would have rejected.
        try:
            validate_password_length(password)
        except ValidationError as exc:
            click.echo(str(exc), err=True)
            raise SystemExit(1)

        try:
            user = create_user(get_db(), name=name, email=email, password=password, role="admin")
        except DuplicateEmailError as exc:
            click.echo(str(exc), err=True)
            raise SystemExit(1)
        except Exception:
            logger.exception("Admin creation failed")
            click.echo("Admin creation failed. See server logs for details.", err=True)
            raise SystemExit(1)

        click.echo(f"Created admin user: {user['email']}")

    @app.cli.command("notify-low-attendance")
    @click.option(
        "--dry-run",
        is_flag=True,
        help="List who would be emailed. Sends nothing and records nothing.",
    )
    def notify_low_attendance_command(dry_run):
        """Email students whose recorded attendance is below the threshold.

        A plain percentage check against what has been recorded -- there
        is no model and nothing is predicted. The threshold and the
        cooldown come from LOW_ATTENDANCE_THRESHOLD and
        NOTIFICATION_COOLDOWN_DAYS.
        """
        try:
            # Only the threshold and cooldown are needed to work out who
            # is short. A dry run therefore works on a machine where SMTP
            # has not been configured yet -- which is exactly when
            # somebody wants to preview the list.
            sweep_settings = load_sweep_settings(Config)

            db = get_db()

            if dry_run:
                # No SmtpTransport is constructed on this branch, so
                # "a dry run opens no connection" holds because there is
                # nothing here that could open one.
                result = notify_low_attendance(db, sweep_settings, dry_run=True)
            else:
                smtp_settings = load_smtp_settings(Config)
                with SmtpTransport(smtp_settings) as transport:
                    result = notify_low_attendance(
                        db, sweep_settings, smtp_settings, transport
                    )
        except (MailerNotConfiguredError, MailerSendError) as exc:
            # These messages are written to be shown: they name a setting
            # or describe a transport failure, and never carry a
            # credential or a recipient address.
            click.echo(str(exc), err=True)
            raise SystemExit(1)
        except Exception:
            logger.exception("Low-attendance notification run failed")
            click.echo(
                "Low-attendance notification run failed. See server logs for details.",
                err=True,
            )
            raise SystemExit(1)

        lines, error_lines = format_run_report(
            result, sweep_settings, dry_run=dry_run
        )
        for line in lines:
            click.echo(line)
        for line in error_lines:
            click.echo(line, err=True)

        # A run that could not reach some students is not a successful
        # run, even though the rest were mailed. Exiting non-zero is what
        # lets a scheduled invocation notice.
        if result["failed"]:
            raise SystemExit(1)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=Config.DEBUG, port=Config.PORT)
