from __future__ import annotations

import os
from pathlib import Path

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .inference import CombinedPredictor
from .offline import register_offline_routes
from .routes import register_routes
from .security import init_security
from .storage import JsonStore


def create_app(test_config: dict | None = None, predictor=None) -> Flask:
    project_root = Path(__file__).resolve().parent.parent

    app = Flask(__name__)
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
        CLASSIFICATION_MODEL_PATH=str(
            project_root
            / "classification_package"
            / "BUSI_EfficientNetB0_classification.keras"
        ),
        CLASSIFICATION_CONFIG_PATH=str(
            project_root / "classification_package" / "config_modele.json"
        ),
        SEGMENTATION_MODEL_PATH=str(
            project_root / "segmentation_package" / "best_model.pt"
        ),
        SEGMENTATION_SELECTION_PATH=str(
            project_root / "segmentation_package" / "selection.json"
        ),
        INFERENCE_DEVICE=os.getenv("INFERENCE_DEVICE", "cpu"),
        DATA_DIR=os.getenv("BUSBRA_DATA_DIR", str(project_root / "instance")),
        BOOTSTRAP_ADMIN_PASSWORD=os.getenv("BUSBRA_ADMIN_PASSWORD"),
        PRODUCTION=os.getenv("BUSBRA_PRODUCTION", "0") == "1",
        OFFLINE_ASSET_DIR=os.getenv("BUSBRA_OFFLINE_ASSET_DIR", str(project_root / "offline_poc")),
        OFFLINE_PWA_URL="/workspace/",
        SECRET_KEY=os.getenv("BUSBRA_SECRET_KEY") or None,
    )

    if test_config:
        app.config.update(test_config)

    if app.config["PRODUCTION"]:
        if len(app.config["BOOTSTRAP_ADMIN_PASSWORD"] or "") < 16:
            raise RuntimeError("Définissez BUSBRA_ADMIN_PASSWORD avec au moins 16 caractères dans les variables privées du service.")
        if app.config["SECRET_KEY"] and len(app.config["SECRET_KEY"]) < 32:
            raise RuntimeError("BUSBRA_SECRET_KEY doit contenir au moins 32 caractères.")
        if os.getenv("RAILWAY_ENVIRONMENT_ID"):
            mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
            if not mount or not Path(app.config["DATA_DIR"]).resolve().is_relative_to(Path(mount).resolve()):
                raise RuntimeError("Attachez un volume Railway persistant à /data et configurez BUSBRA_DATA_DIR dans ce volume.")
        app.config.update(SESSION_COOKIE_SECURE=True, PREFERRED_URL_SCHEME="https")
        # Railway is the single trusted edge proxy; never trust forwarded hosts.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0)

    store = JsonStore(app.config["DATA_DIR"], app.config["BOOTSTRAP_ADMIN_PASSWORD"])
    app.secret_key = app.config.get("SECRET_KEY") or store.secret_key
    app.extensions["store"] = store
    init_security(app, store)

    @app.template_filter("localdate")
    def localdate(value):
        from datetime import datetime, timedelta, timezone
        if not value:
            return "—"
        date = datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=1)))
        return date.strftime("%d/%m/%Y à %H:%M")

    @app.template_filter("review_status")
    def review_status(value):
        return {
            "pending": "À examiner",
            "validated": "Validée",
            "discordance_review": "Discordance à revoir",
            "system_confirmed": "Prédiction du système confirmée",
            "medical_correction_confirmed": "Correction médicale confirmée",
            "additional_exam_required": "Examen complémentaire requis",
        }.get(value, value or "—")

    if predictor is None:
        predictor = CombinedPredictor(
            classification_model_path=app.config["CLASSIFICATION_MODEL_PATH"],
            classification_config_path=app.config["CLASSIFICATION_CONFIG_PATH"],
            segmentation_model_path=app.config["SEGMENTATION_MODEL_PATH"],
            segmentation_selection_path=app.config["SEGMENTATION_SELECTION_PATH"],
            device=app.config["INFERENCE_DEVICE"],
        )

    app.extensions["combined_predictor"] = predictor
    register_offline_routes(app)
    register_routes(app)
    return app
