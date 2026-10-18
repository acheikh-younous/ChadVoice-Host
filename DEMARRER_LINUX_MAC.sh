#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
printf 'Ouvrez http://127.0.0.1:8000\n'
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
