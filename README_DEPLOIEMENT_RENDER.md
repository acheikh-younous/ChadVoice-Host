# ChadVoice v1.2 — Guide complet Render + Neon (pilote gratuit)

**État : code préparé, tests SQLite 13/13 réussis, mais tests réels Neon/Render à effectuer sur les comptes du projet avant toute collecte scientifique.**

## A. Avant de commencer : sécurité et sauvegarde
1. Faites une copie complète de `private_data` depuis votre ChadVoice v1.1 local ; ne la mettez jamais sur GitHub.
2. Gardez l'ancien serveur comme référence ; stoppez-le avant une éventuelle migration des données locales.
3. Choisissez si l'essai en ligne démarre à vide (recommandé pour tests) ou reprend la base locale (voir section H).
4. Les audios doivent avoir les droits nécessaires pour une diffusion même limitée aux contributeurs.

## B. Préparer GitHub (dépôt PRIVÉ)
1. Ouvrez https://github.com/new et créez un dépôt **privé** `chadvoice` SANS fichier README initial.
2. Décompressez cette archive dans un dossier tel que `C:\ChadVoice_Render_v1_2`.
3. Dans PowerShell ouvert à la racine du dossier :

```powershell
cd C:\ChadVoice_Render_v1_2
git init
git branch -M main
git add .
git commit -m "ChadVoice v1.2: preparation Render et Neon"
git remote add origin https://github.com/VOTRE_UTILISATEUR/chadvoice.git
git push -u origin main
```

**Vérifiez avant le push** qu'aucun fichier secret, `.env`, `private_data`, `.sqlite3` ou audio n'apparaisse dans `git status`. Les scripts de migration ne sont pas des données : ils peuvent être versionnés.

## C. Créer Neon PostgreSQL et un stockage audio PRIVÉ
1. Ouvrez https://console.neon.tech/ ; créez un projet `chadvoice` avec le plan Free.
2. Préférez une région proche de Render, **uniquement si Object Storage y est proposé** ; gardez la même branche `main` pour PostgreSQL et les audios.
3. Node.js doit être installé sur votre ordinateur. Dans PowerShell, à la racine du projet, exécutez :

```powershell
npm install -g neon
npm install @neon/config
neon login
neon link
neon branches list
neon deploy
neon env pull
```

L'outil Neon crée le bucket privé `chadvoice-private` défini dans `neon.ts` et écrit ses paramètres techniques dans `.env.local` (ou `.env` selon votre configuration). **Ces fichiers sont secrets : ne jamais les envoyer sur GitHub.** Les commandes `neon login/link` et l'existence des fonctions peuvent varier selon la version du CLI ; référez-vous à la documentation courante si un menu diffère.

4. Récupérez dans le fichier local, sans le partager publiquement, les variables :
`DATABASE_URL`, `AWS_ENDPOINT_URL_S3`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`.
5. La chaîne `DATABASE_URL` doit être celle de la base du projet (idéalement connexion *poolée* si proposée), contenant `sslmode=require`.
6. Le bucket s'appelle `chadvoice-private` et doit rester en **private** (aucun accès public).

Source Neon (documentation officielle) : https://neon.com/docs/storage/get-started

## D. Configurer Render et déployer
1. Ouvrez https://dashboard.render.com/ et connectez votre GitHub.
2. Sélectionnez **New → Blueprint** (ou `New → Web Service` si vous préférez les commandes manuelles).
3. Choisissez le dépôt **privé** `chadvoice` et la branche `main`.
4. Le `render.yaml` fourni configure Python, Free, Frankfurt, HTTPS et le point de contrôle `/health`.
5. Dans la configuration Render, entrez les valeurs secrètes (NE PAS les envoyer dans le chat) :

| Nom | Valeur |
|---|---|
| `DATABASE_URL` | chaîne Neon PostgreSQL |
| `AWS_ENDPOINT_URL_S3` | endpoint S3 Neon |
| `AWS_ACCESS_KEY_ID` | identifiant S3 Neon |
| `AWS_SECRET_ACCESS_KEY` | clé secrète S3 Neon |
| `AWS_REGION` | région retournée par Neon |
| `S3_BUCKET` | `chadvoice-private` |
| `CHADVOICE_BOOTSTRAP_TOKEN` | secret aléatoire (au moins 40 caractères) |

Les variables `CHADVOICE_AUDIO_STORAGE=s3` et `CHADVOICE_HTTPS=1` sont déjà dans `render.yaml`.

Créer un token fort avec :

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Si Render est configuré manuellement (Web Service), renseignez :
- Runtime : **Python 3** ; Plan : **Free**
- Build command : `pip install -r requirements.txt`
- Start command : `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Health check path : `/health`

Attendez un statut de déploiement **Live**. Ouvrez `https://VOTRE_SERVICE.onrender.com/health` et vérifiez `{"ok":true}`. Si erreur, voyez **Logs** sur Render : problèmes habituels `DATABASE_URL` manquant, stockage S3 mal configuré, ou accès Neon.

