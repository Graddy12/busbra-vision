from __future__ import annotations

import json

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from cancer_app import create_app
from scripts.manage_recovery import main
from test_app import FakePredictor, analyze, app, client, csrf, login


PASSWORD = "Recovery-Test-Only!2026-Long"
NEW_PASSWORD = "Recovery-Rotated!2026-Long"


def provision(app):
    return app.extensions["store"].manage_recovery("create", PASSWORD)


def test_recovery_is_opt_in_and_not_a_default_password(app):
    store = app.extensions["store"]
    assert not (store.root / "recovery.json").exists()
    assert store.login_user("secours") is None
    assert "incorrect" in login(app.test_client(), "secours", PASSWORD).get_data(as_text=True)


def test_optional_recovery_configuration_does_not_break_normal_sessions(app, client):
    store = app.extensions["store"]
    with store.lock():
        store.write("recovery", {"invalid_configuration": True})
    assert client.get("/admin").status_code == 200
    normal = app.test_client()
    assert login(normal).location == "/admin"
    assert normal.get("/admin").status_code == 200


def test_recovery_authenticates_but_is_not_in_user_directory(app, client):
    recovery = provision(app)
    store = app.extensions["store"]
    raw = store.read("recovery")
    assert check_password_hash(raw["password_hash"], PASSWORD)
    assert PASSWORD not in (store.root / "recovery.json").read_text()
    assert "password_hash" not in recovery
    assert store.user(recovery["id"]) is None
    assert store.users() == store.read("users") and len(store.users()) == 1
    directory = client.get("/admin").get_data(as_text=True)
    assert "Administrateur de secours" not in directory and recovery["id"] not in directory
    emergency = app.test_client()
    assert login(emergency, "secours", PASSWORD).location == "/admin"
    assert emergency.get("/").location == "/admin"
    page = emergency.get("/admin").get_data(as_text=True)
    assert "Mode secours" in page and "1 compte(s)" in page
    assert recovery["id"] not in page
    with emergency.session_transaction() as state:
        assert "password_hash" not in state and PASSWORD not in json.dumps(dict(state))
    event = next(e for e in store.events() if e["action"] == "Connexion de secours")
    assert event["recovery"] is True and event["actor_id"] == recovery["id"]
    assert event["actor_username"] == "secours"
    assert "Connexion de secours" in client.get("/admin/audit").get_data(as_text=True)


def test_recovery_cannot_be_modified_from_http_even_by_itself(app, client):
    recovery = provision(app)
    store = app.extensions["store"]
    before = (store.root / "recovery.json").read_bytes()
    for browser in (client, app.test_client()):
        if browser is not client:
            login(browser, "secours", PASSWORD)
        path = f"/admin/users/{recovery['id']}"
        assert browser.get(f"{path}/edit").status_code == 404
        assert browser.post(f"{path}/edit", data={"csrf_token": csrf(browser), "password": "OtherPassword!2026"}).status_code == 404
        response = browser.post(f"{path}/toggle", data={"csrf_token": csrf(browser)}, follow_redirects=True)
        assert "introuvable" in response.get_data(as_text=True)
    assert (store.root / "recovery.json").read_bytes() == before
    with pytest.raises(ValueError, match="introuvable"):
        store.save_user(store.users()[0], {"username": "another", "name": "Other", "role": "admin"}, recovery["id"])


def test_reserved_username_and_no_web_creation_of_recovery(app, client):
    response = client.post("/admin", data={
        "csrf_token": csrf(client), "username": "Secours", "name": "Reserved",
        "password": PASSWORD, "role": "admin", "is_recovery": "true",
    })
    assert "réservé" in response.get_data(as_text=True)
    store = app.extensions["store"]
    assert len(store.users()) == 1 and not (store.root / "recovery.json").exists()
    client.post("/admin", data={
        "csrf_token": csrf(client), "username": "regular", "name": "Regular",
        "password": PASSWORD, "role": "admin", "is_recovery": "true",
    })
    assert not store.login_user("regular").get("is_recovery")


