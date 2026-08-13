"""Flask-request helpers shared by every route blueprint.

Kept separate from common/validators.py, which is deliberately Flask-free
so it can be unit tested and reused without an app context. This module
is the one place a blueprint-level HTTP concern like "how do I read the
JSON body" belongs.
"""

from flask import request


def json_body():
    """A non-dict JSON payload (e.g. a bare list, string, or number) is
    valid JSON but not a usable request body -- treating it as empty
    routes it through the normal "missing field" -> ValidationError -> 400
    path instead of raising AttributeError from body.get(...) deep inside
    a validator.
    """
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}
