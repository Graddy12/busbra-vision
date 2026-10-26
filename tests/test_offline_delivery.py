"""Safety checks for the isolated public static demo, without loading models."""
import importlib.util
import io
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("offline_delivery_server", ROOT / "offline_poc" / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class Probe:
    def __init__(self, path):
        self.path = path
        self.errors = []
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_error(self, code, *_):
        self.errors.append(code)

    def send_response(self, code):
        self.status = code

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass


@pytest.mark.parametrize("path", ["/instance/users.json", "/instance/analyses.json", "/server.py",
                                 "/README.md", "/../instance/users.json", "/%2e%2e/instance/users.json"])
def test_public_laboratory_never_serves_application_data_or_source(path):
    probe = Probe(path)
    server.Handler.do_GET(probe)
    server.Handler.do_HEAD(probe)
    assert probe.errors == [404, 404]


def test_public_laboratory_refuses_uploads_and_server_analysis():
    probe = Probe("/api/analyze")
    server.Handler.do_POST(probe)
    assert probe.errors == [405]


def test_health_exposes_no_credentials_or_medical_data():
    probe = Probe("/health")
    server.Handler.do_GET(probe)
    assert probe.status == 200
    assert b"static-delivery-only" in probe.wfile.getvalue()
    assert set(server.PUBLIC) == {"/", "/index.html", "/styles.css", "/app.js", "/preprocess.js",
        "/inference-worker.js", "/sw.js", "/manifest.webmanifest", "/icon.svg", "/assets-manifest.json",
        "/ONNX_RUNTIME_LICENSE.txt", "/vendor/ort.wasm.min.js", "/vendor/ort-wasm-simd-threaded.mjs",
        "/vendor/ort-wasm-simd-threaded.wasm", "/models/classification.onnx", "/models/segmentation.onnx"}


def test_offline_interface_matches_online_analysis_workflow_and_keeps_history_local():
    html = (ROOT / "offline_poc" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "offline_poc" / "app.js").read_text(encoding="utf-8")
    assert "Analyser" in html and "Résultats combinés" in html
    assert "Échographie source" in html and "Historique local" in html
    assert "La prédiction est-elle incorrecte ?" in html
    assert "Motif du désaccord" in html
    assert 'id="toast-region"' in html
    assert "function showToast" in script
    assert 'showToast(data.message)' in script
    assert 'if (engineMode() === "local")' in script
    assert "activateLocalEngineIfNeeded" in script
    assert 'createObjectStore("analyses"' in script
    assert 'crypto.subtle.digest("SHA-256"' in script
    assert "isIncorrect: assessment === \"incorrect\"" in script
    assert "correctedClass: assessment === \"incorrect\" ? correctedClass : \"\"" in script
    assert "reason: assessment === \"incorrect\" ? reason : \"\"" in script
    assert "imageBlob: selection.imageBlob" in script
    assert "mask: result.mask ? new Uint8Array(result.mask) : null" in script
    assert 'reportButton.dataset.recordAction = "report"' in script
    assert "Rapport d'analyse d'échographie mammaire" in html
    assert 'id="print-report"' in html
    assert 'source.data);' in script
    assert "/api/analyze" not in script


def test_server_analysis_stays_available_during_optional_offline_download():
    html = (ROOT / "offline_poc" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "offline_poc" / "app.js").read_text(encoding="utf-8")
    assert 'id="choose-image"' in html
    assert "Sélectionner une image" in html
    assert "busy = false, preparing = false" in script
    assert '$("#image").disabled = busy;' in script
    assert '$("#prepare").disabled = busy || preparing || localAssetsReady;' in script
    assert "received / model.bytes" in script
