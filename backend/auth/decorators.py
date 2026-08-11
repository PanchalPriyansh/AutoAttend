from functools import wraps

from flask import jsonify
from flask_jwt_extended import get_jwt, jwt_required


def role_required(*roles):
    """Require a valid access token AND that its `role` claim is one of `roles`.

    Every future protected route in the project should use this decorator
    rather than checking roles ad hoc -- authorization must live on the
    backend, never only in the frontend.
    """

    def decorator(fn):
        @jwt_required()
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if get_jwt().get("role") not in roles:
                return jsonify({"error": "Forbidden"}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator
