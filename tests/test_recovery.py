"""The retired hidden account must stay unusable, even with legacy data/config."""
import copy
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

from cancer_app import create_app
from test_app import FakePredictor, app, client, csrf, login


PASSWORD = "Retired-Test-Only!2026-Long"


def legacy_record():
    return {"id": "recovery-retired-test", "username": "secours", "role": "admin",
            "name": "Ancien accès", "active": True, "is_recovery": True,
            "auth_version": 3, "password_hash": generate_password_hash(PASSWORD)}


def restart(app):
    return create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"]}, predictor=FakePredictor())


def test_legacy_file_removed_without_changing_users_analyses_or_past_audit(app, client):
    store = app.extensions["store"]
    with store.lock():
        store.write("recovery", legacy_record())
    store.audit({"id": "old", "name": "Ancien accès", "is_recovery": True}, "Ancienne action", "historique")
    before = {name: (store.root / f"{name}.json").read_bytes() for name in ("users", "analyses")}
    old_audit = copy.deepcopy(store.events())
    new_app = restart(app)
    assert not (store.root / "recovery.json").exists()
    for name, content in before.items():
        assert (store.root / f"{name}.json").read_bytes() == content
    assert new_app.extensions["store"].events()[1:] == old_audit
    assert "Suppression" in new_app.extensions["store"].events()[0]["action"]
    assert login(new_app.test_client()).location == "/admin"
    assert client.get("/admin").status_code == 200


def test_old_credentials_never_authenticate_even_if_legacy_file_is_reintroduced(app):
    store = app.extensions["store"]
    record = legacy_record()
    with store.lock():
        store.write("recovery", record)
    assert store.login_user("secours") is None
    assert store.session_user(record["id"]) is None
    assert "incorrect" in login(app.test_client(), "secours", PASSWORD).get_data(as_text=True)


def test_existing_hidden_admin_sessions_are_revoked(app):
    browser = app.test_client()
    with browser.session_transaction() as state:
        state.update(user_id="recovery-retired-test", auth_version=3, csrf_token="old-token")
    assert browser.get("/admin").location.startswith("/login")
    assert browser.get("/api/session").status_code == 401
    with browser.session_transaction() as state:
        assert "user_id" not in state


def test_old_environment_variable_cannot_recreate_hidden_account(app, monkeypatch):
    monkeypatch.setenv("BUSBRA_RECOVERY_PASSWORD_HASH", generate_password_hash(PASSWORD))
    new_app = restart(app)
    store = new_app.extensions["store"]
    assert not (store.root / "recovery.json").exists()
    assert not hasattr(store, "manage_recovery")
    assert not hasattr(store, "bootstrap_recovery")
    assert not (Path(__file__).resolve().parents[1] / "scripts" / "manage_recovery.py").exists()
    assert store.login_user("secours") is None


def test_retired_identifier_cannot_be_recreated_through_admin_ui(app, client):
    response = client.post("/admin", data={"csrf_token": csrf(client), "username": "secours",
        "name": "Compte caché", "role": "admin", "password": PASSWORD, "is_recovery": "true"})
    assert "retiré" in response.get_data(as_text=True)
    assert len(app.extensions["store"].users()) == 1


def test_migration_prevents_lockout_if_no_ordinary_admin_is_active(app):
    store = app.extensions["store"]
    with store.lock():
        store.write("recovery", legacy_record())
        users = store.read("users")
        users[0]["active"] = False
        store.write("users", users)
    with pytest.raises(RuntimeError, match="administrateur ordinaire actif"):
        restart(app)
    assert (store.root / "recovery.json").exists()


def test_corrupt_legacy_file_cannot_break_regular_admin_login(app):
    store = app.extensions["store"]
    (store.root / "recovery.json").write_text("invalid legacy data", encoding="utf-8")
    new_app = restart(app)
    assert not (store.root / "recovery.json").exists()
    assert login(new_app.test_client()).location == "/admin"
