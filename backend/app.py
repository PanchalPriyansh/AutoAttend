import logging

import click
from flask import Flask
from flask_cors import CORS

from config import Config
from database.db import get_db
from database.init_db import init_database
from routes.health import health_bp

logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # CORS_ORIGINS is a single origin string today. If multiple origins are
    # ever needed, split it into a list here rather than loosening this to "*".
    CORS(app, origins=Config.CORS_ORIGINS)

    app.register_blueprint(health_bp)

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

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=Config.DEBUG, port=Config.PORT)
