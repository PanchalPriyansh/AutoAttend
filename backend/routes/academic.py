"""REST endpoints for the academic hierarchy
(Institute -> Department -> Semester -> Course -> Class).

Handlers here only translate HTTP to the domain and back: validate input,
call academic/service.py, serialize the result. The integrity rules live
in the service; the exception-to-status-code mapping lives in the
blueprint error handlers below, so no handler needs its own try/except.

Writes are admin-only and reads are open to admin and faculty -- faculty
need the hierarchy to choose an attendance context. Authorization is
enforced here on every route, never only in the React app.
"""

import logging

from flask import Blueprint, jsonify, request
from pymongo.errors import PyMongoError

from academic.errors import (
    DuplicateError,
    HasChildrenError,
    NotFoundError,
    ValidationError,
)
from academic.levels import CLASS, COURSE, DEPARTMENT, INSTITUTE, SEMESTER
from academic.serializers import serialize, serialize_many
from academic.service import create_item, delete_item, list_items, update_item
from academic.validators import (
    parse_date,
    parse_object_id,
    require_end_after_start,
    require_non_empty_string,
)
from auth.decorators import role_required
from database.db import get_db

logger = logging.getLogger(__name__)

academic_bp = Blueprint("academic", __name__, url_prefix="/api")

READ_ROLES = ("admin", "faculty")


@academic_bp.errorhandler(ValidationError)
def _handle_validation_error(exc):
    return jsonify({"error": str(exc)}), 400


@academic_bp.errorhandler(NotFoundError)
def _handle_not_found_error(exc):
    return jsonify({"error": str(exc)}), 404


@academic_bp.errorhandler(DuplicateError)
@academic_bp.errorhandler(HasChildrenError)
def _handle_conflict_error(exc):
    return jsonify({"error": str(exc)}), 409


@academic_bp.errorhandler(PyMongoError)
@academic_bp.errorhandler(RuntimeError)
def _handle_database_error(exc):
    """Mirrors routes/auth.py: keeps responses on the JSON error contract
    instead of Flask's HTML/traceback page when MongoDB is unreachable
    (RuntimeError is what get_db() raises when MONGODB_URI is unset), and
    keeps raw driver text out of the response body.
    """
    logger.exception("Database error in an academic route")
    return jsonify({"error": "Service temporarily unavailable"}), 500


def _json_body():
    """A non-dict JSON payload (e.g. a bare list, string, or number) is
    valid JSON but not a usable request body -- treating it as empty
    routes it through the normal "missing field" -> ValidationError -> 400
    path instead of raising AttributeError from body.get(...) deep inside
    a validator.
    """
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _parent_id_from_query(level):
    """Child listings must be scoped to a parent, so the parent id is a
    required query parameter rather than an optional filter.
    """
    return parse_object_id(request.args.get(level.parent.field), level.parent.field)


def _text_fields(level, body):
    return {name: require_non_empty_string(body, name) for name in level.fields}


def _semester_fields(body):
    fields = _text_fields(SEMESTER, body)
    start_date = parse_date(body, "start_date")
    end_date = parse_date(body, "end_date")
    require_end_after_start(start_date, end_date)
    fields["start_date"] = start_date
    fields["end_date"] = end_date
    return fields


def _deleted_response():
    return jsonify({"deleted": True}), 200


# --- Institutes -------------------------------------------------------


@academic_bp.route("/institutes", methods=["GET"])
@role_required(*READ_ROLES)
def list_institutes():
    documents = list_items(get_db(), INSTITUTE)
    return jsonify({INSTITUTE.plural: serialize_many(INSTITUTE, documents)}), 200


@academic_bp.route("/institutes", methods=["POST"])
@role_required("admin")
def create_institute():
    fields = _text_fields(INSTITUTE, _json_body())
    document = create_item(get_db(), INSTITUTE, None, fields)
    return jsonify(serialize(INSTITUTE, document)), 201


@academic_bp.route("/institutes/<item_id>", methods=["PUT"])
@role_required("admin")
def update_institute(item_id):
    object_id = parse_object_id(item_id, "id")
    fields = _text_fields(INSTITUTE, _json_body())
    document = update_item(get_db(), INSTITUTE, object_id, fields)
    return jsonify(serialize(INSTITUTE, document)), 200


@academic_bp.route("/institutes/<item_id>", methods=["DELETE"])
@role_required("admin")
def delete_institute(item_id):
    delete_item(get_db(), INSTITUTE, parse_object_id(item_id, "id"))
    return _deleted_response()


# --- Departments ------------------------------------------------------


@academic_bp.route("/departments", methods=["GET"])
@role_required(*READ_ROLES)
def list_departments():
    documents = list_items(get_db(), DEPARTMENT, _parent_id_from_query(DEPARTMENT))
    return jsonify({DEPARTMENT.plural: serialize_many(DEPARTMENT, documents)}), 200


