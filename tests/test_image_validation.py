"""Targeted input rejection tests; no model loading or clinical accuracy claims."""
import io
from pathlib import Path
from unittest.mock import Mock

import numpy as np
from PIL import Image
import pytest

from cancer_app.image_validation import validate_ultrasound_image
from test_app import app, client, csrf, png_bytes


def invalid_image(kind):
    y, x = np.indices((128, 128))
    if kind == "color_photo":
        rgb = np.random.default_rng(4).integers(0, 255, (128, 128, 3), dtype=np.uint8)
        return Image.fromarray(rgb)
    if kind == "gradient":
        gray = x * 2
    elif kind == "graphic":
        gray = ((x // 8 + y // 8) % 2) * 255
    elif kind == "noise":
        gray = np.random.default_rng(4).integers(0, 255, (128, 128), dtype=np.uint8)
    else:
        gray = np.full((128, 128), {"black": 0, "white": 255, "flat": 100}[kind])
    return Image.fromarray(gray.astype(np.uint8)).convert("RGB")


@pytest.mark.parametrize("kind", ["color_photo", "black", "white", "flat", "gradient", "graphic", "noise"])
def test_non_ultrasound_rejected_before_models_and_storage(app, client, kind):
    predictor = app.extensions["combined_predictor"]
    predictor.analyze = Mock(side_effect=AssertionError("Must not run inference"))
    before = app.extensions["store"].read("analyses")
    upload = io.BytesIO()
    invalid_image(kind).save(upload, "PNG")
    upload.seek(0)
    # Even a misleading filename cannot bypass content validation.
    response = client.post("/api/analyze", headers={"X-CSRF-Token": csrf(client)},
                           data={"image": (upload, "echographie.png")})
    assert response.status_code == 400
    assert "Image refusée" in response.get_json()["error"]
    assert "classification" not in response.get_json()
    predictor.analyze.assert_not_called()
    assert app.extensions["store"].read("analyses") == before


def test_textured_grayscale_fixture_passes():
    assert validate_ultrasound_image(Image.open(io.BytesIO(png_bytes())))["entropy"] > 4.5


SAMPLES = sorted((Path(__file__).resolve().parents[1] / "cancer_seins").glob("*/*.png"))


@pytest.mark.parametrize("path", SAMPLES or [None], ids=lambda path: path.name if path else "no-local-samples")
def test_existing_public_ultrasound_samples_remain_accepted(path):
    if path is None:
        pytest.skip("Public local samples not installed")
    with Image.open(path) as image:
        validate_ultrasound_image(image)
