"""Tests for the JWT_SECRET_KEY startup guard in backend/app.py.

Spec contract under test (03-authentication.md, "Rules for implementation"
+ "Definition of done"):
  - `JWT_SECRET_KEY` comes from the environment with no hardcoded
    fallback. If it is missing, `create_app()` must fail loudly at
    startup (a clear `RuntimeError`) rather than silently signing tokens
    with an empty or default key.
  - Starting the app with `JWT_SECRET_KEY` set succeeds.

`config.Config.JWT_SECRET_KEY` is monkeypatched directly (rather than via
environment variables + module reload) because `Config` is evaluated once
at process/collection time and other test modules already import it --
this only overrides the attribute for the duration of each test.
"""

import config
import pytest
from app import create_app


class TestJwtSecretKeyIsRequiredAtStartup:
    def test_create_app_raises_when_jwt_secret_key_is_none(self, monkeypatch):
        monkeypatch.setattr(config.Config, "JWT_SECRET_KEY", None)

        with pytest.raises(RuntimeError):
            create_app()

    def test_create_app_raises_when_jwt_secret_key_is_an_empty_string(self, monkeypatch):
        monkeypatch.setattr(config.Config, "JWT_SECRET_KEY", "")

        with pytest.raises(RuntimeError):
            create_app()

    def test_create_app_succeeds_when_jwt_secret_key_is_set(self, monkeypatch):
        monkeypatch.setattr(config.Config, "JWT_SECRET_KEY", "test-only-fake-signing-key")

        application = create_app()

        assert application is not None
        assert application.config["JWT_SECRET_KEY"] == "test-only-fake-signing-key"
