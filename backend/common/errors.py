"""Domain exceptions shared by every feature package.

These started life in academic/errors.py. User management needs the same
four ideas -- bad input, missing target, conflicting state -- so they live
here rather than being duplicated per feature. Feature-specific errors
still belong to their own package (see academic.errors.HasChildrenError).

Route blueprints map these onto status codes; the mapping is spelled out
in each class's docstring so the two never drift.

Every message carried by these exceptions is returned verbatim in a JSON
error body, so it must never carry MongoDB internals, driver text, a
password, or anything about the query that produced it.
"""


class AppError(Exception):
    """Base for every domain error raised by application code."""


class ValidationError(AppError):
    """Malformed or missing input -- maps to 400."""


class NotFoundError(AppError):
    """A target document, or a referenced parent, does not exist -- maps to 404."""


class ConflictError(AppError):
    """The request collides with existing state -- maps to 409.

    Blueprints can register a single handler for this class and have it
    cover every 409 in the package, since Flask resolves error handlers
    through the exception's __mro__.
    """


class DuplicateError(ConflictError):
    """Violates a unique index -- maps to 409."""
