"""Domain exceptions for the academic hierarchy.

The generic three (bad input, missing target, duplicate) now live in
common/errors.py so user management shares them rather than declaring a
parallel hierarchy. They are re-exported here so `academic` code keeps
importing from its own package -- the re-export binds the *same* class
objects, so the `errorhandler` registrations in routes/academic.py match
exactly as before.

Only HasChildrenError is specific to the hierarchy, so only it is
declared here.

Every message raised with these is client-safe: routes/academic.py
returns it verbatim in the JSON error body, so it must never carry
MongoDB internals, driver text, or anything about the query that
produced it.
"""

from common.errors import ConflictError, DuplicateError, NotFoundError, ValidationError

__all__ = [
    "ValidationError",
    "NotFoundError",
    "DuplicateError",
    "HasChildrenError",
]


class HasChildrenError(ConflictError):
    """Delete blocked because child documents still reference it -- maps to 409."""
