"""Registering face samples for many students of one class in one pass.

This module orchestrates; it owns no enrolment rule of its own. Every
per-file decision -- the five-sample cap, the one-face requirement, the
check that a face does not already belong to a different student -- is
applied by calling `recognition/service.py::register_encoding` once per
matched file. That is deliberate and load-bearing: a bulk path that
enforced any of those differently is how a second identity gets created
for one face, and the single-upload path is the one already reviewed and
tested.

What is new here is only:

  - resolving each file name to a student (delegated to filenames.py),
  - deciding what happens when one file fails, and
  - reporting on every file that was submitted.

**Partial success is the normal outcome.** A folder of sixty photos will
contain a blurry one, a group shot, a student who left, and a typo. An
all-or-nothing import would refuse all sixty for any one of them, so a
file that a domain rule refuses becomes a `rejected` row and the loop
continues. Successes commit as they go and there is no transaction: a
partial import is correct, and re-importing the remainder is the repair.

Only `ValidationError` and `ConflictError` are caught. A driver error, a
missing database, or an unavailable recognition library is a failure of
the *request*, not of one photo, and must reach the blueprint's error
handlers rather than be flattened into a per-file message that makes an
outage look like a bad JPEG.

No Flask objects appear in any signature, and nothing here imports a CV
library -- the recognition library is reached only through service.py
and encoder.py. The source image is never persisted: the bytes are
passed to the encoder and dropped, and no file name is ever logged,
because a file name in this feature is a student's roll number, which
identifies them as surely as an address would.
"""

import logging

from common.errors import ConflictError, ValidationError
from recognition import encoder
from recognition.errors import RecognitionUnavailableError
from recognition.filenames import resolve_roster
from recognition.service import register_encoding
from users.assignments import list_enrollments

logger = logging.getLogger(__name__)

STATUS_REGISTERED = "registered"
STATUS_NO_MATCH = "no_match"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_REJECTED = "rejected"

# Every sample this path writes is an uploaded photo, and `source` is
# fixed here rather than read from the request: a client cannot claim a
# bulk import came from a camera, and cannot send a value the
# face_encodings validator would refuse.
IMPORT_SOURCE = "upload"

NO_MATCH_MESSAGE = (
    "No student on this class roster has this ID. Name each photo with "
    "the student's ID, as in 24DCS001.jpg."
)


def _ambiguous_message(match_count):
    """The one place the full-email form is advertised.

    It is the escape hatch for the only case an ID cannot resolve -- two
    students sharing one ID across different email domains -- so the
    message names it exactly where it is needed and nowhere else.
    """
    return (
        f"This ID matches {match_count} students on the roster. Rename the "
        "file with the student's full email address instead."
    )


def _require_recognition_available():
    """Gate the whole request, once, before any photo is encoded.

    service.register_encoding checks this too, and would raise on the
    first file -- but a server missing `dlib` should say so plainly
    rather than encoding nothing nine more times to produce a report of
    ten identical failures.
    """
    if not encoder.is_available():
        raise RecognitionUnavailableError(
            "Face recognition is unavailable on the server. The recognition "
            "library is not installed."
        )


def _summarize(results):
    summary = {
        "submitted": len(results),
        STATUS_REGISTERED: 0,
        STATUS_NO_MATCH: 0,
        STATUS_AMBIGUOUS: 0,
        STATUS_REJECTED: 0,
    }
    for result in results:
        summary[result["status"]] += 1

    return summary


def import_class_faces(db, class_id, files, *, created_by):
    """Register a face sample for each of `files` against one class.

    `files` is a list of `(filename, image_bytes)` pairs, already
    validated by recognition/validators.py::require_import_files.
    Returns `(results, summary)`, with one result per submitted file, in
    the order the files were given.

    Checks run cheapest-and-most-decisive-first, as register_encoding's
    own do: an unavailable library and an unknown class both fail the
    whole request, so neither pays for a single image decode. The roster
    is then loaded exactly once -- a per-file roster query would multiply
    an already index-backed join by the size of the batch for nothing.
    """
    _require_recognition_available()

    # Raises NotFoundError for an unknown class, before any image is
    # touched. Deactivated students stay on the roster deliberately
    # (see list_enrollments): deactivating an account does not unenroll
    # it, though register_encoding still refuses to enrol one.
    roster = [student for _, student in list_enrollments(db, class_id)]
    resolutions = resolve_roster([filename for filename, _ in files], roster)

    results = []
    for (filename, image_bytes), resolution in zip(files, resolutions):
        results.append(
            _import_one(
                db,
                filename,
                image_bytes,
                resolution,
                created_by=created_by,
            )
        )

    summary = _summarize(results)
    # Counts only. No file name, and nothing derived from an image: an
    # operational log must not become a roster of who was enrolled when.
    logger.info(
        "Bulk face import for class %s: %s of %s files registered",
        class_id,
        summary[STATUS_REGISTERED],
        summary["submitted"],
    )

    return results, summary


def _import_one(db, filename, image_bytes, resolution, *, created_by):
    """One file's outcome, as a plain dict carrying the raw student
    document (serialization happens at the edge, in serializers.py).
    """
    if resolution.match_count == 0:
        return {
            "filename": filename,
            "status": STATUS_NO_MATCH,
            "student": None,
            "message": NO_MATCH_MESSAGE,
        }

    if resolution.match_count > 1:
        return {
            "filename": filename,
            "status": STATUS_AMBIGUOUS,
            "student": None,
            "message": _ambiguous_message(resolution.match_count),
        }

    student = resolution.student
    try:
        register_encoding(
            db,
            student["_id"],
            image_bytes=image_bytes,
            source=IMPORT_SOURCE,
            created_by=created_by,
        )
    except (ValidationError, ConflictError) as exc:
        # The domain message verbatim, so the admin reads the same
        # sentence the single-upload path shows them -- no face, several
        # faces, at the sample cap, or a face that belongs to somebody
        # else. Rewording it here would give this project two vocabularies
        # for one rule.
        return {
            "filename": filename,
            "status": STATUS_REJECTED,
            "student": student,
            "message": str(exc),
        }

    return {
        "filename": filename,
        "status": STATUS_REGISTERED,
        "student": student,
        "message": None,
    }
