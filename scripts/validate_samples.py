from __future__ import annotations

import csv
import json
from pathlib import Path

from PIL import Image

from cancer_app import create_app


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "cancer_seins"
OUTPUT = ROOT / "validation_results"
VALID_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def main() -> None:
    files = sorted(
        path
        for path in SAMPLES.rglob("*")
        if path.is_file() and path.suffix.lower() in VALID_EXTENSIONS
    )
    if not files:
        raise SystemExit(f"Aucune image trouvée dans {SAMPLES}")

    predictor = create_app().extensions["combined_predictor"]
    rows = []
    for index, path in enumerate(files, start=1):
        expected = path.parent.name.lower()
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        result = predictor.analyze(image)
        classification = result["classification"]
        segmentation = result["segmentation"]
        row = {
            "image": path.relative_to(SAMPLES).as_posix(),
            "expected": expected,
            "predicted": classification["code"],
            "correct": classification["code"] == expected,
            "confidence_percent": classification["confidence_percent"],
            "segmentation_performed": segmentation["performed"],
            "lesion_detected": segmentation["lesion_detected"],
            "lesion_area_percent": segmentation["lesion_area_percent"],
            "inference_ms": result["meta"]["inference_ms"],
        }
        rows.append(row)
        print(
            f"[{index:02d}/{len(files)}] {row['image']} -> "
            f"{row['predicted']} ({row['confidence_percent']:.1f} %)"
        )

    OUTPUT.mkdir(exist_ok=True)
    with (OUTPUT / "sample_predictions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    per_class = {}
    for label in sorted({row["expected"] for row in rows}):
        selected = [row for row in rows if row["expected"] == label]
        per_class[label] = {
            "count": len(selected),
            "correct": sum(row["correct"] for row in selected),
            "accuracy": round(sum(row["correct"] for row in selected) / len(selected), 4),
            "segmented_images": sum(row["segmentation_performed"] for row in selected),
            "mean_lesion_area_percent": (
                round(sum(row["lesion_area_percent"] for row in selected if row["segmentation_performed"]) / sum(row["segmentation_performed"] for row in selected), 2)
                if any(row["segmentation_performed"] for row in selected) else None
            ),
        }

    summary = {
        "images": len(rows),
        "correct": sum(row["correct"] for row in rows),
        "classification_accuracy": round(
            sum(row["correct"] for row in rows) / len(rows), 4
        ),
        "per_class": per_class,
        "note": (
            "Aucun masque de référence n'est présent dans cancer_seins ; "
            "Dice et IoU ne peuvent donc pas être calculés sur cet échantillon."
        ),
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
