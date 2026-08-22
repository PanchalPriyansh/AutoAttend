"""Domain exceptions specific to attendance.

Bad input, missing target, and conflicting state already exist in
common/errors.py and are reused rather than redeclared -- only the idea
below is genuinely new to this feature.
"""

from common.errors import AppError

__all__ = ["ForbiddenError"]


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
