def interpret(row):
    result = row["result"]
    classification = result["classification"]
    segmentation = result["segmentation"]
    paragraphs = [
        f"Le classificateur privilégie la catégorie « {classification['label']} » "
        f"avec un score de {classification['confidence_percent']:.1f} %. "
        "Ce score décrit la sortie du modèle et ne constitue pas une probabilité clinique calibrée.",
    ]
    if classification["code"] == "normal":
        paragraphs.append("Segmentation non appliquée : la règle de l'application réserve le masque aux classes prédites bénigne et maligne. Aucune surface n'est mesurée. L'absence de masque résulte de cette règle et ne constitue pas une vérification indépendante de l'absence de lésion.")
    else:
        paragraphs.append(f"La région segmentée représente {segmentation['lesion_area_percent']:.2f} % de la surface de l'image. Il s'agit d'une proportion de pixels, et non d'une taille physique ni d'une mesure de gravité.")
    paragraphs.append("Le Grad-CAM met en évidence les zones qui contribuent au score de la classe prédite. Il ne représente pas le contour d'une lésion et ne valide pas à lui seul la prédiction.")
    if classification["code"] != "normal" and not segmentation["lesion_detected"]:
        paragraphs.append("La classification privilégie une catégorie lésionnelle, mais aucun masque n'est retenu au seuil utilisé. Les deux sorties sont discordantes et doivent être revues.")
    paragraphs.append("Ces résultats sont destinés à une démonstration académique. L'observation et la validation du médecin figurent séparément ; cette interprétation automatique n'établit pas de diagnostic.")
    return paragraphs