def test_recovery_can_restore_access_and_create_accounts_without_deleting_data(app, client):
    analyze(client)
    store = app.extensions["store"]
    users = store.users()
    admin_id = users[0]["id"]
    saved_analyses = (store.root / "analyses.json").read_bytes()
    saved_images = {p.relative_to(store.assets): p.read_bytes() for p in store.assets.rglob("*.png")}
    # Simulate a lost/disabled normal administrator; recovery does not depend on it.
    with store.lock():
        users[0]["active"] = False
        store.write("users", users)
    provision(app)
    emergency = app.test_client()
    assert login(emergency, "secours", PASSWORD).location == "/admin"
    response = emergency.post(f"/admin/users/{admin_id}/edit", data={
        "csrf_token": csrf(emergency), "username": "admin", "name": "Administrateur",
        "role": "admin", "password": "RestoredAdmin!2026",
    })
    assert response.status_code == 302
    emergency.post(f"/admin/users/{admin_id}/toggle", data={"csrf_token": csrf(emergency)})
    assert login(app.test_client(), "admin", "RestoredAdmin!2026").location == "/admin"
    assert emergency.post("/admin", data={
        "csrf_token": csrf(emergency), "username": "new_admin", "name": "Nouvel admin",
        "role": "admin", "password": "NewAdminPass!2026",
    }).status_code == 302
    assert store.login_user("new_admin")["role"] == "admin"
    assert (store.root / "analyses.json").read_bytes() == saved_analyses
    assert {p.relative_to(store.assets): p.read_bytes() for p in store.assets.rglob("*.png")} == saved_images
    assert any(e["recovery"] and e["action"] == "Modification utilisateur" for e in store.events())


def test_rotate_and_disable_revoke_sessions_immediately_and_survive_restart(app):
    recovery = provision(app)
    emergency = app.test_client()
    login(emergency, "secours", PASSWORD)
    store = app.extensions["store"]
    assert store.manage_recovery("rotate", NEW_PASSWORD)["id"] == recovery["id"]
    assert emergency.get("/admin").location.startswith("/login")
    assert "incorrect" in login(emergency, "secours", PASSWORD).get_data(as_text=True)
    assert login(emergency, "secours", NEW_PASSWORD).location == "/admin"
    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"]}, predictor=FakePredictor())
    assert login(restarted.test_client(), "secours", NEW_PASSWORD).location == "/admin"
    store.manage_recovery("disable")
    assert emergency.get("/admin").location.startswith("/login")
    assert "incorrect" in login(restarted.test_client(), "secours", NEW_PASSWORD).get_data(as_text=True)
    store.manage_recovery("rotate", PASSWORD)
    assert login(emergency, "secours", PASSWORD).location == "/admin"


@pytest.mark.parametrize("operation,password", [("create", "short"), ("rotate", "short"), ("unknown", PASSWORD), ("disable", None), ("rotate", PASSWORD)])
def test_invalid_maintenance_operations_do_not_create_an_account(app, operation, password):
    store = app.extensions["store"]
    with pytest.raises(ValueError):
        store.manage_recovery(operation, password)
    assert not (store.root / "recovery.json").exists()


def test_create_does_not_silently_reset_existing_recovery(app):
    provision(app)
    store = app.extensions["store"]
    with pytest.raises(ValueError, match="existe déjà"):
        store.manage_recovery("create", NEW_PASSWORD)
    assert check_password_hash(store.read("recovery")["password_hash"], PASSWORD)


def test_recovery_uses_csrf_and_login_rate_limit(app):
    provision(app)
    guest = app.test_client()
    guest.get("/login")
    assert guest.post("/login", data={"username": "secours", "password": PASSWORD}).status_code == 400
    for _ in range(10):
        assert login(guest, "secours", "wrong").status_code == 200
    assert login(guest, "secours", PASSWORD).status_code == 429
    assert any(e["action"] == "Connexion de secours refusée" for e in app.extensions["store"].events())


