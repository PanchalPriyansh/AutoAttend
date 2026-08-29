"""Domain exceptions specific to attendance.

Bad input, missing target, and conflicting state already exist in
common/errors.py and are reused rather than redeclared -- only the two
ideas below are genuinely new to this feature.
"""

from common.errors import AppError

__all__ = ["ForbiddenError", "ExportUnavailableError"]


class ForbiddenError(AppError):
    """The caller holds the right role but not this particular record --
    maps to 403.

    Distinct from the 403 `role_required` produces. That decorator answers
    "is this user a faculty member?", which a token can settle on its own;
    this answers "is this *their* class?", which needs the document. A
    faculty member reaching another lecturer's class is authenticated,
    correctly-roled, and still not allowed.

    Not a 404: pretending the class does not exist would hide a real
    misconfiguration (an unassigned class, or an assignment given to the
    wrong person) behind a message that sends the faculty member hunting
    for a typo instead of asking the admin.
    """


class ExportUnavailableError(AppError):
    """The PDF writer is not importable -- maps to 503.

    Same reasoning as recognition.errors.RecognitionUnavailableError: the
    request was valid, nothing broke, the server is missing a library,
    and the identical request succeeds once it is installed. A 500 would
    send an operator hunting for a bug that does not exist.

    Deliberately **not** a subclass of RecognitionUnavailableError,
    despite mapping to the same status. They have nothing to do with each
    other -- one is a document writer, the other is computer vision -- and
    a shared parent would let one `except` swallow both and report the
    wrong missing dependency. Only the CSV's sibling endpoint can never
    raise this: writing a CSV needs nothing that is not in the stdlib.
    """
