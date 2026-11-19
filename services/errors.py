"""Stable API error helpers."""
from flask import jsonify


def api_error(message, status=400):
    return jsonify({"success": False, "error": message}), status
