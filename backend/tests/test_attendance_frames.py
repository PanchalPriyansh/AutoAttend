"""Tests for backend/recognition/frames.py -- the project's only `cv2`
importer.

Spec contract under test (07-attendance-capture.md, "Rules for
implementation" + "Definition of done"):
  - Lazy import: `is_available()` reports False without raising when
    `cv2` is not importable, and the module otherwise behaves normally.
  - Video is bounded: no more than `MAX_FRAMES` stills come back from one
    extraction.
  - An unopenable or empty video raises `VideoDecodeError` (a 400 upstream),
    never a crash.
  - The temp file `cv2.VideoCapture` requires is deleted in a `finally`
    before the function returns, on every path -- including when the
    video cannot be opened at all. This is explicitly called out in the
    Definition of done as something that "must be covered by a test."

`cv2` itself is never imported by this test file or exercised for real:
every test monkeypatches `recognition.frames._cv2` to a small fake object
that mimics only the handful of `cv2.VideoCapture`/`cv2.imencode` methods
`frames.py` calls, so the suite runs identically whether or not OpenCV is
actually installed. "Frames" here are plain synthetic bytes, never real
image or video data.
"""

import os

import pytest

from recognition import frames
from recognition.errors import VideoDecodeError


class _FakeBuffer:
    def __init__(self, payload):
        self._payload = payload

    def tobytes(self):
        return self._payload if isinstance(self._payload, bytes) else str(self._payload).encode()


class _FakeVideoCapture:
    """Stands in for `cv2.VideoCapture`. `frames` is the full sequence the
    "video" would yield; `opens` simulates a file cv2 could not open at
    all (a corrupt/non-video upload), and `report_frame_count` simulates
    a container that will not report its length up front, forcing
    frames.py's scanning fallback.
    """

    def __init__(self, frames_, *, opens=True, report_frame_count=True):
        self._frames = list(frames_)
        self._opens = opens
        self._frame_count = len(self._frames) if report_frame_count else 0
        self._pos = 0

    def isOpened(self):
        return self._opens

    def get(self, _prop):
        return self._frame_count

    def set(self, _prop, value):
        self._pos = int(value)

    def read(self):
        if 0 <= self._pos < len(self._frames):
            frame = self._frames[self._pos]
            self._pos += 1
            return True, frame
        return False, None

    def release(self):
        pass


class _FakeCv2:
    CAP_PROP_FRAME_COUNT = "frame_count"
    CAP_PROP_POS_FRAMES = "pos_frames"
    IMWRITE_JPEG_QUALITY = 1

    def __init__(self, capture):
        self._capture = capture

    def VideoCapture(self, _path):
        return self._capture

    def imencode(self, _ext, frame, _params):
        if frame is None:
            return False, None
        return True, _FakeBuffer(frame)


def _spy_on_temp_file(monkeypatch):
    """Wraps the real `_write_temp_video` so a test can assert the path it
    returned no longer exists once `extract_frames` is done with it.
    """
    recorded = {}
    original = frames._write_temp_video

    def spy(video_bytes, suffix):
        path = original(video_bytes, suffix)
        recorded["path"] = path
        return path

    monkeypatch.setattr(frames, "_write_temp_video", spy)
    return recorded


# --- is_available ------------------------------------------------------------


class TestIsAvailable:
    def test_returns_false_without_raising_when_cv2_is_not_importable(self, monkeypatch):
        def _raise():
            raise ImportError("cv2 not installed")

        monkeypatch.setattr(frames, "_cv2", _raise)

        assert frames.is_available() is False

    def test_returns_true_when_cv2_imports_successfully(self, monkeypatch):
        monkeypatch.setattr(frames, "_cv2", lambda: object())

        assert frames.is_available() is True


# --- extract_frames: happy path -----------------------------------------------


