# Déploiement VPS — version unifiée, sans compte de secours

Branche à utiliser : **main**. L'application conserve les modes Serveur / Local /
Automatique dans `/workspace/` et l'administration séparée dans `/admin`.

## Récupérer et construire

Sur le VPS Linux, avec Git et Docker déjà installés :

```sh
git clone --branch codex/render-offline-poc https://github.com/Graddy12/busbra-vision.git
cd busbra-vision
docker build -t busbra-vision:unified .
docker volume create busbra-data
```

La construction télécharge les poids serveur depuis `models-v1`, puis les
ressources ONNX/WASM depuis `offline-poc-v1`. Les tailles et SHA-256 sont vérifiés.
L'archive technique n'est PAS l'application à déployer : le script en extrait
uniquement les binaires autorisés et utilise les sources actuelles du dépôt.
Ne jamais copier `instance/`, des logs ou des fichiers patient depuis le PC.

## Configuration privée et démarrage

Créer sur le VPS un fichier `.env.vps` privé (permissions `600`) contenant :

```dotenv
BUSBRA_PRODUCTION=1
BUSBRA_DATA_DIR=/data
BUSBRA_ADMIN_PASSWORD=REMPLACER_PAR_UN_SECRET_UNIQUE_D_AU_MOINS_16_CARACTERES
BUSBRA_FAST_INFERENCE=1
PORT=8080
```

Remplacer impérativement le secret d'exemple. Aucun compte caché n'existe.
Ne pas définir `BUSBRA_RECOVERY_PASSWORD_HASH`. La clé de session est générée
sur le volume et reste persistante. Le mot de passe d'initialisation crée
uniquement le premier administrateur, sans réinitialiser un compte existant.

```sh
docker run -d --name busbra-vision --restart unless-stopped \
  --env-file .env.vps -v busbra-data:/data \
  -p 127.0.0.1:8080:8080 busbra-vision:unified
docker logs --tail 100 busbra-vision
```

Conserver **une seule instance** : le stockage JSON n'est pas prévu pour plusieurs
répliques. Prévoir suffisamment de RAM pour Keras/PyTorch et Grad-CAM ; mesurer
la consommation réelle. Le premier démarrage peut prendre plusieurs minutes.
`BUSBRA_FAST_INFERENCE=1` conserve les mêmes poids, partage la passe de
classification avec Grad-CAM et désactive uniquement la seconde passe TTA de la
segmentation. Utiliser `0` pour reproduire le mode d'évaluation complet.

## Domaine HTTPS et contrôles

Placer le conteneur derrière un reverse proxy HTTPS (Nginx ou Caddy) configuré
par l'administrateur du VPS. Le port 8080 reste lié à la boucle locale. Le proxy
doit transmettre l'hôte et `X-Forwarded-Proto`, et accepter les fichiers de 16 Mo
ainsi que les requêtes d'analyse longues. Le mode production utilise des cookies
Secure : une connexion HTTP publique ne suffit pas pour la session ni pour la PWA.

- `/api/health` doit répondre avec `models.loaded=true`.
- `/login` doit permettre la connexion de l'administrateur initial.
- `/admin` doit rester séparé de l'espace d'analyse `/workspace/`.
- Vérifier le mode serveur et son rapport, puis préparer le mode local et tester
  une nouvelle image après coupure réseau.
- Une prédiction normale ne doit pas produire de masque ; une image manifestement
  incompatible doit être refusée avant l'analyse.
- L'ancien accès `secours` et ses anciennes sessions doivent être refusés.

Au démarrage, un éventuel ancien `recovery.json` est supprimé et journalisé,
à condition qu'un administrateur ordinaire actif existe. Le journal passé et les
autres comptes ne sont pas effacés. Sauvegarder le volume avant toute migration.

Les historiques serveur et navigateur ne sont pas synchronisés. Les archives
locales restent accessibles sur un appareil partagé après déconnexion ; utiliser
uniquement des données de démonstration non identifiantes.

Ce dépôt ne contient ni adresse ni accès SSH au VPS. Ces commandes constituent
le transfert technique ; elles ne signifient pas que le VPS est déjà déployé.
