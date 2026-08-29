"""REST endpoints for capturing and saving attendance.

Handlers here only translate HTTP to the domain and back: pull the media
or JSON off the request, validate it, call attendance/service.py,
serialize the result. The rules live in that module; the
exception-to-status-code mapping lives in the blueprint error handlers
below, so no handler needs its own try/except.

Every endpoint here is single-role, and every one naming a class is
additionally restricted to the person that class belongs to. The capture
and history routes are faculty-only and check `classes.faculty_id`; the
two `/attendance/me` routes are student-only and check enrollment.
`role_required` covers the first half from the token; the second half
needs the document and is enforced in the service layer, on every route
rather than only on the ones that write. Authorization is never left to
React.

No route in this module accepts a student id. The `/attendance/me` pair
reads the caller's own identity from the verified token, so there is no
addressable "some other student" to authorize, guess, or forget to check.

Nothing in this module writes the captured image or video anywhere. The
bytes are read, passed to the service, and dropped when the request ends.
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, request
from flask_jwt_extended import get_jwt_identity
from pymongo.errors import PyMongoError

from attendance import csv_export, export_naming, pdf_export
from attendance.errors import ExportUnavailableError, ForbiddenError
from attendance.serializers import (
    serialize_assigned_classes,
    serialize_proposal,
    serialize_session,
    serialize_session_summaries,
    serialize_student_class_attendance,
    serialize_student_overview,
)
from attendance.service import (
    delete_session,
    export_records,
    export_summary,
    get_session,
    get_session_by_id,
    list_assigned_classes,
    list_sessions,
    recognize,
    save_session,
    update_session_records,
)
from attendance.summary import class_attendance_overview, student_class_attendance
from attendance.threshold import current_threshold
from attendance.validators import (
    parse_attendance_date,
    parse_date_range,
    parse_export_filters,
    parse_session_filters,
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


@attendance_bp.errorhandler(ExportUnavailableError)
def _handle_export_unavailable(exc):
    """The same 503 reasoning, for the PDF writer rather than the CV
    stack. Registered separately because the two exceptions are unrelated
    -- one handler catching both would report the wrong missing library.
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


def _now():
    """The clock, read here so the formatter modules do not have to.

    attendance/csv_export.py and attendance/pdf_export.py take the
    generation date as an argument and read no clock of their own, which
    is what makes a rendered file a function of its inputs.
    """
    return datetime.now(timezone.utc)


