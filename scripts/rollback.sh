#!/usr/bin/env bash
set -euo pipefail
sudo systemctl disable --now strata.service
docker start radlight-vllm
echo 'Restored radlight-vllm; check http://127.0.0.1:8080/v1/models before use.'
