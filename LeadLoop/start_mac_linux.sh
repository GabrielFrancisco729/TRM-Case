#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -f .venv/bin/python ]; then python3 -m venv .venv; fi
source .venv/bin/activate
if ! python -c 'import fastapi,uvicorn,pandas,numpy,requests,dotenv' >/dev/null 2>&1; then
  python -m pip install -r requirements.txt
fi
echo 'Open http://127.0.0.1:7860 in a browser. Default manager demo PIN: quillon-demo'
python server.py
