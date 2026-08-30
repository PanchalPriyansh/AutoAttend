"""Input validation rules for uploaded media -- enrolment photos and the
classroom photo/video an attendance capture arrives as.

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

# A classroom video is a few seconds of a static shot, not a recording of
# the lecture. Larger than the photo ceiling because even a short clip
# dwarfs a still, but bounded for the same reason: it is decoded in the
# request, and recognition/frames.py only samples a handful of frames from
# it however long it runs.
MAX_VIDEO_BYTES = 25 * 1024 * 1024

# What app.py applies as MAX_CONTENT_LENGTH, so Werkzeug refuses an
# outsized body (413) instead of buffering it into memory.
#
# Sized from the largest media type any route accepts, and deliberately
# above it rather than equal to it: a multipart body carries boundary
# framing, headers, and fields like `source`/`class_id` on top of the file
# itself, so a legal 25 MB video arrives as slightly more than 25 MB on the
# wire. Setting them equal would reject the largest permitted file as a 413
# and make the 400 below unreachable.
MAX_REQUEST_BYTES = MAX_VIDEO_BYTES + 1024 * 1024

# How many photos one bulk-import request may carry.
#
# Not the real bound on a batch, and deliberately so: MAX_IMPORT_FILES x
# MAX_IMAGE_BYTES is 50 MB, well above the MAX_REQUEST_BYTES ceiling
# Werkzeug already refuses beyond, so a batch of ten maximal images is
# rejected as a 413 before this constant is consulted. Raising the global
# ceiling to fit them would weaken the guard on every other route.
#
# What this bounds is the *work* one request may ask for. Encoding is
# synchronous and CPU-bound, so a request holds a worker for as long as
# its photos take to encode; ten keeps that short enough to report
# progress between batches and cheap enough to retry when one fails.
MAX_IMPORT_FILES = 10

ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")

# What a browser produces from a file picker or a MediaRecorder capture.
# Deliberately short: every entry is a container OpenCV can actually open,
# so an accepted upload does not fail later in the decoder.
ALLOWED_VIDEO_CONTENT_TYPES = ("video/mp4", "video/webm", "video/quicktime")


def _require_media(data, content_type, field_name, allowed_types, max_bytes):
    """The shared shape of every media check: present, of an allowed type,
    and within its ceiling -- in that order, so the cheapest rejection
    happens first and nothing hostile reaches a decoder.

    Shared by require_image and require_video rather than duplicated: the
    two differ only in their allow-list and ceiling, and a second copy is
    how one of them ends up missing a fix the other got.
    """
    if not data:
        raise ValidationError(f"{field_name} file is required")

    # Compared case-insensitively and without any "; charset=" or
    # "; codecs=" suffix, which browsers are entitled to append.
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized not in allowed_types:
        raise ValidationError(f"{field_name} must be one of: {', '.join(allowed_types)}")

    if len(data) > max_bytes:
        raise ValidationError(
            f"{field_name} must be smaller than {max_bytes // (1024 * 1024)} MB"
        )

    return data


def require_image(data, content_type):
    """Validate a raw image upload before anything tries to decode it."""
    return _require_media(
        data, content_type, "image", ALLOWED_CONTENT_TYPES, MAX_IMAGE_BYTES
    )


def require_import_count(count):
    """The batch-size rule, applied to the number of parts alone.

    Separate from require_import_files so a caller can apply it *before*
    reading anything: the route knows how many `images` parts arrived
    long before it has buffered their bytes, and an over-cap batch
    should be refused without paying to read one. Taking an int rather
    than the parts themselves is what keeps that possible while this
    module still knows nothing about Flask.

    require_import_files calls it too, so the rule is written once and
    holds however the validation is reached.
    """
    if count == 0:
        raise ValidationError("At least one image is required")

    if count > MAX_IMPORT_FILES:
        raise ValidationError(
            f"An import carries at most {MAX_IMPORT_FILES} photos at a time; "
            f"{count} were sent."
        )


def require_import_files(files):
    """Validate a whole bulk-import batch before any of it is encoded.

    `files` is a list of `(filename, data, content_type)` triples -- the
    route has already pulled the parts off the request, exactly as it
    does for a single upload, so this module still knows nothing about
    Flask.

    Every file is checked before any is registered, and that ordering is
    the rule rather than an implementation detail: an import that stored
    six photos and then rejected the seventh for being a PDF would leave
    the admin guessing which half of their folder had landed. One bad
    part fails the request; a bad *photo* -- unreadable, no face, wrong
    person -- is a per-file outcome the import reports, and is not this
    function's business.
    """
    require_import_count(len(files))

    return [
        (filename, require_image(data, content_type))
        for filename, data, content_type in files
    ]


def require_video(data, content_type):
    """Validate a raw video upload before anything tries to decode it."""
    return _require_media(
        data, content_type, "video", ALLOWED_VIDEO_CONTENT_TYPES, MAX_VIDEO_BYTES
    )


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
