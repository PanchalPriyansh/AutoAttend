"""REST endpoints for capturing and saving attendance.

Handlers here only translate HTTP to the domain and back: pull the media
or JSON off the request, validate it, call attendance/service.py,
serialize the result. The rules live in that module; the
exception-to-status-code mapping lives in the blueprint error handlers
below, so no handler needs its own try/except.

Every endpoint is faculty-only, and every endpoint naming a class is
additionally restricted to the faculty member that class is assigned to.
`role_required` covers the first half from the token; the second half
needs the document and is enforced in the service, on every route rather
than only on the one that writes. Authorization is never left to React.

Nothing in this module writes the captured image or video anywhere. The
bytes are read, passed to the service, and dropped when the request ends.
"""

import logging

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pymongo.errors import PyMongoError

from attendance.errors import ForbiddenError
from attendance.serializers import (
    serialize_assigned_classes,
    serialize_proposal,
    serialize_session,
)
from attendance.service import (
    get_session,
    list_assigned_classes,
    recognize,
    save_session,
)
from attendance.validators import (
    parse_attendance_date,
    require_replace_flag,
    require_session_source,
)
from auth.decorators import role_required
from common.errors import ConflictError, NotFoundError, ValidationError
from common.http import json_body
from common.validators import parse_object_id
from database.db import get_db
from recognition.errors import RecognitionUnavailableError
from recognition.validators import require_image, require_video

logger = logging.getLogger(__name__)

attendance_bp = Blueprint("attendance", __name__, url_prefix="/api")


@attendance_bp.errorhandler(ValidationError)
def _handle_validation_error(exc):
    """Also covers recognition.errors.ImageDecodeError and
    VideoDecodeError, which subclass ValidationError -- Flask resolves
    handlers through the raised exception's __mro__.
    """
    return jsonify({"error": str(exc)}), 400


@attendance_bp.errorhandler(ForbiddenError)
def _handle_forbidden_error(exc):
    """The class exists and the caller is faculty, but it is not theirs."""
    return jsonify({"error": str(exc)}), 403


@attendance_bp.errorhandler(NotFoundError)
def _handle_not_found_error(exc):
    return jsonify({"error": str(exc)}), 404


@attendance_bp.errorhandler(ConflictError)
def _handle_conflict_error(exc):
    return jsonify({"error": str(exc)}), 409


@attendance_bp.errorhandler(RecognitionUnavailableError)
def _handle_recognition_unavailable(exc):
    """503 rather than 500: the request was valid and nothing failed --
    the server is missing an optional native dependency, and the same
    request will succeed once it is installed.
    """
    return jsonify({"error": str(exc)}), 503


@attendance_bp.errorhandler(PyMongoError)
@attendance_bp.errorhandler(RuntimeError)
def _handle_database_error(exc):
    """Mirrors routes/faces.py: keeps responses on the JSON error contract
    instead of Flask's HTML/traceback page when MongoDB is unreachable
    (RuntimeError is what get_db() raises when MONGODB_URI is unset), and
    keeps raw driver text out of the body.
    """
    logger.exception("Database error in an attendance route")
    return jsonify({"error": "Service temporarily unavailable"}), 500


def _acting_user_id():
    """The faculty member making the request, taken from the verified
    token rather than from anything the client sent.
    """
    return parse_object_id(get_jwt_identity(), "id")


def _uploaded_capture():
    """Read exactly one media part and validate it before anything decodes
    it, so an oversized or non-media upload is rejected on cheap checks.

    Both parts present is an error rather than a preference for one:
    guessing which the faculty member meant would silently analyse a file
    they did not intend to submit.

    This is the only place a Flask FileStorage is touched;
    recognition/validators.py sees plain bytes.
    """
    image = request.files.get("image")
    video = request.files.get("video")

    if image is not None and video is not None:
        raise ValidationError("Send either an image or a video, not both")
    if image is None and video is None:
        raise ValidationError("An image or video file is required")

    if image is not None:
        return {"image_bytes": require_image(image.read(), image.mimetype)}

    return {
        "video_bytes": require_video(video.read(), video.mimetype),
        "content_type": video.mimetype,
    }


# --- Assigned classes -------------------------------------------------


@attendance_bp.route("/classes/assigned", methods=["GET"])
@role_required("faculty")
def list_my_classes():
    entries = list_assigned_classes(get_db(), _acting_user_id())
    return jsonify({"classes": serialize_assigned_classes(entries)}), 200


# --- Recognition ------------------------------------------------------


@attendance_bp.route("/attendance/recognize", methods=["POST"])
@role_required("faculty")
def recognize_attendance():
    proposal = recognize(
        get_db(),
        parse_object_id(request.form.get("class_id"), "class_id"),
        _acting_user_id(),
        **_uploaded_capture(),
    )
    return jsonify(serialize_proposal(proposal)), 200


# --- Sessions ---------------------------------------------------------


@attendance_bp.route("/attendance/session", methods=["GET"])
@role_required("faculty")
def get_attendance_session():
    session, records = get_session(
        get_db(),
        parse_object_id(request.args.get("class_id"), "class_id"),
        parse_attendance_date(request.args.to_dict()),
        _acting_user_id(),
    )
    return jsonify(serialize_session(session, records)), 200


@attendance_bp.route("/attendance", methods=["POST"])
@role_required("faculty")
def create_attendance_session():
    body = json_body()

    session, records, created = save_session(
        get_db(),
        parse_object_id(body.get("class_id"), "class_id"),
        parse_attendance_date(body),
        _acting_user_id(),
        source=require_session_source(body.get("source")),
        records=body.get("records"),
        replace=require_replace_flag(body.get("replace")),
    )
    return jsonify(serialize_session(session, records)), 201 if created else 200
