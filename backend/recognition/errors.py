"""Domain exceptions specific to face recognition.

Bad input, missing target, and conflicting state already exist in
common/errors.py and are reused rather than redeclared -- only the two
ideas below are genuinely new to this feature.

ImageDecodeError subclasses ValidationError deliberately: a file the
decoder cannot read is bad input, so it should reach the client as the
same 400 every other malformed field produces, without routes/faces.py
needing a second handler registration.
"""

from common.errors import AppError, ValidationError

__all__ = ["RecognitionUnavailableError", "ImageDecodeError", "VideoDecodeError"]


class RecognitionUnavailableError(AppError):
    """A native recognition dependency is not importable -- maps to 503.

    Distinct from a 500: nothing is broken and nothing was corrupted, the
    server is simply missing an optional native dependency, and the same
    request will succeed once it is installed. Telling the two apart is
    what stops an operator hunting for a bug that does not exist.

    Covers both optional libraries -- `face_recognition` (every capture)
    and OpenCV (video only) -- with the message naming which one is
    missing. One exception for one status keeps every route's mapping to
    a single handler; the two cases differ in what an operator installs,
    not in what the client should do.
    """


class ImageDecodeError(ValidationError):
    """The uploaded bytes are not a readable image -- maps to 400."""


class VideoDecodeError(ValidationError):
    """The uploaded bytes are not a readable video -- maps to 400.

    Separate from ImageDecodeError only so the message can name the right
    thing; both are bad input and both reach the client as a 400.
    """
