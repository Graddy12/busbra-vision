# Déploiement GitHub → Railway

Pour le transfert de la version unifiée sur VPS, utiliser [DEPLOYMENT_VPS.md](DEPLOYMENT_VPS.md).

Cette application est une démonstration académique, pas un dispositif médical.
Ne pas y charger de données patient identifiantes. Les données locales et les
comptes de développement ne sont jamais publiés.

## Code et modèles

Le code est dans `Graddy12/busbra-vision`, branche `main`. Les deux poids sont
distribués comme pièces jointes de la release `models-v1`, hors de l'historique
Git. `models/manifest.json` épingle les tailles et SHA-256 ; un téléchargement
incorrect interrompt la construction avant tout chargement du checkpoint PyTorch.

Après un clone neuf, installer les poids pour un lancement local :

```powershell
python scripts/download_models.py
```

## Service Railway

1. Créer ou sélectionner le service associé au dépôt GitHub et à `main`.
2. Railway détecte le `Dockerfile`. Le conteneur utilise Python 3.11, PyTorch CPU,
   Keras/PyTorch et Gunicorn. Aucun TensorFlow ni runtime CUDA n'est installé.
3. Attacher **un volume persistant monté à `/data`**. Les JSON, les images et la
   clé de session doivent survivre aux redéploiements. Sans volume sur Railway,
   le démarrage échoue volontairement pour éviter de perdre les comptes/données.
4. Définir dans les variables privées du service :

   - `BUSBRA_ADMIN_PASSWORD` : mot de passe inédit, aléatoire, au moins 16 caractères ;
   - `BUSBRA_DATA_DIR=/data` (déjà défini dans le conteneur) ;
   - facultatif : `BUSBRA_SECRET_KEY`, secret aléatoire d'au moins 32 caractères.

5. Conserver **une seule réplique**. Gunicorn utilise un processus et deux threads,
   les inférences sont sérialisées. Prévoir une enveloppe mémoire suffisante pour
   les deux modèles et leurs gradients Grad-CAM ; 2 Go est un point de départ à
   mesurer, pas une garantie. Ne pas supposer qu'une offre gratuite suffira.
6. Déployer puis attendre que `/api/health` réponde avec `models.loaded=true`.
   Le contrôle de santé autorise 600 secondes pour le chargement initial.
7. Générer un domaine HTTPS dans les réglages réseau du service, puis ouvrir
   `/login` : identifiant `admin`, mot de passe privé défini à l'étape 4.

`PORT` est fourni par Railway. Gunicorn écoute sur `0.0.0.0:$PORT`. Les cookies
sont Secure/HttpOnly/SameSite et les en-têtes du proxy Railway sont pris en compte.
Le serveur Flask de développement n'est pas utilisé en ligne.

Le mot de passe de bootstrap n'est utilisé que si le fichier des utilisateurs est
vide. Modifier la variable après création ne réinitialise pas un compte existant.

## Retrait de l'ancien accès de secours

Cette version ne contient plus de compte caché. Ne pas définir
`BUSBRA_RECOVERY_PASSWORD_HASH` ; supprimer cette ancienne variable si elle existe.
Au premier démarrage de la nouvelle version, l'ancien fichier `recovery.json` est
supprimé et ses sessions sont refusées. Un administrateur ordinaire actif doit
exister avant la migration. Les utilisateurs, les analyses et le journal passé
ne sont pas effacés. Les anciens services déjà en ligne ne bénéficient de ce
retrait qu'après déploiement de cette version.

## Vérifications et limites

- Ne pas copier `instance/`, `.env`, les logs ou les archives ZIP vers GitHub.
- Sauvegarder le volume et tester la restauration avant toute donnée importante.
- Vérifier la persistance après un redémarrage et tester une analyse de chaque classe.
- Le stockage JSON et la limitation de connexion en mémoire conviennent à cette
  démonstration mono-instance, pas à un service clinique multi-utilisateur durci.
- Docker n'a pas besoin des mots de passe à la construction : ils sont fournis
  uniquement au démarrage par Railway, jamais dans l'image ou le dépôt.

Documentation officielle : [Flask](https://docs.railway.com/guides/flask),
[volumes](https://docs.railway.com/volumes),
[healthchecks](https://docs.railway.com/deployments/healthchecks).
