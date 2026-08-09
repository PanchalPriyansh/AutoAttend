from flask import Flask
from flask_cors import CORS

from config import Config
from routes.health import health_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # CORS_ORIGINS is a single origin string today. If multiple origins are
    # ever needed, split it into a list here rather than loosening this to "*".
    CORS(app, origins=Config.CORS_ORIGINS)

    app.register_blueprint(health_bp)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=Config.DEBUG, port=Config.PORT)
