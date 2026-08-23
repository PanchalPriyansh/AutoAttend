"""Tests for backend/app.py (Flask application factory).

Spec contract under test (01-project-foundation.md, updated for
03-authentication.md and 04-academic-hierarchy-management.md):
  - `create_app()` builds and returns a working Flask app that serves
    `GET /api/health`.
  - CORS origins come from `Config.CORS_ORIGINS` (env-driven), not a
    hardcoded value.
  - Authentication (`/api/auth/*`) is in scope as of 03-authentication.md,
    the academic hierarchy (`/api/institutes`, `/api/departments`,
    `/api/semesters`, `/api/courses`, `/api/classes`) as of
    04-academic-hierarchy-management.md, and user management
    (`/api/users`, plus the class-scoped `/api/classes/<id>/faculty` and
    `/api/classes/<id>/students`) as of 05-admin-user-management.md, and
    face enrolment (`/api/students/<id>/face-encodings`,
    `/api/classes/<id>/face-enrollment`) as of 06-face-enrollment.md --
    all four are expected to be registered. Attendance matching, ML, and
    the role portal endpoints remain out of scope until their own feature
    specs, so we still assert those are not registered yet.

    The out-of-scope check is split in two because the role words are no
    longer safely matched as substrings: `/api/classes/<id>/faculty` and
    `/api/classes/<id>/students` are class-scoped resources owned by
    05-admin-user-management.md, not role portals. A role portal is a
    top-level namespace, so it is checked as a path prefix -- which still
    catches a premature `/api/faculty/classes` or `/api/student/me`.
    "enrollment" is gone from the list entirely: student enrollment is
    implemented as of 05, so it is no longer out of scope. (It would not
    have matched `/api/classes/<id>/students` regardless; it is removed
    because it is no longer true, not to make an assertion pass.)
"""

from flask import Flask

OUT_OF_SCOPE_ROUTE_PREFIXES = [
    "/api/student",
    "/api/faculty",
    "/api/admin",
]


def _is_role_portal_namespace(path, prefix):
    """A role portal is a whole top-level path segment (`/api/student`,
    `/api/student/me`), not any path that merely starts with those
    characters.

    Matched on the segment boundary since 06-face-enrollment.md added
    `/api/students/<id>/face-encodings` -- a plural *resource* collection in
    the style of the existing `/api/classes/<id>/students`, not a role
    portal. A bare `startswith` would read the two as the same thing and
    still reject the resource route. `/api/faculty/classes` and
    `/api/student/me` remain caught.
    """
    return path == prefix or path.startswith(f"{prefix}/")

# "attendance" was removed when .claude/specs/07-attendance-capture.md
# landed and legitimately introduced those routes, the same way
# 06-face-enrollment.md earned face enrolment.
#
# The two remaining entries are not the same kind of thing:
#   - "prediction"/"risk" are permanent. AutoAttend does no model training
#     or prediction, and CLAUDE.md now rules the academic-risk model out of
#     scope entirely -- so nothing should ever earn these back.
#   - "notification" is temporary, and the low-attendance email spec is
#     expected to remove it the way 07 removed "attendance".
OUT_OF_SCOPE_ROUTE_FRAGMENTS = [
    "prediction",
    "risk",
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

    def test_user_management_routes_are_registered(self, app_instance):
        registered_paths = {rule.rule for rule in app_instance.url_map.iter_rules()}

        for path in (
            "/api/users",
            "/api/users/<user_id>",
            "/api/users/<user_id>/status",
            "/api/users/<user_id>/password",
            "/api/classes/<class_id>/faculty",
            "/api/classes/<class_id>/students",
            "/api/classes/<class_id>/students/<student_id>",
        ):
            assert path in registered_paths

    def test_face_enrollment_routes_are_registered(self, app_instance):
        registered_paths = {rule.rule for rule in app_instance.url_map.iter_rules()}

        for path in (
            "/api/students/<student_id>/face-encodings",
            "/api/students/<student_id>/face-encodings/<encoding_id>",
            "/api/classes/<class_id>/face-enrollment",
        ):
            assert path in registered_paths

    def test_no_user_delete_route_is_registered(self, app_instance):
        """Accounts are deactivated, never removed -- a hard delete would
        orphan the user's enrollments, and later their attendance records
        and face encodings.
        """
        methods = set()
        for rule in app_instance.url_map.iter_rules():
            if rule.rule == "/api/users/<user_id>":
                methods |= rule.methods

        assert "GET" in methods
        assert "PUT" in methods
        assert "DELETE" not in methods

    def test_no_role_portal_routes_exist_yet(self, app_instance):
        registered_paths = [rule.rule for rule in app_instance.url_map.iter_rules()]

        assert "/api/health" in registered_paths

        for path in registered_paths:
            lowered = path.lower()
            for prefix in OUT_OF_SCOPE_ROUTE_PREFIXES:
                assert not _is_role_portal_namespace(lowered, prefix), (
                    f"Route '{path}' looks like a role portal namespace; the "
                    "admin, faculty, and student portal endpoints are not "
                    "implemented until their own feature specs."
                )

    def test_no_ml_or_notification_routes_exist(self, app_instance):
        registered_paths = [rule.rule for rule in app_instance.url_map.iter_rules()]

        assert "/api/health" in registered_paths

        for path in registered_paths:
            lowered = path.lower()
            for fragment in OUT_OF_SCOPE_ROUTE_FRAGMENTS:
                assert fragment not in lowered, (
                    f"Route '{path}' looks like out-of-scope business logic; "
                    "risk prediction is permanently out of scope for this "
                    "project, and notifications are not implemented until "
                    "their own feature spec. (Face *enrolment* is in "
                    "scope as of 06-face-enrollment.md and classroom "
                    "recognition as of 07-attendance-capture.md.)"
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