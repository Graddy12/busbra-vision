from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from cancer_app import create_app
from cancer_app.inference import _png_data_url, normalize_uploaded_image
import numpy as np


class FakePredictor:
    def status(self):
        return {"loaded": True, "device": "cpu"}

    def analyze(self, image):
        assert image.mode == "RGB"
        return {
            "classification": {
                "code": "benign",
                "label": "Bénigne",
                "confidence": 0.8,
                "confidence_percent": 80.0,
                "probabilities": [{"code": "benign", "label": "Bénigne", "percent": 80.0, "probability": 0.8}],
            },
            "segmentation": {
                "threshold": 0.5,
                "lesion_detected": True,
                "lesion_area_percent": 12.5,
                "mask_data_url": _png_data_url(np.zeros((64, 64), dtype=np.uint8)),
                "overlay_data_url": _png_data_url(np.zeros((64, 64, 3), dtype=np.uint8)),
            },
            "gradcam": {"available": True, "class_code": "benign", "overlay_data_url": _png_data_url(np.zeros((64, 64, 3), dtype=np.uint8))},
            "meta": {"device": "cpu", "inference_ms": 1.0, "disclaimer": "test"},
        }


def png_bytes(size=(64, 64)):
    buffer = io.BytesIO()
    # Textured grayscale fixture: a flat square is no longer a valid analysis input.
    gray = np.random.default_rng(42).integers(35, 125, (size[1], size[0]), dtype=np.uint8)
    Image.fromarray(gray).convert("RGB").save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "BOOTSTRAP_ADMIN_PASSWORD": "TestAdmin!2026"}, predictor=FakePredictor())


def csrf(client):
    with client.session_transaction() as state:
        return state["csrf_token"]


def login(client, username="admin", password="TestAdmin!2026"):
    client.get("/login")
    return client.post("/login", data={"csrf_token": csrf(client), "username": username, "password": password})


@pytest.fixture
def client(app):
    client = app.test_client()
    assert login(client).status_code == 302
    return client


def analyze(client):
    return client.post("/api/analyze", headers={"X-CSRF-Token": csrf(client)}, data={"reference": "TEST-001", "image": (io.BytesIO(png_bytes()), "echo.png")})


def test_home_page(client):
    assert client.get("/").location == "/admin"
    assert client.get("/workspace").location == "/workspace/"
    response = client.get("/workspace/")
    assert response.status_code == 200
    assert "Résultats combinés" in response.get_data(as_text=True)
    assert b'id="engine-mode"' in response.data
    assert b'5091' not in response.data
    assert b'href="/workspace/"' in client.get("/admin").data


def test_pwa_invitation_requires_authentication(app):
    response = app.test_client().get("/login")
    assert response.status_code == 200
    assert b'Essayer la PWA hors ligne' not in response.data


