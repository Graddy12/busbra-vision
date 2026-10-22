# BUSBRA Vision

Application Flask combinant les deux modèles fournis :

- classification BUSI avec EfficientNetB0 (`.keras`) ;
- segmentation de la lésion avec U-Net ResNet34 (`.pt`).
- Grad-CAM réel, calculé à partir des gradients du score avant softmax.

L'application reçoit une échographie, applique à chaque modèle son prétraitement
d'origine, puis renvoie la classe, les probabilités, le masque et une superposition
du masque sur l'image source.

> Prototype académique uniquement. Les résultats ne constituent pas un diagnostic
> médical et ne doivent pas guider une décision clinique.

## Hébergement Railway

Pour la version unifiée à déployer sur VPS, voir [DEPLOYMENT_VPS.md](DEPLOYMENT_VPS.md).

Le dépôt inclut un Dockerfile CPU, Gunicorn et la configuration Railway.
Voir [DEPLOYMENT.md](DEPLOYMENT.md) pour le volume persistant, les secrets et
le démarrage. Les modèles sont distribués séparément dans la release `models-v1`
et téléchargés avec vérification SHA-256 par `python scripts/download_models.py`.
Les comptes et l'historique locaux ne sont pas transférés sur l'hébergement.

## Installation

Python 3.10 ou 3.11 est recommandé.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Le classificateur reste chargé depuis son fichier Keras, mais Keras utilise ici le
backend PyTorch. Les deux modèles partagent donc le même moteur et l'application
n'a pas à charger simultanément TensorFlow et PyTorch. Le verrou `numpy<2` conserve
également la compatibilité avec les bibliothèques utilisées pendant l'entraînement.

Les modèles sont lus par défaut depuis :

```text
classification_package/BUSI_EfficientNetB0_classification.keras
segmentation_package/best_model.pt
```

## Lancement

Dans PowerShell, activer l'environnement (la politique ne change que pour le
terminal courant) :

```powershell
cd D:\cancer
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Puis lancer le serveur avec le Python de cet environnement :

```powershell
.\.venv\Scripts\python.exe app.py --port 5001 --open-browser
```

Sous Windows, le moyen le plus simple est de double-cliquer sur `start_app.bat`.
Ce lanceur sélectionne directement le Python de `.venv` et force le backend Keras
correct ; il n'est pas nécessaire d'activer manuellement l'environnement virtuel.

Attendre les messages `Les deux modeles sont charges` et
`Running on http://127.0.0.1:5001`, puis ouvrir <http://127.0.0.1:5001>.
Un seul serveur et un seul port sont nécessaires : la PWA fait maintenant partie
de Flask, sous `/workspace/`. Après connexion, le médecin arrive sur cette interface
unifiée ; l'administrateur conserve son accueil `/admin` et peut ouvrir l'analyse.
Les anciennes options `--pwa-port` et `--no-pwa` ne sont plus nécessaires.

Dans la même page, choisir le moteur :

- **Automatique** : serveur si une session connectée est disponible, sinon local ;
- **Serveur** : image envoyée à Flask, Grad-CAM, historique et rapport du compte ;
- **Local** : calcul dans le navigateur, sans envoi d'image, historique et rapport locaux.

Cliquer sur **Préparer cet appareil** pendant que le serveur est disponible,
puis attendre **Prêt** avant de couper la connexion. Installer ensuite l'application
depuis le navigateur si souhaité. Le lien à conserver pour un démarrage hors ligne est
<http://127.0.0.1:5001/workspace/> (ou le même chemin sur le domaine HTTPS public).
L'ancien chemin `/offline/` redirige vers cet espace neutre ; le mode de calcul
est déterminé par le sélecteur, pas par le nom de l'adresse.
La page de connexion et les pages d'administration ne sont jamais mises en cache.
Laisser le terminal ouvert pour le mode serveur ; `Ctrl+C` arrête cet unique serveur.
Une réponse serveur perdue ne déclenche jamais une seconde analyse automatiquement.

Les historiques restent séparés et ne sont pas synchronisés à ce stade. Les données
locales ne sont pas protégées par la session Flask : sur un appareil partagé, elles
restent accessibles après déconnexion. Utiliser uniquement des images de démonstration
non identifiantes. Aucun jeton de session ni mot de passe n'est enregistré dans la PWA.

Les fichiers ONNX et le moteur WASM doivent être présents dans `offline_poc/models`
et `offline_poc/vendor` pour préparer le mode local. Ils sont déjà disponibles dans
cet espace de travail, mais ne sont pas suivis dans Git. Sur un autre hébergement,
installer également ces ressources publiques vérifiées (voir `offline_poc/README.md`),
ou définir `BUSBRA_OFFLINE_ASSET_DIR` vers un dossier construit par
`scripts/build_offline_static.py`. Sans ces binaires, le mode serveur reste disponible
et l'interface indique que le moteur local n'est pas installé. Cette modification
locale ne redéploie pas automatiquement les services déjà publiés.

Le mode CPU est utilisé par défaut. Pour tenter l'inférence CUDA côté PyTorch :

```powershell
$env:INFERENCE_DEVICE = "cuda"
python app.py
```

`INFERENCE_DEVICE=auto` sélectionne CUDA lorsqu'il est disponible. En production,
utiliser un seul processus de travail par copie des modèles, ou séparer les deux
modèles en services dédiés si le trafic devient important.

## API

L'API d'analyse nécessite une session connectée et le jeton CSRF de la page
(`X-CSRF-Token`). L'interface web gère ces deux éléments automatiquement.

