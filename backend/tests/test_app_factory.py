"""Tests for backend/app.py (Flask application factory).

Spec contract under test (01-project-foundation.md):
  - `create_app()` builds and returns a working Flask app that serves
    `GET /api/health`.
  - CORS origins come from `Config.CORS_ORIGINS` (env-driven), not a
    hardcoded value.
  - This feature is foundation-only: "No authentication, authorization,
    or academic-data logic exists anywhere in this feature's code" (DoD).
    We assert no such routes are registered yet, rather than testing any
    real auth/academic behavior that doesn't exist.
"""

from flask import Flask

OUT_OF_SCOPE_ROUTE_FRAGMENTS = [
    "login",
    "auth",
    "student",
    "faculty",
    "admin",
    "attendance",
    "course",
    "class",
    "department",
    "institute",
]


class TestAppFactory:
    def test_create_app_returns_a_flask_application(self, app_instance):
        assert isinstance(app_instance, Flask)

    def test_health_route_is_registered_and_reachable(self, app_instance, monkeypatch):
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        assert response.status_code == 200

    def test_no_authentication_or_academic_hierarchy_routes_exist_yet(self, app_instance):
        registered_paths = [rule.rule for rule in app_instance.url_map.iter_rules()]

        assert "/api/health" in registered_paths

        for path in registered_paths:
            lowered = path.lower()
            for fragment in OUT_OF_SCOPE_ROUTE_FRAGMENTS:
                assert fragment not in lowered, (
                    f"Route '{path}' looks like out-of-scope business logic; "
                    "the project-foundation feature must not implement "
                    "authentication or academic-data endpoints yet."
                )


class TestCorsConfiguration:
    def test_cors_allows_the_configured_origin(self, monkeypatch):
        import config
        from app import create_app

        monkeypatch.setattr(config.Config, "CORS_ORIGINS", "https://allowed.example.com")
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})

        application = create_app()
        with application.test_client() as client:
            response = client.get(
                "/api/health", headers={"Origin": "https://allowed.example.com"}
            )

        assert response.headers.get("Access-Control-Allow-Origin") == "https://allowed.example.com"

    def test_cors_does_not_echo_back_an_unconfigured_origin(self, monkeypatch):
        import config
        from app import create_app

        monkeypatch.setattr(config.Config, "CORS_ORIGINS", "https://allowed.example.com")
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})

        application = create_app()
        with application.test_client() as client:
            response = client.get(
                "/api/health", headers={"Origin": "https://not-allowed.example.com"}
            )

        allow_origin = response.headers.get("Access-Control-Allow-Origin")
        assert allow_origin != "https://not-allowed.example.com"