# Scar.lab Strata deployment

Quality-first deployment of Qwen3.8-Flash-Next on an AMD Radeon AI PRO R9700 (`UD-Q4_K_XL` currently active; `GSQ-RCO IQ3_S` retained on `/nvme/Strata-data` for A/B comparison). Strata runs as a native HIP systemd service; the existing vLLM container is retained for rollback.

- [Requested goals](goals.md)
- [Project plan](PROJECT_PLAN.md)
- [Deployment record and rollback](docs/DEPLOYMENT.md)
- [Measurement caveats](benchmarks/NOTES.md)
- [IQ3_S controlled benchmark](benchmarks/20261018-1647-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S.md)
- [UD-Q4_K_XL controlled benchmark](benchmarks/20261018-1757-Qwen3.8-Flash-Next-UD-Q4_K_XL.md)

## Operations

```bash
sudo systemctl status strata
sudo journalctl -u strata -f
```

The intended API is `http://scar.lab:8080/v1`, bound to lab address `10.1.10.10`, with model alias `scar-coder`. The API key is stored at `/home/cpaquin/.config/strata/api-key` with mode 0600. Configure clients to read that key locally; do not commit it.

```bash
export STRATA_API_KEY="$(cat /home/cpaquin/.config/strata/api-key)"
curl -fsS http://scar.lab:8080/v1/models \
  -H "Authorization: Bearer $STRATA_API_KEY"
```

To restore the old backend:

```bash
bash scripts/rollback.sh
```

## Reproduce installation and checks

Install the stable release recorded in `docs/strata-release.json` under `/opt/strata`. Use the existing ROCm installation. Then (defaulting to `UD-Q4_K_XL`, or set `STRATA_MODEL_PROFILE=iq3_s` for the `GSQ-RCO IQ3_S` candidate):

```bash
bash scripts/setup-strata.sh
python3 scripts/configure.py
python3 scripts/check_config.py /opt/strata/strata-unsloth-ud-q4_k_xl.json
sudo install -m 0644 config/strata.service /etc/systemd/system/strata.service
sudo systemctl daemon-reload
```

Review the generated configuration before launch. Stop `radlight-vllm` before starting Strata. Setup explicitly avoids automatic startup.

Live checks use one request at a time:

```bash
python3 scripts/benchmark.py --url http://scar.lab:8080 --repeat 3 \
  --output benchmarks/strata-short.json
python3 scripts/validate_api.py --url http://scar.lab:8080 \
  --output benchmarks/strata-engineering.json
python3 scripts/agent_eval.py --url http://scar.lab:8080 \
  --output benchmarks/strata-agent.json
python3 scripts/check_responses.py --output benchmarks/strata-responses.json
python3 scripts/benchmark.py --url http://scar.lab:8080 \
  --prompt-file benchmarks/inputs/context-256k.txt.gz --max-tokens 64 \
  --output benchmarks/strata-256k.json
bash scripts/resource_snapshot.sh
```

The coding trial runs generated code in a container with no network, a read-only filesystem and a read-only fixture mount. It uses the preserved vLLM image as a Python runtime, without GPU devices. Diagnostic replies are saved for review; recording an answer does not automatically establish correctness.

Do not declare all goals met from API health alone. Long-context retrieval, sustained stability, comparative performance and real engineering completion are separate acceptance checks.

## Reasoning comparison and dashboard

[Reasoning comparison](benchmarks/REASONING_COMPARISON.md) records the medium versus xhigh trial: medium reduced total completion time by 36% across 18 cases, with 17/18 correct versus 18/18. This was not a TTFT comparison. The default remains xhigh (`high` in Strata's configuration).

The dashboard at http://scar.lab:8088/ now supports native Strata discovery and authenticated streaming benchmarks. Its implementation and deployment overlay are in the sibling `vLLM-Management-Portal` repository. The service exports a whitelist of public settings with `scripts/export_dashboard.py`; the portal receives a separate read-only API key file, never the private engine configuration. Browser validation passed, including a persisted benchmark at 381 ms TTFT and 71.4 output tokens/s.
