"""REST endpoints for registering and managing student face encodings.

Handlers here only translate HTTP to the domain and back: pull the file
part off the request, validate it, call recognition/service.py, serialize
the result. The rules live in that module; the exception-to-status-code
mapping lives in the blueprint error handlers below, so no handler needs
its own try/except.

Every endpoint is admin-only. CLAUDE.md assigns face enrolment to the
admin, and faculty do not need it to take attendance -- the attendance
feature will read encodings server-side inside its own pipeline rather
than through a client-facing endpoint. Authorization is enforced here,
never only in React.

Nothing in this module writes the uploaded image anywhere. The bytes are
read, passed to the service, and dropped when the request ends.
"""

import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pymongo.errors import PyMongoError

from auth.decorators import role_required
from common.errors import ConflictError, NotFoundError, ValidationError
from common.validators import parse_object_id
from database.db import get_db
from recognition.bulk_import import import_class_faces
from recognition.errors import RecognitionUnavailableError
from recognition.serializers import (
    serialize_encoding,
    serialize_encodings,
    serialize_enrollment_statuses,
    serialize_import_results,
    serialize_import_summary,
)
from recognition.service import (
    class_enrollment_status,
    delete_encoding,
    delete_student_encodings,
    list_encodings,
    register_encoding,
)
from recognition.validators import (
    require_image,
    require_import_count,
    require_import_files,
    require_source,
)

logger = logging.getLogger(__name__)

faces_bp = Blueprint("faces", __name__, url_prefix="/api")


@faces_bp.errorhandler(ValidationError)
def _handle_validation_error(exc):
    """Also covers recognition.errors.ImageDecodeError, which subclasses
    ValidationError -- Flask resolves handlers through the raised
    exception's __mro__.
    """
    return jsonify({"error": str(exc)}), 400


@faces_bp.errorhandler(NotFoundError)
def _handle_not_found_error(exc):
    return jsonify({"error": str(exc)}), 404


@faces_bp.errorhandler(ConflictError)
def _handle_conflict_error(exc):
    return jsonify({"error": str(exc)}), 409


@faces_bp.errorhandler(RecognitionUnavailableError)
def _handle_recognition_unavailable(exc):
    """503 rather than 500: the request was valid and nothing failed --
    the server is missing an optional native dependency, and the same
    request will succeed once it is installed.
    """
    return jsonify({"error": str(exc)}), 503


@faces_bp.errorhandler(PyMongoError)
@faces_bp.errorhandler(RuntimeError)
def _handle_database_error(exc):
    """Mirrors routes/users.py: keeps responses on the JSON error contract
    instead of Flask's HTML/traceback page when MongoDB is unreachable
    (RuntimeError is what get_db() raises when MONGODB_URI is unset), and
    keeps raw driver text out of the body.
    """
    logger.exception("Database error in a face enrolment route")
    return jsonify({"error": "Service temporarily unavailable"}), 500


def _acting_user_id():
    """The admin making the request, taken from the verified token rather
    than from anything the client sent.
    """
    return parse_object_id(get_jwt_identity(), "id")


def _uploaded_image():
    """Read the `image` file part and validate it before anything decodes
    it, so an oversized or non-image upload is rejected on cheap checks.

    This is the only place a Flask FileStorage is touched;
    recognition/validators.py sees plain bytes.
    """
    uploaded = request.files.get("image")
    if uploaded is None:
        raise ValidationError("image file is required")

    return require_image(uploaded.read(), uploaded.mimetype)


def _uploaded_images():
    """Read every `images` part of a bulk import and validate the batch
    before any of it reaches the encoder.

    The same job `_uploaded_image` does for a single upload, and the same
    boundary: this is where a Flask FileStorage stops, and
    recognition/validators.py sees plain bytes. Werkzeug has already
    refused a body over MAX_CONTENT_LENGTH by this point, so what is read
    here is bounded by the same ceiling one video upload is.
    """
    parts = request.files.getlist("images")

    # The count first, on the parts themselves: an over-cap batch is
    # refused here without buffering a single part's bytes. Doing it
    # after the read below would still be correct and would still refuse
    # the request, but it would read everything it was sent in order to
    # say no.
    require_import_count(len(parts))

    return require_import_files(
        [(part.filename, part.read(), part.mimetype) for part in parts]
    )


# --- Face encodings ---------------------------------------------------


@faces_bp.route("/students/<student_id>/face-encodings", methods=["GET"])
@role_required("admin")
def list_student_face_encodings(student_id):
    documents = list_encodings(get_db(), parse_object_id(student_id, "student_id"))
    return jsonify({"encodings": serialize_encodings(documents)}), 200


@faces_bp.route("/students/<student_id>/face-encodings", methods=["POST"])
@role_required("admin")
def create_student_face_encoding(student_id):
    document = register_encoding(
        get_db(),
        parse_object_id(student_id, "student_id"),
        image_bytes=_uploaded_image(),
        source=require_source(request.form.get("source")),
        created_by=_acting_user_id(),
    )
    return jsonify(serialize_encoding(document)), 201


@faces_bp.route(
    "/students/<student_id>/face-encodings/<encoding_id>", methods=["DELETE"]
)
@role_required("admin")
def delete_student_face_encoding(student_id, encoding_id):
    delete_encoding(
        get_db(),
        parse_object_id(student_id, "student_id"),
        parse_object_id(encoding_id, "id"),
    )
    return jsonify({"deleted": True}), 200


@faces_bp.route("/students/<student_id>/face-encodings", methods=["DELETE"])
@role_required("admin")
def delete_all_student_face_encodings(student_id):
    deleted = delete_student_encodings(
        get_db(), parse_object_id(student_id, "student_id")
    )
    return jsonify({"deleted": deleted}), 200


# --- Class enrolment status -------------------------------------------


@faces_bp.route("/classes/<class_id>/face-enrollment", methods=["GET"])
@role_required("admin")
def get_class_face_enrollment(class_id):
    entries = class_enrollment_status(get_db(), parse_object_id(class_id, "id"))
    return jsonify({"students": serialize_enrollment_statuses(entries)}), 200


# --- Bulk import ------------------------------------------------------


@faces_bp.route("/classes/<class_id>/face-enrollment/import", methods=["POST"])
@role_required("admin")
def import_class_face_encodings(class_id):
    """Register a sample for many students of one class in one request.

    A 200 even when every file failed: the request was well-formed,
    authorized, and fully processed, and the report is the body. A mixed
    outcome is a result to be read, not an error to be caught -- and the
    frontend's shared client turns every non-2xx into a thrown Error
    carrying one message string, which a per-file report does not fit.

    Note what this handler does not do: it never decides which student a
    file belongs to. Nothing in the request names a student -- the server
    resolves that from the roster, because a mis-parse attributes one
    person's face to another and that decision belongs behind the same
    boundary as the authorization guarding it.
    """
    results, summary = import_class_faces(
        get_db(),
        parse_object_id(class_id, "id"),
        _uploaded_images(),
        created_by=_acting_user_id(),
    )

    return (
        jsonify(
            {
                "results": serialize_import_results(results),
                "summary": serialize_import_summary(summary),
            }
        ),
        200,
    )
