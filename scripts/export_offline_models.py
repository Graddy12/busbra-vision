"""Export unchanged local weights for the isolated offline-browser prototype.

No training, quantization, network download or patient history is involved.
Numerical comparisons use public demo images, not a clinical validation set.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ["KERAS_BACKEND"] = "torch"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
import onnx
import onnxruntime as ort
import torch
from PIL import Image, ImageOps

from cancer_app.inference import (CombinedPredictor, IMAGENET_MEAN, IMAGENET_STD,
                                  _letterbox, _restore_probability, _keep_largest_component)


MODELS = ROOT / "offline_poc" / "models"
RESULTS = ROOT / "output" / "offline_poc"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Classifier(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.network = model

    def forward(self, image):
        return self.network(image, training=False)


class Segmenter(torch.nn.Module):
    def __init__(self, model, tta):
        super().__init__()
        self.network = model
        self.tta = tta

    def forward(self, image):
        probability = torch.sigmoid(self.network(image)).float()
        if self.tta:
            mirrored = torch.sigmoid(self.network(torch.flip(image, dims=[3]))).float()
            probability = (probability + torch.flip(mirrored, dims=[3])) / 2
        return probability


def main():
    torch.set_num_threads(4)
    MODELS.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    predictor = CombinedPredictor(
        ROOT / "classification_package" / "BUSI_EfficientNetB0_classification.keras",
        ROOT / "classification_package" / "config_modele.json",
        ROOT / "segmentation_package" / "best_model.pt",
        ROOT / "segmentation_package" / "selection.json", device="cpu")
    print("Chargement des poids originaux…", flush=True)
    predictor.load()
    classifier = Classifier(predictor.classification_model).eval()
    segmenter = Segmenter(predictor.segmentation_model, bool(predictor.segmentation_config["tta"])).eval()
    exports = [
        ("classification", classifier, torch.zeros(1, 224, 224, 3), "probabilities"),
        ("segmentation", segmenter, torch.zeros(1, 3, 384, 384), "probability_map"),
    ]
    for name, model, example, output_name in exports:
        target = MODELS / f"{name}.onnx"
        if not target.exists():
            print(f"Export ONNX FP32 : {name}…", flush=True)
            with torch.inference_mode():
                torch.onnx.export(model, (example,), str(target), input_names=["image"],
                    output_names=[output_name], opset_version=17, dynamo=False,
                    do_constant_folding=True, export_params=True)
        onnx.checker.check_model(str(target))
        print(f"  Graphe vérifié : {target.name}, {target.stat().st_size / 1e6:.1f} Mo", flush=True)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    sessions = {name: ort.InferenceSession(str(MODELS / f"{name}.onnx"), sess_options=options,
                                         providers=["CPUExecutionProvider"]) for name in ("classification", "segmentation")}
    rows = []
    public_samples = sorted((ROOT / "cancer_seins").rglob("*.png"))
    for index, path in enumerate(public_samples):
        print(f"Comparaison {index + 1}/{len(public_samples)} : {path.name}", flush=True)
        image = Image.open(path).convert("RGB")
        original = np.array(image)
        padded = np.array(ImageOps.pad(image, (224, 224), method=Image.Resampling.BILINEAR, color=(0, 0, 0)))
        batch = padded.astype(np.float32)[None]
        with torch.inference_mode():
            native = classifier(torch.from_numpy(batch)).cpu().numpy()[0]
        converted = sessions["classification"].run(None, {"image": batch})[0][0]
        winner = int(native.argmax())
        name = f"sample_{index:02d}"
        row = dict(id=name, source=path.relative_to(ROOT).as_posix(), width=image.width, height=image.height,
                   expected_folder=path.parent.name, probabilities=native.tolist(),
                   predicted=predictor.classification_config["classes"][winner],
                   conversion_class_max_abs_error=float(np.abs(native - converted).max()),
                   conversion_same_class=bool(winner == int(converted.argmax())))
        # Inputs/reference outputs stay in ignored QA output, never served or cached by the PWA.
        batch.tofile(RESULTS / f"{name}_classification_input.bin")
        original.tofile(RESULTS / f"{name}_rgb.bin")
        if row["predicted"] != "normal":
            square, geometry = _letterbox(original, 384)
            normalized = (square.astype(np.float32) / 255 - IMAGENET_MEAN) / IMAGENET_STD
            tensor = np.ascontiguousarray(normalized.transpose(2, 0, 1))[None]
            with torch.inference_mode():
                native_map = segmenter(torch.from_numpy(tensor)).cpu().numpy()[0, 0]
            converted_map = sessions["segmentation"].run(None, {"image": tensor})[0][0, 0]
            probability_map = _restore_probability(native_map, geometry)
            mask = _keep_largest_component(probability_map >= float(predictor.segmentation_config["threshold"]))
            mask.astype(np.uint8).tofile(RESULTS / f"{name}_mask.bin")
            tensor.tofile(RESULTS / f"{name}_segmentation_input.bin")
            native_map.tofile(RESULTS / f"{name}_probability_map.bin")
            row.update(segmentation_geometry=geometry, lesion_area_percent=float(100 * mask.mean()),
                       conversion_segmentation_max_abs_error=float(np.abs(native_map - converted_map).max()))
        assert row["conversion_same_class"] and row["conversion_class_max_abs_error"] < 1e-4, row
        if "conversion_segmentation_max_abs_error" in row:
            assert row["conversion_segmentation_max_abs_error"] < 1e-3, row
        rows.append(row)
    write_json(RESULTS / "python_reference.json", dict(
        samples=rows, note="Comparaison technique de conversion sur exemples BUSI publics ; pas une mesure de performance clinique.",
        versions=dict(torch=torch.__version__, onnx=onnx.__version__, onnxruntime=ort.__version__)))
    manifest = dict(format_version=1, version="offline-poc-fp32-v1", runtime_version="1.23.2",
                    classes=predictor.classification_config["classes"], labels=predictor.classification_config["fr"],
                    classifier=dict(size=224, layout="NHWC", normalization="internal", resize="Pillow bilinear"),
                    segmenter=dict(size=384, layout="NCHW", mean=IMAGENET_MEAN.tolist(), std=IMAGENET_STD.tolist(),
                                   threshold=float(predictor.segmentation_config["threshold"]),
                                   tta=bool(predictor.segmentation_config["tta"]),
                                   largest=bool(predictor.segmentation_config["largest"]),
                                   tta_in_graph=True),
                    models={name: dict(url=f"./models/{name}.onnx", sha256=sha256(MODELS / f"{name}.onnx"),
                                       bytes=(MODELS / f"{name}.onnx").stat().st_size) for name in sessions})
    write_json(ROOT / "offline_poc" / "assets-manifest.json", manifest)
    print(json.dumps(dict(samples=len(rows), same_class=sum(row["conversion_same_class"] for row in rows),
                          classification_max_error=max(row["conversion_class_max_abs_error"] for row in rows),
                          segmentation_max_error=max(row.get("conversion_segmentation_max_abs_error", 0) for row in rows),
                          manifest=str(ROOT / "offline_poc" / "assets-manifest.json")), indent=2), flush=True)


if __name__ == "__main__":
    main()
