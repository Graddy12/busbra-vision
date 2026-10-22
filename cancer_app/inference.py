from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .image_validation import validate_ultrasound_image
from .segmentation_policy import enforce_segmentation_policy, segmentation_applicable, skipped_segmentation


IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class ModelLoadError(RuntimeError):
    """Raised when one of the two inference models cannot be initialized."""


class CombinedPredictor:
    """Loads the Keras classifier and PyTorch segmenter once, on first use."""

    def __init__(
        self,
        classification_model_path: str,
        classification_config_path: str,
        segmentation_model_path: str,
        segmentation_selection_path: str,
        device: str = "cpu",
    ) -> None:
        self.classification_model_path = Path(classification_model_path)
        self.classification_config_path = Path(classification_config_path)
        self.segmentation_model_path = Path(segmentation_model_path)
        self.segmentation_selection_path = Path(segmentation_selection_path)
        self.requested_device = device.lower().strip()

        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._loaded = False
        self._load_error: str | None = None

        self.classification_model = None
        self.classification_cam_model = None
        self.classification_config: dict = {}
        self.segmentation_model = None
        self.segmentation_config: dict = {}
        self.torch = None
        self.device = "cpu"

    def status(self) -> dict:
        return {
            "loaded": self._loaded,
            "load_error": self._load_error,
            "device": self.device if self._loaded else self.requested_device,
            "classification_model": self.classification_model_path.name,
            "segmentation_model": self.segmentation_model_path.name,
        }

    def load(self) -> None:
        """Initialize both models before accepting requests from the browser."""
        self._ensure_loaded()

    def analyze(self, image: Image.Image) -> dict:
        self._ensure_loaded()
        started = time.perf_counter()

        # Serializing inference avoids memory spikes when Flask receives concurrent
        # requests. A production deployment can replace this with a job queue.
        with self._inference_lock:
            classification = self._classify(image)
            gradcam = self._gradcam(image, classification["code"])
            segmentation = (
                self._segment(image)
                if segmentation_applicable(classification["code"])
                else skipped_segmentation(image)
            )

        return enforce_segmentation_policy({
            "classification": classification,
            "segmentation": segmentation,
            "gradcam": gradcam,
            "meta": {
                "device": self.device,
                "inference_ms": round((time.perf_counter() - started) * 1000, 1),
                "disclaimer": (
                    "Prototype académique : ces résultats ne constituent pas "
                    "un diagnostic médical."
                ),
            },
        }, image)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return

        with self._load_lock:
            if self._loaded:
                return
            try:
                self._validate_paths()
                self._load_classification_model()
                self._load_segmentation_model()
                self._loaded = True
                self._load_error = None
            except Exception as exc:
                self._load_error = f"{type(exc).__name__}: {exc}"
                raise ModelLoadError(self._load_error) from exc

    def _validate_paths(self) -> None:
        for path in (
            self.classification_model_path,
            self.classification_config_path,
            self.segmentation_model_path,
            self.segmentation_selection_path,
        ):
            if not path.is_file():
                raise FileNotFoundError(f"Fichier modèle introuvable : {path}")

    def _load_classification_model(self) -> None:
        # Keras can run this portable .keras file on top of PyTorch. Sharing one
        # backend with the segmenter avoids loading TensorFlow and PyTorch into the
        # same Flask process, which substantially lowers memory pressure.
        os.environ["KERAS_BACKEND"] = "torch"
        import keras

        if int(keras.__version__.split(".")[0]) < 3:
            raise RuntimeError(
                "Keras 3 est requis. Lancez l'application avec "
                r".\.venv\Scripts\python.exe app.py"
            )
        if keras.backend.backend() != "torch":
            raise RuntimeError(
                "Keras doit utiliser le backend torch. Fermez le processus Python "
                "en cours et relancez app.py depuis l'environnement .venv."
            )

        self.classification_config = json.loads(
            self.classification_config_path.read_text(encoding="utf-8")
        )
        dense_class = keras.layers.Dense
        original_from_config = dense_class.from_config
        had_local_from_config = "from_config" in dense_class.__dict__
        original_local_from_config = dense_class.__dict__.get("from_config")

        # The supplied model was saved with Keras 3.13.2. Keras 3.12 does not yet
        # recognize this optional, null-only field, so ignore it while loading.
        dense_class.from_config = classmethod(
            lambda _class, config: original_from_config(
                {
                    key: value
                    for key, value in config.items()
                    if key != "quantization_config"
                }
            )
        )
        try:
            self.classification_model = keras.models.load_model(
                self.classification_model_path, compile=False
            )
        finally:
            if had_local_from_config:
                dense_class.from_config = original_local_from_config
            else:
                delattr(dense_class, "from_config")

        self.classification_cam_model = keras.Model(
            inputs=self.classification_model.inputs,
            outputs=[
                self.classification_model.get_layer("gradcam_features").output,
                self.classification_model.get_layer("dropout_2").output,
            ],
        )

    def _load_segmentation_model(self) -> None:
        try:
            import segmentation_models_pytorch as smp
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "La dépendance segmentation-models-pytorch est absente. "
                "Installez les paquets avec `pip install -r requirements.txt`."
            ) from exc

        if self.requested_device in {"auto", "cuda"} and torch.cuda.is_available():
            self.device = "cuda"
        elif self.requested_device == "cuda":
            raise RuntimeError("CUDA demandé, mais aucun GPU CUDA n'est disponible.")
        else:
            self.device = "cpu"

        # This checkpoint is a local project artifact and contains NumPy RNG state,
        # which requires the non-weights-only loader in recent PyTorch versions.
        checkpoint = torch.load(
            self.segmentation_model_path,
            map_location=self.device,
            weights_only=False,
        )
        spec = checkpoint["spec"]
        architectures = {
            "Unet": smp.Unet,
            "UnetPlusPlus": smp.UnetPlusPlus,
        }
        if spec["architecture"] not in architectures:
            raise ValueError(
                f"Architecture de segmentation inconnue : {spec['architecture']}"
            )

        self.segmentation_model = architectures[spec["architecture"]](
            encoder_name=spec["encoder"],
            encoder_weights=None,
            in_channels=3,
            classes=1,
            activation=None,
        )
        self.segmentation_model.load_state_dict(checkpoint["model_state"])
        self.segmentation_model.to(self.device)
        self.segmentation_model.eval()
        self.segmentation_config = json.loads(
            self.segmentation_selection_path.read_text(encoding="utf-8")
        )
        self.segmentation_config["image_size"] = int(
            checkpoint["run_config"]["image_size"]
        )
        self.torch = torch

    def _classify(self, image: Image.Image) -> dict:
        size = int(self.classification_config["taille_image"])
        padded = ImageOps.pad(
            image,
            (size, size),
            method=Image.Resampling.BILINEAR,
            color=(0, 0, 0),
        )
        batch = np.asarray(padded, dtype=np.float32)[None, ...]
        scores = np.asarray(
            self.classification_model.predict(batch, verbose=0)[0],
            dtype=np.float64,
        )

        # The supplied model ends in softmax. The fallback keeps the API sound if
        # a future checkpoint exports logits instead.
        if np.any(scores < 0) or not np.isclose(scores.sum(), 1.0, atol=1e-3):
            scores = np.exp(scores - np.max(scores))
            scores = scores / scores.sum()

        classes = self.classification_config["classes"]
        translations = self.classification_config["fr"]
        winner = int(np.argmax(scores))
        probabilities = [
            {
                "code": class_name,
                "label": translations[class_name],
                "probability": round(float(scores[index]), 6),
                "percent": round(float(scores[index]) * 100, 2),
            }
            for index, class_name in enumerate(classes)
        ]
        probabilities.sort(key=lambda item: item["probability"], reverse=True)

        return {
            "code": classes[winner],
            "label": translations[classes[winner]],
            "confidence": round(float(scores[winner]), 6),
            "confidence_percent": round(float(scores[winner]) * 100, 2),
            "probabilities": probabilities,
        }

    def _segment(self, image: Image.Image) -> dict:
        original = np.asarray(image, dtype=np.uint8)
        size = int(self.segmentation_config["image_size"])
        padded, geometry = _letterbox(original, size)
        normalized = padded.astype(np.float32) / 255.0
        normalized = (normalized - IMAGENET_MEAN) / IMAGENET_STD
        tensor = self.torch.from_numpy(
            np.ascontiguousarray(normalized.transpose(2, 0, 1))
        )[None].to(self.device)

        with self.torch.inference_mode():
            probability = self.torch.sigmoid(self.segmentation_model(tensor)).float()
            if bool(self.segmentation_config.get("tta", True)):
                mirrored = self.torch.sigmoid(
                    self.segmentation_model(self.torch.flip(tensor, dims=[3]))
                ).float()
                probability = (
                    probability + self.torch.flip(mirrored, dims=[3])
                ) / 2

        probability_map = _restore_probability(
            probability[0, 0].cpu().numpy(), geometry
        )
        threshold = float(self.segmentation_config.get("threshold", 0.5))
        mask = probability_map >= threshold
        if bool(self.segmentation_config.get("largest", True)):
            mask = _keep_largest_component(mask)

        overlay = _create_overlay(original, mask)
        mask_image = (mask.astype(np.uint8) * 255)
        lesion_pixels = int(mask.sum())
        total_pixels = int(mask.size)

        return {
            "applicable": True,
            "performed": True,
            "threshold": threshold,
            "lesion_detected": lesion_pixels > 0,
            "lesion_area_percent": round(100 * lesion_pixels / total_pixels, 2),
            "image_width": int(original.shape[1]),
            "image_height": int(original.shape[0]),
            "mask_data_url": _png_data_url(mask_image),
            "overlay_data_url": _png_data_url(overlay),
        }

    def _gradcam(self, image: Image.Image, class_code: str) -> dict:
        """Class-specific Grad-CAM using real gradients of the pre-softmax logit."""
        size = int(self.classification_config["taille_image"])
        padded = ImageOps.pad(image, (size, size), method=Image.Resampling.BILINEAR, color=(0, 0, 0))
        tensor = self.torch.from_numpy(np.asarray(padded, dtype=np.float32).copy()[None]).requires_grad_(True)
        target = self.classification_config["classes"].index(class_code)
        with self.torch.enable_grad():
            features, head = self.classification_cam_model(tensor, training=False)
            final_layer = self.classification_model.get_layer("classes")
            logits = head @ final_layer.kernel.value + final_layer.bias.value
            gradients = self.torch.autograd.grad(logits[0, target], features)[0]
            weights = gradients.mean(dim=(1, 2), keepdim=True)
            activation = self.torch.relu((weights * features).sum(dim=-1))[0]
            peak = float(activation.max().detach().cpu())
            normalized = activation / max(peak, 1e-12)
            heat = normalized.detach().cpu().numpy()

        # Match PIL's exact pad geometry, rather than stretching the padded CAM.
        contained = ImageOps.contain(image, (size, size), method=Image.Resampling.BILINEAR)
        geometry = {
            "height": image.height, "width": image.width,
            "top": round((size - contained.height) / 2),
            "left": round((size - contained.width) / 2),
            "new_height": contained.height, "new_width": contained.width,
        }
        heat = cv2.resize(heat, (size, size), interpolation=cv2.INTER_LINEAR)
        heat = _restore_probability(heat, geometry)
        colored = cv2.cvtColor(cv2.applyColorMap(np.round(heat * 255).astype(np.uint8), cv2.COLORMAP_TURBO), cv2.COLOR_BGR2RGB)
        alpha = heat[..., None] * 0.55
        original = np.asarray(image, dtype=np.float32)
        overlay = np.clip(original * (1 - alpha) + colored.astype(np.float32) * alpha, 0, 255).astype(np.uint8)
        return {
            "class_code": class_code, "layer": "gradcam_features", "method": "Grad-CAM sur logit",
            "available": peak > 1e-12,
            "overlay_data_url": _png_data_url(overlay), "heatmap_data_url": _png_data_url(colored),
        }