@academic_bp.route("/departments", methods=["POST"])
@role_required("admin")
def create_department():
    body = _json_body()
    parent_id = parse_object_id(body.get("institute_id"), "institute_id")
    document = create_item(get_db(), DEPARTMENT, parent_id, _text_fields(DEPARTMENT, body))
    return jsonify(serialize(DEPARTMENT, document)), 201


@academic_bp.route("/departments/<item_id>", methods=["PUT"])
@role_required("admin")
def update_department(item_id):
    object_id = parse_object_id(item_id, "id")
    fields = _text_fields(DEPARTMENT, _json_body())
    document = update_item(get_db(), DEPARTMENT, object_id, fields)
    return jsonify(serialize(DEPARTMENT, document)), 200


@academic_bp.route("/departments/<item_id>", methods=["DELETE"])
@role_required("admin")
def delete_department(item_id):
    delete_item(get_db(), DEPARTMENT, parse_object_id(item_id, "id"))
    return _deleted_response()


# --- Semesters --------------------------------------------------------


@academic_bp.route("/semesters", methods=["GET"])
@role_required(*READ_ROLES)
def list_semesters():
    documents = list_items(get_db(), SEMESTER, _parent_id_from_query(SEMESTER))
    return jsonify({SEMESTER.plural: serialize_many(SEMESTER, documents)}), 200


@academic_bp.route("/semesters", methods=["POST"])
@role_required("admin")
def create_semester():
    body = _json_body()
    parent_id = parse_object_id(body.get("department_id"), "department_id")
    document = create_item(get_db(), SEMESTER, parent_id, _semester_fields(body))
    return jsonify(serialize(SEMESTER, document)), 201


@academic_bp.route("/semesters/<item_id>", methods=["PUT"])
@role_required("admin")
def update_semester(item_id):
    object_id = parse_object_id(item_id, "id")
    fields = _semester_fields(_json_body())
    document = update_item(get_db(), SEMESTER, object_id, fields)
    return jsonify(serialize(SEMESTER, document)), 200


@academic_bp.route("/semesters/<item_id>", methods=["DELETE"])
@role_required("admin")
def delete_semester(item_id):
    delete_item(get_db(), SEMESTER, parse_object_id(item_id, "id"))
    return _deleted_response()


# --- Courses ----------------------------------------------------------


@academic_bp.route("/courses", methods=["GET"])
@role_required(*READ_ROLES)
def list_courses():
    documents = list_items(get_db(), COURSE, _parent_id_from_query(COURSE))
    return jsonify({COURSE.plural: serialize_many(COURSE, documents)}), 200


@academic_bp.route("/courses", methods=["POST"])
@role_required("admin")
def create_course():
    body = _json_body()
    parent_id = parse_object_id(body.get("semester_id"), "semester_id")
    document = create_item(get_db(), COURSE, parent_id, _text_fields(COURSE, body))
    return jsonify(serialize(COURSE, document)), 201


@academic_bp.route("/courses/<item_id>", methods=["PUT"])
@role_required("admin")
def update_course(item_id):
    object_id = parse_object_id(item_id, "id")
    fields = _text_fields(COURSE, _json_body())
    document = update_item(get_db(), COURSE, object_id, fields)
    return jsonify(serialize(COURSE, document)), 200


@academic_bp.route("/courses/<item_id>", methods=["DELETE"])
@role_required("admin")
def delete_course(item_id):
    delete_item(get_db(), COURSE, parse_object_id(item_id, "id"))
    return _deleted_response()


# --- Classes ----------------------------------------------------------


@academic_bp.route("/classes", methods=["GET"])
@role_required(*READ_ROLES)
def list_classes():
    documents = list_items(get_db(), CLASS, _parent_id_from_query(CLASS))
    return jsonify({CLASS.plural: serialize_many(CLASS, documents)}), 200


@academic_bp.route("/classes", methods=["POST"])
@role_required("admin")
def create_class():
    body = _json_body()
    parent_id = parse_object_id(body.get("course_id"), "course_id")
    document = create_item(get_db(), CLASS, parent_id, _text_fields(CLASS, body))
    return jsonify(serialize(CLASS, document)), 201


@academic_bp.route("/classes/<item_id>", methods=["PUT"])
@role_required("admin")
def update_class(item_id):
    object_id = parse_object_id(item_id, "id")
    fields = _text_fields(CLASS, _json_body())
    document = update_item(get_db(), CLASS, object_id, fields)
    return jsonify(serialize(CLASS, document)), 200


@academic_bp.route("/classes/<item_id>", methods=["DELETE"])
@role_required("admin")
def delete_class(item_id):
    delete_item(get_db(), CLASS, parse_object_id(item_id, "id"))
    return _deleted_response()
