"""Shared pytest fixtures for the AutoAttend backend test suite.

These tests target the "Project Foundation" feature (see
.claude/specs/01-project-foundation.md): a Flask app factory, an
env-driven config module, a lazily-connected MongoDB helper, and a
public /api/health endpoint. No authentication/authorization or
academic-data logic exists yet, so tests here only verify the
foundation contract, not any business logic.
"""

import os
import sys

import pytest

# Make the `backend/` package importable as top-level modules (app, config,
# database.db, routes.health), matching how the app is actually run
# (`python app.py` from within `backend/`), regardless of the directory
# pytest is invoked from.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


@pytest.fixture(autouse=True)
def reset_mongo_client_cache():
    """Reset the module-level cached MongoClient before/after every test.

    database/db.py memoizes a single MongoClient instance in a module
    global. Without resetting it, a fake/mocked client created by one
    test could leak into a later test and hide real behavior.
    """
    import database.db as db_module

    db_module._client = None
    yield
    db_module._client = None


@pytest.fixture
def app_instance():
    """A freshly created Flask application via the create_app() factory."""
    from app import create_app

    application = create_app()
    application.testing = True
    return application