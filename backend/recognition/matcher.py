"""Turning detected faces into per-student claims.

Pure numeric logic: encodings in, decisions about which known encoding
each detected face is closest to out. No MongoDB, no Flask, no knowledge
of what a student, a class, or an attendance session is -- the caller
supplies opaque keys and gets the same keys back, so this module can be
tested with a handful of synthetic vectors and nothing else.

Distance work is delegated to encoder.py, which owns the recognition
library and the tolerance. Nothing here declares a second threshold for
what counts as a match.

Every result is a heuristic. A face matched here is evidence that a
person was in the room, never proof; the caller is expected to put the
result in front of a human before anything is recorded.
"""

import logging

from recognition import encoder

logger = logging.getLogger(__name__)

# Distances at or below this read as a confident match; between it and
# encoder.MATCH_TOLERANCE the match still counts but is flagged for
# review. This is a display band, not a second tolerance -- moving it
# changes which matches get a second look, never which ones are matches.
#
# Derived as a fraction of MATCH_TOLERANCE rather than a second hardcoded
# distance: a fixed value silently stops flagging anything as "low" the
# moment MATCH_TOLERANCE is tuned below it (as happened when the
# tolerance was retuned from 0.6 to 0.42 against real enrollment photos
# on 2026-08-22 -- every match became "high" because 0.5 no longer fit
# inside the now-narrower match range). Keeping this as a ratio means a
# future retune rescales the band automatically instead of quietly
# breaking it again.
#
# Like the tolerance itself it is a heuristic tuned on the library
# author's data, not this college's. Check it against real captures
# before reading anything into the labels.
LOW_CONFIDENCE_RATIO = 5 / 6
LOW_CONFIDENCE_DISTANCE = encoder.MATCH_TOLERANCE * LOW_CONFIDENCE_RATIO

HIGH_CONFIDENCE = "high"
LOW_CONFIDENCE = "low"


def confidence_for(distance):
    """The review band a match falls into."""
    return HIGH_CONFIDENCE if distance <= LOW_CONFIDENCE_DISTANCE else LOW_CONFIDENCE


def _match_one_frame(face_encodings, keys, known_encodings):
    """Return `(best_by_key, unknown_faces)` for a single frame.

    Each detected face votes for at most one key: its nearest known
    encoding, and only if that neighbour is within tolerance. Faces
    matching nobody are counted, never attached to the closest person
    anyway -- a visitor, a passer-by in a doorway, or a poorly enrolled
    student would otherwise be silently recorded as somebody.

    Two faces claiming one key collapse to the better distance and the
    surplus face becomes unknown. Marking one student present twice is
    impossible by construction, and the duplicate is surfaced rather than
    quietly reassigned to whoever was second-nearest -- that reassignment
    is exactly how one person's attendance ends up on another's record.
    """
    best_by_key = {}
    unknown_faces = 0

    for face in face_encodings:
        match = encoder.closest_match(face, known_encodings)
        if match is None:
            unknown_faces += 1
            continue

        index, distance = match
        if distance > encoder.MATCH_TOLERANCE:
            unknown_faces += 1
            continue

        key = keys[index]
        previous = best_by_key.get(key)
        if previous is None:
            best_by_key[key] = distance
        else:
            # The surplus face is unknown whichever of the two wins.
            unknown_faces += 1
            best_by_key[key] = min(previous, distance)

    return best_by_key, unknown_faces


def match_frames(frames_encodings, known):
    """Match every face across one or more frames against `known`.

    `frames_encodings` is a list of frames, each a list of encodings
    detected in it; a photo is simply the one-frame case. `known` is a
    list of `(key, encoding)` pairs -- several per key is normal, since a
    student may have registered several samples.

    Returns `(best_by_key, detected_faces, unknown_faces)`:

      - `best_by_key` maps each matched key to its smallest distance
        across every frame. A student caught in any single frame counts as
        found; extra frames are more chances at the same room, so the union
        is taken rather than an intersection or a majority vote.
      - `detected_faces` and `unknown_faces` are the *maximum* over frames,
        not the sum. Consecutive frames show the same people, so summing
        would report one visitor standing at the back as eight visitors in
        an eight-frame clip. The busiest single frame is the closest
        honest estimate of how many distinct people were there.
    """
    keys = [key for key, _ in known]
    known_encodings = [encoding for _, encoding in known]

    best_by_key = {}
    detected_faces = 0
    unknown_faces = 0

    for face_encodings in frames_encodings:
        frame_best, frame_unknown = _match_one_frame(
            face_encodings, keys, known_encodings
        )

        for key, distance in frame_best.items():
            previous = best_by_key.get(key)
            if previous is None or distance < previous:
                best_by_key[key] = distance

        detected_faces = max(detected_faces, len(face_encodings))
        unknown_faces = max(unknown_faces, frame_unknown)

    return best_by_key, detected_faces, unknown_faces
