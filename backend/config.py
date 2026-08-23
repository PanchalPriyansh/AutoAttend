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

    # --- Low-attendance notifications --------------------------------
    #
    # Read here, but only ever consumed by the `notify-low-attendance` CLI
    # command via notifications/settings.py. Nothing on a request path
    # touches these, and no route exposes them.
    #
    # The four credential-shaped values have no fallback, for the same
    # reason JWT_SECRET_KEY has none: a missing credential must fail
    # loudly where it is used, never silently send mail from a made-up
    # default address.
    SMTP_HOST = os.environ.get("SMTP_HOST")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
    SMTP_FROM_ADDRESS = os.environ.get("SMTP_FROM_ADDRESS")
    SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "AutoAttend")
    # Mirrors JWT_COOKIE_SECURE's idiom: anything other than an explicit
    # "false" keeps STARTTLS on, so a typo fails closed rather than
    # sending credentials over a plaintext connection.
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() != "false"

    # The attendance bar a student must clear, as a plain percentage --
    # not a model output and not a prediction. Parsed here the way PORT
    # is; the *range* check ((0, 100] and >= 0 respectively) belongs to
    # notifications/settings.py, since it is a domain rule rather than a
    # type conversion.
    LOW_ATTENDANCE_THRESHOLD = float(os.environ.get("LOW_ATTENDANCE_THRESHOLD", "75"))
    # How long a student is left alone about one class after being mailed
    # about it. 0 disables the cooldown entirely.
    NOTIFICATION_COOLDOWN_DAYS = int(os.environ.get("NOTIFICATION_COOLDOWN_DAYS", "7"))
