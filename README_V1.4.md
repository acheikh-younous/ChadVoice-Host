# ChadVoice v1.4 — Gestion du corpus et révocation des validations

## Livrables
- Bibliothèque `/admin/segments` : recherche (code, nom, source, locuteur, texte), filtre et pages de 40.
- Détail `/admin/segments/<id>` : écoute admin, traduction, historique, renommage du nom affiché, modification source/locuteur.
- Rejet a posteriori d'une validation erronée : passe `approved` à `rejected`, conserve l'approbation dans l'historique, notifie le contributeur, exclut immédiatement des exports validés.
- Correction administrative de la traduction finale approuvée : nouvelle entrée de révision sans toucher au texte original du contributeur, motif obligatoire.
- Validation multiple : 30 max, confirmation obligatoire « VALIDER », uniquement les contributions soumises complètes, non inaudibles, non archivées, et qui ne sont pas celles de l'administrateur.
- Fonctionnalités v1.3 incluses : archivage/restauration et suppression des seuls fichiers sans annotations ni historique.

## Déploiement sûr
1. En tant qu'administrateur, télécharger `/admin/backup` et conserver le ZIP hors GitHub.
2. Créer une branche depuis le dernier `main` : `git switch -c feature/chadvoice-v14-curation`.
3. À la racine du dépôt `ChadVoice-Host`, appliquer `git apply --check <chemin_du_patch>`, puis `git apply <chemin_du_patch>`.
4. Installer `pip install -r requirements.txt pytest httpx` ; lancer `python -m pytest -q tests/test_app.py tests/test_cloud_adapters.py`.
5. Revoir `git diff`, enregistrer dans cette branche et créer une pull request vers `main`.
6. Après les tests de validation, fusionner ; Render redéploiera automatiquement. Surveiller les logs et l'état `/health`.
7. Tester avec des segments fictifs AVANT d'utiliser des contributions scientifiques réelles.

Ce correctif applique sur le dépôt GitHub actuel (version 1.2 avec la correction existante du `row_factory`).
Il ne modifie pas le logo personnalisé (`templates/base.html`, `templates/login.html`, `static/app.css`) ni les secrets Render.
La migration PostgreSQL v1.3 `archived_at` est idempotente à l'import du code. Il n'y a aucune suppression de tables.
Les tests réalisés utilisent SQLite temporaire et des adaptateurs simulés : il reste à tester la compatibilité Neon S3 et PostgreSQL sur un environnement de préproduction.

## Limites et décisions
- Le nouveau nom n'affecte que le nom affiché. L'objet S3 et son identifiant restent inchangés.
- La suppression irréversible n'est pas disponible si le segment a été annoté ou révisé ; utiliser Archiver.
- Les validations groupées ne remplacent pas une vraie réécoute scientifique : sélectionnez des contributions déjà examinées.
- La possibilité d'attribuer simultanément plusieurs rôles à une personne sera traitée dans une version ultérieure (ce code garde un rôle principal par compte).
- Aucun envoi d'e-mail externe n'est garanti sans configuration SMTP ; notification interne oui.
