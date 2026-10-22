# BUSBRA Vision — test d'analyse hors ligne sur téléphone

Ce paquet fournit une interface d'analyse hors ligne assortie à l'espace d'analyse
de l'application BUSBRA Vision. C'est une application web installable distincte :
elle ne réutilise pas la session ni les données serveur de l'application principale.

## Test sur téléphone

Dans la version Flask intégrée, un seul lancement `python app.py` sert l'application
et cette interface sous `/workspace/`, sur le même domaine et le même port. La page
propose Automatique / Serveur / Local. Le serveur nécessite une session connectée
et conserve ses examens dans le compte ; le mode local ne transmet aucune image.
Les instructions de coupure ci-dessous concernent le mode local après préparation.
Les historiques ne sont pas synchronisés et l'archive locale reste accessible sur
un appareil partagé, y compris après déconnexion du compte serveur.

1. Ouvrir l'adresse HTTPS du laboratoire dans Chrome sur Android ou Safari sur iPhone.
   Utiliser le navigateur directement, pas le navigateur intégré de WhatsApp ou Facebook,
   et éviter la navigation privée.
2. Avec une connexion stable (Wi-Fi conseillé), appuyer sur **Préparer le mode hors ligne**.
   Laisser la page ouverte jusqu'au message **Prêt**. Prévoir environ 127 Mo à télécharger
   et davantage de mémoire disponible pour les calculs. Ne pas verrouiller l'écran pendant cette étape.
3. Facultatif : ajouter l'application à l'écran d'accueil. Android : menu du navigateur,
   **Installer l'application** / **Ajouter à l'écran d'accueil** selon le navigateur.
   iPhone : menu de partage puis **Sur l'écran d'accueil**.
   Si le mode installé ouvre un espace de stockage distinct, le préparer également avant la coupure.
4. Avoir une image d'échographie de démonstration enregistrée dans les fichiers du téléphone.
   Ne pas utiliser de données patient identifiantes.
5. Activer le mode avion et vérifier que Wi-Fi et données mobiles sont coupés. Recharger
   la page : elle doit encore s'ouvrir avec le moteur prêt.
6. Choisir une image dans cette PWA et appuyer sur **Analyser l'échographie**. Garder
   l'application au premier plan ; le premier calcul peut être plus long.
7. Ouvrir **Historique**, puis **Ouvrir** pour revoir l'examen ou **Rapport** pour consulter
   et imprimer/enregistrer le rapport en PDF. Les mêmes pages fonctionnent hors ligne.
8. Refaire le test avec plusieurs images. Si la classe PRÉDITE est normale, aucun masque
   ni surface de segmentation ne doit apparaître. Pour bénigne ou maligne, le masque et
   sa superposition doivent apparaître.

Pour signaler un problème, transmettre le modèle du téléphone, sa RAM si connue,
le navigateur et sa version, le message exact et le temps affiché. Une capture d'écran
de la démonstration publique aide aussi, sans aucune donnée personnelle.

L'URL 127.0.0.1 du PC ne désigne PAS ce PC depuis le téléphone. Une adresse HTTP
sur le Wi-Fi local ne suffit pas pour installer correctement le service worker :
utiliser le lien HTTPS publié. Un ZIP décompressé puis ouvert en file:// sur mobile
n'est pas non plus un mode de lancement pris en charge.

## Ce que fait cette version

- Classification EfficientNetB0 et segmentation U-Net / ResNet34 avec les poids fournis,
  convertis en ONNX FP32 sans réentraînement ni quantification.
- Exécution CPU locale dans un Web Worker avec ONNX Runtime Web 1.23.2.
- Prétraitements Pillow/OpenCV reproduits en JavaScript ; segmentation avec TTA,
  seuil 0,5 et plus grande composante ; pas de segmentation pour une classe prédite normale.
- Téléchargement des modèles vérifié par SHA-256, cache de l'interface et du moteur,
  rechargement possible sans serveur une fois la préparation terminée.
- En mode local : aucun envoi d'image, aucun appel à l'API d'analyse et aucune écriture dans les dossiers
  médicaux. L'image source, l'empreinte, les résultats, le masque, la date et les retours
  médecin facultatifs sont conservés dans IndexedDB sur cet appareil pour rouvrir
  l'examen et son rapport hors ligne. L'espace de stockage utilisé dépend de la taille
  et du nombre d'images analysées ; supprimer les données du site efface aussi ces archives.
- Le même fichier exact est détecté par empreinte SHA-256 et signalé avant une nouvelle
  analyse. Le tableau de bord et l'historique présentent les analyses et retours locaux.
