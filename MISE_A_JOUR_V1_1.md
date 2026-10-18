# ChadVoice v1.1 — mise à jour depuis v1.0 (Windows)

## SÉCURITÉ : ne perdez pas votre corpus
1. **Arrêtez** `DEMARRER_WINDOWS.bat` (Ctrl+C) ; aucun contributeur ne doit travailler pendant la migration.
2. Dans votre ancienne application, faites **Administration → Sauvegarde intégrale**. Copiez aussi votre dossier `private_data` dans un disque de sauvegarde sécurisé, serveur arrêté.
3. Décompressez la **v1.1 dans un nouveau dossier**. Ne remplacez pas les fichiers audio, la base ni les comptes.
4. **Copiez le dossier `private_data` entier** depuis l'ancien dossier `ChadVoice_Local_v1` vers le nouveau dossier contenant `app.py`. Conservez l'ancien dossier intact jusqu'à validation.
5. Exécutez `INSTALLER_WINDOWS.bat`, puis `DEMARRER_WINDOWS.bat`. **Ne lancez pas `CREER_ADMIN_WINDOWS.bat`** : votre administrateur et les contributeurs existent déjà dans la base copiée.
6. Ouvrez http://127.0.0.1:8000 et connectez-vous avec les anciens comptes. Les tables nécessaires à v1.1 sont créées automatiquement à la première ouverture.
7. Testez un segment de démonstration, une correction de rejet et une notification, puis faites une nouvelle sauvegarde.

## Comportements v1.1
- Un clic sur « Obtenir un segment » **crée seulement une consultation provisoire** de 30 minutes. Le segment reste au statut « Disponible » et son champ `assigned_to` est vide.
- Le brouillon prolonge la consultation à 48 h. Si vous ne contribuez pas, la consultation expire et sera libérée automatiquement lors d'une prochaine activité. Les versions de brouillon restent dans l'historique ; le brouillon courant expiré est retiré.
- **L'attribution définitive n'intervient qu'au clic sur « Soumettre à révision »** : statut `submitted`, contributeur associé et réservation libérée.
- Le contributeur peut libérer lui-même sa consultation. L'administrateur peut libérer les consultations depuis Administration, pour éviter les blocages.
- Les décisions « Rejeter » et « Demander correction » sont **toutes deux corrigeables** : le contributeur voit les motifs et peut resoumettre.
- L'option « Inaudible » marque un signalement à confirmer par le réviseur. Si confirmé, le segment est clôturé avec `inaudible` et n'entre pas dans les exports validés.
- Une notification interne est créée pour chaque décision. Elle est conservée dans la base et visible depuis le menu Notifications.
- **E-mails non envoyés par défaut.** Pour activer SMTP, copier `EMAIL_SETTINGS.example.bat` en `EMAIL_SETTINGS.bat`, modifier les paramètres et redémarrer le serveur. Ne jamais transmettre ce fichier ni le commiter. La connexion Internet et des identifiants SMTP valides sont nécessaires. Un échec est enregistré dans `mail_outbox` et la notification reste dans l'application.
- Pour tester sans e-mail : un réviseur rejette un segment avec un motif, puis le contributeur ouvre `Notifications` et choisit `Lire / ouvrir`.

## Technologies et hébergement
Application Python / FastAPI, base SQLite, HTML Jinja2, CSS et JavaScript natif, serveur ASGI Uvicorn. **Ce n'est pas une application React** dans cette version locale.

Pour un test local, conserver `127.0.0.1:8000` (ne jamais exposer directement Uvicorn sur Internet). Pour une mise en ligne, prévoir au minimum un serveur HTTPS avec proxy inverse, stockage privé persistant, une base de données et des sauvegardes automatiques. **Cloudflare Pages seul ne peut pas héberger cette application Python/SQLite**. Pour un site multi-utilisateurs durable, une migration vers PostgreSQL est recommandée. Ne pas déployer les audios avant de vérifier les droits de diffusion et le consentement.
