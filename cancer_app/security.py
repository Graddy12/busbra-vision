from __future__ import annotations

import secrets
from datetime import timedelta
from functools import wraps
from urllib.parse import urlsplit

from flask import abort, g, jsonify, redirect, request, session, url_for


def init_security(app, store):
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=timedelta(hours=8))

    @app.before_request
    def authenticate():
        g.user = None
        if session.get("user_id"):
            user = store.session_user(session["user_id"])
            if user and user["active"] and user["auth_version"] == session.get("auth_version"):
                g.user = {k: v for k, v in user.items() if k != "password_hash"}
            else:
                session.clear()
        if request.endpoint not in {"login", "static", "health"} and not g.user:
            if request.path.startswith("/api/"):
                return jsonify(error="Votre session a expiré. Reconnectez-vous.", login_url=url_for("login")), 401
            return redirect(url_for("login", next=request.path))
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(str(supplied), str(expected)):
                if request.path.startswith("/api/"):
                    return jsonify(error="La page a expiré. Actualisez-la avant de réessayer."), 400
                abort(400, "La page a expiré. Actualisez-la avant de réessayer.")

    @app.context_processor
    def shared_context():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(32)
        return {
            "current_user": g.user, "csrf_token": session["csrf_token"],
            "medical_home_url": url_for("medical_workspace" if g.user and g.user["role"] == "admin" else "index"),
        }

    @app.after_request
    def response_headers(response):
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return response


def admin_only(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not g.user or g.user["role"] != "admin":
            abort(403)
        return function(*args, **kwargs)
    return wrapped


def safe_next(value):
    if value and any(ord(character) < 32 for character in value):
        return url_for("index")
    parts = urlsplit(value or "")
    return value if value and value.startswith("/") and not value.startswith("//") and not parts.netloc and not parts.scheme and "\\" not in value else url_for("index")