def test_combined_analysis_endpoint(client, app):
    response = client.post(
        "/api/analyze",
        headers={"X-CSRF-Token": csrf(client)},
        data={"image": (io.BytesIO(png_bytes()), "echographie.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["classification"]["code"] == "benign"
    assert payload["segmentation"]["lesion_detected"] is True
    assert payload["meta"]["filename"] == "echographie.png"
    assert payload["analysis"]["id"]
    assert payload["analysis"]["feedback_url"].endswith(f"/api/analyses/{payload['analysis']['id']}/feedback")
    row = app.extensions["store"].read("analyses")[0]
    assert "overlay_data_url" not in row["result"]["gradcam"]
    assert client.get(payload["analysis"]["history_url"]).status_code == 200
    assert client.get(payload["analysis"]["report_url"]).status_code == 200


def test_server_feedback_is_saved_and_visible_in_report(client, app):
    payload = analyze(client).get_json()
    ident = payload["analysis"]["id"]
    response = client.post(
        payload["analysis"]["feedback_url"],
        headers={"X-CSRF-Token": csrf(client)},
        json={"assessment": "incorrect", "corrected_class": "malignant", "note": "À revoir en réunion."},
    )
    assert response.status_code == 200
    row = app.extensions["store"].analysis(app.extensions["store"].users()[0], ident)
    assert row["feedback"]["is_incorrect"] is True
    assert row["feedback"]["corrected_class"] == "malignant"
    assert row["status"] == "validated"
    report = client.get(payload["analysis"]["report_url"]).get_data(as_text=True)
    assert "classe corrigée : Maligne" in report
    assert "À revoir en réunion." in report


def test_missing_upload_is_rejected(client):
    response = client.post("/api/analyze", headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 400


def test_invalid_image_is_rejected(client):
    response = client.post(
        "/api/analyze",
        headers={"X-CSRF-Token": csrf(client)},
        data={"image": (io.BytesIO(b"not-an-image"), "fake.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_normalization_rejects_tiny_image():
    try:
        normalize_uploaded_image(png_bytes((16, 16)))
    except ValueError as exc:
        assert "32" in str(exc)
    else:
        raise AssertionError("A tiny image should be rejected")


def test_pages_and_api_require_login(app):
    guest = app.test_client()
    for page in ("/", "/history", "/admin", "/dashboard"):
        assert guest.get(page).status_code == 302
    assert guest.post("/api/analyze").status_code == 401


def test_csrf_and_login_errors(app, client):
    assert client.post("/api/analyze").status_code == 400
    guest = app.test_client()
    guest.get("/login")
    assert guest.post("/login", data={"username": "admin", "password": "TestAdmin!2026"}).status_code == 400
    response = login(guest, password="wrong")
    assert "incorrect" in response.get_data(as_text=True)


def doctor(app, username):
    store = app.extensions["store"]
    return store.save_user(store.users()[0], {"username": username, "name": f"Dr {username}", "specialty": "Oncologie", "role": "doctor", "password": "DoctorPass!2026"})


def test_doctors_cannot_access_other_history_or_assets(app):
    doctor(app, "alice")
    doctor(app, "bob")
    alice, bob = app.test_client(), app.test_client()
    login(alice, "alice", "DoctorPass!2026")
    login(bob, "bob", "DoctorPass!2026")
    assert alice.get("/admin").status_code == 403
    payload = analyze(alice).get_json()
    ident = payload["analysis"]["id"]
    for path in (f"/history/{ident}", f"/history/{ident}/report", f"/history/{ident}/assets/original"):
        assert alice.get(path).status_code == 200
        assert bob.get(path).status_code == 404
    assert b"TEST-001" not in bob.get("/history").data
    assert bob.post(f"/history/{ident}/delete", data={"csrf_token": csrf(bob)}).status_code == 404


def test_soft_delete_restore_review_and_report(client):
    payload = analyze(client).get_json()
    ident = payload["analysis"]["id"]
    response = client.post(f"/history/{ident}/review", data={"csrf_token": csrf(client), "status": "validated", "note": "Observation test <script>alert(1)</script>"})
    assert response.status_code == 302
    report = client.get(f"/history/{ident}/report").get_data(as_text=True)
    assert "&lt;script&gt;" in report and "<script>alert(1)" not in report
    assert "Interprétation automatique" in report and "Observation test" in report
    assert client.post(f"/history/{ident}/delete", data={"csrf_token": csrf(client)}).status_code == 302
    assert client.get(f"/history/{ident}").status_code == 404
    assert client.get(f"/history/{ident}/assets/original").status_code == 404
    assert b"TEST-001" in client.get("/history?trash=1").data
    client.post(f"/history/{ident}/restore", data={"csrf_token": csrf(client)})
    assert client.get(f"/history/{ident}").status_code == 200


def test_admin_create_edit_disable_and_reset(app, client):
    form = {"csrf_token": csrf(client), "username": "oncologue", "name": "Dr Démo", "specialty": "Oncologie", "role": "doctor", "password": "DoctorPass!2026"}
    assert client.post("/admin", data=form).status_code == 302
    store = app.extensions["store"]
    user = next(u for u in store.users() if u["username"] == "oncologue")
    assert "DoctorPass!2026" not in user["password_hash"]
    other = app.test_client()
    login(other, "oncologue", "DoctorPass!2026")
    assert other.get("/", follow_redirects=True).status_code == 200
    client.post(f"/admin/users/{user['id']}/toggle", data={"csrf_token": csrf(client)})
    assert other.get("/").status_code == 302
    client.post(f"/admin/users/{user['id']}/toggle", data={"csrf_token": csrf(client)})
    login(other, "oncologue", "DoctorPass!2026")
    form["password"] = "NewPassword!2026"
    client.post(f"/admin/users/{user['id']}/edit", data=form)
    assert other.get("/").status_code == 302
    assert login(other, "oncologue", "NewPassword!2026").status_code == 302


def test_last_admin_and_open_redirect(app, client):
    store = app.extensions["store"]
    admin = store.users()[0]
    with pytest.raises(ValueError):
        store.toggle_user(admin, admin["id"])
    with pytest.raises(ValueError):
        store.save_user(admin, {"username": "admin", "name": "Admin", "role": "doctor"}, admin["id"])
    guest = app.test_client()
    guest.get("/login")
    response = guest.post("/login", data={"csrf_token": csrf(guest), "username": "admin", "password": "TestAdmin!2026", "next": "//evil.example/"})
    assert response.location == "/admin"


def test_storage_concurrent_writes_and_restart(app):
    store = app.extensions["store"]
    actor = store.users()[0]
    def save(index):
        image = Image.new("RGB", (64, 64))
        result = FakePredictor().analyze(image)
        return store.save_analysis(actor, image, result, f"CONCURRENT-{index}")
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(save, range(12)))
    assert len(store.list_analyses(actor)) == 12
    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"]}, predictor=FakePredictor())
    assert restarted.secret_key == app.secret_key
    assert len(restarted.extensions["store"].list_analyses(actor)) == 12


def test_filtered_history_and_dashboard(client):
    analyze(client)
    assert b"TEST-001" in client.get("/history?q=TEST-001&category=benign&status=pending").data
    assert b"TEST-001" not in client.get("/history?category=malignant").data
    assert client.get("/dashboard").status_code == 200
    assert client.get("/admin").status_code == 200


@pytest.mark.parametrize("destination", ["/", "/history", "/admin", "//evil.example/"])
def test_admin_lands_directly_in_administration(app, destination):
    user = app.test_client()
    user.get("/login")
    response = user.post("/login", data={"csrf_token": csrf(user), "username": "admin", "password": "TestAdmin!2026", "next": destination})
    assert response.location == "/admin"
    assert user.get("/login").location == "/admin"


def test_admin_pages_have_a_separate_layout(app, client):
    admin_id = app.extensions["store"].users()[0]["id"]
    for path in ("/admin", "/admin/audit", f"/admin/users/{admin_id}/edit"):
        response = client.get(path)
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "data-admin-shell" in html
        assert 'aria-label="Navigation administration"' in html
        assert 'aria-label="Navigation principale"' not in html
    workspace = client.get("/workspace", follow_redirects=True).get_data(as_text=True)
    assert 'aria-label="Navigation principale"' in workspace
    assert "data-admin-shell" not in workspace


def test_doctor_lands_in_medical_workspace(app):
    doctor(app, "medecin")
    user = app.test_client()
    response = login(user, "medecin", "DoctorPass!2026")
    assert response.location == "/"
    assert user.get("/").location == "/workspace/"
    assert user.get("/", follow_redirects=True).status_code == 200
    assert user.get("/admin/audit").status_code == 403
    user.post("/logout", data={"csrf_token": csrf(user)})
    user.get("/login")
    response = user.post("/login", data={"csrf_token": csrf(user), "username": "medecin", "password": "DoctorPass!2026", "next": "/admin/audit"})
    assert response.location == "/"
