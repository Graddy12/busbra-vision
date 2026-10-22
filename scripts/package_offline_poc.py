"""Create an isolated, verified delivery folder and ZIP, never including app data."""
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "offline_poc"
DESTINATION = ROOT / "output" / "offline_delivery" / "busbra_offline_telephone"
INCLUDE = ["index.html", "styles.css", "app.js", "preprocess.js", "inference-worker.js", "sw.js",
           "manifest.webmanifest", "icon.svg", "assets-manifest.json", "server.py", "Dockerfile",
           "railway.toml", ".dockerignore", "README.md", "ONNX_RUNTIME_LICENSE.txt", "models/classification.onnx", "models/segmentation.onnx",
           "vendor/ort.wasm.min.js", "vendor/ort-wasm-simd-threaded.mjs", "vendor/ort-wasm-simd-threaded.wasm"]


def main():
    manifest = json.loads((SOURCE / "assets-manifest.json").read_text(encoding="utf-8"))
    for item in manifest["models"].values():
        path = SOURCE / item["url"].removeprefix("./")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], path
    DESTINATION.mkdir(parents=True, exist_ok=True)
    inventory = []
    for name in INCLUDE:
        path = SOURCE / name
        target = DESTINATION / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
        inventory.append({"file": name, "bytes": target.stat().st_size, "sha256": digest})
    (DESTINATION / "CONTENU_VERIFIE.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    archive = DESTINATION.parent / "busbra_offline_telephone.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as handle:
        for name in INCLUDE + ["CONTENU_VERIFIE.json"]:
            handle.write(DESTINATION / name, "busbra_offline_telephone/" + name)
    with zipfile.ZipFile(archive) as handle:
        assert handle.testzip() is None
        assert len(handle.namelist()) == len(INCLUDE) + 1
    print(json.dumps({"deployment_folder": str(DESTINATION), "zip": str(archive),
                      "files": len(INCLUDE) + 1, "zip_bytes": archive.stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
