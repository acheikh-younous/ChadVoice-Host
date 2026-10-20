# Tests locaux isolés

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
DATABASE_URL= CHADVOICE_AUDIO_STORAGE=local .venv/bin/python -m pytest -q
```

Si Chromium est déjà installé, éviter le téléchargement et sélectionner son
exécutable : `CHADVOICE_TEST_BROWSER=/usr/bin/chromium`.

Les tests applicatifs utilisent SQLite dans un répertoire temporaire et des
échantillons audio synthétiques. Ne jamais les lancer avec une connexion Neon.
Les adaptateurs PostgreSQL et S3 sont simulés ; aucune validation sur les services
cloud réels n'est effectuée. Les tests navigateur servent des templates rendus
avec un utilisateur fictif, sans base de données. Ils vérifient le chargement du
PNG, les dimensions, la visibilité et l'absence de débordement aux largeurs
320, 390, 800, 801 et 1440 pixels, sur la connexion et l'interface authentifiée.
