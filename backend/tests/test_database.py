"""Tests for backend/database/db.py (MongoDB connection helper).

Spec contract under test:
  - 01-project-foundation.md:
    - The MongoDB connection is driven entirely by the `MONGODB_URI`
      environment variable (via config.Config).
    - Connection failures must be handled gracefully -- `get_db_status()`
      must never raise, and must report `connected: False` instead.
    - No raw credentials should ever appear in the returned status.
  - 02-database-setup.md:
    - `get_db()` returns the pymongo `Database` selected via
      `Config.MONGODB_DB_NAME`, reusing the existing `_get_client()`
      singleton rather than opening a second connection.
    - `get_db()` raises a clear error (not a raw pymongo failure) when
      `MONGODB_URI` is not configured, and never attempts to construct a
      client in that case.

pymongo's MongoClient is monkeypatched so these tests run without any
live MongoDB instance (CI-safe, deterministic, no network access).
"""

import database.db as db_module
import pytest
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


class _FakeDatabase:
    """Stands in for a pymongo `Database` object returned by
    `client[db_name]` -- a distinguishable sentinel so tests can assert
    identity/attributes without a real pymongo dependency.
    """

    def __init__(self, name):
        self.name = name


class _FakeMongoClientWithDbAccess:
    """Stands in for a pymongo `MongoClient` that supports both the
    `.admin.command(...)` calls used by `get_db_status()` and the
    `client[db_name]` subscript access used by `get_db()`.
    """

    def __init__(self, uri, admin=None):
        self.uri = uri
        self.admin = admin or _FakeAdminOk()
        self.requested_db_names = []

    def __getitem__(self, name):
        self.requested_db_names.append(name)
        return _FakeDatabase(name)


class TestGetDbWhenUriMissing:
    def test_raises_runtime_error_when_mongodb_uri_is_not_configured(self, monkeypatch):
        monkeypatch.setattr(Config, "MONGODB_URI", "")

        with pytest.raises(RuntimeError):
            db_module.get_db()

    def test_does_not_attempt_to_construct_a_client_when_uri_is_missing(self, monkeypatch):
        monkeypatch.setattr(Config, "MONGODB_URI", "")

        def _fail_if_called(*args, **kwargs):
            raise AssertionError("MongoClient should not be constructed without a configured URI")

        monkeypatch.setattr(db_module, "MongoClient", _fail_if_called)

        with pytest.raises(RuntimeError):
            db_module.get_db()


class TestGetDbSelectsConfiguredDatabase:
    def test_returns_the_database_selected_by_mongodb_db_name(self, monkeypatch):
        monkeypatch.setattr(Config, "MONGODB_URI", "mongodb://localhost:27017")
        monkeypatch.setattr(Config, "MONGODB_DB_NAME", "autoattend_test")

        constructed_clients = []

        def factory(uri, **kwargs):
            client = _FakeMongoClientWithDbAccess(uri)
            constructed_clients.append(client)
            return client

        monkeypatch.setattr(db_module, "MongoClient", factory)

        result = db_module.get_db()

        assert len(constructed_clients) == 1
        client = constructed_clients[0]
        assert client.requested_db_names == ["autoattend_test"]
        assert isinstance(result, _FakeDatabase)
        assert result.name == "autoattend_test"

    def test_get_db_reuses_the_same_underlying_client_as_get_db_status(self, monkeypatch):
        monkeypatch.setattr(Config, "MONGODB_URI", "mongodb://localhost:27017")
        monkeypatch.setattr(Config, "MONGODB_DB_NAME", "autoattend_test")

        constructed_clients = []

        def factory(uri, **kwargs):
            client = _FakeMongoClientWithDbAccess(uri)
            constructed_clients.append(client)
            return client

        monkeypatch.setattr(db_module, "MongoClient", factory)

        status = db_module.get_db_status()
        db = db_module.get_db()

        assert status["connected"] is True
        # Only one MongoClient should ever be constructed across both
        # calls -- get_db() must reuse the existing _get_client() singleton
        # rather than opening a second connection.
        assert len(constructed_clients) == 1
        assert db.name == "autoattend_test"


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