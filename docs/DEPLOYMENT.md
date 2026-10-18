# Scar.lab deployment record

## Installation

- Started 2026-10-18, America/New_York.
- Stable upstream Strata v0.1.45, commit `477ac13cbe8e582c20484871ce459d20d82df09d`.
- Source: https://github.com/Niko1221/Strata/releases/tag/v0.1.45
- Native HIP build for gfx1201, using existing ROCm 7.2.2.
- Runtime `/opt/strata`, model and generated data `/nvme/Strata-data`.
- Installer command: `bash scripts/setup-strata.sh`. Installer uses `--no-start`; the generated configuration must pass `scripts/check_config.py` before launch.
- The compiled engine reports `strata 0.1.45`; build metadata retained in `/opt/strata/engine/BUILD.json`.
- Model repository: `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF`; pinned revision `ed59f92082b1e93c0e96d60a8b11aab089b52f09`. Shard metadata and SHA-256 are captured in `/nvme/Strata-data/model-manifest.json`.

## Configuration decisions

- Setup CLI lacks `xhigh` as a saved default: saved `reasoning_effort=high` maps to the template's `xhigh`. Requests may explicitly send `xhigh`. This was verified in `serve/frontend.py` and the pinned GGUF chat-template metadata.
- Preserved thinking is the shipped chat template default: `preserve_thinking is undefined or preserve_thinking is true`. The server retains assistant `reasoning_content` in multi-turn history. No unsupported config flag is added.
- Loop recovery is enabled. It may raise presence penalty during one recovery attempt; ordinary requests retain the requested penalty of zero.
- `STRATA_HC_Q8=0` preserves BF16 hyper-connection projections. RDNA4 otherwise quantizes these at load and reports slightly changed answers; the first load was stopped before validation to disable it. Native Q5_K output-head weights are read from the original model shard rather than newly quantized.
- Route-tail skip is explicitly disabled despite RDNA4 runtime defaults. Probabilistic, coupled and Gumbel speculative variants are disabled. Normal exact-match MTP stays at depth 4.
- PLE is explicitly `direct`, avoiding an automatic I/O-mode substitution.
- No driver changes, swap removal or memory-speed changes.

## Rollback

Run `bash scripts/rollback.sh` from this repository. It disables/stops Strata and starts the preserved `radlight-vllm` container. Check `curl -fsS http://127.0.0.1:8080/v1/models` and make a generation request before using it.

The old Docker restart policy is `unless-stopped`; stopping the container preserves its configuration, image, mounts and model files. The original compose file is `/home/cpaquin/Workspace/Git/Radiance-vLLM-AMD-R9700/compose.radlight.yaml`. Its model alias is `scar-coder`; target model is `/var/lib/radiance-vllm/hf-cache/radlight-models/Qwen3.8-27B-Quark-AWQ-MXFP4`, draft model is the adjacent `Qwen3.8-27B-DFlash2-FP8`. Full inventory: `rollback-inventory.json`.

## Validation status

Operational since 2026-10-18 15:21 EDT. `strata.service` is enabled. Live generation, authentication enforcement and a tool-call/result round trip passed. The API is `http://scar.lab:8080/v1`, bound to `10.1.10.10:8080`; alias `scar-coder`. Clients must use the key stored at `/home/cpaquin/.config/strata/api-key` (0600). The previous backend used the same port without this new key requirement.

The runtime loaded the complete 46.84 GiB expert arena in RAM and filled 24.87 GiB of VRAM with 13103 cached expert slots. INT8 KV uses 3.09 GiB pinned host memory with 32768 of 262144 cells resident per QSA layer. The configured reserve is 700 MiB; startup reports 562 MiB free after all buffers, with roughly 405 MiB observed free during short inference and 302 MiB in the long-context sample. The process RSS was about 51.5 GiB; process VmSwap was zero. Host MemAvailable was about 70 GiB. Host swap contained about 308 KiB of other pages after loading, with zero swap-in/out in the inference sample. Swap remains enabled.

The PLE direct-I/O probe measured about 98,700 rows/sec. Short decode measured 68.5, 77.3 and 88.2 tokens/sec, versus a vLLM median of 122.4. This is a material speed regression; the performance acceptance gate is open. No quality-reducing setting was enabled to hide that gap.

Controlled validation is complete: Chat Completions, tool-result continuation, Responses API, the five-test executable coding task, and marker retrieval at 31,934 / 127,946 / 257,951 prompt tokens passed. The coding task took 39.69 seconds versus vLLM's 23.34 seconds, with nine versus eight tool calls. Strata's repeated run_tests call was the expected pre/post-edit check. Diagnostic review found mixed answers; see `benchmarks/ASSESSMENT.md`.

| Prompt tokens | Strata TTFT | Fresh prompt tokens/sec | Cached tokens |
| --- | --- | --- | --- |
| 31,934 | 17.45 s | 1836.9 | 0 |
| 127,946 | 64.07 s | 1744.9 | 16,384 |
| 257,951 | 93.73 s | 1532.6 | 114,688 |

The long-context sample had about 69 GiB host MemAvailable, zero sampled swap-in/out and about 52.5 GiB model RSS. Both Python server and native model reported VmSwap=0. Expert-cache decode hit rates ranged from roughly 80% to 98% across these probes; the near-256K marker request reported 90.1%. Expert data stays in RAM; logs show the arena path and no mmap/budget mode.

The final service start at 15:20:32 remained on the same PID with zero automatic restarts through the checks ending around 15:30 EDT. No kernel messages were recorded after that final start. The earlier interrupted first load emitted three amdgpu SVM workqueue CPU-hog warnings; no GPU reset, OOM or HIP fault was observed. DIMMs report DDR4-3200, but this is not an extended memory stress test.

Full quality improvement and sustained stability are not yet established. The speed gate is not met. Keep Strata as the requested quality baseline and retain vLLM for comparison/rollback; evaluate real engineering work before declaring the entire goals.md definition of done met. Raw observations are in `benchmarks/`; see their measurement caveats before comparing timings.

## Slack notification

The existing Hermes Slack bot is configured for `#hermes-local` (`C0BQCSN2N67`); destination verified through `conversations.info`. Credentials remain in `/home/cpaquin/.hermes/.env` and are never copied into this repository. The readiness notification was sent after successful generation and a tool-call/result round trip. Delivery receipt: `slack-readiness-receipt.json`; message text: `slack-readiness-message.txt`. It explicitly states the speed regression and validation that was pending at that time. The subsequent thread reply reports the completed controlled checks and remaining acceptance limits; see `slack-validation-message.txt` and `slack-validation-receipt.json`.
