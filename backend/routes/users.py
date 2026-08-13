"""REST endpoints for admin-managed user accounts, faculty assignment,
and student enrollment.

Handlers here only translate HTTP to the domain and back: validate input,
call users/service.py or users/assignments.py, serialize the result. The
rules live in those modules; the exception-to-status-code mapping lives in
the blueprint error handlers below, so no handler needs its own
try/except.

Every endpoint is admin-only, reads included. Faculty do not need a user
directory to take attendance, and a roster scoped to the requesting
faculty member belongs to the attendance feature, which needs its own
ownership check. Authorization is enforced here, never only in React.

View functions are named `*_account` / `*_class_*` rather than after the
service functions they call: a view named `update_user` would rebind the
imported `update_user` in this module's namespace and quietly recurse
into itself.
"""

import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pymongo.errors import PyMongoError

from academic.levels import CLASS
from academic.serializers import serialize as serialize_level
from auth.decorators import role_required
from common.errors import ConflictError, NotFoundError, ValidationError
from common.http import json_body as _json_body
from common.validators import parse_object_id, require_non_empty_string
from database.db import get_db
from users.assignments import (
    assign_faculty,
    enroll_student,
    list_enrollments,
    unenroll_student,
)
from users.serializers import (
    serialize_enrollment,
    serialize_enrollments,
    serialize_user,
    serialize_users,
)
from users.service import (
    create_managed_user,
    get_user,
    list_users,
    set_user_password,
    set_user_status,
    update_user,
)
from users.validators import (
    parse_optional_object_id,
    parse_optional_role,
    require_bool,
    require_email,
    require_nullable_object_id,
    require_password,
    require_role,
)

logger = logging.getLogger(__name__)

users_bp = Blueprint("users", __name__, url_prefix="/api")


@users_bp.errorhandler(ValidationError)
def _handle_validation_error(exc):
    return jsonify({"error": str(exc)}), 400


@users_bp.errorhandler(NotFoundError)
def _handle_not_found_error(exc):
    return jsonify({"error": str(exc)}), 404


@users_bp.errorhandler(ConflictError)
def _handle_conflict_error(exc):
    """DuplicateError (raised by users/service.py and users/assignments.py
    on a unique-index collision) subclasses ConflictError, and Flask
    resolves error handlers by walking the raised exception's __mro__ --
    so this single registration already covers both.
    """
    return jsonify({"error": str(exc)}), 409


@users_bp.errorhandler(PyMongoError)
@users_bp.errorhandler(RuntimeError)
def _handle_database_error(exc):
    """Mirrors routes/auth.py and routes/academic.py: keeps responses on
    the JSON error contract instead of Flask's HTML/traceback page when
    MongoDB is unreachable (RuntimeError is what get_db() raises when
    MONGODB_URI is unset), and keeps raw driver text out of the body.
    """
    logger.exception("Database error in a user route")
    return jsonify({"error": "Service temporarily unavailable"}), 500


def _acting_user_id():
    """The admin making the request, taken from the verified token rather
    than from anything the client sent.

    Parsed into an ObjectId because get_jwt_identity() returns a string,
    and a string never equals the ObjectId stored in `_id` -- comparing
    the two directly would leave the self-deactivation guard permanently
    unable to fire.
    """
    return parse_object_id(get_jwt_identity(), "id")


# --- User accounts ----------------------------------------------------


@users_bp.route("/users", methods=["GET"])
@role_required("admin")
def list_user_accounts():
    documents = list_users(
        get_db(),
        role=parse_optional_role(request.args.get("role")),
        institute_id=parse_optional_object_id(
            request.args.get("institute_id"), "institute_id"
        ),
        q=request.args.get("q"),
    )
    return jsonify({"users": serialize_users(documents)}), 200


@users_bp.route("/users", methods=["POST"])
@role_required("admin")
def create_user_account():
    body = _json_body()
    document = create_managed_user(
        get_db(),
        name=require_non_empty_string(body, "name"),
        email=require_email(body),
        password=require_password(body),
        role=require_role(body.get("role")),
        institute_id=parse_optional_object_id(body.get("institute_id"), "institute_id"),
    )
    return jsonify(serialize_user(document)), 201


@users_bp.route("/users/<user_id>", methods=["GET"])
@role_required("admin")
def get_user_account(user_id):
    document = get_user(get_db(), parse_object_id(user_id, "id"))
    return jsonify(serialize_user(document)), 200


@users_bp.route("/users/<user_id>", methods=["PUT"])
@role_required("admin")
def update_user_account(user_id):
    body = _json_body()
    document = update_user(
        get_db(),
        parse_object_id(user_id, "id"),
        name=require_non_empty_string(body, "name"),
        email=require_email(body),
        # Required rather than optional: PUT replaces the full set of
        # editable fields, so an omitted key is a malformed request, not
        # "leave this unchanged" -- an admin who forgets to send
        # institute_id should get a 400, not silently unassign it.
        institute_id=require_nullable_object_id(body, "institute_id"),
    )
    return jsonify(serialize_user(document)), 200


@users_bp.route("/users/<user_id>/status", methods=["PUT"])
@role_required("admin")
def update_user_status(user_id):
    document = set_user_status(
        get_db(),
        parse_object_id(user_id, "id"),
        is_active=require_bool(_json_body(), "is_active"),
        acting_user_id=_acting_user_id(),
    )
    return jsonify(serialize_user(document)), 200


@users_bp.route("/users/<user_id>/password", methods=["PUT"])
@role_required("admin")
def update_user_password(user_id):
    document = set_user_password(
        get_db(),
        parse_object_id(user_id, "id"),
        password=require_password(_json_body()),
    )
    return jsonify(serialize_user(document)), 200


# --- Faculty assignment -----------------------------------------------


@users_bp.route("/classes/<class_id>/faculty", methods=["PUT"])
@role_required("admin")
def assign_class_faculty(class_id):
    document = assign_faculty(
        get_db(),
        parse_object_id(class_id, "id"),
        require_nullable_object_id(_json_body(), "faculty_id"),
    )
    # Serialized as a class, so the response matches GET /api/classes and
    # the admin UI can update in place without a refetch.
    return jsonify(serialize_level(CLASS, document)), 200


# --- Student enrollment -----------------------------------------------


@users_bp.route("/classes/<class_id>/students", methods=["GET"])
@role_required("admin")
def list_class_students(class_id):
    pairs = list_enrollments(get_db(), parse_object_id(class_id, "id"))
    return jsonify({"enrollments": serialize_enrollments(pairs)}), 200


@users_bp.route("/classes/<class_id>/students", methods=["POST"])
@role_required("admin")
def enroll_class_student(class_id):
    enrollment, student = enroll_student(
        get_db(),
        parse_object_id(class_id, "id"),
        parse_object_id(_json_body().get("student_id"), "student_id"),
    )
    return jsonify(serialize_enrollment(enrollment, student)), 201


@users_bp.route("/classes/<class_id>/students/<student_id>", methods=["DELETE"])
@role_required("admin")
def unenroll_class_student(class_id, student_id):
    unenroll_student(
        get_db(),
        parse_object_id(class_id, "id"),
        parse_object_id(student_id, "student_id"),
    )
    return jsonify({"deleted": True}), 200
