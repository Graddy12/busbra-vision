"""Exercise the real HTTP pipeline on raw BUSI images, without changing app history."""
import argparse
import io
import json
import re
import tempfile
from pathlib import Path

from cancer_app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--per-class", type=int, default=5)
    args = parser.parse_args()
    rows = []
    with tempfile.TemporaryDirectory(prefix="busbra-gate-") as data:
        app = create_app({"TESTING": True, "DATA_DIR": data, "BOOTSTRAP_ADMIN_PASSWORD": "GateTest!2026"})
        client = app.test_client()
        client.get("/login")
        with client.session_transaction() as state:
            token = state["csrf_token"]
        client.post("/login", data={"username": "admin", "password": "GateTest!2026", "csrf_token": token})
        with client.session_transaction() as state:
            token = state["csrf_token"]
        for label in ("benign", "malignant", "normal"):
            originals = [p for p in (args.dataset / label).glob("*.png") if "_mask" not in p.stem.lower()]
            originals.sort(key=lambda p: int(re.search(r"\((\d+)\)", p.name).group(1)))
            assert originals, f"Aucune image : {args.dataset / label}"
            for path in originals[:args.per_class]:
                response = client.post("/api/analyze", headers={"X-CSRF-Token": token}, data={"image": (io.BytesIO(path.read_bytes()), path.name)})
                result = response.get_json()
                assert response.status_code == 200, result
                predicted = result["classification"]["code"]
                segmentation = result["segmentation"]
                enabled = predicted in {"benign", "malignant"}
                assert segmentation["performed"] == enabled
                assert bool(segmentation.get("mask_data_url")) == enabled
                assert bool(segmentation.get("overlay_data_url")) == enabled
                if not enabled:
                    assert segmentation["lesion_area_percent"] is None
                    mask_response = client.get(f"/history/{result['analysis']['id']}/assets/segmentation_mask")
                    assert mask_response.status_code == 404
                    report = client.get(result["analysis"]["report_url"]).get_data(as_text=True)
                    assert "Segmentation non appliquée" in report and "Surface segmentée :" not in report
                rows.append({"image": f"{label}/{path.name}", "label": label, "predicted": predicted, "confidence_percent": result["classification"]["confidence_percent"], "segmentation_performed": segmentation["performed"], "mask_emitted": bool(segmentation.get("mask_data_url"))})
                print(f"{label}/{path.name} -> {predicted}; segmentation={segmentation['performed']}", flush=True)
    summary = {
        "tested": len(rows), "normal_predictions": sum(r["predicted"] == "normal" for r in rows),
        "normal_masks_emitted": sum(r["predicted"] == "normal" and r["mask_emitted"] for r in rows),
        "gate_violations": 0,
        "classification_mismatches": [r["image"] for r in rows if r["predicted"] != r["label"]],
        "note": "La règle dépend de la classe prédite, jamais du dossier ni du nom du fichier.",
        "predictions": rows,
    }
    output = Path(__file__).resolve().parents[1] / "validation_results" / "segmentation_gate.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "predictions"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
