"""Fetch the pinned model release; never load a checkpoint before SHA-256 validation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def matches(path, spec):
    if not path.is_file() or path.stat().st_size != spec["size"]:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == spec["sha256"]


def fetch_model(root, spec, verify_only=False):
    root = Path(root).resolve()
    target = (root / spec["path"]).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError("Chemin de modèle hors du projet.")
    if matches(target, spec):
        print(f"Modèle vérifié : {spec['path']}", flush=True)
        return target
    if verify_only:
        raise ValueError(f"Modèle manquant ou empreinte incorrecte : {spec['path']}")
    if not spec["url"].startswith("https://"):
        raise ValueError("Le téléchargement des modèles exige HTTPS.")
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        descriptor, temporary_name = tempfile.mkstemp(prefix=".model-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            request = urllib.request.Request(spec["url"], headers={"User-Agent": "BUSBRA-Vision-model-installer"})
            with os.fdopen(descriptor, "wb") as output:
                with urllib.request.urlopen(request, timeout=120) as response:
                    downloaded = 0
                    while chunk := response.read(1024 * 1024):
                        downloaded += len(chunk)
                        if downloaded > spec["size"]:
                            raise ValueError("Le fichier reçu dépasse la taille attendue.")
                        output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if not matches(temporary, spec):
                raise ValueError(f"Échec de la vérification SHA-256 : {spec['path']}")
            os.replace(temporary, target)
            print(f"Modèle téléchargé et vérifié : {spec['path']}", flush=True)
            return target
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Installer les deux modèles BUSBRA depuis la release épinglée")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    manifest = json.loads((PROJECT_ROOT / "models" / "manifest.json").read_text(encoding="utf-8"))
    for spec in manifest["models"]:
        fetch_model(PROJECT_ROOT, spec, args.verify_only)


if __name__ == "__main__":
    main()