- L'interface reprend le parcours, les couleurs et les composants de la zone d'analyse
  en ligne. Lorsqu'elle est servie par Flask sous `/workspace/`, le mode serveur réutilise
  la session du compte et l'API protégée, avec Grad-CAM et liens vers le rapport et
  l'historique du compte. La distribution statique autonome reste locale uniquement.
  Les historiques locaux ne sont pas synchronisés entre appareils ni vers le compte.

## Limites à connaître

- Test matériel réalisé sur PC dans Edge. L'affichage mobile a été contrôlé, mais les
  performances et la mémoire doivent encore être mesurées sur un vrai téléphone.
- PNG/JPEG opaques uniquement ; 16 Mo par fichier et 4 millions de pixels maximum.
- Un filtre local contrôle les couleurs, le contraste, l'entropie et la texture avant
  toute inférence : photos couleur, aplats et captures/graphismes incompatibles sont
  refusés sans analyse ni enregistrement. Seul le mode B en niveaux de gris est pris
  en charge (pas le Doppler couleur). Ce filtre heuristique n'est pas un détecteur
  médical validé : certaines autres images en niveaux de gris peuvent encore passer,
  et une échographie de qualité insuffisante peut être rejetée. Un détecteur dédié
  entraîné sur échographies ET images hors domaine reste nécessaire pour aller plus loin.
- Pas encore de Grad-CAM hors ligne, de connexion de compte hors ligne ni de
  synchronisation. Un retour médecin local ne modifie pas les données de la plateforme
  et ne déclenche aucun apprentissage du modèle.
- Les scores ne sont pas des probabilités cliniques calibrées. L'absence de masque pour
  une classe prédite normale est une règle de l'application, pas une preuve d'absence de lésion.
- Si le navigateur efface les données du site, télécharger à nouveau les ressources.
  Les téléphones peu puissants peuvent manquer de mémoire ou dépasser le délai de calcul.

## Vérifications déjà effectuées

Le protocole automatisé prépare les ressources, arrête le serveur, coupe le réseau du
navigateur, recharge la page et sélectionne ensuite 15 images locales publiques qui
n'ont pas été placées dans le cache de la PWA.

- 15/15 classes identiques à l'implémentation Python originale (ce n'est PAS une exactitude
  clinique de 100 % ; cela inclut les erreurs éventuelles du modèle original).
- Écart maximal entre scores du navigateur et Python : environ 0,0000051.
- Masques identiques à ceux de Python sur les 9 cas segmentés ; les 6 classes prédites
  normales ne produisent aucun masque.
- Sur le PC de test, environ 0,2–0,3 seconde sans segmentation et 6–10 secondes avec
  segmentation/TTA. Ces temps ne préjugent pas de ceux d'un téléphone.
- 56 tests Python de l'application principale et 5 tests JavaScript de prétraitement passent.

Le détail et les captures sont dans `output/offline_poc/` du projet de développement.
Les scores Dice éventuels de ce contrôle comparent les DEUX IMPLÉMENTATIONS, pas les
prédictions à des masques de vérité terrain. Ce n'est pas une validation médicale.

## Lancement local sur PC

Après décompression du paquet, avec Python 3.10 ou supérieur :

```powershell
python .\server.py --port 5091
```

Puis ouvrir http://127.0.0.1:5091/ dans Chrome ou Edge. Aucun paquet Python tiers n'est
nécessaire pour servir ce paquet déjà exporté. La préparation initiale doit être faite
avant d'arrêter le serveur.

## Hébergement

Pour Render, publier uniquement un **Static Site** depuis la branche
`codex/render-offline-poc` du dépôt `Graddy12/busbra-vision` :

- Build : `python3 scripts/build_offline_static.py`
- Dossier publié : `output/render_static`
- Variable non secrète : `SKIP_INSTALL_DEPS=true`

Le script récupère l'archive publique de la release `offline-poc-v1`, vérifie sa
taille et son SHA-256 puis publie exclusivement les fichiers de la PWA, du moteur
et des modèles. Aucun environnement Flask, aucune dépendance ML Python et aucune
donnée de l'application ne sont installés ou publiés. Le téléchargement initial
sur chaque téléphone représente environ 127 Mo et consomme du trafic de sortie.

Le Dockerfile et railway.toml permettent de déployer ce dossier seul comme service
statique indépendant. Le serveur ne reçoit aucun téléversement et ne fait aucun calcul
d'inférence : il distribue l'interface, le moteur et les modèles. Railway fournit PORT
et termine HTTPS. Aucune variable de mot de passe, aucun volume et aucun poids Python
ne doivent être ajoutés à ce service. Les modèles ONNX sont volontairement téléchargeables.

ONNX Runtime Web est fourni sous licence MIT :
https://github.com/microsoft/onnxruntime/blob/v1.23.2/LICENSE
La provenance des exemples de test est le dossier public BUSI de démonstration ; les
images de test ne sont pas intégrées au cache de l'application.
