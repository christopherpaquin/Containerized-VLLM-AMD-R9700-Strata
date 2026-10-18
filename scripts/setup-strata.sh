#!/usr/bin/env bash
set -euo pipefail
cd /opt/strata
exec ./setup.sh --yes --backend hip --family qwen --model IQ3_S \
  --context 262144 --rope-scaling none --kv int8 --vision no \
  --data-dir /nvme/Strata-data --vram-reserve-mib 700 --low-ram off \
  --parallel 1 --kv-streaming on --experimental-speed-projection off \
  --host 127.0.0.1 --port 8080 --no-browser --no-start
