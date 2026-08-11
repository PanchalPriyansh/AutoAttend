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

from auth.service import authenticate_user, get_user_by_id, to_safe_profile
from database.db import get_db

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


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    user = get_user_by_id(get_db(), get_jwt_identity())
    if user is None:
        return jsonify({"error": "Authentication required"}), 401
    return jsonify(to_safe_profile(user)), 200
