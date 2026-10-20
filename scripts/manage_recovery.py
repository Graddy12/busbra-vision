"""Provision or revoke the emergency administrator from the local terminal only."""
from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path

from cancer_app.storage import JsonStore, RECOVERY_USERNAME


def main(argv=None):
    parser = argparse.ArgumentParser(description="Gestion locale du compte administrateur de secours BUSBRA")
    parser.add_argument("operation", choices=("create", "rotate", "disable"))
    parser.add_argument("--data-dir", default=os.getenv("BUSBRA_DATA_DIR", str(Path(__file__).resolve().parent.parent / "instance")))
    args = parser.parse_args(argv)
    # No password in command-line arguments, environment, source code or plaintext file.
    password = secrets.token_urlsafe(24) if args.operation != "disable" else None
    store = JsonStore(args.data_dir)
    try:
        store.manage_recovery(args.operation, password)
    except ValueError as exc:
        parser.exit(1, f"Erreur : {exc}\n")
    print("Compte de secours mis à jour. Les anciennes sessions de secours sont révoquées.")
    if password:
        print(f"Identifiant : {RECOVERY_USERNAME}")
        print(f"Mot de passe (affiché une seule fois) : {password}")
        print("Conservez-le dans un gestionnaire de mots de passe privé. Il n'est pas récupérable depuis les JSON.")
    else:
        print("Accès de secours désactivé. Utilisez rotate pour le réactiver avec un nouveau mot de passe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
