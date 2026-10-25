# ChadVoice v1.5 — tableau de bord personnel, mobile et activité des comptes

## Objectifs

- Le contributeur ne voit que ses **propres** contributions, corrections, validations et segments en révision. Le total du corpus et le nombre global de fichiers disponibles ne sont plus affichés à son rôle.
- Les textes de la page Contribuer guident sans avertissements anxiogènes sur les expirations techniques. **Les durées techniques de réservation restent inchangées côté serveur**, pour éviter qu'un clic ne bloque un audio.
- En affichage mobile, la barre supérieure utilise le logo PNG existant `static/chadvoice-logo.png` et fournit un bouton de déconnexion. La référence erronée `static/chadvoice_logo.png` a été corrigée. Le logo PNG existant sur GitHub doit être conservé.
- Le tableau d'administration présente la dernière activité enregistrée, la rubrique consultée et un indicateur de présence. « En ligne » = session valide + activité dans les 5 dernières minutes ; ne prétend pas suivre les connexions en temps réel.
- Un enregistrement `user_presence` par compte (dernier horodatage et rubrique, pas d'adresse IP ni de navigation complète) est automatiquement créé par le serveur. Les anciens accès ne peuvent pas être reconstruits.

## Sécurité et mise en production

**Ne pas déployer en recopiant aveuglément tous les fichiers si le dépôt a évolué.** Faire examiner et fusionner par Codex les changements pertinents sur une branche séparée, en conservant les améliorations 1.4 (corpus, révocation, batch) et le logo du dépôt courant.

Sauvegarder la base et les audios avant fusion. Aucun mot de passe ni secret Neon / Render n'est nécessaire pour les tests SQLite.

Cette version inclut un ajout compatible aux schémas SQLite et PostgreSQL : `CREATE TABLE IF NOT EXISTS user_presence`. Aucun `DROP TABLE`, aucun effacement de contribution.

Les tests locaux sont exécutables avec :

```bash
python -m pip install -r requirements.txt pytest httpx
python -m pytest -q tests/test_app.py tests/test_cloud_adapters.py
```

## Contrôles après déploiement

1. Un contributeur voit seulement ses statistiques ; un autre contributeur voit des chiffres différents.
2. L'administrateur conserve la vue globale.
3. Sous 375 px et 768 px de largeur, le logo et le bouton Déconnexion sont visibles et fonctionnels.
4. L'administrateur voit les dernières activités en UTC, les rubriques, et le statut estimatif. Une déconnexion invalide la session correspondante.
5. Vérifier les imports, les corrections, l'export scientifique, la migration Neon et les permissions avec des audios de test.

Ne pas fusionner sans sauvegarde, validation manuelle, tests avec un vrai PostgreSQL et une vérification de l'interface mobile.
