"""Conservative B-mode input checks, NOT a clinically validated OOD detector.

Keep the pixel rules identical to OfflinePreprocess.validateUltrasound.
The three-class disease model cannot determine whether an input is ultrasound.
"""
from __future__ import annotations

import numpy as np
from PIL import Image


REJECTION = (
    "Image refusée : contenu incompatible avec une échographie mammaire "
    "en niveaux de gris (mode B), ou qualité insuffisante. "
    "Importez l'image échographique originale, sans graphique ni capture d'écran."
)


def ultrasound_features(image: Image.Image) -> dict:
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if min(width, height) < 32 or not 0.4 <= width / height <= 2.5:
        raise ValueError(REJECTION)
    # Ignore scanner annotations at the margins; sample at most ~512x512 pixels.
    step = max(1, (max(width, height) + 511) // 512)
    crop = rgb[height // 8:height - height // 8:step,
               width // 8:width - width // 8:step].astype(np.int32)
    gray = crop.sum(axis=2) // 3
    hist = np.bincount(gray.ravel(), minlength=256)
    probabilities = hist[hist > 0] / gray.size
    center = gray[1:-1, 1:-1]
    residual = np.abs(4 * center - gray[:-2, 1:-1] - gray[2:, 1:-1]
                      - gray[1:-1, :-2] - gray[1:-1, 2:])
    edge_count = np.count_nonzero(np.abs(np.diff(gray, axis=0)) > 60)
    edge_count += np.count_nonzero(np.abs(np.diff(gray, axis=1)) > 60)
    edge_total = (gray.shape[0] - 1) * gray.shape[1] + gray.shape[0] * (gray.shape[1] - 1)
    return {
        "color_fraction": float(np.mean(crop.max(axis=2) - crop.min(axis=2) > 18)),
        "std": float(gray.std()),
        "entropy": float(-np.sum(probabilities * np.log2(probabilities))),
        "midtone_fraction": float(np.mean((gray > 20) & (gray < 235))),
        "texture_fraction": float(np.mean(residual > 12)),
        "edge_fraction": float(edge_count / edge_total),
    }


def validate_ultrasound_image(image: Image.Image) -> dict:
    features = ultrasound_features(image)
    if (features["color_fraction"] > 0.08 or features["std"] < 12
            or features["entropy"] < 4.5 or features["midtone_fraction"] < 0.25
            or features["texture_fraction"] < 0.08 or features["edge_fraction"] > 0.18):
        raise ValueError(REJECTION)
    return features
