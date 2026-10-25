# Gestion des codes audio et des utilisateurs

Branche : `feature/audio-codes-user-management`.
Base de revue : `main` au commit `61c24c76574173bb29d9c9ad7f959fa12863e7aa`.

## Fonctionnement vérifié

- Les migrations ajoutent `must_change_password` avec la valeur zéro pour les
  comptes existants. Les hachages de mots de passe, sessions et contributions
  restent conservés. Les migrations sont testées deux fois sur chaque moteur.
- Les mots de passe temporaires sont générés avec `secrets`, individuellement,
  sous forme de dix chiffres. Ils ne sont affichés que dans la réponse de
  création/réinitialisation réservée à l'administrateur, avec `no-store`.
  Ils sont hachés en base et ne sont pas ajoutés aux journaux ou aux CSV.
- La première connexion avec un mot de passe temporaire conduit au changement
  obligatoire avant l'accès aux contributions et aux autres fonctions.
  Les réinitialisations et changements de rôle invalident les sessions.
- Modification du nom, de l'e-mail et du rôle, refus d'un e-mail déjà utilisé,
  protection contre sa propre désactivation/dégradation, désactivation et
  réactivation : parcours couverts par les tests HTTP sur SQLite et PostgreSQL.
  La désactivation ne supprime ni les contributions ni leurs versions.
- Le changement de code accepte `CV` suivi de 4 à 8 chiffres ASCII, refuse les
  doublons et les formats incorrects, et journalise l'ancien et le nouveau code.
  L'identifiant interne, la clé de stockage, les octets audio et les annotations
  ne changent pas. Les exports reflètent le nouveau code.
- Les actions d'administration exigent le rôle administrateur et un jeton CSRF
  valide. Les visiteurs, contributeurs et réviseurs n'accèdent pas au CSV des
  comptes ni aux mutations administratives.
- Le CSV utilisateurs utilise UTF-8 avec BOM, séparateur point-virgule et fins
  de ligne CRLF. Les accents, guillemets, séparateurs et retours à la ligne sont
  conservés ; les cellules à risque de formule Excel sont préfixées d'une
  apostrophe. Les noms et e-mails sont volontairement exportés, uniquement aux
  administrateurs : ce CSV contient donc des données personnelles à protéger.
  Aucun mot de passe, hachage, jeton CSRF/session ni chemin audio privé n'est
  inclus dans ses huit colonnes autorisées.

## Corrections de revue

Les tests supplémentaires ont reproduit sur les deux moteurs l'acceptation d'un
nouveau mot de passe de 257 caractères, alors que la création était limitée à
256. Changement et réinitialisation appliquent maintenant la même limite de
10 à 256 caractères, sans modifier les mots de passe déjà enregistrés.

Les actions de désactivation/réinitialisation manuelle renvoient désormais 404
pour un compte inexistant au lieu d'annoncer un succès. La réinitialisation est
effectuée dans une transaction avec vérification préalable de l'utilisateur.

La page Mon compte affiche explicitement l'obligation de remplacer le mot de
passe temporaire, indique la limite de longueur, et ne contient plus de balise
de paragraphe dans le titre HTML.

## Reproduction des tests

Résultat final de la revue : **96 tests du dépôt réussis** en 89,34 s et
**8 contrôles indépendants réussis** en 35,08 s, soit 104 cas réussis,
sans échec, erreur ni test ignoré. La suite comprend les tests SQLite et
24 cas d'intégration PostgreSQL (12 existants, 12 ajoutés dans cette revue).
Chaque exécution émet un avertissement Starlette/httpx non bloquant.
Les résultats JUnit de cette instance sont `/tmp/chadvoice-users-final.xml`
et `/tmp/chadvoice-users-extra.xml`.

Installer les dépendances de `requirements-dev.txt` dans `.venv`. Pour Chromium,
suivre `tests/README.md`. La suite SQLite s'exécute depuis la racine :

```bash
DATABASE_URL= CHADVOICE_AUDIO_STORAGE=local \
CHADVOICE_TEST_BROWSER=/usr/bin/chromium \
.venv/bin/python -m pytest -q
```

Pour activer aussi les tests d'intégration PostgreSQL, préparer un conteneur
strictement local et jetable, attendre sa disponibilité puis lancer la suite :

```bash
docker run -d --name chadvoice-users-review-pg \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=chadvoice_test \
  -p 127.0.0.1:55432:5432 postgres:17
docker exec chadvoice-users-review-pg pg_isready -U postgres -d chadvoice_test
DATABASE_URL= CHADVOICE_AUDIO_STORAGE=local \
CHADVOICE_TEST_BROWSER=/usr/bin/chromium \
CHADVOICE_TEST_POSTGRES_URL=postgresql://postgres@127.0.0.1:55432/chadvoice_test \
.venv/bin/python -m pytest -q
docker rm -f -v chadvoice-users-review-pg
```

`tests/test_user_management.py` ajoute les régressions de cette revue à la suite
existante. Les tests PostgreSQL créent des schémas aléatoires isolés et les
suppriment après utilisation ; cette URL de test n'accepte que localhost.
Les tests historiques SQLite ne sont pas tous exécutables sur PostgreSQL : la
variable active les cas d'intégration paramétrés pour ce moteur.

Les parcours v1.5.1, recherches et filtres Unicode, archivage/restauration,
révocation, validations groupées, exports scientifiques et affichage responsive
restent couverts par la suite et les contrôles indépendants de revue.

## Périmètre

Tous les comptes et audios utilisés sont fictifs. Aucun accès aux données Neon,
aux audios réels ou à Render. PostgreSQL 17 est local ; S3 est simulé et SMTP
désactivé pendant les tests. L'importation réelle dans Microsoft Excel n'a pas
été exécutée : les octets et la structure CSV sont vérifiés automatiquement.
Un avertissement de dépréciation Starlette/httpx peut être émis.

La fusion et tout déploiement restent soumis à l'autorisation explicite du
propriétaire. Cette validation ne déclenche aucune opération en production.