class TestExtractFramesHappyPath:
    def test_returns_one_still_per_available_frame_under_the_cap(self, monkeypatch):
        raw_frames = [f"frame-{i}".encode() for i in range(3)]
        capture = _FakeVideoCapture(raw_frames)
        monkeypatch.setattr(frames, "_cv2", lambda: _FakeCv2(capture))

        result = frames.extract_frames(b"synthetic-video-bytes", "video/mp4")

        assert len(result) == 3
        assert all(isinstance(item, bytes) for item in result)

    def test_never_returns_more_than_max_frames(self, monkeypatch):
        raw_frames = [f"frame-{i}".encode() for i in range(frames.MAX_FRAMES * 3)]
        capture = _FakeVideoCapture(raw_frames)
        monkeypatch.setattr(frames, "_cv2", lambda: _FakeCv2(capture))

        result = frames.extract_frames(b"synthetic-video-bytes", "video/mp4")

        assert len(result) == frames.MAX_FRAMES

    def test_falls_back_to_scanning_when_the_container_reports_no_frame_count(self, monkeypatch):
        raw_frames = [f"frame-{i}".encode() for i in range(frames.FALLBACK_FRAME_STRIDE * 2)]
        capture = _FakeVideoCapture(raw_frames, report_frame_count=False)
        monkeypatch.setattr(frames, "_cv2", lambda: _FakeCv2(capture))

        result = frames.extract_frames(b"synthetic-video-bytes", "video/webm")

        assert len(result) >= 1
        assert len(result) <= frames.MAX_FRAMES


# --- extract_frames: failure modes --------------------------------------------


class TestExtractFramesFailureModes:
    def test_a_video_that_cannot_be_opened_raises_video_decode_error(self, monkeypatch):
        capture = _FakeVideoCapture([], opens=False)
        monkeypatch.setattr(frames, "_cv2", lambda: _FakeCv2(capture))

        with pytest.raises(VideoDecodeError):
            frames.extract_frames(b"not-a-real-video", "video/mp4")

    def test_a_video_with_no_readable_frames_raises_video_decode_error(self, monkeypatch):
        capture = _FakeVideoCapture([], opens=True, report_frame_count=False)
        monkeypatch.setattr(frames, "_cv2", lambda: _FakeCv2(capture))

        with pytest.raises(VideoDecodeError):
            frames.extract_frames(b"empty-video", "video/mp4")


# --- temp file cleanup --------------------------------------------------------


class TestTempFileCleanup:
    def test_temp_file_is_removed_after_a_successful_extraction(self, monkeypatch):
        capture = _FakeVideoCapture([b"frame-0", b"frame-1"])
        monkeypatch.setattr(frames, "_cv2", lambda: _FakeCv2(capture))
        recorded = _spy_on_temp_file(monkeypatch)

        frames.extract_frames(b"synthetic-video-bytes", "video/mp4")

        assert recorded["path"]
        assert not os.path.exists(recorded["path"])

    def test_temp_file_is_removed_even_when_the_video_cannot_be_opened(self, monkeypatch):
        capture = _FakeVideoCapture([], opens=False)
        monkeypatch.setattr(frames, "_cv2", lambda: _FakeCv2(capture))
        recorded = _spy_on_temp_file(monkeypatch)

        with pytest.raises(VideoDecodeError):
            frames.extract_frames(b"not-a-real-video", "video/mp4")

        assert recorded["path"]
        assert not os.path.exists(recorded["path"])

    def test_temp_file_is_removed_even_when_no_frames_could_be_read(self, monkeypatch):
        capture = _FakeVideoCapture([], opens=True, report_frame_count=False)
        monkeypatch.setattr(frames, "_cv2", lambda: _FakeCv2(capture))
        recorded = _spy_on_temp_file(monkeypatch)

        with pytest.raises(VideoDecodeError):
            frames.extract_frames(b"empty-video", "video/mp4")

        assert recorded["path"]
        assert not os.path.exists(recorded["path"])