def _file_response(payload, *, content_type, filename):
    """A downloadable file on the same blueprint as the JSON routes.

    `filename` has been through attendance/export_naming.py, which builds
    it from an `[a-z0-9-]` whitelist -- so the quoting below cannot be
    broken out of by a class name containing a quote, a semicolon, or a
    newline. That whitelist is the header-injection defence; the quotes
    here are only the header's own syntax.

    `no-store` because both files name students and their attendance, and
    neither should sit in a shared or on-disk cache after the faculty
    member signs out.
    """
    return Response(
        payload,
        content_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


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


# --- History ----------------------------------------------------------
#
# Addressed by id, unlike the singular /attendance/session above, which
# answers "has this lecture been taken yet?" by class and date during
# capture. Both are kept: they are looked up by different things.


@attendance_bp.route("/attendance/sessions", methods=["GET"])
@role_required("faculty")
def list_attendance_sessions():
    filters = parse_session_filters(request.args.to_dict())

    entries, total = list_sessions(
        get_db(),
        parse_object_id(request.args.get("class_id"), "class_id"),
        _acting_user_id(),
        **filters,
    )
    return (
        jsonify(
            {
                "sessions": serialize_session_summaries(entries),
                # The unpaged count, so the screen can say what it is showing
                # a page of. limit is echoed because it may have been clamped.
                "total": total,
                "limit": filters["limit"],
                "skip": filters["skip"],
            }
        ),
        200,
    )


@attendance_bp.route("/attendance/sessions/<session_id>", methods=["GET"])
@role_required("faculty")
def get_attendance_session_by_id(session_id):
    session, records = get_session_by_id(
        get_db(), parse_object_id(session_id, "id"), _acting_user_id()
    )
    return jsonify(serialize_session(session, records)), 200


@attendance_bp.route("/attendance/sessions/<session_id>", methods=["PUT"])
@role_required("faculty")
def update_attendance_session(session_id):
    # Only `records` is read. A class_id, date, source, or taken_by sent
    # alongside it is ignored rather than rejected -- those are properties of
    # the lecture, and correcting who was present does not change any of them.
    body = json_body()

    session, records = update_session_records(
        get_db(),
        parse_object_id(session_id, "id"),
        _acting_user_id(),
        body.get("records"),
    )
    return jsonify(serialize_session(session, records)), 200


@attendance_bp.route("/attendance/sessions/<session_id>", methods=["DELETE"])
@role_required("faculty")
def delete_attendance_session(session_id):
    delete_session(get_db(), parse_object_id(session_id, "id"), _acting_user_id())
    return jsonify({"deleted": True}), 200


# --- Export -----------------------------------------------------------
#
# Two paths rather than one with a `format` parameter: the two answers
# differ in shape, not just in encoding -- different rows, different
# service call, and only the PDF can be unavailable. A parameter would
# put a branch in a handler and invite a third value nobody implemented.
#
# `/attendance/export/*` and not `/attendance/sessions/export`, which
# would sit under the `<session_id>` rule above. Werkzeug does rank a
# static segment higher, so it would route correctly today, but a URL
# whose correctness rests on that is one refactor from being a 404.
#
# Both are reads. Neither records that it happened.


@attendance_bp.route("/attendance/export/csv", methods=["GET"])
@role_required("faculty")
def export_attendance_csv():
    filters = parse_export_filters(request.args.to_dict())

    class_document, _, triples = export_records(
        get_db(),
        parse_object_id(request.args.get("class_id"), "class_id"),
        _acting_user_id(),
        **filters,
    )

    return _file_response(
        csv_export.render_register(triples),
        content_type="text/csv; charset=utf-8",
        filename=export_naming.export_filename(
            class_document.get("name"),
            generated_on=_now(),
            extension="csv",
            **filters,
        ),
    )


def _render_pdf(**report):
    """Render the report, turning a writer failure into a 503.

    The one try/except in this module, and it earns its place. The
    library parses its own markup dialect and reaches the filesystem and
    the network for an `<img>` -- attendance/pdf_export.py escapes every
    string that reaches a Paragraph precisely so it cannot be steered
    into doing either, and that escaping is the actual fix. This is the
    net under it: a future reportlab surprise on some input nobody
    predicted should be a 503 saying the report could not be produced,
    not a 500 with a traceback, and certainly not on a blueprint whose
    stated contract is that errors stay JSON.

    Narrow on purpose. ValueError and OSError are what a bad render
    raises; everything else -- a bug in the service layer, a database
    failure -- keeps travelling to the handlers that know what it is.
    The try/except lives here rather than in the writer, so the writer
    stays a pure function that either returns bytes or raises.
    """
    try:
        return pdf_export.render_report(**report)
    except (ValueError, OSError) as exc:
        logger.exception("The PDF writer failed to render an attendance report")
        raise ExportUnavailableError(
            "The report could not be produced. The CSV export is unaffected."
        ) from exc


@attendance_bp.route("/attendance/export/pdf", methods=["GET"])
@role_required("faculty")
def export_attendance_pdf():
    # Checked before anything is read, so a server without the library
    # answers 503 instead of doing a term's worth of queries and then
    # failing to render them.
    if not pdf_export.is_available():
        raise ExportUnavailableError(pdf_export.UNAVAILABLE_MESSAGE)

    filters = parse_export_filters(request.args.to_dict())

    class_document, context, faculty, lectures, standings = export_summary(
        get_db(),
        parse_object_id(request.args.get("class_id"), "class_id"),
        _acting_user_id(),
        **filters,
    )

    # Read once and used for both the report and its filename. Two calls
    # could straddle midnight and put one date inside the document and a
    # different one on the file holding it.
    generated_on = _now()

    return _file_response(
        _render_pdf(
            class_name=class_document.get("name"),
            course=context.get("course"),
            context=context,
            prepared_by=faculty.get("name") if faculty else None,
            generated_on=generated_on,
            lectures=lectures,
            standings=standings,
            # Passed in rather than read inside the writer, the same way
            # the student serializers below receive it. A misconfigured
            # bar degrades to None and the report simply states none.
            threshold=current_threshold(),
            **filters,
        ),
        content_type="application/pdf",
        filename=export_naming.export_filename(
            class_document.get("name"),
            generated_on=generated_on,
            extension="pdf",
            **filters,
        ),
    )


# --- A student's own attendance ---------------------------------------
#
# `me` is a literal segment, not a placeholder. Both handlers take the
# student from `_acting_user_id()` and nowhere else, so a student id in
# the query string or body is simply never read.


@attendance_bp.route("/attendance/me", methods=["GET"])
@role_required("student")
def get_my_attendance():
    entries, overall = class_attendance_overview(get_db(), _acting_user_id())
    return (
        jsonify(serialize_student_overview(entries, overall, current_threshold())),
        200,
    )


@attendance_bp.route("/attendance/me/sessions", methods=["GET"])
@role_required("student")
def get_my_class_attendance():
    document, context, counts, monthly, lectures = student_class_attendance(
        get_db(),
        parse_object_id(request.args.get("class_id"), "class_id"),
        _acting_user_id(),
        **parse_date_range(request.args.to_dict()),
    )
    return (
        jsonify(
            serialize_student_class_attendance(
                document, context, counts, monthly, lectures, current_threshold()
            )
        ),
        200,
    )
