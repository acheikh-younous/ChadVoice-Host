# ChadVoice — hébergement et évolution vers un service public

## Stack réelle v1.1
- Python 3.10+ : logique métier
- FastAPI + Uvicorn : routes HTTP et serveur ASGI
- SQLite : base locale persistante sous `private_data/chadvoice.sqlite3`
- Jinja2, HTML, CSS, JavaScript : interface sans React, sans CDN
- `private_data/audio` : audios jamais placés dans `static`
- SMTP optionnel : uniquement motifs des décisions, sans audios

## Pourquoi Cloudflare Pages seul ne suffit pas
Cloudflare Pages héberge des sites statiques et des fonctions compatibles avec sa plateforme ; le serveur FastAPI/SQLite fourni n’y tourne pas directement. La configuration React + Supabase discutée dans le passé correspondait à une autre architecture et **n'est pas le code de cette version**.

## Pour une campagne de 2 à 3 mois avec contributeurs distants
La voie la plus simple pour **le code actuel** est un hébergement Linux ayant des disques persistants et HTTPS :
1. Souscrire un VPS ou une plateforme proposant un volume durable adapté à vos audios et à la base. Estimer volume réel, débit et nombre de connexions.
2. Installer Python 3.11+, créer un utilisateur système non administrateur et un environnement virtuel.
3. Copier uniquement le code, installer `pip install -r requirements.txt`, puis transférer `private_data` de façon **privée**.
4. Définir `CHADVOICE_DATA_DIR` sur le disque persistant, accessible uniquement au processus serveur.
5. Placer FastAPI/Uvicorn derrière Caddy ou Nginx avec HTTPS, cookies sécurisés (`CHADVOICE_HTTPS=1`) et gestion de domaine. Configurer un service de supervision et des sauvegardes automatiques chiffrées hors site.
6. Ajouter avant ouverture publique : limitation des tentatives de connexion, journalisation d'accès, vérification des droits de diffusion et du consentement, surveillance, rotation des clés/session si besoin, tests de charge et politique de confidentialité.
7. Lancer des comptes de test, vérifier les exports et des restaurations de sauvegarde, puis seulement importer votre corpus autorisé.

**SQLite** : recommandé pour cette campagne locale ; pour plusieurs contributeurs simultanés, étudier PostgreSQL et le stockage objet privé après validation locale. La migration de modèle de données est un travail technique supplémentaire ; il n'est pas réalisée dans v1.1.

## Gratuit ou payant ?
Un site statique peut être gratuit, mais une application Python avec base et fichiers persistants ne bénéficie pas automatiquement du même hébergement. Les services gratuits « à filesystem éphémère » ne conviennent **pas** à ChadVoice parce que les données disparaissent au redémarrage. Comparez le coût réel d'un VPS petit format ou des volumes persistants avant de choisir. Ne mettez pas `private_data` dans un dépôt Git.

## Mails
Les notifications **dans l'application fonctionnent sans serveur d'e-mail**. Pour envoyer de vrais e-mails, il faut un compte SMTP autorisé, une connexion Internet et la configuration de `EMAIL_SETTINGS.bat` sur Windows ou des variables `CHADVOICE_SMTP_HOST`, `CHADVOICE_SMTP_PORT`, `CHADVOICE_SMTP_FROM`, `CHADVOICE_SMTP_USER`, `CHADVOICE_SMTP_PASSWORD` sous Linux. Conserver ces secrets hors du code et du dépôt. Un échec d'envoi n'efface pas la notification interne.
