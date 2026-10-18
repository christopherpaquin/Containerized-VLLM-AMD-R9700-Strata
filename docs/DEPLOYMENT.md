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

The runtime loaded the complete 46.84 GiB expert arena in RAM and filled 24.73 GiB of VRAM with 13032 cached expert slots (adjusted from 13103 slots / 24.87 GiB when increasing `--vram-reserve-mib` from 700 to 832 MiB to keep free VRAM above 512 MiB during graph capture and inference). INT8 KV uses 3.09 GiB pinned host memory with 32768 of 262144 cells resident per QSA layer. With the 832 MiB reserve, startup reports 692 MiB free after all buffers (644 MiB after capturing verification windows), compared to 562 MiB at startup and 302–405 MiB under the initial 700 MiB reserve. The process RSS was about 51.5 GiB; process VmSwap was zero. Host MemAvailable was about 70 GiB. Host swap contained about 308 KiB of other pages after loading, with zero swap-in/out in the inference sample. Swap remains enabled.

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

## A/B Model Comparison: `UD-Q4_K_XL` vs `GSQ-RCO IQ3_S`

- On 2026-10-18, `unsloth/Qwen3.8-Flash-Next-GGUF` (`UD-Q4_K_XL`, revision `38bb39ee97821de2c9009abb7e93950eec396e66`, 4 shards totaling `103.688 GiB`) was downloaded to `/nvme/Strata-data/models/unsloth-UD-Q4_K_XL` (manifest: `docs/model-manifest-ud-q4_k_xl.json`), converted to `/nvme/Strata-data/packs/unsloth-ud-q4_k_xl` (`72.42 GiB`), and configured in `/opt/strata/strata-unsloth-ud-q4_k_xl.json` with `--vram-reserve-mib 832` (example config: `config/strata-unsloth-ud-q4_k_xl.example.json`).
- Both models (`unsloth-ud-q4_k_xl` and `iq3_s`) remain preserved on `/nvme/Strata-data` (`256 GiB` of `477 GiB` used, `57%`) and can be selected via `STRATA_MODEL_PROFILE=ud-q4_k_xl` (default) or `STRATA_MODEL_PROFILE=iq3_s` across `scripts/setup-strata.sh`, `scripts/configure.py`, `scripts/check_config.py`, and `scripts/export_dashboard.py`.
- Under `UD-Q4_K_XL`, the full `71.73 GiB` expert arena stays 100% resident in system RAM (`0` file-paged blobs, `0` inference NVMe disk reads, `0` native-engine swap). Peak Strata RSS is `77.270 GiB` (vs `52.096 GiB` on `IQ3_S`), leaving `44.778 GiB` minimum host `MemAvailable`. With `--vram-reserve-mib 832`, the GPU expert cache holds `8,131 / 24,576` expert slots (`33.1%`, `23.73 GiB` vs `13,103` slots / `53.3%` on `IQ3_S`) and maintains `520 MiB` minimum free VRAM across all tests (fixing the `<512 MiB` free-VRAM dip observed at `700 MiB` reserve).
- Controlled 3-test benchmark comparison (`benchmarks/20261018-1757-Qwen3.8-Flash-Next-UD-Q4_K_XL.md` vs `benchmarks/20261018-1647-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S.md`):
  - **Reasoning convergence**: `UD-Q4_K_XL` eliminated the Python coding reasoning loop (`0` loop recoveries, `coverage=0.158` vs `1` loop recovery at `9,728` tokens, `coverage=0.290` on `IQ3_S`) and passed all `4/4` container execution tests.
  - **Systems accuracy**: Approximately equal (`Mixed`)—`UD-Q4_K_XL` fixed `217/USER` and `216/GROUP` and used safer read-only SELinux/probe recommendations, but hallucinated `status=214/PATH` (actual: `SETSCHEDULER`), `status=211/START_LIMIT_HIT` (actual: `IOPRIO`), `-p DropPaths` (actual: `DropInPaths`), and `systemctl show foo.service -p ExecStart --value | awk '{print $1}'` (which prints `{`).
  - **Throughput**: Average decode throughput decreased by `32.9%` (`45.80 tok/s` vs `68.27 tok/s`), and average task time increased by `69.2%` (`412.30 s` vs `243.60 s`).
- Follow-up `UD-Q4_K_XL` reasoning-budget evaluation at `reasoning_effort = xhigh` (`benchmarks/20261018-1846-Qwen3.8-Flash-Next-UD-Q4_K_XL-xhigh-12K.md` and `benchmarks/20261018-1905-Qwen3.8-Flash-Next-UD-Q4_K_XL-xhigh-8K.md`):
  - Both `12288` (`12K`) and `8192` (`8K`) preserved `0/3` reasoning-loop recoveries and reduced total 3-test benchmark time from `1,236.89 s` (`16K`) to `1,089.60 s` (`12K`, `-11.9%`) and `822.07 s` (`8K`, `-33.5%`).
  - However, truncating `xhigh` reasoning at `12K` or `8K` degraded executable Python coding correctness from `4/4` (`16K`) to `1/4` (`12K`: used `st.st_size >= min_size` instead of `>` and left external sort as a `NotImplementedError` stub) and `2/4` (`8K`: omitted external sort and forgot to seed `root` into `visited_dirs` when following directory symlinks). The default `reasoning_budget_tokens` remains `16384`.