def test_local_command_generates_unique_password_and_never_stores_it_plaintext(app, capsys):
    store = app.extensions["store"]
    arguments = ["--data-dir", str(store.root)]
    capsys.readouterr()
    assert main(["create", *arguments]) == 0
    output = capsys.readouterr().out
    password = output.split("Mot de passe (affiché une seule fois) : ")[1].splitlines()[0]
    assert len(password) == 32
    assert check_password_hash(store.read("recovery")["password_hash"], password)
    for p in store.root.glob("*.json"):
        assert password not in p.read_text(encoding="utf-8")
    assert main(["rotate", *arguments]) == 0
    rotated = capsys.readouterr().out.split("Mot de passe (affiché une seule fois) : ")[1].splitlines()[0]
    assert rotated != password
    assert main(["disable", *arguments]) == 0
    assert store.read("recovery")["active"] is False


def test_private_environment_bootstraps_recovery_without_changing_users(app, monkeypatch, capsys):
    store = app.extensions["store"]
    users_before = (store.root / "users.json").read_bytes()
    analyses_before = (store.root / "analyses.json").read_bytes()
    hashed = generate_password_hash(PASSWORD, method="scrypt:32768:8:1")
    monkeypatch.setenv("BUSBRA_RECOVERY_PASSWORD_HASH", hashed)
    configured = create_app({"TESTING": True, "DATA_DIR": str(store.root)}, predictor=FakePredictor())
    browser = configured.test_client()
    assert login(browser, "secours", PASSWORD).location == "/admin"
    page = browser.get("/admin").get_data(as_text=True)
    assert "Mode secours" in page and "1 compte(s)" in page
    assert (store.root / "users.json").read_bytes() == users_before
    assert (store.root / "analyses.json").read_bytes() == analyses_before
    assert store.read("recovery")["password_hash"] == hashed
    assert any(e["action"] == "Initialisation du compte de secours" for e in store.events())
    logs = capsys.readouterr().out
    assert PASSWORD not in logs and hashed not in logs
    assert PASSWORD not in (store.root / "recovery.json").read_text(encoding="utf-8")


def test_bootstrap_never_overwrites_rotated_or_disabled_account(app):
    store = app.extensions["store"]
    hashed = generate_password_hash(PASSWORD, method="scrypt:32768:8:1")
    assert store.bootstrap_recovery(hashed) is True
    store.manage_recovery("rotate", NEW_PASSWORD)
    store.manage_recovery("disable")
    before = (store.root / "recovery.json").read_bytes()
    assert store.bootstrap_recovery(hashed) is False
    assert (store.root / "recovery.json").read_bytes() == before
    assert store.read("recovery")["active"] is False


def test_recovery_survives_removal_of_bootstrap_environment(app, monkeypatch):
    store = app.extensions["store"]
    hashed = generate_password_hash(PASSWORD, method="scrypt:32768:8:1")
    store.bootstrap_recovery(hashed)
    recovery_before = store.read("recovery")
    monkeypatch.delenv("BUSBRA_RECOVERY_PASSWORD_HASH", raising=False)
    restarted = create_app({"TESTING": True, "DATA_DIR": str(store.root)}, predictor=FakePredictor())
    assert login(restarted.test_client(), "secours", PASSWORD).location == "/admin"
    assert restarted.extensions["store"].read("recovery") == recovery_before


@pytest.mark.parametrize("value", [PASSWORD, "pbkdf2:sha256:1$salt$untrusted", "scrypt:32768:8:1$short$invalid"])
def test_bootstrap_refuses_plaintext_or_invalid_hashes(app, value):
    store = app.extensions["store"]
    with pytest.raises(ValueError, match="empreinte scrypt valide"):
        store.bootstrap_recovery(value)
    assert not (store.root / "recovery.json").exists()


def test_bootstrap_cannot_shadow_a_legacy_user_named_secours(app):
    store = app.extensions["store"]
    with store.lock():
        users = store.read("users")
        users[0]["username"] = "secours"
        store.write("users", users)
    hashed = generate_password_hash(PASSWORD, method="scrypt:32768:8:1")
    with pytest.raises(ValueError, match="déjà utilisé"):
        store.bootstrap_recovery(hashed)
    assert not (store.root / "recovery.json").exists()
