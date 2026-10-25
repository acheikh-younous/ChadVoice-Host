# ChadVoice v1.3 — Mise à jour GitHub / Render

## Livraison
Cette mise à jour ajoute **archivage**, **restauration**, **suppression définitive réservée aux segments non annotés**.
Elle conserve PostgreSQL Neon, le stockage privé S3, les comptes et les historiques existants.

**IMPORTANT :** la publication automatique depuis ChatGPT est bloquée par les permissions de l’intégration GitHub (403). Les modifications sont prêtes, mais **elles ne sont pas encore publiées sur votre dépôt**.

## Sauvegarde avant installation
Connectez-vous avec le compte administrateur sur https://chadvoice-pilote.onrender.com/admin et téléchargez **Sauvegarde intégrale privée**. Conservez-la hors ligne et protégez-la (elle contient des données personnelles).

## Publication par commandes Git (Windows)
1. Téléchargez `ChadVoice_v1.3_GitHub.patch` et enregistrez-le, par exemple dans `Téléchargements`.
2. Dans PowerShell, dans un dossier de travail quelconque :

```powershell
git clone https://github.com/acheikh-younous/ChadVoice-Host.git
cd ChadVoice-Host
git switch -c feature/audio-archive-delete
# Remplacez CHEMIN_DU_PATCH par le chemin réel du fichier téléchargé.
git apply --check "CHEMIN_DU_PATCH\ChadVoice_v1.3_GitHub.patch"
git apply "CHEMIN_DU_PATCH\ChadVoice_v1.3_GitHub.patch"
python -m pytest -q tests/test_app.py tests/test_cloud_adapters.py
git add app.py postgres_backend.py postgres_schema.sql templates/admin.html tests/test_app.py VERSION
git commit -m "ChadVoice v1.3: archivage et suppression securisee des audios"
git push -u origin feature/audio-archive-delete
```

3. Ouvrez GitHub et créez une Pull Request de `feature/audio-archive-delete` vers `main`.
4. Après vérification, fusionnez cette Pull Request. Render redéploiera la branche `main` automatiquement.
5. Testez avec de **nouveaux audios fictifs**, jamais en supprimant d’emblée les originaux scientifiques.

## Fonctionnement
- Un segment archivé est conservé dans Neon mais exclu des contributions, de la file de révision et des exports validés. L'administrateur peut le restaurer.
- Le bouton **Supprimer définitivement** n'apparaît que pour les segments `available`, sans attribution, consultation, contribution, version, révision ni notification. Le serveur revérifie toutes les conditions.
- Les audios sont supprimés du stockage privé seulement après contrôle d'accès et confirmation du code. Les historiques d'audit sont conservés, sans référence à une ligne effacée.
- L'archivage d'un segment en consultation est refusé : utilisez d'abord **Libérer**.
- Une base PostgreSQL existante reçoit automatiquement la colonne `archived_at` via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` sans effacer les données.

## Contrôle qualité
- 20 tests automatisés réussis dans l’environnement de développement (SQLite local, clients HTTP et simulateur de stockage S3).
- Test de migration depuis une base SQLite v1.2 contenant un administrateur réussi.
- **Non encore testé sur votre vraie base Neon et votre hébergement Render.** Avant l'ouverture aux contributeurs, testez les trois actions avec des fichiers audio temporaires.

## Sécurité
Conservez `.env`, secrets, fichiers audio et sauvegardes hors du dépôt GitHub. Configurez le dépôt en **Private** si possible.
