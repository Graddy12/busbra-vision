from __future__ import annotations

import hashlib
import io
import runpy

import pytest

from cancer_app import create_app
from scripts import download_models
from test_app import FakePredictor


def production_config(tmp_path, **updates):
    return {"TESTING": True, "PRODUCTION": True, "DATA_DIR": str(tmp_path),
            "BOOTSTRAP_ADMIN_PASSWORD": "Cloud-Unique-Test!2026", **updates}


def test_cloud_bootstrap_does_not_print_password_and_sets_secure_cookies(tmp_path, capsys):
    app = create_app(production_config(tmp_path), predictor=FakePredictor())
    assert "Cloud-Unique-Test!2026" not in capsys.readouterr().out
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["PREFERRED_URL_SCHEME"] == "https"
    response = app.test_client().get("/login", base_url="https://demo.example")
    cookie = response.headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie


@pytest.mark.parametrize("password", [None, "short"])
def test_production_requires_private_strong_initial_password(tmp_path, password):
    with pytest.raises(RuntimeError, match="BUSBRA_ADMIN_PASSWORD"):
        create_app(production_config(tmp_path, BOOTSTRAP_ADMIN_PASSWORD=password), predictor=FakePredictor())
    assert not (tmp_path / "users.json").exists()


def test_railway_refuses_ephemeral_data_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "test-environment")
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    with pytest.raises(RuntimeError, match="volume Railway"):
        create_app(production_config(tmp_path), predictor=FakePredictor())
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path / "different-volume"))
    with pytest.raises(RuntimeError, match="volume Railway"):
        create_app(production_config(tmp_path), predictor=FakePredictor())
    monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
    app = create_app(production_config(tmp_path), predictor=FakePredictor())
    assert app.test_client().get("/login", base_url="https://demo.example").status_code == 200


def test_production_rejects_short_optional_session_secret(tmp_path):
    with pytest.raises(RuntimeError, match="BUSBRA_SECRET_KEY"):
        create_app(production_config(tmp_path, SECRET_KEY="weak"), predictor=FakePredictor())


def spec(content=b"trusted model bytes"):
    return {"path": "weights/model.pt", "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "url": "https://example.invalid/model.pt"}


def test_download_is_verified_and_reused_without_network(tmp_path, monkeypatch):
    content = b"trusted model bytes"
    opened = []
    def fake_open(request, timeout):
        opened.append(request.full_url)
        return io.BytesIO(content)
    monkeypatch.setattr(download_models.urllib.request, "urlopen", fake_open)
    target = download_models.fetch_model(tmp_path, spec(content))
    assert target.read_bytes() == content
    assert download_models.fetch_model(tmp_path, spec(content)) == target
    assert len(opened) == 1
    assert not list(target.parent.glob(".model-*"))


def test_download_rejects_bad_checksum_without_overwriting_existing_file(tmp_path, monkeypatch):
    target = tmp_path / "weights" / "model.pt"
    target.parent.mkdir()
    target.write_bytes(b"previous model")
    monkeypatch.setattr(download_models.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"x" * len(b"trusted model bytes")))
    monkeypatch.setattr(download_models.time, "sleep", lambda _: None)
    with pytest.raises(ValueError, match="SHA-256"):
        download_models.fetch_model(tmp_path, spec())
    assert target.read_bytes() == b"previous model"
    assert not list(target.parent.glob(".model-*"))


def test_verify_only_never_downloads_and_paths_stay_inside_project(tmp_path):
    with pytest.raises(ValueError, match="manquant"):
        download_models.fetch_model(tmp_path, spec(), verify_only=True)
    invalid = {**spec(), "path": "../outside.pt"}
    with pytest.raises(ValueError, match="hors du projet"):
        download_models.fetch_model(tmp_path, invalid)


def test_gunicorn_binds_railway_port_with_one_model_copy(monkeypatch):
    monkeypatch.setenv("PORT", "4321")
    config = runpy.run_path("gunicorn.conf.py")
    assert config["bind"] == "0.0.0.0:4321"
    assert config["workers"] == 1 and config["preload_app"] is True
