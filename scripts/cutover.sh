#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/check_config.py "${STRATA_CONFIG:-/opt/strata/strata-unsloth-ud-q4_k_xl.json}"
sudo install -m 0644 config/strata.service /etc/systemd/system/strata.service
sudo systemctl daemon-reload
restore() {
  echo 'Strata failed its readiness check; restoring the preserved vLLM container.' >&2
  sudo systemctl disable --now strata.service
  docker start radlight-vllm
}
docker stop --timeout 120 radlight-vllm
trap restore ERR
docker stop --timeout 30 hermes hermes-dashboard
sudo systemctl enable --now strata.service
python3 - <<'PY'
import json
import time
import urllib.request
from pathlib import Path
key=Path('/home/cpaquin/.config/strata/api-key').read_text().strip()
for attempt in range(180):
    try:
        req=urllib.request.Request('http://10.1.10.10:8080/health',headers={'Authorization':'Bearer '+key})
        data=json.load(urllib.request.urlopen(req,timeout=5))
        if data.get('loaded') and data.get('status')=='ok':
            print(json.dumps(data,indent=2))
            break
    except Exception:
        pass
    time.sleep(5)
else:
    raise SystemExit('Model did not become ready within 15 minutes')
PY
trap - ERR
echo 'Health check passed. Verify actual generation before declaring readiness.'
