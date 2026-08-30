import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt_identity,
    jwt_required,
    set_access_cookies,
    set_refresh_cookies,
    unset_jwt_cookies,
)
from pymongo.errors import PyMongoError

from auth.errors import InactiveAccountError, IncorrectPasswordError
from auth.password_change import change_password
from auth.service import authenticate_user, get_user_by_id, to_safe_profile
from common.errors import NotFoundError, ValidationError
from database.db import get_db
from users.validators import require_existing_password, require_password

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.errorhandler(PyMongoError)
@auth_bp.errorhandler(RuntimeError)
def _handle_database_error(exc):
    """Keeps auth responses on the JSON error contract instead of falling
    through to Flask's default HTML/traceback handler when MongoDB is
    unreachable or misconfigured (RuntimeError is raised by get_db() when
    MONGODB_URI is unset).
    """
    logger.exception("Database error in an auth route")
    return jsonify({"error": "Service temporarily unavailable"}), 500


@auth_bp.errorhandler(ValidationError)
def _handle_validation_error(exc):
    return jsonify({"error": str(exc)}), 400


@auth_bp.errorhandler(NotFoundError)
def _handle_not_found_error(exc):
    """Insurance, not a live path -- and deliberately not part of
    /api/auth/password's documented status codes.

    Nothing in this blueprint can currently raise a bare NotFoundError:
    the one source is set_user_password, and auth/password_change.py
    catches it and re-raises InactiveAccountError, because the answer to
    "your account no longer exists" is authenticate-again rather than a
    missing-resource page. This handler exists so that a LATER route
    added to auth_bp cannot fall through to Flask's default HTML
    traceback instead of the JSON error contract. 404 matches how every
    other blueprint in the project maps this exception, which is the
    right default for a route that has not been written yet.
    """
    return jsonify({"error": str(exc)}), 404


@auth_bp.errorhandler(IncorrectPasswordError)
def _handle_incorrect_password_error(exc):
    """403 rather than 401 -- see the class docstring in auth/errors.py.
    A 401 would be transparently retried by the frontend's apiFetch,
    submitting the same wrong password twice per attempt.
    """
    return jsonify({"error": str(exc)}), 403


@auth_bp.errorhandler(InactiveAccountError)
def _handle_inactive_account_error(exc):
    return jsonify({"error": str(exc)}), 401


@auth_bp.route("/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    email = body.get("email")
    password = body.get("password")

    if not isinstance(email, str) or not isinstance(password, str) or not email or not password:
        return jsonify({"error": "email and password are required"}), 400

    user = authenticate_user(get_db(), email, password)
    if user is None:
        return jsonify({"error": "Invalid email or password"}), 401

    identity = str(user["_id"])
    access_token = create_access_token(identity=identity, additional_claims={"role": user["role"]})
    refresh_token = create_refresh_token(identity=identity)

    response = jsonify(to_safe_profile(user))
    set_access_cookies(response, access_token)
    set_refresh_cookies(response, refresh_token)
    return response, 200


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    identity = get_jwt_identity()
    user = get_user_by_id(get_db(), identity)
    if user is None or not user.get("is_active", False):
        return jsonify({"error": "Authentication required"}), 401

    access_token = create_access_token(identity=identity, additional_claims={"role": user["role"]})
    response = jsonify({"refreshed": True})
    set_access_cookies(response, access_token)
    return response, 200


@auth_bp.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    response = jsonify({"message": "Logged out"})
    unset_jwt_cookies(response)
    return response, 200


@auth_bp.route("/password", methods=["POST"])
@jwt_required()
def change_own_password():
    """Change the signed-in user's own password.

    Any signed-in role, with no role check at all: an admin, a faculty
    member and a student have exactly equal standing over their own
    credential.

    No user id is accepted anywhere -- not in the path, not in the body.
    The account changed is the one the token identifies, so there is no id
    for a caller to tamper with, and any id-shaped field in the body is
    simply never read.

    Nothing is logged here. A failed attempt carries the submitted
    password in the caller's hand, and the surest way never to write one
    to a log file is to have no log statement on the path at all.
    """
    body = request.get_json(silent=True) or {}

    change_password(
        get_db(),
        get_jwt_identity(),
        # The current password is validated as present and non-empty only;
        # the length policy belongs to the password being SET. See
        # users/validators.py::require_existing_password.
        current_password=require_existing_password(body),
        # The same require_password that flask create-admin and the admin
        # reset use, so the three cannot disagree about the minimum.
        new_password=require_password(body, "new_password"),
    )

    # Deliberately not the profile, not updated_at, not the id: a write
    # endpoint that returns a document invites the client to start
    # trusting it as a read.
    return jsonify({"message": "Password changed"}), 200


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    user = get_user_by_id(get_db(), get_jwt_identity())
    if user is None:
        return jsonify({"error": "Authentication required"}), 401
    return jsonify(to_safe_profile(user)), 200