def normalize_uploaded_image(payload: bytes) -> Image.Image:
    """Validate and normalize an uploaded image without keeping it on disk."""
    if not payload:
        raise ValueError("Le fichier envoyé est vide.")

    Image.MAX_IMAGE_PIXELS = 30_000_000
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            if opened.format not in {"JPEG", "PNG", "BMP", "TIFF", "WEBP"}:
                raise ValueError("Format d'image non pris en charge.")
            opened.verify()
        with Image.open(io.BytesIO(payload)) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.load()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Le fichier n'est pas une image valide.") from exc

    if image.width < 32 or image.height < 32:
        raise ValueError("L'image doit mesurer au moins 32 × 32 pixels.")
    validate_ultrasound_image(image)
    return image


def _letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, dict]:
    height, width = image.shape[:2]
    scale = min(size / height, size / width)
    new_height = max(1, min(size, round(height * scale)))
    new_width = max(1, min(size, round(width * scale)))
    top = (size - new_height) // 2
    left = (size - new_width) // 2
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    canvas[top : top + new_height, left : left + new_width] = cv2.resize(
        image, (new_width, new_height), interpolation=cv2.INTER_LINEAR
    )
    return canvas, {
        "height": height,
        "width": width,
        "top": top,
        "left": left,
        "new_height": new_height,
        "new_width": new_width,
    }


def _restore_probability(probability: np.ndarray, geometry: dict) -> np.ndarray:
    top = geometry["top"]
    left = geometry["left"]
    new_height = geometry["new_height"]
    new_width = geometry["new_width"]
    unpadded = probability[
        top : top + new_height,
        left : left + new_width,
    ]
    restored = cv2.resize(
        unpadded,
        (geometry["width"], geometry["height"]),
        interpolation=cv2.INTER_LINEAR,
    )
    return np.clip(restored, 0, 1)


def _keep_largest_component(mask: np.ndarray) -> np.ndarray:
    component_count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    if component_count <= 1:
        return mask.astype(bool)
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return labels == largest


def _create_overlay(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    overlay = image.copy()
    tint = np.array([255, 96, 72], dtype=np.float32)
    overlay[mask] = np.clip(
        overlay[mask].astype(np.float32) * 0.48 + tint * 0.52,
        0,
        255,
    ).astype(np.uint8)

    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    cv2.drawContours(overlay, contours, -1, (255, 235, 110), 2)
    return overlay


def _png_data_url(array: np.ndarray) -> str:
    image = Image.fromarray(array)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"
