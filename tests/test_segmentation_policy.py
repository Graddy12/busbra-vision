import copy
import io
import threading
from unittest.mock import Mock

import pytest
from PIL import Image

from cancer_app.inference import CombinedPredictor
from cancer_app.segmentation_policy import enforce_segmentation_policy
from test_app import FakePredictor, app, client, csrf, png_bytes


@pytest.mark.parametrize("code", ["normal", "benign", "malignant"])
def test_segmenter_called_only_for_predicted_lesion_classes(code):
    predictor = CombinedPredictor.__new__(CombinedPredictor)
    predictor._loaded = True
    predictor._inference_lock = threading.Lock()
    predictor.device = "cpu"
    image = Image.new("RGB", (64, 80))
    predictor._classify = Mock(return_value={"code": code})
    predictor._gradcam = Mock(return_value={"available": True})
    predictor._segment = Mock(return_value={"lesion_area_percent": 12.5, "mask_data_url": "mask", "overlay_data_url": "overlay"})
    result = predictor.analyze(image)
    if code == "normal":
        predictor._segment.assert_not_called()
        assert result["segmentation"]["performed"] is False
        assert result["segmentation"]["mask_data_url"] is None
        assert result["segmentation"]["overlay_data_url"] is None
        assert result["segmentation"]["lesion_area_percent"] is None
        assert result["segmentation"]["lesion_detected"] is None
    else:
        predictor._segment.assert_called_once_with(image)
        assert result["segmentation"]["performed"] is True
        assert result["segmentation"]["mask_data_url"] == "mask"


def normal_result(image):
    result = FakePredictor().analyze(image)
    result["classification"].update(code="normal", label="Normale")
    return result


def test_api_and_new_storage_do_not_keep_masks_for_normal_predictions(app, client):
    # Deliberately simulate a future/old predictor that violates the rule.
    app.extensions["combined_predictor"].analyze = normal_result
    response = client.post("/api/analyze", headers={"X-CSRF-Token": csrf(client)}, data={"image": (io.BytesIO(png_bytes()), "normal.png")})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["segmentation"]["performed"] is False
    assert payload["segmentation"]["mask_data_url"] is None
    assert payload["segmentation"]["overlay_data_url"] is None
    row = app.extensions["store"].read("analyses")[0]
    assert not any(key.startswith("segmentation_") for key in row["images"])
    image_folder = app.extensions["store"].assets / row["id"]
    assert not (image_folder / "segmentation_mask.png").exists()
    assert client.get(payload["analysis"]["original_url"]).status_code == 200
    for kind in ("segmentation_mask", "segmentation_overlay"):
        assert client.get(f"/history/{row['id']}/assets/{kind}").status_code == 404
    report = client.get(payload["analysis"]["report_url"]).get_data(as_text=True)
    assert "Segmentation non appliquée" in report
    assert "Surface segmentée :" not in report
    assert 'kind=segmentation_mask' not in report
    detail = client.get(payload["analysis"]["history_url"]).get_data(as_text=True)
    assert "Non appliquée" in detail
    assert "/assets/segmentation_mask" not in detail


def test_legacy_normal_masks_are_hidden_and_denied_without_rewriting_history(app, client):
    store = app.extensions["store"]
    image = Image.new("RGB", (64, 64))
    row = store.save_analysis(store.users()[0], image, FakePredictor().analyze(image), "OLD-NORMAL")
    # Reconstruct an old normal record that really contained a segmentation.
    with store.lock():
        rows = store.read("analyses")
        rows[0]["result"]["classification"].update(code="normal", label="Normale")
        rows[0]["note"] = "Observation historique à conserver."
        store.write("analyses", rows)
        original_record = copy.deepcopy(rows[0])
    for suffix in ("", "/report"):
        response = client.get(f"/history/{row['id']}{suffix}")
        assert response.status_code == 200
        text = response.get_data(as_text=True)
        assert "ancien masque" in text
        assert "/assets/segmentation_mask" not in text
        assert "/assets/segmentation_overlay" not in text
    assert client.get(f"/history/{row['id']}/assets/segmentation_mask").status_code == 404
    assert client.get(f"/history/{row['id']}/assets/segmentation_overlay").status_code == 404
    assert store.read("analyses")[0] == original_record
    listed = store.list_analyses(store.users()[0])[0]
    assert listed["result"]["segmentation"]["performed"] is False
    assert "segmentation_mask" not in listed["images"]


def test_unknown_class_cannot_enable_segmentation():
    with pytest.raises(ValueError):
        enforce_segmentation_policy({"classification": {"code": "unknown"}})
