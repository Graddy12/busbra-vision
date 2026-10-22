from pathlib import Path

import pytest

from test_app import app, client, csrf


def test_same_origin_public_shell_contains_no_session_or_account_data(app, client):
    signed_in = client.get("/workspace/")
    public = app.test_client().get("/workspace/")
    assert signed_in.status_code == public.status_code == 200
    assert signed_in.data == public.data
    assert csrf(client).encode() not in public.data
    assert b'csrf-token' not in public.data
    assert b'id="engine-mode"' in public.data
    assert b'../login?next=/workspace/' in public.data
    assert public.headers["Cache-Control"] == "no-store"


def test_server_context_requires_login_and_is_never_cached(app, client):
    assert app.test_client().get("/api/session").status_code == 401
    response = client.get("/api/session")
    assert response.status_code == 200
    context = response.get_json()
    assert context["csrf_token"] == csrf(client)
    assert context["analyze_url"] == "/api/analyze"
    assert context["history_url"] == "/history"
    assert context["admin_url"] == "/admin"
    assert set(context["user"]) == {"name", "role"}
    assert response.headers["Cache-Control"] == "no-store"
    assert "password" not in response.get_data(as_text=True)


@pytest.mark.parametrize("path", ["/workspace/server.py", "/workspace/README.md",
    "/workspace/distribution.json", "/workspace/../instance/users.json",
    "/workspace/%2e%2e/instance/analyses.json", "/workspace/.env", "/offline/server.py"])
def test_unified_mount_cannot_serve_private_files(app, path):
    assert app.test_client().get(path).status_code != 200


def test_worker_scope_and_runtime_mime_types(app):
    guest = app.test_client()
    worker = guest.get("/workspace/sw.js")
    assert worker.status_code == 200
    assert worker.headers["Service-Worker-Allowed"] == "/workspace/"
    assert "text/javascript" in worker.content_type
    manifest = guest.get("/workspace/manifest.webmanifest")
    assert manifest.content_type == "application/manifest+json"
    assert manifest.get_json()["scope"] == "./"
    # A fixture root verifies MIME + range serving without reading model weights.
    root = Path(app.config["DATA_DIR"]) / "public-test-assets"
    (root / "vendor").mkdir(parents=True)
    (root / "models").mkdir()
    (root / "vendor" / "ort-wasm-simd-threaded.wasm").write_bytes(b"\x00asm")
    (root / "models" / "classification.onnx").write_bytes(b"model-bytes")
    app.config["OFFLINE_ASSET_DIR"] = str(root)
    assert guest.head("/workspace/vendor/ort-wasm-simd-threaded.wasm").content_type == "application/wasm"
    response = guest.get("/workspace/models/classification.onnx", headers={"Range": "bytes=0-3"})
    assert response.status_code == 206 and response.data == b"mode"


def test_worker_never_caches_auth_api_or_server_medical_history(app):
    source = app.test_client().get("/workspace/sw.js").get_data(as_text=True)
    assert 'event.request.method !== "GET"' in source
    assert "!ALLOWED.has(url.pathname)" in source
    for private in ("/login", "/api/session", "/api/analyze", "/admin", "/history"):
        assert private not in source


def test_legacy_offline_links_redirect_and_old_worker_can_be_upgraded(app):
    guest = app.test_client()
    assert guest.get("/offline/").location == "/workspace/"
    assert guest.get("/offline/app.js").location == "/workspace/app.js"
    worker = guest.get("/offline/sw.js")
    assert worker.status_code == 200
    assert worker.headers["Service-Worker-Allowed"] == "/offline/"
    assert worker.headers["Cache-Control"] == "no-store"
    assert b"client.navigate" in worker.data
    assert b"/workspace/" in worker.data
