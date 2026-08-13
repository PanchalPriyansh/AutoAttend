"""The project's only computer-vision module.

This is the single place `face_recognition` and `numpy` may be imported.
Nothing here touches MongoDB, Flask, or any application rule: bytes go
in, numeric descriptors come out. Keeping that boundary is what lets the
rest of the backend -- and the whole test suite -- run on a machine where
the native library is not built.

Both imports are deliberately deferred into the functions that need them
rather than done at module scope. `face_recognition` depends on `dlib`,
which compiles from source and is not installable everywhere; a
module-level import would make `import app` fail on such a machine and
take the entire API down over a feature most requests never touch. Call
`is_available()` first and raise RecognitionUnavailableError (503) when
it returns False.

Face recognition is a heuristic, not an identity guarantee. Callers must
treat every result here as evidence to be reviewed, never as proof of who
someone is.
"""

import io
import logging

from recognition.errors import ImageDecodeError

logger = logging.getLogger(__name__)

# Mirrors the `encoding` array bounds in FACE_ENCODINGS_VALIDATOR
# (database/schema.py). `face_recognition` produces a 128-dimension
# descriptor; both ends assert it so a truncated vector cannot be stored.
ENCODING_LENGTH = 128

# The detector `face_recognition` should use. "hog" runs on CPU and is
# fast enough for one enrolment photo; "cnn" is more accurate but needs a
# GPU to be practical. Stored on every document so encodings produced by
# different detectors stay distinguishable.
DETECTOR_MODEL = "hog"

# Maximum Euclidean distance between two encodings for them to be treated
# as the same person. 0.6 is the value `face_recognition` documents as a
# reasonable default; it is a heuristic that trades false positives
# against false negatives, not a threshold below which identity is
# certain. Tune it against real data before trusting it.
MATCH_TOLERANCE = 0.6


def _face_recognition():
    """Import the library on first use, or raise ImportError.

    Callers should gate on is_available() rather than catching this.
    """
    import face_recognition

    return face_recognition


def is_available():
    """Whether face recognition can run in this process.

    Checked at request time rather than cached at import: a machine that
    gains the dependency should not need the Flask process restarted to
    notice, and the import itself is memoised by Python after the first
    successful call.
    """
    try:
        _face_recognition()
    except ImportError:
        logger.warning(
            "face_recognition is not importable; face enrolment endpoints "
            "will report the feature as unavailable"
        )
        return False

    return True


def encode_faces(image_bytes):
    """Return one encoding per face found in `image_bytes`.

    An empty list means no face was detected, and more than one entry
    means the image holds several people -- both are ordinary outcomes
    that the caller decides what to do about, not errors here.

    Each encoding is converted to a plain list of Python floats:
    `face_recognition` returns numpy arrays whose float64 values pymongo
    cannot encode, and the `bsonType: "double"` validator requires real
    floats.
    """
    face_recognition = _face_recognition()

    try:
        image = face_recognition.load_image_file(io.BytesIO(image_bytes))
    except Exception as exc:
        # Deliberately broad: PIL raises several unrelated exception types
        # for a corrupt or unsupported file, and every one of them means
        # the same thing to the caller. The original is logged, but the
        # message that reaches the client says nothing about the bytes.
        logger.warning("Rejected an unreadable image upload", exc_info=exc)
        raise ImageDecodeError("The uploaded file could not be read as an image") from exc

    # Locations are computed separately so the detector model is explicit;
    # face_encodings() would otherwise silently use its own default.
    locations = face_recognition.face_locations(image, model=DETECTOR_MODEL)
    encodings = face_recognition.face_encodings(image, known_face_locations=locations)

    return [[float(value) for value in encoding] for encoding in encodings]


def closest_match(candidate, known_encodings):
    """Return `(index, distance)` for the nearest of `known_encodings`, or
    None when there is nothing to compare against.

    The caller compares the distance to MATCH_TOLERANCE; this function
    deliberately does not decide what counts as a match, so the threshold
    lives in exactly one place.
    """
    if not known_encodings:
        return None

    face_recognition = _face_recognition()
    import numpy

    distances = face_recognition.face_distance(
        numpy.array(known_encodings), numpy.array(candidate)
    )
    index = int(numpy.argmin(distances))

    return index, float(distances[index])
