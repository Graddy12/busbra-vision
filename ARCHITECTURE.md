# Plan de conception

## Objectif

Fournir une seule application Flask qui exécute, sur la même échographie :

1. la classification Keras EfficientNetB0 (`normal`, `benign`, `malignant`) ;
2. la segmentation PyTorch U-Net ResNet34 ;
3. la classe, les scores, le masque et un Grad-CAM réel ;
4. une connexion par rôle, un historique privé, une revue humaine et un rapport imprimable.

L'application est un prototype de recherche et ne doit pas être présentée comme
un dispositif médical.

## Flux d'une requête

```text
Navigateur
   │ image multipart (16 Mo max.)
   ▼
Flask /api/analyze
   │ session + droits + CSRF + validation Pillow + conversion RGB
   ▼
CombinedPredictor (chargé une seule fois)
   ├── Keras / EfficientNetB0
   │      letterbox 224 × 224 ──> 3 probabilités + Grad-CAM sur logit
   │
   └── Si classe prédite benign ou malignant : PyTorch / U-Net ResNet34
          letterbox 384 × 384 + normalisation ImageNet
          ──> probabilité par pixel ──> seuil 0,5 + plus grande composante
          Si classe prédite normal : segmentation non appliquée, aucun masque
   │
   ▼
Réponse JSON
   ├── classe + scores
   ├── masque PNG en data URL
   └── superposition et Grad-CAM PNG en data URL
       + enregistrement dans instance/analyses.json et instance/images/<id>/
```

## Décisions techniques

- **Backend partagé** : Keras 3 exécute le `.keras` sur le backend PyTorch. Cela
  évite de garder TensorFlow et PyTorch en mémoire dans le même processus.
- **Chargement unique** : les modèles sont chargés au démarrage de app.py, avant
  l'ouverture du serveur, puis réutilisés. La fabrique de tests reste paresseuse.
- **Inférence sérialisée** : un verrou limite les pics mémoire sur le prototype.
  Une file de tâches ou deux services de modèles seront préférables à fort trafic.
- **Historique protégé** : métadonnées JSON atomiques, fichiers PNG hors du dossier
  static, vérification du propriétaire ou du rôle admin à chaque consultation.
- **Segmentation conditionnelle** : uniquement pour les classes prédites bénigne
  et maligne. Pour normale, ni exécution du U-Net, ni masque, ni surface mesurée.
  La même règle est appliquée dans l'API, le stockage et les vues historiques ;
  les anciens masques normaux restent archivés mais sont inaccessibles par HTTP.
- **Identités** : mots de passe hachés, cookies HttpOnly/SameSite, CSRF, limitation
  des tentatives de connexion et révocation des sessions via version de compte.
- **Accès de secours explicite** : compte admin optionnel dans `recovery.json`,
  non inclus dans l'annuaire `users.json`. Même authentification que les comptes
  ordinaires, mais gestion exclusivement par `scripts.manage_recovery` en local.
  Rotation et désactivation révoquent ses sessions ; les opérations de maintenance
  et toutes ses actions restent journalisées avec une identité distincte.
  Aucun mot de passe maître intégré ni route de remise à zéro destructive.
- **Espaces séparés** : l'administrateur arrive sur `/admin`, avec un gabarit et
  une navigation dédiés (`/admin/audit` pour le journal). Le médecin arrive sur
  `/`. Le passage volontaire de l'administrateur vers l'espace médical utilise
  `/workspace` et conserve les contrôles d'accès existants.
- **Suppression réversible** : marquage dans le JSON ; les images restent privées
  et ne sont plus accessibles par HTTP jusqu'à une restauration.
- **Prétraitements séparés** : chaque modèle conserve exactement la taille et la
  normalisation décrites dans son archive.
- **CPU par défaut** : comportement prévisible sur une machine sans CUDA. Le mode
  `cuda` ou `auto` est configurable avec `INFERENCE_DEVICE`.

## Structure

```text
app.py                         point d'entrée Flask
cancer_app/__init__.py         configuration et fabrique d'application
cancer_app/routes.py           interface HTTP et gestion des erreurs
cancer_app/inference.py        chargement et inférence des deux modèles
cancer_app/storage.py          JSON, verrou interprocessus et stockage des images
cancer_app/security.py         sessions, CSRF et rôles
cancer_app/interpretation.py   texte automatique déterministe pour le rapport
cancer_app/templates/          interface web
cancer_app/static/             styles et interactions navigateur
classification_package/        poids et métadonnées Keras fournis
segmentation_package/          poids et métadonnées PyTorch fournis
tests/                         tests HTTP sans chargement des gros modèles
```

## Étapes de consolidation

1. Tester avec plusieurs images BUSI brutes, et non les planches de résultats.
2. Comparer les sorties de l'application aux scripts d'origine sur un jeu témoin.
3. Définir une politique de conservation et un stockage adapté avant des données réelles.
4. Conteneuriser et fixer les ressources CPU/RAM/GPU pour le déploiement.
5. Faire valider l'interface, les seuils et les avertissements par les responsables
   du projet avant toute démonstration à des utilisateurs externes.
