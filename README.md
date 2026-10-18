# ChadVoice Web v1.2 — Render + Neon

**Dernière étape validée :** 15 tests locaux réussis (interface FastAPI/SQLite et adaptateurs de migration), fichiers prêts à déployer. Aucun serveur public n'a été créé automatiquement : configuration des comptes Neon, GitHub et Render nécessaire.

➡️ **Commencez par [README_DEPLOIEMENT_RENDER.md](README_DEPLOIEMENT_RENDER.md)**.

## Fonctionnalités conservées
- Comptes admin, contributeur et réviseur ; accès privé aux audios
- Import WAV, MP3, ZIP ; réservations temporaires sans bloquer définitivement les segments
- Transcription latine, traduction française, brouillons, nouvelle soumission après rejet
- Révision humaine, motifs de correction, option inaudible, notifications internes
- Export CSV/JSON des textes validés ; sauvegarde complète au format ZIP
- PostgreSQL (Neon) et stockage d'objets privé S3 (Neon Object Storage), ou SQLite/local pour essais

## Fichiers de déploiement
- `render.yaml` : déploiement Render Free
- `neon.ts` : stockage audio privé à provisionner sur Neon
- `postgres_schema.sql` et `postgres_backend.py` : base PostgreSQL
- `object_storage.py` : accès audio S3 contrôlé via le serveur
- `migrer_sqlite_vers_cloud.py` : transfert facultatif depuis la v1.1
- `restaurer_sauvegarde_cloud.py` : restauration dans un projet Neon vide

## Tester localement
L'application garde un mode SQLite/local si `DATABASE_URL` est absent. Ne pas utiliser ce mode sur un Render Free : le stockage local n'y est pas durable.

```bash
pip install -r requirements.txt
pytest -q
uvicorn app:app --reload
```

Ne partagez ni les audios non autorisés à la diffusion, ni les variables `.env`, ni les archives de sauvegarde. Une sauvegarde contenant les mots de passe hachés reste une donnée sensible.
