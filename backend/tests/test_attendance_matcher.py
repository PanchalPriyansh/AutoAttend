"""Tests for backend/recognition/matcher.py.

Spec contract under test (07-attendance-capture.md, "Rules for
implementation" + "Definition of done"):
  - A face matching nobody increments `unknown_faces` and is attached to
    no key.
  - Two faces both matching one key collapse to the better (smaller)
    distance; the surplus face becomes an unknown face -- never a second
    presence, never reassigned to the runner-up.
  - A key recognised in *any* frame counts as found (frame union), taking
    its best distance across every frame it appears in.
  - `detected_faces` / `unknown_faces` are the *maximum* over frames, not
    the sum -- consecutive frames show the same room.
  - `confidence_for` reports "high" at/under `LOW_CONFIDENCE_DISTANCE` and
    "low" above it, up to `encoder.MATCH_TOLERANCE`.
  - A key can only ever be produced from an encoding present in `known` --
    the structural basis for the service layer's cross-class isolation.

Pure unit tests: `recognition.encoder.closest_match` is monkeypatched to
`fake_closest_match` (attendance_test_helpers.py) throughout, so no real
face_recognition/numpy/dlib is ever imported and no real biometric
computation happens anywhere in this file. Every "encoding" is a short,
synthetic vector from `synthetic_encoding()`.
"""

import pytest
from attendance_test_helpers import fake_closest_match, synthetic_encoding

from recognition import encoder, matcher


@pytest.fixture(autouse=True)
def _stub_closest_match(monkeypatch):
    monkeypatch.setattr("recognition.encoder.closest_match", fake_closest_match)


# --- confidence banding --------------------------------------------------


class TestConfidenceFor:
    def test_a_distance_at_the_low_confidence_boundary_is_high_confidence(self):
        assert matcher.confidence_for(matcher.LOW_CONFIDENCE_DISTANCE) == matcher.HIGH_CONFIDENCE

    def test_a_distance_just_past_the_boundary_is_low_confidence(self):
        assert (
            matcher.confidence_for(matcher.LOW_CONFIDENCE_DISTANCE + 0.001)
            == matcher.LOW_CONFIDENCE
        )

    def test_a_near_zero_distance_is_high_confidence(self):
        assert matcher.confidence_for(0.0) == matcher.HIGH_CONFIDENCE

    def test_a_distance_near_the_match_tolerance_is_low_confidence(self):
        assert (
            matcher.confidence_for(encoder.MATCH_TOLERANCE - 0.001) == matcher.LOW_CONFIDENCE
        )


# --- single-frame matching -------------------------------------------------


class TestMatchFramesSingleFrame:
    def test_a_face_within_tolerance_is_recognized(self):
        known = [("student-a", synthetic_encoding(0.0))]
        face = synthetic_encoding(0.0)

        best_by_key, detected, unknown = matcher.match_frames([[face]], known)

        assert best_by_key == {"student-a": 0.0}
        assert detected == 1
        assert unknown == 0

    def test_a_face_matching_nobody_is_counted_as_unknown_and_attached_to_no_key(self):
        known = [("student-a", synthetic_encoding(0.0))]
        # Far beyond MATCH_TOLERANCE from the only known encoding.
        stranger_face = synthetic_encoding(0.0 + encoder.MATCH_TOLERANCE + 10.0)

        best_by_key, detected, unknown = matcher.match_frames([[stranger_face]], known)

        assert best_by_key == {}
        assert unknown == 1
        assert detected == 1

    def test_no_known_encodings_means_every_face_is_unknown(self):
        face = synthetic_encoding(0.0)

        best_by_key, detected, unknown = matcher.match_frames([[face]], [])

        assert best_by_key == {}
        assert unknown == 1
        assert detected == 1

    def test_no_faces_detected_is_a_valid_empty_result_not_an_error(self):
        known = [("student-a", synthetic_encoding(0.0))]

        best_by_key, detected, unknown = matcher.match_frames([[]], known)

        assert best_by_key == {}
        assert detected == 0
        assert unknown == 0

    def test_two_faces_matching_one_student_collapse_to_the_better_distance(self):
        known = [("student-a", synthetic_encoding(0.0))]
        closer_face = synthetic_encoding(0.05)
        farther_face = synthetic_encoding(0.20)

        best_by_key, detected, unknown = matcher.match_frames(
            [[farther_face, closer_face]], known
        )

        assert best_by_key == {"student-a": pytest.approx(0.05)}
        # The surplus (worse) face becomes unknown rather than a second
        # presence or a reassignment to a runner-up student.
        assert unknown == 1
        assert detected == 2

    def test_a_key_can_only_be_produced_from_an_encoding_present_in_known(self):
        """Structural basis of cross-class isolation: a face can never be
        attributed to a key whose encoding was never supplied.
        """
        known = [("student-a", synthetic_encoding(0.0))]
        face = synthetic_encoding(0.0)

        best_by_key, _, _ = matcher.match_frames([[face]], known)

        assert set(best_by_key.keys()) <= {key for key, _ in known}


# --- multi-frame (video) matching ------------------------------------------


class TestMatchFramesAcrossFrames:
    def test_a_key_recognized_in_any_single_frame_counts_as_found(self):
        known = [("student-a", synthetic_encoding(0.0))]
        frame_with_match = [synthetic_encoding(0.0)]
        frame_without_match = []

        best_by_key, _, _ = matcher.match_frames(
            [frame_without_match, frame_with_match, frame_without_match], known
        )

        assert "student-a" in best_by_key

    def test_the_best_distance_across_frames_is_kept(self):
        known = [("student-a", synthetic_encoding(0.0))]
        worse_frame = [synthetic_encoding(0.30)]
        better_frame = [synthetic_encoding(0.05)]

        best_by_key, _, _ = matcher.match_frames([worse_frame, better_frame], known)

        assert best_by_key["student-a"] == pytest.approx(0.05)

    def test_detected_and_unknown_faces_take_the_maximum_over_frames_not_the_sum(self):
        known = []
        busy_frame = [
            synthetic_encoding(1.0),
            synthetic_encoding(2.0),
            synthetic_encoding(3.0),
        ]
        quiet_frame = [synthetic_encoding(1.0)]

        best_by_key, detected, unknown = matcher.match_frames(
            [busy_frame, quiet_frame, busy_frame], known
        )

        # Never 3 + 1 + 3 = 7; the busiest single frame is the estimate.
        assert detected == 3
        assert unknown == 3

    def test_a_student_recognized_in_only_one_frame_is_still_proposed_present(self):
        known = [("student-a", synthetic_encoding(0.0)), ("student-b", synthetic_encoding(5.0))]
        frame_one = [synthetic_encoding(0.0)]  # only student-a appears
        frame_two = []  # empty frame

        best_by_key, _, _ = matcher.match_frames([frame_one, frame_two], known)

        assert "student-a" in best_by_key
        assert "student-b" not in best_by_key
