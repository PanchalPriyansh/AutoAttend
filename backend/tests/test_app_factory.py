"""Tests for backend/app.py (Flask application factory).

Spec contract under test (01-project-foundation.md, updated for
03-authentication.md and 04-academic-hierarchy-management.md):
  - `create_app()` builds and returns a working Flask app that serves
    `GET /api/health`.
  - CORS origins come from `Config.CORS_ORIGINS` (env-driven), not a
    hardcoded value.
  - Authentication (`/api/auth/*`) is in scope as of 03-authentication.md
    and the academic hierarchy (`/api/institutes`, `/api/departments`,
    `/api/semesters`, `/api/courses`, `/api/classes`) as of
    04-academic-hierarchy-management.md -- both are expected to be
    registered. Attendance, face recognition, ML, and the role portal
    endpoints remain out of scope until their own feature specs, so we
    still assert those are not registered yet.
"""

from flask import Flask

OUT_OF_SCOPE_ROUTE_FRAGMENTS = [
    "student",
    "faculty",
    "admin",
    "attendance",
    "recognition",
    "enrollment",
    "prediction",
    "notification",
]


class TestAppFactory:
    def test_create_app_returns_a_flask_application(self, app_instance):
        assert isinstance(app_instance, Flask)

    def test_health_route_is_registered_and_reachable(self, app_instance, monkeypatch):
        monkeypatch.setattr("routes.health.get_db_status", lambda: {"connected": True})

        with app_instance.test_client() as client:
            response = client.get("/api/health")

        assert response.status_code == 200

    def test_authentication_routes_are_registered(self, app_instance):
        registered_paths = {rule.rule for rule in app_instance.url_map.iter_rules()}

        for path in ("/api/auth/login", "/api/auth/refresh", "/api/auth/logout", "/api/auth/me"):
            assert path in registered_paths

    def test_academic_hierarchy_routes_are_registered(self, app_instance):
        registered_paths = {rule.rule for rule in app_instance.url_map.iter_rules()}

        for path in ("/api/institutes", "/api/departments", "/api/semesters",
                     "/api/courses", "/api/classes"):
            assert path in registered_paths
            assert f"{path}/<item_id>" in registered_paths

    def test_no_attendance_or_portal_routes_exist_yet(self, app_instance):
        registered_paths = [rule.rule for rule in app_instance.url_map.iter_rules()]

        assert "/api/health" in registered_paths

        for path in registered_paths:
            lowered = path.lower()
            for fragment in OUT_OF_SCOPE_ROUTE_FRAGMENTS:
                assert fragment not in lowered, (
                    f"Route '{path}' looks like out-of-scope business logic; "
                    "attendance, face recognition, ML, enrollment, and the "
                    "role portal endpoints are not implemented until their "
                    "own feature specs."
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