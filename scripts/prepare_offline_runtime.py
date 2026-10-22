"""Download a pinned official browser runtime; do not execute package scripts."""
import hashlib
import io
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-1.23.2.tgz"
SHA256 = "0b8707d7efab9a2bea63564d66cad79e897cfd0bd627e5ae000488ffc1c45c7d"
NAMES = ["ort.wasm.min.js", "ort-wasm-simd-threaded.mjs", "ort-wasm-simd-threaded.wasm"]


def main():
    downloaded = ROOT / "tmp" / "onnxruntime-web-1.23.2.tgz"
    data = downloaded.read_bytes() if downloaded.exists() else urllib.request.urlopen(URL, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit("Archive du moteur inattendue ; aucun fichier n'a été installé.")
    destination = ROOT / "offline_poc" / "vendor"
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for name in NAMES:
            # Explicit members only: no extraction of arbitrary paths or install scripts.
            (destination / name).write_bytes(archive.extractfile("package/dist/" + name).read())
            print("Installé :", name)


if __name__ == "__main__":
    main()
