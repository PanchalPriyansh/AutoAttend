import logging

import click
from flask import Flask, jsonify
from flask_cors import CORS
from flask_jwt_extended import JWTManager

from auth.service import DuplicateEmailError, create_user
from common.errors import ValidationError
from config import Config
from database.db import get_db
from database.init_db import init_database
from routes.academic import academic_bp
from routes.auth import auth_bp
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

    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(academic_bp)
    app.register_blueprint(users_bp)

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

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=Config.DEBUG, port=Config.PORT)
