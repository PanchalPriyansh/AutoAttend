import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()


class Config:
    MONGODB_URI = os.environ.get("MONGODB_URI", "")
    MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "autoattend")
    FLASK_ENV = os.environ.get("FLASK_ENV", "production")
    PORT = int(os.environ.get("PORT", "5000"))
    CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "http://localhost:5173")
    DEBUG = FLASK_ENV == "development"

    # JWT_SECRET_KEY has no fallback -- create_app() must fail loudly at
    # startup if it is unset rather than silently signing tokens with an
    # empty/default key.
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
    JWT_TOKEN_LOCATION = ["cookies"]
    JWT_COOKIE_CSRF_PROTECT = True
    JWT_COOKIE_SAMESITE = "Lax"
    # Must be "false" for local http development; real deployments should
    # keep the default (secure) True.
    JWT_COOKIE_SECURE = os.environ.get("JWT_COOKIE_SECURE", "true").lower() != "false"
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(minutes=15)
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=7)
    # Scopes the refresh cookie to only be sent on the refresh call itself,
    # not on every ordinary API request.
    JWT_REFRESH_COOKIE_PATH = "/api/auth/refresh"
