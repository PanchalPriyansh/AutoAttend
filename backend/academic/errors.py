"""Domain exceptions for the academic hierarchy.

Kept in their own module because both validators.py (which raises
ValidationError while parsing input) and service.py (which raises the
other three) need them -- putting them in either module would make the
other import it just for an exception class.

Every message here is client-safe: routes/academic.py returns it verbatim
in the JSON error body, so it must never carry MongoDB internals, driver
text, or anything about the query that produced it.
"""


class AcademicError(Exception):
    """Base for every academic-hierarchy domain error."""


class ValidationError(AcademicError):
    """Malformed or missing input -- maps to 400."""


class NotFoundError(AcademicError):
    """A target document, or a referenced parent, does not exist -- maps to 404."""


class DuplicateError(AcademicError):
    """Violates a scoped unique index -- maps to 409."""


class HasChildrenError(AcademicError):
    """Delete blocked because child documents still reference it -- maps to 409."""
