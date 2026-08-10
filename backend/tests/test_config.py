"""Tests for backend/config.py.

Spec contract under test:
  - 01-project-foundation.md:
    - `MONGODB_URI` and any other configuration must come from environment
      variables (`.env`), never hardcoded.
    - `PORT`, `CORS_ORIGINS`, and `FLASK_ENV`/debug likewise come from the
      environment, with sane defaults when unset.
  - 02-database-setup.md:
    - `get_db()` selects the database via `Config.MONGODB_DB_NAME`, which
      itself is read from the `MONGODB_DB_NAME` environment variable and
      defaults to `"autoattend"` when unset.

Each test reloads the `config` module after adjusting `os.environ` via
monkeypatch, so Config's class attributes (assigned at import time) are
recomputed from the environment being tested. `load_dotenv` is stubbed
out during the reload so a real `.env` file on a developer's or CI
machine can never influence these tests.
"""

import importlib

import config as config_module
import pytest


@pytest.fixture(autouse=True)
def _restore_config_module_after_test():
    """Leave the config module reflecting the real process environment
    again once each test (and its monkeypatches) has finished, so later
    test modules that only ever imported `Config` once are unaffected.
    """
    yield
    importlib.reload(config_module)


def _reload_with_env(monkeypatch, env):
    # Never let a real .env file leak into these deterministic tests.
    #
    # Patching config_module.load_dotenv directly doesn't survive the
    # importlib.reload() below: config.py's `from dotenv import load_dotenv`
    # re-executes on reload and rebinds the name back to the real function
    # before it's called. Patching the attribute on the `dotenv` module
    # itself does survive, since config.py re-imports it from there.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)

    for key in ("MONGODB_URI", "MONGODB_DB_NAME", "FLASK_ENV", "PORT", "CORS_ORIGINS"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    importlib.reload(config_module)
    return config_module.Config


class TestMongoDbUriFromEnvironment:
    def test_mongodb_uri_is_read_from_environment_variable(self, monkeypatch):
        Config = _reload_with_env(monkeypatch, {"MONGODB_URI": "mongodb://test-host:27017/testdb"})

        assert Config.MONGODB_URI == "mongodb://test-host:27017/testdb"

    def test_mongodb_uri_defaults_to_empty_string_when_unset(self, monkeypatch):
        Config = _reload_with_env(monkeypatch, {})

        assert Config.MONGODB_URI == ""


class TestMongoDbNameFromEnvironment:
    def test_mongodb_db_name_is_read_from_environment_variable(self, monkeypatch):
        Config = _reload_with_env(monkeypatch, {"MONGODB_DB_NAME": "autoattend_staging"})

        assert Config.MONGODB_DB_NAME == "autoattend_staging"

    def test_mongodb_db_name_defaults_to_autoattend_when_unset(self, monkeypatch):
        Config = _reload_with_env(monkeypatch, {})

        assert Config.MONGODB_DB_NAME == "autoattend"


class TestPortFromEnvironment:
    def test_port_is_read_from_environment_and_cast_to_int(self, monkeypatch):
        Config = _reload_with_env(monkeypatch, {"PORT": "8080"})

        assert Config.PORT == 8080
        assert isinstance(Config.PORT, int)

    def test_port_defaults_to_5000_when_unset(self, monkeypatch):
        Config = _reload_with_env(monkeypatch, {})

        assert Config.PORT == 5000


class TestCorsOriginsFromEnvironment:
    def test_cors_origins_is_read_from_environment_variable(self, monkeypatch):
        Config = _reload_with_env(monkeypatch, {"CORS_ORIGINS": "https://autoattend.example.com"})

        assert Config.CORS_ORIGINS == "https://autoattend.example.com"

    def test_cors_origins_has_a_non_empty_default_when_unset(self, monkeypatch):
        Config = _reload_with_env(monkeypatch, {})

        assert Config.CORS_ORIGINS


class TestFlaskEnvAndDebugFromEnvironment:
    def test_debug_is_true_when_flask_env_is_development(self, monkeypatch):
        Config = _reload_with_env(monkeypatch, {"FLASK_ENV": "development"})

        assert Config.DEBUG is True

    @pytest.mark.parametrize("flask_env", ["production", "testing", "staging"])
    def test_debug_is_false_when_flask_env_is_not_development(self, monkeypatch, flask_env):
        Config = _reload_with_env(monkeypatch, {"FLASK_ENV": flask_env})

        assert Config.DEBUG is False

    def test_debug_defaults_to_false_when_flask_env_unset(self, monkeypatch):
        Config = _reload_with_env(monkeypatch, {})

        assert Config.DEBUG is False
        assert Config.FLASK_ENV == "production"