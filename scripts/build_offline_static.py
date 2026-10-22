"""Build only the public PWA assets, without installing Flask/ML dependencies.

The release archive is pinned by size and SHA-256. Model checksums are verified
again against the source manifest. No arbitrary ZIP paths are extracted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ASSETS = (
    "index.html", "styles.css", "app.js", "preprocess.js", "inference-worker.js", "sw.js",
    "manifest.webmanifest", "icon.svg", "assets-manifest.json", "ONNX_RUNTIME_LICENSE.txt",
)
BINARY_ASSETS = (
    "models/classification.onnx", "models/segmentation.onnx", "vendor/ort.wasm.min.js",
    "vendor/ort-wasm-simd-threaded.mjs", "vendor/ort-wasm-simd-threaded.wasm",
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path, expected):
    if Path(path).stat().st_size != expected["bytes"] or sha256(path) != expected["sha256"]:
        raise ValueError("Empreinte SHA-256 ou taille incorrecte : " + Path(path).name)


def download_archive(destination, expected):
    if not expected["url"].startswith("https://github.com/Graddy12/busbra-vision/releases/download/"):
        raise ValueError("La distribution doit provenir de la release HTTPS du dépôt du projet.")
    request = urllib.request.Request(expected["url"], headers={"User-Agent": "BUSBRA-offline-static-build"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        count = 0
        while chunk := response.read(1024 * 1024):
            count += len(chunk)
            if count > expected["bytes"]:
                raise ValueError("L'archive dépasse la taille attendue.")
            output.write(chunk)
    verify(destination, expected)


def build(source: Path, destination: Path, archive: Path):
    distribution = json.loads((source / "distribution.json").read_text(encoding="utf-8"))
    verify(archive, distribution["archive"])
    model_manifest = json.loads((source / "assets-manifest.json").read_text(encoding="utf-8"))
    destination.mkdir(parents=True, exist_ok=True)
    existing = {p.relative_to(destination).as_posix() for p in destination.rglob("*") if p.is_file()}
    if existing - set(SOURCE_ASSETS + BINARY_ASSETS):
        raise ValueError("Le dossier de publication contient des fichiers hors de la liste autorisée.")
    for name in SOURCE_ASSETS:
        shutil.copy2(source / name, destination / name)
    with zipfile.ZipFile(archive) as zipped:
        for name in BINARY_ASSETS:
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            member = zipped.getinfo("busbra_offline_telephone/" + name)
            if member.file_size > 110 * 1024 * 1024:
                raise ValueError("Ressource trop grande dans l'archive.")
            with zipped.open(member) as incoming, target.open("wb") as output:
                shutil.copyfileobj(incoming, output, length=1024 * 1024)
    for model in model_manifest["models"].values():
        name = model["url"].removeprefix("./")
        if name not in BINARY_ASSETS:
            raise ValueError("Chemin de modèle inattendu.")
        verify(destination / name, model)
    print(f"Site statique vérifié : {len(SOURCE_ASSETS + BINARY_ASSETS)} fichiers publics dans {destination}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use the already downloaded, verified archive for local tests.")
    parser.add_argument("--output", type=Path, default=ROOT / "output" / "render_static")
    args = parser.parse_args()
    source = ROOT / "offline_poc"
    if args.archive:
        build(source, args.output, args.archive)
    else:
        spec = json.loads((source / "distribution.json").read_text(encoding="utf-8"))["archive"]
        with tempfile.TemporaryDirectory(prefix="busbra-public-assets-") as temporary:
            archive = Path(temporary) / "assets.zip"
            print("Téléchargement de la release publique épinglée…", flush=True)
            download_archive(archive, spec)
            build(source, args.output, archive)


if __name__ == "__main__":
    main()
