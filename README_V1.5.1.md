# ChadVoice v1.5.1 — correctif à examiner avant fusion

Le correctif joint a été appliqué après `git apply --check` sur `main`
(`0a19778a8968699ef2f258ee8e63babbae9dd1c2`), sur la branche
`feature/chadvoice-v1-5-1`. Le PNG `static/chadvoice-logo.png` est conservé
sans modification.

## Changements

- Bibliothèque audio paginée, recherche, métadonnées et historique des décisions.
- Correction motivée et révocation des validations, exports mis à jour ; les
  textes originaux et les décisions précédentes restent conservés.
- Validation groupée contrôlée, sans auto-validation, limitée à 30 segments.
- Archivage/restauration et suppression réservée aux audios sans annotation,
  réservation ni historique de consultation. Une ancienne consultation reste
  protégée après libération de sa réservation.
- Tableau de bord personnel pour les contributeurs et présence estimative
  visible uniquement dans l'administration.
- Logo et déconnexion sur mobile ; retour à la ligne des identifiants longs
  dans les contributions récentes pour éviter le débordement horizontal.
- Migration additive et idempotente de `archived_at` et `user_presence`.

La revue a aussi corrigé le refus des antislashs dans les noms affichés.

## Vérifications reproductibles

Installer les dépendances avec `pip install -r requirements-dev.txt` dans un
environnement virtuel. Pour les tests Chromium, utiliser le navigateur installé
ou installer celui de Playwright suivant `tests/README.md`.

```bash
DATABASE_URL= CHADVOICE_AUDIO_STORAGE=local \
CHADVOICE_TEST_BROWSER=/usr/bin/chromium \
.venv/bin/python -m pytest -q
```

Les tests applicatifs utilisent une base SQLite temporaire et des audios
synthétiques. Les tests S3 utilisent un stockage simulé.

La suite supplémentaire peut aussi tester un vrai PostgreSQL **local jetable** :

```bash
docker run -d --name chadvoice-v151-test-pg \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=chadvoice_test \
  -p 127.0.0.1:55432:5432 postgres:17
# Attendre que pg_isready confirme la disponibilité.
docker exec chadvoice-v151-test-pg pg_isready -U postgres -d chadvoice_test
DATABASE_URL= CHADVOICE_AUDIO_STORAGE=local \
CHADVOICE_TEST_BROWSER=/usr/bin/chromium \
CHADVOICE_TEST_POSTGRES_URL=postgresql://postgres@127.0.0.1:55432/chadvoice_test \
.venv/bin/python -m pytest -q
docker rm -f -v chadvoice-v151-test-pg
```

Cette URL facultative n'accepte que localhost. Chaque cas utilise un schéma
aléatoire supprimé après le test, initialise un schéma v1.2 avec un utilisateur
fictif, applique deux fois la migration et vérifie la conservation des données.
Les parcours testent les droits et CSRF, la consultation privée, la conservation
des fichiers après renommage, la validation, la correction, la révocation,
l'archivage/restauration, la suppression et ses échecs, l'atomicité des validations
groupées et l'isolation des tableaux de bord. Le stockage audio y reste local et
synthétique ; aucun service S3 réel n'est appelé.

Les tests navigateur vérifient le PNG réel et les dimensions, les largeurs
320 à 1440 px, la navigation mobile et l'absence de débordement avec un nom
d'utilisateur et un identifiant longs. Le traitement serveur de la déconnexion
est vérifié par les tests applicatifs.

## Limites avant production

Aucune donnée Neon, aucun audio réel et aucun service Render n'ont été utilisés
ou modifiés. PostgreSQL 17 local a été testé, pas Neon en production. Les services
S3 et SMTP réels, la charge et les téléphones physiques n'ont pas été validés.
Les dépendances sont déclarées par plages de versions, sans verrouillage.

La fusion dans `main` et tout déploiement nécessitent l'autorisation explicite
du propriétaire. Les anciennes notes v1.3–v1.5 sont des documents historiques du
correctif ; elles n'autorisent aucune action en production.
