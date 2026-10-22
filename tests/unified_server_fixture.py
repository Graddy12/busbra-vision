"""Disposable browser-test server. Never uses real accounts or patient storage."""
import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from cancer_app import create_app
from cancer_app.inference import _png_data_url
from test_app import FakePredictor


class BrowserPredictor(FakePredictor):
    def status(self):
        return {"loaded": True, "backend": "browser-test-double"}

    def analyze(self, image):
        result = super().analyze(image)
        pixels = np.asarray(image)
        mask = np.zeros((image.height, image.width), dtype=np.uint8)
        mask[image.height // 3:2 * image.height // 3,
             image.width // 3:2 * image.width // 3] = 255
        result["classification"]["probabilities"] = [
            {"code": "normal", "label": "Normale", "probability": 0.1, "percent": 10.0},
            {"code": "benign", "label": "Bénigne", "probability": 0.8, "percent": 80.0},
            {"code": "malignant", "label": "Maligne", "probability": 0.1, "percent": 10.0},
        ]
        result["segmentation"]["mask_data_url"] = _png_data_url(mask)
        result["segmentation"]["overlay_data_url"] = _png_data_url(pixels)
        result["gradcam"]["overlay_data_url"] = _png_data_url(pixels)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="busbra-unified-browser-test-") as directory:
        app = create_app({"TESTING": True, "PRODUCTION": False, "DATA_DIR": directory,
                          "BOOTSTRAP_ADMIN_PASSWORD": "BrowserTestOnly!2026"}, predictor=BrowserPredictor())
        app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
