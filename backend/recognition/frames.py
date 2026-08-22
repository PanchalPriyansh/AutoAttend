"""Video frame extraction -- the project's only OpenCV module.

This is the single place `cv2` may be imported. Like encoder.py it is
pure media handling: video bytes in, a bounded list of still-image bytes
out. No MongoDB, no Flask, no application rules.

The import is deferred into the functions that need it for the same
reason encoder.py defers `face_recognition`: OpenCV is optional. A server
without it must still start, still serve health checks, and still take
attendance from a photo -- only video should report 503. A module-level
import would trade all of that for one feature.

Frames are returned as encoded JPEG bytes rather than raw arrays so that
encoder.encode_faces() -- which already accepts bytes -- is reused
unchanged. That costs one encode/decode round-trip per sampled frame, a
rounding error next to face detection itself, and it keeps this module's
contract to "bytes in, bytes out" with no array type crossing between the
two CV libraries.

Nothing here keeps the video. The temp file OpenCV requires is deleted in
a `finally` before this function returns, on every path including
failure, because a classroom recording holds many people who never agreed
to it being stored.
"""

import logging
import os
import tempfile

from recognition.errors import VideoDecodeError

logger = logging.getLogger(__name__)

# How many frames one video contributes. More frames means more chances
# to catch a face that was turned away or blurred in any single moment,
# with diminishing returns: it is the same room throughout, so beyond a
# handful the extra passes mostly re-detect the same people at the cost of
# a full detection run each.
MAX_FRAMES = 8

# The stride the sequential fallback samples at, in frames. Roughly half a
# second at 30fps -- far enough apart that consecutive samples are not the
# same instant, close enough that a short clip still yields MAX_FRAMES.
FALLBACK_FRAME_STRIDE = 15

# A ceiling on frames *read* when seeking is unavailable, so a long or
# malformed video cannot turn the fallback into an unbounded scan.
MAX_SCANNED_FRAMES = FALLBACK_FRAME_STRIDE * MAX_FRAMES * 4

# JPEG rather than PNG for the intermediate: detection is unaffected by
# mild compression artefacts and a lossless re-encode of a full frame is
# markedly slower.
JPEG_QUALITY = 90

# OpenCV's ffmpeg backend sniffs the container, but a matching extension
# helps it pick a demuxer on the first try. Keys mirror
# ALLOWED_VIDEO_CONTENT_TYPES in validators.py.
_SUFFIX_BY_CONTENT_TYPE = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
}
DEFAULT_SUFFIX = ".mp4"


def _cv2():
    """Import OpenCV on first use, or raise ImportError.

    Callers should gate on is_available() rather than catching this.
    """
    import cv2

    return cv2


def is_available():
    """Whether video frames can be extracted in this process.

    Checked at request time rather than cached at import, matching
    encoder.is_available(): a machine that gains the dependency should not
    need the Flask process restarted to notice, and Python memoises the
    import after the first success anyway.
    """
    try:
        _cv2()
    except ImportError:
        logger.warning(
            "cv2 is not importable; video attendance capture will report the "
            "feature as unavailable (photo capture is unaffected)"
        )
        return False

    return True


def _suffix_for(content_type):
    normalized = (content_type or "").split(";")[0].strip().lower()
    return _SUFFIX_BY_CONTENT_TYPE.get(normalized, DEFAULT_SUFFIX)


def _write_temp_video(video_bytes, suffix):
    """Spill the upload to a temp file, because `cv2.VideoCapture` cannot
    read an in-memory buffer -- it takes a path or a device index.

    Closed before returning: on Windows a second handle cannot open a file
    still held open by this one.
    """
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(video_bytes)
    finally:
        handle.close()

    return handle.name


def _encode_frame(cv2, frame):
    encoded, buffer = cv2.imencode(
        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
    )
    if not encoded:
        return None

    return buffer.tobytes()


def _evenly_spaced(total, count):
    """Indices spread across the whole clip rather than the first second of
    it -- a burst of consecutive frames is effectively one sample.
    """
    if total <= count:
        return list(range(total))

    step = total / count
    return [min(total - 1, int(index * step)) for index in range(count)]


def _sample_by_seeking(cv2, capture, total):
    frames = []
    for index in _evenly_spaced(total, MAX_FRAMES):
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        read, frame = capture.read()
        if not read:
            continue
        encoded = _encode_frame(cv2, frame)
        if encoded is not None:
            frames.append(encoded)

    return frames


def _sample_by_scanning(cv2, capture):
    """Fallback for containers that report no frame count or refuse to
    seek -- streamed WebM in particular, which is exactly what a browser
    MediaRecorder produces.
    """
    frames = []
    for index in range(MAX_SCANNED_FRAMES):
        read, frame = capture.read()
        if not read:
            break
        if index % FALLBACK_FRAME_STRIDE:
            continue

        encoded = _encode_frame(cv2, frame)
        if encoded is not None:
            frames.append(encoded)
        if len(frames) == MAX_FRAMES:
            break

    return frames


def extract_frames(video_bytes, content_type=None):
    """Return up to MAX_FRAMES stills, as JPEG bytes, sampled across the
    clip.

    An unreadable or empty video raises VideoDecodeError (a 400): the
    upload is bad input, not a server fault.
    """
    cv2 = _cv2()
    path = _write_temp_video(video_bytes, _suffix_for(content_type))

    try:
        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            raise VideoDecodeError("The uploaded file could not be read as a video")

        try:
            total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            # Seeking is tried first because it samples the whole clip
            # evenly. Some containers report a frame count and still fail
            # every seek, so an empty result falls through to scanning
            # rather than being reported as an unreadable video.
            frames = _sample_by_seeking(cv2, capture, total) if total > 0 else []
            if not frames:
                capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                frames = _sample_by_scanning(cv2, capture)
        finally:
            capture.release()
    finally:
        # Deleted here rather than after the return so it goes away on the
        # error paths too. Swallowed and logged rather than raised: a
        # cleanup failure must not replace the real error on its way out.
        try:
            os.unlink(path)
        except OSError:
            logger.warning("Could not remove the temporary video file")

    if not frames:
        raise VideoDecodeError("No frames could be read from the uploaded video")

    return frames