- `GET /api/health` : état du service et chargement des modèles ;
- `GET /api/session` : contexte de connexion et jeton CSRF en mémoire, sans cache ;
- `POST /api/analyze` : analyse combinée, champ multipart `image` ;
- taille maximale : 16 Mo ; formats : PNG, JPEG, BMP, TIFF et WebP.

## Comptes et administration

Au premier lancement, le compte `admin` est créé avec un mot de passe aléatoire
affiché une seule fois dans le terminal. Aucun mot de passe en clair n'est conservé
dans les JSON. Pour choisir ce premier mot de passe, définir
`BUSBRA_ADMIN_PASSWORD` avant le premier lancement uniquement.

- `/login` : connexion ; les sessions durent huit heures et la déconnexion est un POST ;
- `/admin` : création, modification, activation/désactivation des utilisateurs ;
- deux rôles : administrateur et médecin, avec une spécialité libre ;
- connexion administrateur : arrivée directe sur `/admin`, dans une interface
  dédiée avec sa propre navigation (utilisateurs et journal) ;
- connexion médecin : arrivée sur `/`, puis redirection vers `/workspace/`, l'analyse unifiée ;
- l'administrateur peut ouvrir explicitement l'espace médical via `/workspace`,
  sans mélanger sa navigation avec l'interface de gestion ;
- un compte désactivé ou un mot de passe réinitialisé ferme les anciennes sessions ;
- le dernier administrateur actif et votre propre compte sont protégés contre une désactivation involontaire.

### Aucun compte caché ni accès de secours

L'ancien compte `secours` a été retiré. Ses identifiants et ses anciennes sessions
ne sont plus acceptés. Les mécanismes de création et de réactivation ont été supprimés.
Une ancienne variable `BUSBRA_RECOVERY_PASSWORD_HASH` est ignorée et doit être retirée
de la configuration de déploiement. Au démarrage, l'ancien `recovery.json` est supprimé
et l'opération journalisée, sans toucher aux comptes ordinaires ni aux analyses.
Un administrateur ordinaire actif doit exister avant cette migration.
Les anciennes entrées du journal sont conservées pour la traçabilité.

## Historique, revue et rapports

La segmentation est conditionnée à la **classe prédite** : `benign` et `malignant`
uniquement. Pour `normal`, le U-Net n'est pas exécuté, aucun masque n'est créé,
aucune surface n'est mesurée et l'image originale est affichée par défaut. Le
Grad-CAM reste une explication distincte du classificateur, accessible séparément.
Cette règle ne change ni les scores ni les erreurs éventuelles de classification.

Les anciens masques associés à une prédiction normale ne sont plus affichés ni
téléchargeables, même par URL directe. Les résultats historiques bruts et les
observations sont conservés pour la traçabilité ; la présentation indique la règle.

- `/history` : recherche par référence, nom de fichier ou médecin, filtres date/classe/statut ;
- `/history/<id>` : images, scores, observation et statut « À examiner / Validée » ;
- `/history/<id>/report` : rapport imprimable ; utiliser le bouton puis « Enregistrer au format PDF » ;
- `/dashboard` : volumes d'analyses et répartition des prédictions, sans les présenter comme des diagnostics ;
- suppression réversible : les analyses passent dans une corbeille et peuvent être restaurées.

Un médecin accède uniquement à ses analyses, y compris aux images et aux rapports.
L'administrateur accède à l'ensemble. Le journal d'actions est accessible dans
l'administration. L'interprétation automatique figure à la fin du rapport,
séparément de l'observation humaine.

## Données locales

Les données sont désormais conservées dans `instance/` (ou `BUSBRA_DATA_DIR`) :

```text
users.json       profils et empreintes des mots de passe
analyses.json    résultats, références, propriétaires et revues
audit.json       journal des 2 000 dernières actions
images/<id>/    original, masque, superposition et Grad-CAM
.session-key    clé persistante des sessions, à garder privée
```

Les écritures JSON utilisent un verrou de processus et un remplacement atomique.
Sauvegarder l'ensemble de ce dossier ensemble. Il est exclu de l'archive de code.
La corbeille masque les analyses et interdit leurs routes d'accès, mais conserve
les fichiers pour permettre leur restauration. Cette version est conçue pour
une démonstration locale ; le stockage n'est pas chiffré au repos.

## Tests

Les tests HTTP utilisent un prédicteur factice et ne chargent pas les poids lourds.
Ils vérifient également les sessions, les droits, le CSRF, la corbeille et les
écritures concurrentes, ainsi que le refus définitif des anciens accès de
secours. `scripts/check_browser.cjs` utilise Playwright et Edge
pour tester le parcours complet avec les vrais modèles, dans un stockage isolé.

```powershell
python -m pytest -q
```

## Validation rapide des exemples locaux

Le dossier `cancer_seins` peut contenir les sous-dossiers `normal`, `benign` et
`malignant`. Pour analyser tout le lot avec les deux vrais modèles :

```powershell
python -m scripts.validate_samples
```

Les prédictions détaillées et le résumé sont enregistrés dans
`validation_results/`.

Pour contrôler la règle avec des images brutes de BUSI (sans modifier l'historique) :

```powershell
python -m scripts.validate_segmentation_gate "C:\Users\GRADDY\Downloads\data-cancer-seins\Dataset_BUSI_originals" --per-class 5
```

Ce contrôle exclut les fichiers `_mask` et écrit `validation_results/segmentation_gate.json`.
