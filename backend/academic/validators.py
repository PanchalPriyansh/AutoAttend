"""Input parsing/validation helpers for the academic hierarchy routes.

All four helpers moved to common/validators.py once user management
needed the same ObjectId and string parsing; they are re-exported here so
routes/academic.py keeps importing from its own package. The re-export
binds the same function objects -- no behaviour, signature, or message
changed in the move.

Semester date parsing (`parse_date`, `require_end_after_start`) is still
only used by this package, but it lives in common/ alongside the other
two rather than being split across both modules.
"""

from common.validators import (
    parse_date,
    parse_object_id,
    require_end_after_start,
    require_non_empty_string,
)

__all__ = [
    "parse_object_id",
    "require_non_empty_string",
    "parse_date",
    "require_end_after_start",
]
