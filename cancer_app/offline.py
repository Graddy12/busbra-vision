"""Deliver only the public PWA files from the same Flask origin.

The cached shell never contains a user name, session token or medical record.
The worker is restricted to /workspace/, not login, administration or API routes.
"""
from pathlib import Path

from flask import abort, current_app, redirect, send_file, url_for


PUBLIC_ASSETS = frozenset({
    "index.html", "styles.css", "app.js", "preprocess.js", "inference-worker.js", "sw.js",
    "manifest.webmanifest", "icon.svg", "assets-manifest.json", "ONNX_RUNTIME_LICENSE.txt",
    "models/classification.onnx", "models/segmentation.onnx", "vendor/ort.wasm.min.js",
    "vendor/ort-wasm-simd-threaded.mjs", "vendor/ort-wasm-simd-threaded.wasm",
})
MIME_TYPES = {".wasm": "application/wasm", ".mjs": "text/javascript",
              ".js": "text/javascript", ".onnx": "application/octet-stream",
              ".webmanifest": "application/manifest+json"}


def register_offline_routes(app):
    @app.get("/workspace/", defaults={"filename": "index.html"})
    @app.get("/workspace/<path:filename>")
    def offline_asset(filename):
        if filename not in PUBLIC_ASSETS:
            abort(404)
        root = Path(current_app.config["OFFLINE_ASSET_DIR"]).resolve()
        target = (root / filename).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            abort(404)
        response = send_file(target, mimetype=MIME_TYPES.get(target.suffix), conditional=True)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self' blob:; "
            "style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        # Never allow this worker to take over /login, /admin or /api.
        if filename == "sw.js":
            response.headers["Service-Worker-Allowed"] = "/workspace/"
        return response

    @app.get("/offline/", defaults={"filename": "index.html"})
    @app.get("/offline/<path:filename>")
    def legacy_offline_asset(filename):
        if filename not in PUBLIC_ASSETS:
            abort(404)
        if filename == "sw.js":
            # An already-installed worker would otherwise keep serving the old
            # cached /offline/ page without reaching the HTTP redirect below.
            response = current_app.send_static_file("retire-offline-sw.js")
            response.headers["Service-Worker-Allowed"] = "/offline/"
            return response
        return redirect(url_for("offline_asset", filename=filename), code=302)
