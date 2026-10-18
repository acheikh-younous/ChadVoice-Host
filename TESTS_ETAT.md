# ChadVoice v1.1 — État des essais automatisés

Exécution : `python -m pytest -q tests/test_app.py`

**Résultat : 13 tests réussis (17 octobre 2026)**. Les essais ont été exécutés avec la base SQLite de test, les services HTTP FastAPI et les faux comptes de test. Aucun audio scientifique réel n'a été utilisé.

Principaux scénarios :
- Authentification et accès refusés hors session ; rôles et CSRF.
- Import audio privé et détection des doublons.
- Consultation temporaire sans attribution définitive ; brouillon et libération.
- Soumission, retour pour correction, approbation et exports restreints.
- Import ZIP, administration des comptes, audio HTTP Range.
- Concurrence entre contributeurs sans doublon et interdiction de s'auto-évaluer.
- **Rejet avec motif → notification interne → ouverture → correction → nouvelle soumission → approbation.**
- Libération par administrateur et expiration des consultations inutilisées.
- Signalement et décision « inaudible », exclus de l'export final.
- Simulation SMTP sécurisée (pas de message envoyé sur Internet pendant les tests).
- Migration d'un ancien brouillon v1.0 conservant ses deux textes.

**Non vérifié sur votre poste Windows :** double-clic des scripts .bat, réseau SMTP réel et disponibilité d'un serveur Internet public. Une validation manuelle sur Windows avec un compte de chaque rôle reste nécessaire.
