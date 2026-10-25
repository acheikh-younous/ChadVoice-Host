# ChadVoice – gestion des codes audio et comptes

Branche de travail : ne pas fusionner ni déployer avant tests.

## Audios
- Modifier uniquement le `segments.code` visible, jamais `segments.id` ni `segments.file_name` (objet S3).
- Convention `CV` suivi de 4 à 8 chiffres; vérifier unicité et journaliser ancien/nouveau code.
- Signaler les codes non conformes dans la bibliothèque.

## Utilisateurs
- Rubrique séparée administration, création et modification nom/e-mail/rôle, activation/désactivation et gestion des mots de passe.
- Générer un mot de passe temporaire individuel et aléatoire de dix chiffres; ne jamais utiliser la valeur commune prévisible `1234567890`.
- Obliger le changement du mot de passe avant la contribution; jamais de mot de passe en clair dans PostgreSQL ou dans les journaux.
- Ne jamais supprimer l'historique des contributions lorsqu'un compte est désactivé.

## Validation
Une version candidate ZIP testée localement est disponible dans la conversation ChatGPT; son code provient d'une archive antérieure et doit être réconcilié avec la branche main actuelle avant tout merge. Le ZIP n'est pas automatiquement synchronisé à cette branche. Ne pas écraser les filtres audios et correctifs Unicode introduits en PR #3.