## E. Créer le premier administrateur SANS shell Render
1. Ouvrez `https://VOTRE_SERVICE.onrender.com/setup` dans le navigateur.
2. Saisissez le token initial créé ci-dessus, votre nom, votre adresse e-mail et un mot de passe fort.
3. Après confirmation, connectez-vous via `/login`.
4. Supprimez la variable **`CHADVOICE_BOOTSTRAP_TOKEN`** dans Render (Environment), sauvegardez et redéployez.
5. La route `/setup` cesse de fonctionner dès qu'un administrateur existe.

## F. Tester avant les vrais audios
1. Créez deux comptes : un **contributor**, un **reviewer**.
2. Importez seulement **trois WAV/MP3 de test** via Administration → Import.
3. Contributeur : « Obtenir un segment » → écouter → soumettre deux textes.
4. Réviseur : refuser avec motif → vérifier la notification interne, revenir corriger, resoumettre, puis valider.
5. Vérifiez « Inaudible » avec un autre fichier.
6. Vérifiez que le contributeur ne peut pas consulter les audios des autres et ne peut pas exporter les fichiers.
7. Administrateur : exporter CSV/JSON et faire une sauvegarde ZIP complète.
8. Redéployez Render une fois : les comptes et les audios doivent **rester accessibles** (Neon est persistant).

## G. Importer les vrais segments : éviter un ZIP gigantesque
Render Free peut avoir une mémoire et des limites d'upload réduites. Importez les audios en **petits lots** (p. ex. 20 à 50 fichiers ou fichiers ZIP bien plus petits que 1 Go) et vérifiez les compteurs après chaque lot. Ne publiez pas un corpus soumis à droits d'auteur sans autorisation.

## H. Reprendre la base locale SQLite v1.1 (OPTIONNEL)
La migration ne doit s'effectuer que vers un **projet Neon vide** ; si des tests existent déjà dans la cible, créez une autre base/projet pour les données réelles ou nettoyez les données de test par un processus maîtrisé.

1. Arrêtez le serveur local v1.1 et copiez `private_data` pour sauvegarde.
2. Sur un ordinateur Windows, chargez dans **votre terminal local** les variables `DATABASE_URL`, `CHADVOICE_AUDIO_STORAGE=s3`, `AWS_ENDPOINT_URL_S3`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET`.
3. Exécutez la migration (depuis le dossier v1.2) :

```powershell
python migrer_sqlite_vers_cloud.py "C:\chemin\vers\ChadVoice_Local_v1\private_data"
```

Les comptes, hachages des mots de passe, traductions, versions, révisions et audios sont transférés ; les anciennes sessions navigateur ne sont pas conservées. Aucun fichier local n'est effacé. Le processus refusera une base Neon déjà remplie pour éviter les doublons. Testez ensuite l'accès en ligne.

## I. Sauvegardes pour la campagne de 15 jours
Ne pas attendre le 15e jour ! Chaque jour : connectez-vous comme admin et téléchargez `/admin/backup` (fichier ZIP avec `database.json` et tous les audios). Conservez une copie sur un ordinateur **et** sur un support indépendant protégé. L'archive est sensible : contient comptes, adresses, hachages de mot de passe et historique. Ne la mettez jamais dans GitHub, un service public ou un chat.

Les exports CSV et JSON standards ne contiennent que les traductions **validées** ; utilisez la sauvegarde complète pour conserver également les rejets, corrections et contributions en attente.

Un utilitaire `restaurer_sauvegarde_cloud.py` est inclus pour restaurer le ZIP de sauvegarde PostgreSQL dans une **base cible vide**, avec les mêmes variables S3. Ne restaurez jamais directement sur un site en service.

## J. Mettre à jour l'application après publication
Après tests locaux, depuis PowerShell :

```powershell
git add .
git commit -m "Correction de l'application ChadVoice"
git push origin main
```

Render reconstruit automatiquement l'application depuis la branche `main` si le déploiement automatique est activé. Un redéploiement ne doit pas supprimer vos données Neon, mais une modification du schéma de la base nécessite des migrations et sauvegardes adaptées.

## K. Limites et sécurité
- Render Free met le service en veille et bloque les ports SMTP 25, 465 et 587. La notification interne fonctionne sans e-mail externe. Une API HTTPS d'e-mails sera une étape ultérieure.
- Le dépôt Git doit rester privé, les audios en bucket **private**, et toutes les variables sensibles dans Environment sur Render, jamais dans le code.
- Cette livraison passe les tests locaux hérités de v1.1, mais aucun déploiement réel n'a été effectué sans vos connexions Neon et Render. Vérifiez tous les parcours avant la collecte scientifique.
- Envisagez une limite de comptes et une surveillance lors de l'essai public ; le formulaire de connexion doit être protégé contre les tentatives répétées avant une grande ouverture.
- D'autres améliorations nécessaires à une exploitation durable : journalisation structurée, protection anti-brute-force, e-mail API, migrations de schéma versionnées, export en flux pour des corpus très volumineux, test de charge et restauration périodique.
