"""The application segments predicted lesions only, never the normal class."""

POLICY = "lesion-classes-only-v1"
LESION_CLASSES = frozenset({"benign", "malignant"})


def segmentation_applicable(class_code):
    return class_code in LESION_CLASSES


def skipped_segmentation(image=None, previous=None):
    previous = previous or {}
    return {
        "applicable": False,
        "performed": False,
        "reason": "normal_classification",
        "message": "Segmentation non appliquée : la classe prédite est normale.",
        "threshold": None,
        "lesion_detected": None,
        "lesion_area_percent": None,
        "image_width": image.width if image is not None else previous.get("image_width"),
        "image_height": image.height if image is not None else previous.get("image_height"),
        "mask_data_url": None,
        "overlay_data_url": None,
    }


def enforce_segmentation_policy(result, image=None):
    """Apply the same rule to inference, API responses and persisted assets."""
    code = result["classification"]["code"]
    if code == "normal":
        result["segmentation"] = skipped_segmentation(image, result.get("segmentation"))
    elif segmentation_applicable(code):
        result["segmentation"].setdefault("applicable", True)
        result["segmentation"].setdefault("performed", True)
    else:
        raise ValueError(f"Classe de classification inconnue : {code}")
    result.setdefault("meta", {})["segmentation_policy"] = POLICY
    return result


def present_analysis(row):
    """Hide obsolete normal-class masks without rewriting historical evidence."""
    if row["result"]["classification"]["code"] == "normal":
        obsolete = any(key.startswith("segmentation_") for key in row["images"])
        row["images"] = {key: value for key, value in row["images"].items() if not key.startswith("segmentation_")}
        if obsolete:
            row["segmentation_notice"] = (
                "Un ancien masque est masqué par la règle actuelle : aucune segmentation pour la "
                "classe prédite normale. La classification et l'observation d'origine sont conservées."
            )
    enforce_segmentation_policy(row["result"])
    return row
