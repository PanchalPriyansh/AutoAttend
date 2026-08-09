from datetime import datetime, timezone

from flask import Blueprint, jsonify

from database.db import get_db_status

health_bp = Blueprint("health", __name__)


@health_bp.route("/api/health", methods=["GET"])
def health():
    db_status = get_db_status()
    overall_status = "ok" if db_status["connected"] else "degraded"

    return jsonify({
        "status": overall_status,
        "backend": "up",
        "database": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), 200
