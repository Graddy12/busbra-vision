from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict, deque

from flask import Flask, abort, current_app, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash

from .inference import ModelLoadError, normalize_uploaded_image
from .interpretation import interpret
from .security import admin_only, safe_next
from .segmentation_policy import enforce_segmentation_policy


def register_routes(app: Flask) -> None:
    store = app.extensions["store"]
    attempts = defaultdict(deque)
    attempts_lock = threading.Lock()

    def get_analysis(ident, include_deleted=False):
        row = store.analysis(g.user, ident, include_deleted)
        if row is None:
            abort(404)
        return row

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("admin" if g.user["role"] == "admin" else "index"))
        error = None
        if request.method == "POST":
            username = request.form.get("username", "").strip().lower()
            key = request.remote_addr or "local"
            with attempts_lock:
                recent = attempts[key]
                while recent and recent[0] < time.monotonic() - 300:
                    recent.popleft()
                limited = len(recent) >= 10
                if not limited:
                    recent.append(time.monotonic())
            if limited:
                return render_template("login.html", error="Trop de tentatives. Réessayez dans cinq minutes."), 429
            user = store.login_user(username)
            if user and user["active"] and check_password_hash(user["password_hash"], request.form.get("password", "")):
                with attempts_lock:
                    attempts.pop(key, None)
                session.clear()
                session.update(user_id=user["id"], auth_version=user["auth_version"], csrf_token=secrets.token_hex(32))
                session.permanent = True
                store.audit(user, "Connexion", username)
                if user["role"] == "admin":
                    return redirect(url_for("admin"))
                destination = safe_next(request.form.get("next"))
                if destination.split("?", 1)[0].rstrip("/").startswith("/admin"):
                    destination = url_for("index")
                return redirect(destination)
            error = "Identifiant ou mot de passe incorrect, ou compte désactivé."
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        if g.user:
            store.audit(g.user, "Déconnexion", g.user["username"])
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    def index():
        if g.user["role"] == "admin":
            return redirect(url_for("admin"))
        return redirect(url_for("offline_asset"))

    @app.get("/workspace")
    def medical_workspace():
        """Explicit access to the medical workspace, separate from admin home."""
        return redirect(url_for("offline_asset"))

    @app.get("/workspace/server")
    def classic_workspace():
        """Compatibility view; the normal medical entry point is now unified."""
        return render_template("index.html")

    @app.get("/api/session")
    def session_context():
        # Read at runtime only: never embed credentials into the cached PWA shell.
        return jsonify(
            user={"name": g.user["name"], "role": g.user["role"]},
            csrf_token=session["csrf_token"], analyze_url=url_for("analyze"),
            history_url=url_for("history"), dashboard_url=url_for("dashboard"),
            admin_url=url_for("admin") if g.user["role"] == "admin" else None,
            logout_url=url_for("logout"),
        )

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", version="2.4", models=current_app.extensions["combined_predictor"].status())

    @app.post("/api/analyze")
    def analyze():
        upload = request.files.get("image")
        if upload is None:
            return _error("Ajoutez une image dans le champ `image`.", 400)
        if not upload.filename:
            return _error("Aucun fichier n'a été sélectionné.", 400)

        reference = request.form.get("reference", "").strip()
        if len(reference) > 100:
            return _error("La référence doit contenir au maximum 100 caractères.", 400)

        try:
            image = normalize_uploaded_image(upload.read())
            predictor = current_app.extensions["combined_predictor"]
            result = enforce_segmentation_policy(predictor.analyze(image), image)
            result["meta"]["filename"] = upload.filename
            row = store.save_analysis(g.user, image, result, reference)
            result["analysis"] = {"id": row["id"], "reference": row["reference"], "history_url": url_for("analysis_detail", ident=row["id"]), "report_url": url_for("report", ident=row["id"]), "original_url": url_for("asset", ident=row["id"], kind="original")}
            return jsonify(result)
        except ValueError as exc:
            return _error(str(exc), 400)
        except ModelLoadError as exc:
            current_app.logger.exception("Impossible de charger les modèles")
            return _error(
                "Les modèles n'ont pas pu être initialisés.",
                503,
                # This is a local academic prototype. Showing the model-loading
                # cause is essential for fixing a missing or incompatible package.
                detail=None if current_app.config["PRODUCTION"] else str(exc),
            )
        except Exception:
            current_app.logger.exception("Erreur pendant l'analyse ou l'enregistrement")
            return _error("L'analyse ou son enregistrement a échoué. Consultez le journal du serveur.", 500)

    @app.get("/dashboard")
    def dashboard():
        rows = store.list_analyses(g.user)
        counts = {key: sum(r["result"]["classification"]["code"] == key for r in rows) for key in ("normal", "benign", "malignant")}
        return render_template("dashboard.html", rows=rows[:6], total=len(rows), pending=sum(r["status"] == "pending" for r in rows), counts=counts)

    @app.get("/history")
    def history():
        trash = request.args.get("trash") == "1"
        rows = store.list_analyses(g.user, deleted=trash)
        query = request.args.get("q", "").strip().lower()
        category = request.args.get("category", "")
        status = request.args.get("status", "")
        date = request.args.get("date", "")
        rows = [r for r in rows if (not query or query in f"{r['reference']} {r['owner_name']} {r['result']['meta'].get('filename', '')}".lower()) and (not category or r["result"]["classification"]["code"] == category) and (not status or r["status"] == status) and (not date or datetime.fromisoformat(r["created_at"]).astimezone(timezone(timedelta(hours=1))).date().isoformat() == date)]
        return render_template("history.html", rows=rows, trash=trash)

    @app.get("/history/<ident>")
    def analysis_detail(ident):
        row = get_analysis(ident)
        return render_template("detail.html", row=row, interpretation=interpret(row))

    @app.get("/history/<ident>/report")
    def report(ident):
        row = get_analysis(ident)
        return render_template("report.html", row=row, interpretation=interpret(row))

    @app.get("/history/<ident>/assets/<kind>")
    def asset(ident, kind):
        row = get_analysis(ident)
        if kind not in row["images"]:
            abort(404)
        path = store.assets / ident / row["images"][kind]
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="image/png", conditional=True)

    @app.post("/history/<ident>/<action>")
    def update_analysis(ident, action):
        get_analysis(ident, include_deleted=action == "restore")
        if action not in {"delete", "restore", "review"}:
            abort(404)
        try:
            store.update_analysis(g.user, ident, action, request.form.get("note", ""), request.form.get("status", "pending"))
            flash({"delete": "Analyse déplacée dans la corbeille.", "restore": "Analyse restaurée.", "review": "Revue enregistrée."}[action], "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("analysis_detail", ident=ident) if action == "review" else url_for("history", trash="1" if action == "restore" else "0"))

    @app.route("/admin", methods=["GET", "POST"])
    @admin_only
    def admin():
        if request.method == "POST":
            try:
                store.save_user(g.user, request.form)
                flash("Compte créé. Transmettez le mot de passe au médecin.", "success")
                return redirect(url_for("admin"))
            except ValueError as exc:
                flash(str(exc), "error")
        return render_template("admin.html", users=store.users())

    @app.get("/admin/audit")
    @admin_only
    def admin_audit():
        return render_template("admin_audit.html", events=store.events())

    @app.route("/admin/users/<ident>/edit", methods=["GET", "POST"])
    @admin_only
    def edit_user(ident):
        user = store.user(ident)
        if user is None:
            abort(404)
        if request.method == "POST":
            try:
                store.save_user(g.user, request.form, ident)
                flash("Profil mis à jour. Une réinitialisation du mot de passe ferme les anciennes sessions.", "success")
                return redirect(url_for("admin"))
            except ValueError as exc:
                flash(str(exc), "error")
        return render_template("edit_user.html", user=user)

    @app.post("/admin/users/<ident>/toggle")
    @admin_only
    def toggle_user(ident):
        try:
            store.toggle_user(g.user, ident)
            flash("État du compte mis à jour.", "success")
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("admin"))

    @app.errorhandler(RequestEntityTooLarge)
    def file_too_large(_error_object):
        return _error("Le fichier dépasse la limite de 16 Mo.", 413)

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    def page_error(error):
        if request.path.startswith("/api/"):
            return _error(error.description, error.code)
        return render_template("error.html", error=error), error.code


def _error(message: str, status: int, detail: str | None = None):
    payload = {"error": message}
    if detail:
        payload["detail"] = detail
    return jsonify(payload), status
