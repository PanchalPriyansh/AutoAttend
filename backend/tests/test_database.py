"""Tests for backend/database/db.py (MongoDB connection helper).

Spec contract under test (01-project-foundation.md):
  - The MongoDB connection is driven entirely by the `MONGODB_URI`
    environment variable (via config.Config).
  - Connection failures must be handled gracefully -- `get_db_status()`
    must never raise, and must report `connected: False` instead.
  - No raw credentials should ever appear in the returned status.

pymongo's MongoClient is monkeypatched so these tests run without any
live MongoDB instance (CI-safe, deterministic, no network access).
"""

import database.db as db_module
from config import Config
from pymongo.errors import PyMongoError


class _FakeAdminOk:
    def command(self, name):
        return {"ok": 1}


class _FakeAdminPyMongoError:
    def command(self, name):
        raise PyMongoError("simulated pymongo connectivity failure")


class _FakeAdminUnexpectedError:
    def command(self, name):
        raise RuntimeError("simulated unexpected failure")


class _FakeMongoClient:
    def __init__(self, uri, admin):
        self.uri = uri
        self.admin = admin


def _fake_client_factory(admin_cls):
    def factory(uri, **kwargs):
        return _FakeMongoClient(uri, admin_cls())

    return factory


class TestGetDbStatusWhenUriMissing:
    def test_returns_disconnected_when_mongodb_uri_is_not_configured(self, monkeypatch):
        monkeypatch.setattr(Config, "MONGODB_URI", "")

        status = db_module.get_db_status()

        assert status["connected"] is False

    def test_does_not_attempt_a_connection_when_uri_is_missing(self, monkeypatch):
        monkeypatch.setattr(Config, "MONGODB_URI", "")

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("MongoClient should not be constructed without a configured URI")

        monkeypatch.setattr(db_module, "MongoClient", _fail_if_called)

        status = db_module.get_db_status()

        assert status["connected"] is False


class TestGetDbStatusConnectivity:
    def test_returns_connected_true_when_ping_succeeds(self, monkeypatch):
        monkeypatch.setattr(Config, "MONGODB_URI", "mongodb://localhost:27017")
        monkeypatch.setattr(db_module, "MongoClient", _fake_client_factory(_FakeAdminOk))

        status = db_module.get_db_status()

        assert status["connected"] is True

    def test_status_connected_field_is_a_boolean(self, monkeypatch):
        monkeypatch.setattr(Config, "MONGODB_URI", "mongodb://localhost:27017")
        monkeypatch.setattr(db_module, "MongoClient", _fake_client_factory(_FakeAdminOk))

        status = db_module.get_db_status()

        assert isinstance(status["connected"], bool)

    def test_returns_disconnected_without_raising_on_pymongo_error(self, monkeypatch):
        monkeypatch.setattr(Config, "MONGODB_URI", "mongodb://localhost:27017")
        monkeypatch.setattr(db_module, "MongoClient", _fake_client_factory(_FakeAdminPyMongoError))

        status = db_module.get_db_status()  # must not raise

        assert status["connected"] is False

    def test_returns_disconnected_without_raising_on_unexpected_error(self, monkeypatch):
        """The DB layer must not let an unanticipated exception bubble up
        and crash the Flask process -- per spec's "handled gracefully"
        requirement.
        """
        monkeypatch.setattr(Config, "MONGODB_URI", "mongodb://localhost:27017")
        monkeypatch.setattr(
            db_module, "MongoClient", _fake_client_factory(_FakeAdminUnexpectedError)
        )

        status = db_module.get_db_status()  # must not raise

        assert status["connected"] is False


class TestGetDbStatusDoesNotLeakCredentials:
    def test_disconnected_status_does_not_include_raw_uri_or_credentials(self, monkeypatch):
        secret_uri = "mongodb+srv://svc_user:S3cretPass@cluster0.example.net/autoattend"
        monkeypatch.setattr(Config, "MONGODB_URI", secret_uri)
        monkeypatch.setattr(db_module, "MongoClient", _fake_client_factory(_FakeAdminPyMongoError))

        status = db_module.get_db_status()
        serialized = str(status)

        assert "svc_user" not in serialized
        assert "S3cretPass" not in serialized
        assert secret_uri not in serialized

    def test_connected_status_does_not_include_raw_uri(self, monkeypatch):
        secret_uri = "mongodb+srv://svc_user:S3cretPass@cluster0.example.net/autoattend"
        monkeypatch.setattr(Config, "MONGODB_URI", secret_uri)
        monkeypatch.setattr(db_module, "MongoClient", _fake_client_factory(_FakeAdminOk))

        status = db_module.get_db_status()
        serialized = str(status)

        assert secret_uri not in serialized