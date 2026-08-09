"""Tests for GET /api/health (see spec: 01-project-foundation.md, "APIs").

Spec contract under test:
  - GET /api/health always returns HTTP 200, with a JSON body containing
    `status`, `backend`, and a nested `database.connected` boolean --
    regardless of whether MongoDB is reachable.
  - When Mongo is down/misconfigured, the process must not crash,
    `database.connected` must be `false`, and no raw connection
    string/credentials should leak into the response.
  - The endpoint requires no authentication (public, per the spec's API list).

The MongoDB connection layer (database.db.get_db_status, used by the
route via `from database.db import get_db_status`) is mocked/monkeypatched
at its point of use (`routes.health.get_db_status`) so these tests never
require a live MongoDB instance.
"""


class TestHealthEndpointContract:
    def test_health_returns_http_200_when_database_connected(self, app_instance, monkeypatch):
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        assert response.status_code == 200

    def test_health_returns_http_200_when_database_disconnected(self, app_instance, monkeypatch):
        monkeypatch.setattr(
            "routes.health.get_db_status",
            lambda: {"connected": False, "error": "MongoDB unreachable"},
        )

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        # Per spec: the backend must stay up and keep answering even when
        # Mongo is unreachable -- this must NOT be a 500 or a dropped connection.
        assert response.status_code == 200

    def test_health_body_contains_required_top_level_fields(self, app_instance, monkeypatch):
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        body = response.get_json()
        assert body is not None
        assert "status" in body
        assert "backend" in body
        assert "database" in body
        assert isinstance(body["database"], dict)
        assert "connected" in body["database"]

    def test_health_reflects_connected_true_in_nested_database_field(self, app_instance, monkeypatch):
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        body = response.get_json()
        assert body["database"]["connected"] is True

    def test_health_reflects_connected_false_in_nested_database_field(self, app_instance, monkeypatch):
        monkeypatch.setattr(
            "routes.health.get_db_status",
            lambda: {"connected": False, "error": "MongoDB unreachable"},
        )

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        body = response.get_json()
        assert body["database"]["connected"] is False

    def test_health_response_content_type_is_json(self, app_instance, monkeypatch):
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        assert response.content_type.startswith("application/json")

    def test_health_requires_no_authentication(self, app_instance, monkeypatch):
        """Per spec: /api/health is public, no auth required."""
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})

        with app_instance.test_client() as client:
            # Deliberately send no Authorization header, no session cookie.
            response = client.get("/api/health")

        assert response.status_code == 200

    def test_health_rejects_unsupported_http_method(self, app_instance):
        with app_instance.test_client() as client:
            response = client.post("/api/health")

        # Flask's default method-not-allowed handling; proves the route is
        # GET-only and misuse doesn't crash the app.
        assert response.status_code == 405


class TestHealthMongoResilience:
    """Verifies the Definition-of-Done requirement that a real, unreachable
    MongoDB does not crash the Flask process and does not leak connection
    details -- simulated by making the underlying pymongo client raise,
    without needing a live database.
    """

    def test_health_survives_mongodb_being_completely_unreachable(self, app_instance, monkeypatch):
        import config
        import database.db as db_module

        monkeypatch.setattr(config.Config, "MONGODB_URI", "mongodb://unreachable-host:27017")

        def _raise_connection_error(*args, **kwargs):
            raise ConnectionError("simulated: could not reach MongoDB")

        monkeypatch.setattr(db_module, "MongoClient", _raise_connection_error)

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        assert response.status_code == 200
        body = response.get_json()
        assert body["database"]["connected"] is False

    def test_health_response_never_leaks_raw_mongodb_credentials(self, app_instance, monkeypatch):
        import config
        import database.db as db_module

        fake_uri = "mongodb+srv://svc_account:S3cretPassw0rd@cluster0.example.net/autoattend"
        monkeypatch.setattr(config.Config, "MONGODB_URI", fake_uri)

        def _raise_connection_error(*args, **kwargs):
            raise ConnectionError("simulated: could not reach MongoDB")

        monkeypatch.setattr(db_module, "MongoClient", _raise_connection_error)

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        raw_body = response.get_data(as_text=True)
        assert "svc_account" not in raw_body
        assert "S3cretPassw0rd" not in raw_body
        assert fake_uri not in raw_body