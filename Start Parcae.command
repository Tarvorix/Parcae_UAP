#!/bin/zsh
set -eu
cd -- "$(dirname -- "$0")"
export PYTHONPATH="$PWD/backend/src"
export UAP_DATA_DIR="$PWD/artifacts/g100-commissioning"
if [[ ! -x backend/.venv/bin/python || ! -f frontend/dist/index.html ]]; then
  print 'Setup is incomplete. Open this project in Codex to finish setup.'
  exit 1
fi
if [[ ! -f artifacts/fixtures/sky.mkv ]]; then
  backend/.venv/bin/python scripts/create_test_videos.py
fi
print 'Parcae local review: http://127.0.0.1:8000'
print 'Press Ctrl-C here to stop. Camera runs stop automatically after ten minutes.'
open 'http://127.0.0.1:8000'
exec backend/.venv/bin/python -m uvicorn uap_observer.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
