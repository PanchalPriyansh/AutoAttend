"""Input validation rules specific to face enrolment.

Pure functions -- no Flask objects, no database access, no CV library.
The route reads the file part off the request and hands the raw bytes
here; nothing in this module knows what a `FileStorage` is.

The generic helpers (ObjectId and string parsing) come from
common/validators.py, and the source enum below mirrors
FACE_ENCODINGS_VALIDATOR in database/schema.py. That `$jsonSchema` is the
last line of defense, not the first: without these checks a bad value
reaches MongoDB, fails the collection validator, and surfaces to the
client as an opaque 500 instead of a 400 naming the field.
"""

from common.errors import ValidationError

# Mirrors the `source` enum in FACE_ENCODINGS_VALIDATOR.
SOURCES = ("upload", "camera")

DEFAULT_SOURCE = "upload"

# An enrolment photo of one face needs nowhere near this much; the ceiling
# exists to reject an oversized or hostile upload cheaply, before any
# decoding is attempted.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# What app.py applies as MAX_CONTENT_LENGTH, so Werkzeug refuses an
# outsized body (413) instead of buffering it into memory.
#
# Deliberately above MAX_IMAGE_BYTES rather than equal to it: a multipart
# body carries boundary framing, headers, and the `source` field on top of
# the file itself, so a legal 5 MB image arrives as slightly more than 5 MB
# on the wire. Setting them equal would reject the largest permitted image
# as a 413 and make the 400 below unreachable.
MAX_REQUEST_BYTES = MAX_IMAGE_BYTES + 1024 * 1024

ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")


def require_image(data, content_type):
    """Validate the raw upload before anything tries to decode it."""
    if not data:
        raise ValidationError("image file is required")

    # Compared case-insensitively and without any "; charset=" suffix,
    # which browsers are entitled to append.
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized not in ALLOWED_CONTENT_TYPES:
        raise ValidationError(
            f"image must be one of: {', '.join(ALLOWED_CONTENT_TYPES)}"
        )

    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationError(
            f"image must be smaller than {MAX_IMAGE_BYTES // (1024 * 1024)} MB"
        )

    return data


def require_source(value):
    """Where the image came from. Absent means an ordinary file upload."""
    if value is None or value == "":
        return DEFAULT_SOURCE
    if not isinstance(value, str):
        raise ValidationError("source must be a string")
    if value not in SOURCES:
        raise ValidationError(f"source must be one of: {', '.join(SOURCES)}")

    return value


def require_single_face(encodings):
    """Return the one encoding in `encodings`, rejecting 0 or 2+.

    Neither rejection is a technicality. An enrolment photo with no
    detectable face would otherwise store nothing useful and quietly mark
    the student as enrolled; a photo with several faces gives no way to
    tell which one is the student, and guessing wrong would attribute one
    person's attendance to another for as long as the record survives.
    """
    if not encodings:
        raise ValidationError(
            "No face was detected in the image. Use a clear, well-lit photo "
            "showing the student's face."
        )

    if len(encodings) > 1:
        raise ValidationError(
            f"{len(encodings)} faces were detected in the image. Use a photo "
            "showing only the student being enrolled."
        )

    return encodings[0]
