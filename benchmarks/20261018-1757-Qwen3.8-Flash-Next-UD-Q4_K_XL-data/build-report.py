import json, hashlib, re, statistics, shutil
from pathlib import Path
from datetime import datetime

root = Path('/nvme/Strata-data/benchmarks/20261018-1757-Qwen3.8-Flash-Next-UD-Q4_K_XL-data')
iq3_root = Path('/nvme/Strata-data/benchmarks/20261018-1647-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-data')

assessment_text = """### Systemd troubleshooting behavior (Test A)

The response frames the problem accurately as a difference-of-context investigation between an interactive root login shell and systemd's spawned execution environment, and explicitly instructs the operator not to edit the unit file, change `User=`, add environment variables, or disable SELinux before collecting evidence. It systematically covers initial status and journal capture (including prior boots and start-limit windows), effective unit and drop-in inspection (`systemctl cat`, `systemctl show`), manual-vs-systemd execution comparison, user/group and capability checks, environment inspection, working directory verification, path/mount/permission traversal (`namei -l`, `findmnt`, `ldd`), sandboxing directives (`ProtectSystem=`, `ProtectHome=`, `PrivateTmp=`, `PrivateDevices=`, `NoNewPrivileges=`), SELinux context/AVC inspection, unit dependency/mount ordering, and non-destructive transient probes.

Compared with the `IQ3_S` baseline, `UD-Q4_K_XL` improves several operational safety details:
- It corrects `IQ3_S`'s mislabeled exit code `201`, accurately listing `status=203/EXEC`, `status=200/CHDIR`, `status=217/USER`, and `status=216/GROUP` (verified against `systemd-analyze exit-status`).
- It avoids `IQ3_S`'s global `setenforce 0`/`setenforce 1` experiment, explicitly warning against disabling SELinux or running `restorecon` before capturing evidence, and using read-only `restorecon -n -v` dry runs.
- Its first `systemd-run` transient probe executes `/usr/bin/env` rather than the service binary, and its later `systemd-run`/`strace` examples explicitly pass `--dry-run` with a safety caveat.
- Its final answer is more compact (`25,308` characters vs `30,532` in `IQ3_S`), with one main 12-step diagnostic progression and a consolidated pre-change evidence package in section 12.

However, `UD-Q4_K_XL` introduces its own systemd-specific technical inaccuracies that require operator review:
- In the exit-code table (section 1), it invents `status=214/PATH` and `status=211/START_LIMIT_HIT`. On RHEL/systemd (verified via `systemd-analyze exit-status 203 200 217 216 214 211`), exit code `214` is `SETSCHEDULER` and `211` is `IOPRIO`; start-limit exhaustion is reported as unit `Result=start-limit-hit`, not process exit code `211`.
- In sections 7, 8, and 12, it repeatedly extracts the executable path using `EXE="$(systemctl show foo.service -p ExecStart --value | awk '{print $1}')"`. Because `ExecStart` is a structured property (`{ path=/path/to/bin ; argv[]=... }`), `awk '{print $1}'` yields `{` rather than the binary path.
- In sections 2 and 12, it queries `systemctl show foo.service -p FragmentPath -p DropPaths`; the actual systemd property name is `DropInPaths` (`DropPaths` returns an empty line).
- In section 5, `--property=EnvironmentFile="$(systemctl show foo.service -p EnvironmentFiles --value)"` passes the ` (ignore_errors=...)` suffix emitted by `systemctl show -p EnvironmentFiles --value`, which is not a valid path for `systemd-run`.
- While reasoning did not trigger loop recovery (`reasoning_recoveries=0`, `reasoning_repeat_coverage=0.013`), internal thinking consumed the full **16,384-token thinking budget** (`Oct 18 18:04:04: thinking budget reached (16384 tokens): wrapping up the thinking`) before generating `6,570` final answer tokens (`22,972` total output tokens vs `16,282` in `IQ3_S`).

### Coding reasoning and executable validation (Test B)

The response diagnoses all three failure modes before writing code: unhandled `OSError`/`PermissionError` on `os.scandir()`/`stat()` calls (including mid-iteration directory errors on network/FUSE mounts or concurrent removals), symlink cycles from `os.walk(..., followlinks=True)`, `os.path.isdir()`, or `Path.rglob()`, and excessive memory growth from materializing full-tree `Path` lists or sorting prior to size filtering. It provides concrete reproduction scripts, `tracemalloc` and `strace` recipes, a clean core generator (`iter_large_files`) and sorted list function (`files_larger_than_gib`), a `pytest` test suite, a bounded `heapq.nlargest` top-N helper (`top_large_files`), and a chunked external merge-sort generator (`iter_sorted_large_files_externally`).

The generated implementation (`coding-block-4.py`, `coding-block-15.py`, `coding-block-16.py`) was assembled without modifying any function logic (`generated-scanner.py`) and tested in the same isolated, network-disabled, read-only container fixture (`UID 65534`, 32 MiB `/tmp` tmpfs) used for `IQ3_S`. All **4/4 executable checks passed** (`coding-execution.txt`):
1. `sorted_strict_threshold_regular_only`: strictly `> 1 GiB` (`1024**3` bytes), excludes exact `1 GiB`, small files, FIFOs, and broken symlinks, and returns paths sorted largest-to-smallest (`["big", "medium"]`).
2. `optional_follow_symlinks_cycle_guard`: with `include_symlinked_files=True`, includes symlinks whose target is a regular file while still refusing to traverse symlinked directories (`sub/loop -> root`), preventing directory symlink loops without needing an unbounded visited-inode set.
3. `external_sort_forced_multiple_chunks`: `iter_sorted_large_files_externally(..., max_buffered=1)` flushes multiple sorted pickle chunk files, merges them lazily via `heapq.merge`, and cleans up temporary files in `finally`.
4. `unreadable_directory_reports_and_skips`: catches `PermissionError` on `chmod 000` directory via `on_error(path, exc)` and continues scanning without crashing.

Crucially, **unlike `IQ3_S` (which got stuck in a reasoning loop at 9,728 tokens with `coverage=0.290` and required `reasoning_loop_recovery`), `UD-Q4_K_XL` did NOT trigger reasoning-loop recovery (`reasoning_recoveries=0`, `reasoning_repeat_coverage=0.158`)**. However, `UD-Q4_K_XL` still deliberated until it hit the **16,384-token thinking budget** (`Oct 18 18:12:31: thinking budget reached (16384 tokens): wrapping up the thinking`), then cleanly wrapped up and emitted `4,286` final tokens (`20,688` total generated tokens). Static caveats remain similar to `IQ3_S`: `iter_sorted_large_files_externally` opens all chunk files simultaneously during `heapq.merge` without a bounded multi-pass merge fan-in, and its `heapq.merge(..., key=lambda item: -item[0])` tie-breaks only on `-size` during the merge step (whereas chunk sorting used `(-item[0], item[1])`).

### Misleading firewall hypothesis / convergence (Test C)

The model immediately and explicitly rejects disabling `firewalld` as a first step, explaining that a blind firewall stop alters security posture, hides root causes, and fails to fix non-firewall issues. It prioritizes read-only evidence collection and systematically branches on observations:
- Distinguishes Unix-domain sockets (`/var/run/postgresql/.s.PGSQL.5432`) from TCP (`127.0.0.1:5432`, `::1:5432`, `localhost`), noting that Unix sockets bypass TCP/IP and `firewalld`/`nftables`/`iptables` entirely.
- Checks whether PostgreSQL is running and which addresses/ports it binds (`ss -ltnp | grep 5432`, `ss -xlnp | grep postgresql`, `listen_addresses` in `postgresql.conf`), explicitly covering IPv4 vs IPv6 `localhost` resolution mismatches (`127.0.0.1` vs `::1`).
- Differentiates `Connection refused` (no listener / immediate reject), `Connection timed out` (possible packet drop or routing/namespace issue), `Permission denied` (DAC/socket permissions, SELinux/AppArmor, or service sandboxing), and `FATAL: no pg_hba.conf entry` / `password authentication failed` (proves traffic reached PostgreSQL and rules out the firewall).
- Validates the firewall hypothesis non-destructively via `firewall-cmd --state`, rule listings (`firewall-cmd --list-all-zones`, `nft list ruleset`, `iptables -L -n -v`), packet counter deltas during a test probe, and loopback `tcpdump -i lo`.
- Covers `pg_hba.conf`, Unix socket directory permissions, SELinux (`ausearch -m avc`, `httpd_can_network_connect_db`), containers/network namespaces (`ip netns`), `max_connections`, and SSL modes.
- Even if packet counters and `tcpdump` prove a firewall drop, it recommends a narrow, time-bounded rule (`firewall-cmd --timeout=300 ...`) rather than disabling `firewalld`.

This test **converged cleanly without hitting the 16,384-token thinking budget and without loop recovery** (`11,899` total tokens generated, ~`7,420` reasoning tokens and ~`4,479` final tokens estimated from phase durations, `reasoning_recoveries=0`, `reasoning_repeat_coverage=0.018`). It is well-structured, less repetitive than `systemd`, and never loops back to endorsing the false premise.
"""
(root / 'assessment.md').write_text(assessment_text.strip() + '\n')

results = [json.loads((root / f'{n}.json').read_text()) for n in ['systemd', 'coding', 'recovery']]
iq3_summary = {x['test']: x for x in json.loads((iq3_root / 'comparison-summary.json').read_text())}
q4_summary = {x['test']: x for x in json.loads((root / 'comparison-summary.json').read_text())}
props = json.loads((root / 'props-before.json').read_text())
engine = json.loads((root / 'metrics-before.json').read_text())['engine']
cfg = json.loads((root / 'config.json').read_text())
samples = [s for r in results for s in r['samples']]
gib = lambda x: f'{x/2**30:.3f}'
kibg = lambda x: f'{x/2**20:.3f}'
text = []
def add(s): text.append(s.strip() + '\n')
def table(headers, rows):
    add('| ' + ' | '.join(headers) + ' |\n| ' + ' | '.join(['---']*len(headers)) + ' |\n' + '\n'.join('| ' + ' | '.join(str(x) for x in row) + ' |' for row in rows))
def proc_total(s, k): return sum(p.get(k, 0) for p in s['processes'].values())

obs_start = datetime.fromtimestamp(results[0]['start_unix']).astimezone().isoformat()
obs_end = datetime.fromtimestamp(results[-1]['start_unix'] + results[-1]['elapsed_s']).astimezone().isoformat()

add(f'''# scar.lab Strata runtime, reasoning, and performance benchmark — Qwen3.8-Flash-Next UD-Q4_K_XL

Observation date: {obs_start}. Host: scar.lab, user cpaquin. All times are seconds unless specified. GiB/MiB are binary units. This report concerns the running **Qwen3.8-Flash-Next UD-Q4_K_XL** deployment and its controlled A/B comparison against the **Qwen3.8-Flash-Next GSQ-RCO IQ3_S** baseline (`benchmarks/20261018-1647-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S.md`).

Three sequential, single-turn streaming requests used the exact supplied prompts from the `IQ3_S` baseline, one trial each, seed 42, max_tokens 24576. No system message, tool definitions, sampling overrides, reasoning overrides, tuning, replacement components, or context stress test was applied. All baseline configuration parameters were preserved identically except for one approved global safety change: **`--vram-reserve-mib` was increased from `700` to `832` MiB** because the `IQ3_S` benchmark showed that a 700 MiB reserve allowed free VRAM to dip below 512 MiB (to ~304–392 MiB) and triggered a Strata low-VRAM warning. Sampling seed does not guarantee deterministic results across quantizations/backends.

Telemetry sampled approximately every 0.5 seconds; short prefill phases can be missed. Existing loaded caches were retained after the initial readiness probe, not flushed. Completed test requests at {obs_end}. Snapshot files, public configuration, exact requests, answers, metrics, sampled resource data, and reproduction procedure are saved in `{root}`. Reasoning text was observed only for stream boundary timing and discarded; this report does not reproduce chain-of-thought.

Key findings: the requested **UD-Q4_K_XL** quality-first configuration is active with its full **71.73 GiB (`73,450 MiB`) expert arena resident in RAM**, zero NVMe expert paging (`file_blobs=0`, `file_mb=0`), and zero active swap-outs (`pswpout=0`, native engine `VmSwap=0 KiB`) with **44.778–45.339 GiB** of host RAM remaining available. With the **832 MiB VRAM reserve**, free VRAM stayed at **520–676 MiB** (`0.508–0.660 GiB`) and **no low-VRAM warning occurred**. All three requests finished normally (`finish_reason=stop`) at **44.6–46.5 decode tokens/s** (~34.2% slower decode than `IQ3_S`'s 65.9–70.2 tok/s). Crucially, **zero reasoning-loop recoveries occurred (`0/3` vs `1/3` in `IQ3_S`)**: the Python coding test that triggered repeated-reasoning loop recovery at 9,728 tokens on `IQ3_S` (`coverage=0.290`) stayed below the loop threshold on `UD-Q4_K_XL` (`coverage=0.158`, `recoveries=0`) and passed all **4/4 executable container checks**. However, `UD-Q4_K_XL` hit the **16,384-token thinking budget on 2 of 3 tests** (`systemd` and `coding`, vs `1 of 3` on `IQ3_S`), increasing total completion time across the three tasks by **69.2%** (`1236.89 s` vs `730.81 s`).''')

add('''## 1. Model identity

- Model: **Qwen3.8-Flash-Next UD-Q4_K_XL** (Unsloth Dynamic 4-bit quantization) from `unsloth/Qwen3.8-Flash-Next-GGUF`. GGUF header metadata identifies `general.quantized_by="Unsloth"`, `general.size_label="512x56B"`, `quantize.imatrix.file="Qwen3.8-Flash-Next-GGUF/imatrix_unsloth.gguf"`, and `quantize.imatrix.dataset="unsloth_calibration_Qwen3.8-Flash-Next.txt"`.
- API canonical name: `qwen3.8-flash-next-unsloth-ud-q4_k_xl`; alias `scar-coder`.
- Pinned Hugging Face revision: `38bb39ee97821de2c9009abb7e93950eec396e66` (`/nvme/Strata-data/model-manifest-ud-q4_k_xl.json` and `docs/model-manifest-ud-q4_k_xl.json`).
- Four GGUF shards under `/nvme/Strata-data/models/unsloth-UD-Q4_K_XL` (total **111,334,654,784 bytes = 103.688 GiB = 111.335 GB**), all verified against pinned LFS SHA-256 hashes before startup:
  - Shard 1: `Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf` — `10,946,624` bytes, SHA-256 `4448186216b3af4cc558bbce2c3213f01608f8f8b2e5267a9767971dd3ec8082` (GGUF metadata and tokenizer; 0 weight tensors).
  - Shard 2: `Qwen3.8-Flash-Next-UD-Q4_K_XL-00002-of-00004.gguf` — `49,859,583,136` bytes, SHA-256 `3f342f1c1580473f1ee94ddd5b28206e8c07a70fa1a366f59d1d6c922919a6c9` (contains `per_layer_token_embd.weight` PLE table plus layers 0–11 tensors).
  - Shard 3: `Qwen3.8-Flash-Next-UD-Q4_K_XL-00003-of-00004.gguf` — `49,376,141,504` bytes, SHA-256 `56758f40269cad5cd9b0d3d6fbae0f40f6d5be6de49e4ab392dbe83157d9cbd3` (layers 11–39 tensors; layer 11 splits `down` in shard 2 and `gate`/`up` in shard 3, recorded via `native_experts.txt` v4 per-role shard column).
  - Shard 4: `Qwen3.8-Flash-Next-UD-Q4_K_XL-00004-of-00004.gguf` — `12,087,983,520` bytes, SHA-256 `753bda48b98ba4f1636134a90a967de1b2d3908a236c026e464777342e53510a` (remaining layers and output head).
- Inspected GGUF tensor quantization mix across all four shards (`gguf-inspection.json`, 1,224 tensors total): `F32` (557), `Q8_0` (503), `Q4_K` (94), `Q5_1` (43), `BF16` (24), `Q5_K` (2), `IQ4_NL` (1, the PLE table). Token embedding is mapped `Q8_0` (`644 MiB` vs `322 MiB` in `IQ3_S`); output head is native `Q5_K` (`675,430,400` bytes vs `521,472,000` bytes in `IQ3_S`). Pack preparation (`tools/iq_pack.py --compat-bf16`) converted 195 `Q8_0` small/hyper-connection projection tensors (`1.20 GiB`) to BF16 in `/nvme/Strata-data/packs/unsloth-ud-q4_k_xl/dense.bin` (`max |err| 0.0144`) while leaving expert and PLE bytes untouched.
- API/engine version: **Strata 0.1.45** (unchanged from `IQ3_S`; tag `v0.1.45`, commit `477ac13cbe8e582c20484871ce459d20d82df09d`, build source fingerprint `b95137167bd9b751`).
- ROCm **7.2.2**, native HIP Release build, architecture `gfx1201`, runtime GPU **AMD Radeon AI PRO R9700**.
- MTP/speculation enabled: `--mtp /nvme/Strata-data/mtp/rt --spec 4 --spec-min-p 0.5` (`mtp_max=4`, verification window `spec=6`, lookup depth `lookup=3`).
- Vision disabled in configuration and API (`vision.enabled=false`, `modalities.vision=false`).''')

add('''## 2. Context and KV configuration

Configured maximum, `/props` `n_ctx`, engine `max_context`, and `/v1/status` `native`/`max_positions` all report **262144**. **INT8** KV is active; **32768** cells per QSA layer are resident in VRAM. KV streaming is active with **3.09 GiB pinned host RAM allocated at startup** for the full 262,144-token authoritative K/V cache (12 QSA layers × 32768 resident cells = 396 MiB resident payload; 262144 total cells = 3168 MiB = 3.09375 GiB).

Native context is active; no `--rope-scaling`, `--rope-scale`, or YaRN options are present. GGUF metadata reports `qwen4exp.context_length = 262144` and `qwen4exp.rope.freq_base = 10000000.0` with `dimension_sections = [11, 11, 10, 0]`. Context and KV settings are 100% identical to the `IQ3_S` baseline.''')

add('## 3. System RAM usage\n\nLoaded idle snapshot (`free -h`):\n\n```text\n' + (root / 'free-before.txt').read_text().strip() + '\n```')
add('''The host has 134,996,344,832 bytes (**125.725 GiB**) physical DDR4-3200 RAM on an Intel Core i7-8086K (6 cores/12 threads, AVX2). Startup loaded the complete **71.73 GiB** (`arena_mib = 73450`) `UD-Q4_K_XL` expert arena into RAM at `3.12 GiB/s`, registered and pinned via `cudaHostRegister PORTABLE ok` with transparent huge pages requested (`MADV_HUGEPAGE`). Neither `--mmap-experts` nor `--resident-budget-gib` is enabled; 100% of the expert pool is resident in system RAM. Mapped `Q8_0` token embeddings occupy 644 MiB of host memory.

Before inference, the native engine (`PID 446232`) had **RSS = 80,469,292 KiB (76.741 GiB)**, VSZ = 120,375,680 KiB (114.800 GiB), 61.0% of host RAM (`smaps_rollup`: `Pss_Anon = 80,390,284 KiB`, `AnonHugePages = 79,097,856 KiB` = 75.434 GiB, `Swap = 0 kB`). The Python server parent (`PID 446197`) had **RSS = 154,032 KiB (0.147 GiB)**, VSZ = 561,980 KiB (0.536 GiB). Summed idle Strata RSS was **76.888 GiB** (an increase of **+25.176 GiB** over `IQ3_S`'s `51.712 GiB` idle RSS).''')

add('Loaded process snapshot:\n\n```text\n' + '\n'.join(l for l in (root / 'processes-before.txt').read_text().splitlines() if l.strip().startswith(('PID', '446232 ', '446197 ')) or 'serve.server' in l or 'engine/strata' in l) + '\n```')

table(['Observed quantity', 'UD-Q4_K_XL value', 'IQ3_S baseline value'], [
    ['Expert arena loaded in RAM', '71.73 GiB (73,450 MiB)', '46.84 GiB (47,962 MiB)'],
    ['Idle summed Strata RSS', '76.888 GiB', '51.712 GiB'],
    ['Peak summed Strata parent + engine RSS', kibg(max(proc_total(s, 'VmRSS') for s in samples)) + ' GiB', '52.096 GiB'],
    ['Minimum host MemAvailable during tests', kibg(min(s['mem']['MemAvailable'] for s in samples)) + ' GiB', '70.316 GiB'],
    ['Peak whole-host used RAM (total − available)', kibg(max(samples[0]['mem']['MemTotal'] - s['mem']['MemAvailable'] for s in samples)) + ' GiB', '55.409 GiB'],
    ['Native engine VmSwap (min / max)', '0 / 0 KiB', '0 / 0 KiB'],
    ['Python parent VmSwap (before / after)', '25,272 / 20,548 KiB (0 pswpout)', '0 / 0 KiB']
])

add('''All experts remained 100% resident in RAM throughout the benchmark: `ram_blobs = 0`, `file_blobs = 0`, `file_mb = 0` across all three requests, with **44.778 GiB** minimum `MemAvailable` (~35.6% of total physical RAM still available for OS/cache/workloads).''')

add('## 4. GPU / VRAM usage')
base = json.loads((root / 'metrics-before.json').read_text())['hardware']
gpus = [s['gpu'] for s in samples if s['gpu'].get('mem_info_vram_used')]
table(['Quantity', 'UD-Q4_K_XL (832 MiB reserve)', 'IQ3_S (700 MiB reserve)'], [
    ['Total VRAM', gib(base['gpu_mem_total']) + ' GiB (' + str(base['gpu_mem_total']) + ' bytes)', '31.860 GiB (34208743424 bytes)'],
    ['Idle used / free before tests', gib(base['gpu_mem_used']) + ' / ' + gib(base['gpu_mem_total'] - base['gpu_mem_used']) + ' GiB', '31.464 / 0.396 GiB'],
    ['Peak sampled VRAM', gib(max(int(g['mem_info_vram_used']) for g in gpus)) + ' GiB', '31.563 GiB'],
    ['Minimum sampled free VRAM', gib(min(int(g['mem_info_vram_total']) - int(g['mem_info_vram_used']) for g in gpus)) + ' GiB (520 MiB)', '0.297 GiB (304 MiB)'],
    ['GPU utilization range', str(min(int(g['gpu_busy_percent']) for g in gpus)) + '–' + str(max(int(g['gpu_busy_percent']) for g in gpus)) + '%', '0–100%'],
    ['Peak edge / junction temperature', str(max(int(g.get('temp1_input', 0)) for g in gpus)/1000) + ' / ' + str(max(int(g.get('temp2_input', 0)) for g in gpus)/1000) + ' °C', '68.0 / 99.0 °C'],
    ['Peak sampled package power', str(max(int(g.get('power1_average', 0)) for g in gpus)/1e6) + ' W', '399.0 W'],
    ['Configured power cap', str(max(int(g.get('power1_cap', 0)) for g in gpus)/1e6) + ' W', '300.0 W'],
    ['Configured VRAM reserve', '832 MiB (676 MiB free after load, 520 MiB min)', '700 MiB (562 MiB free after load, 304 MiB min)'],
    ['Expert cache', '8131 slots, 24299 MiB (23.73 GiB), prefilled at startup', '13103 slots, 25465 MiB (24.87 GiB), prefilled at startup'],
    ['Cache policy', 'PROFILE, ranked by routing frequency, no eviction', 'PROFILE, ranked by routing frequency, no eviction']
])

for phase in ['reading the prompt', 'thinking', 'answering']:
    ss = [s for s in samples if (s.get('live') or {}).get('phase') == phase]
    if ss:
        add(f"Sampled phase `{phase}`: {len(ss)} samples; GPU utilization {min(int(s['gpu'].get('gpu_busy_percent', 0)) for s in ss)}–{max(int(s['gpu'].get('gpu_busy_percent', 0)) for s in ss)}%.")

rows = []
for r in results:
    ss = r['samples']; g = [x['gpu'] for x in ss]
    active = lambda k: [int(m.group(1)) for x in g for line in x.get(k, '').splitlines() if '*' in line for m in [re.search(r'(\d+)Mhz', line)] if m]
    sc = active('pp_dpm_sclk'); mc = active('pp_dpm_mclk'); pw = [int(x.get('power1_average', 0))/1e6 for x in g]; util = [int(x.get('gpu_busy_percent', 0)) for x in g]
    rows.append([r['name'], f'{min(sc)}/{statistics.median(sc):.0f}/{max(sc)}', f'{min(mc)}/{statistics.median(mc):.0f}/{max(mc)}', f'{min(pw):.0f}/{statistics.median(pw):.0f}/{max(pw):.0f}', f'{min(util)}/{statistics.median(util):.0f}/{max(util)}'])
table(['Test', 'Active sclk MHz min/median/max', 'Active mclk MHz min/median/max', 'Power W min/median/max', 'GPU use % min/median/max'], rows)

add('''Because `UD-Q4_K_XL` expert blobs are larger than `IQ3_S` blobs (up to `3.99 MB` per layer/expert pair vs `IQ3_S`) and native projection matrices occupy `2,975.00 MiB` plus a `275.7 MiB` draft head (plus the `832 MiB` VRAM reserve), the GPU expert cache holds **8,131 slots (23.73 GiB = 24,299 MiB)**, representing **33.1%** of the 24,576 total layer/expert pairs (compared with **13,103 slots = 53.3%** for `IQ3_S` at 700 MiB reserve, or 13,032 slots = 53.0% for `IQ3_S` at 832 MiB reserve). Prompt prefill borrows 2,665 cache slots (`7.79 GiB`). Raising `--vram-reserve-mib` from `700` to `832` MiB kept minimum sampled free VRAM at **520 MiB (`0.508 GiB`)**, completely eliminating the `<512 MiB` free-VRAM warning seen during `IQ3_S` testing.''')

add('## 5. NVMe / storage\n\n```text\n' + (root / 'df-before.txt').read_text().strip() + '\n```')
add('''Data root: `/nvme/Strata-data` on XFS (`/dev/nvme0n1p1`, 477 GiB capacity, 209 GiB used, **269 GiB available** with both `IQ3_S` and `UD-Q4_K_XL` installed side-by-side).
- `UD-Q4_K_XL` model shards: **111,334,654,784 bytes (103.688 GiB)** in `/nvme/Strata-data/models/unsloth-UD-Q4_K_XL`.
- `IQ3_S` model shards (retained): **83,617,662,656 bytes (77.875 GiB)** in `/nvme/Strata-data/models/IQ3_S`.
- `UD-Q4_K_XL` pack: `/nvme/Strata-data/packs/unsloth-ud-q4_k_xl` (~1.4 GiB; `dense.bin` `1,485,688,320` bytes, `index.txt` `93,560` bytes, `conversions.json` `167,871` bytes, `compat-bf16.json` `21,073` bytes, `native_experts.txt` `5,795` bytes, and `tokenizer/`).
- PLE table: `per_layer_token_embd.weight` in shard 2 (`Qwen3.8-Flash-Next-UD-Q4_K_XL-00002-of-00004.gguf`), shape `[160, 320001536]`, `IQ4_NL`, **28,800,138,240 bytes** (identical size and quant to `IQ3_S`). `--ple-io direct` reads selected rows directly from NVMe; startup direct-read probe measured **99,448 rows/s** (vs 99,241 rows/s on `IQ3_S`).
- MTP runtime files: `/nvme/Strata-data/mtp/rt` (`experts.bin` `707,788,800` bytes, `dense.bin` `116,099,072` bytes, `draft_vocab.bin` `425,196` bytes; `835 MiB` VRAM + `275.7 MiB` draft head).''')

add('## 6. Actual running configuration and quality baseline\n\nService: `strata.service`; Python parent `446197`, native engine child `446232`. Actual config: `/opt/strata/strata-unsloth-ud-q4_k_xl.json`. API key is deliberately omitted.\n\n```json\n' + json.dumps(cfg, indent=2) + '\n```')
add('Explicit `STRATA_` environment variables from the live engine (`PID 446232`):\n\n```text\n' + (root / 'pid-446232-strata-env.txt').read_text().strip() + '\n```')
add('''Controlled A/B verification against the `IQ3_S` baseline:
- **Changed (intended)**: Model switched from `Qwen3.8-Flash-Next GSQ-RCO IQ3_S` to `Qwen3.8-Flash-Next UD-Q4_K_XL`; `--vram-reserve-mib` adjusted from `700` to `832` MiB (approved global safety margin adjustment). Note that for 4-shard Unsloth GGUFs, Strata's `setup.py` omits `--ple-gguf` because the engine automatically locates `per_layer_token_embd.weight` in shard 2 from shard 1 (`--native`); startup logs confirm `PLE on, table 320001536 rows of .../Qwen3.8-Flash-Next-UD-Q4_K_XL-00002-of-00004.gguf` with `--ple-io direct`.
- **Preserved identically**: `--max-context 262144` (native, no RoPE scaling), `--kv int8`, `--kv-resident 32768`, `--expert-cache auto`, full RAM expert arena (no `--mmap-experts` or `--resident-budget-gib`), `--ple-io direct`, `--spec 4`, `--spec-min-p 0.5`, `--mtp /nvme/Strata-data/mtp/rt`, vision disabled, `parallel = 1`, `reasoning_effort = high` (`xhigh` in template), `reasoning_budget_tokens = 16384`, `reasoning_loop_recovery = recover`, `STRATA_ROUTE_TAIL_SKIP = 0`, `STRATA_SPEC_PROB = 0`, `STRATA_SPEC_COUPLED = 0`, `STRATA_SPEC_GUMBEL = 0`, `STRATA_HC_Q8 = 0`, and identical sampling parameters.''')

add('''## 7. Sampling configuration

Verified server configuration and `/props`: temperature **1.0**, top_p **0.95**, top_k **20**, min_p **0.0**, presence_penalty **0.0**, repetition_penalty **1.0**, `n_predict = -1`. Benchmark requests explicitly sent **max_tokens = 24576** and **seed = 42** with no sampling overrides.''')

add('''## 8. Reasoning configuration and semantics

- Thinking enabled by default (`enable_thinking` undefined -> `true`).
- Configured `reasoning_effort = "high"`, mapped by the Qwen chat template to **`xhigh`**.
- `preserve_thinking` defaults to **`true`** in the chat template.
- Configured thinking budget: **16384 tokens** (`reasoning_budget_tokens = 16384`). With `max_tokens = 24576`, the 75% answer-room ceiling (`18,432`) leaves the `16,384` thinking budget untouched.
- `reasoning_loop_recovery = "recover"`, checking every 512 tokens with a `0.25` repeated 12-word coverage threshold over the last 2,000 words.''')

for r in results:
    title = {'systemd': '9. Reasoning behavior test — systemd (Test A)', 'coding': '10. Coding reasoning test (Test B)', 'recovery': '11. Wrong-hypothesis recovery test (Test C)'}[r['name']]
    add('## ' + title + '\n\nExact prompt (unchanged):\n\n> ' + r['prompt'] + '\n\nFull final response is archived in `' + str(root / (r['name'] + '.json')) + '` under `answer` (and `' + str(root / (r['name'] + '-answer.md')) + '`); reasoning text was discarded. Behavioral characterization is in section 16; request metrics are in section 12.')

table(['Prompt', 'SHA-256 of exact UTF-8 text'], [[r['name'], hashlib.sha256(r['prompt'].encode()).hexdigest()] for r in results])

add('## 12. Per-test performance measurements')
# Note: From engine log, systemd had 16,385 thinking-phase tokens + 6,570 post-wrapup tokens (22,955 engine_generated, 22,972 API completion_tokens);
# coding had 16,387 thinking-phase tokens + 4,286 post-wrapup tokens (20,673 engine_generated, 20,688 API completion_tokens);
# recovery completed in a single pass of 11,899 tokens (no wrap-up boundary in engine log; API usage does not split reasoning_tokens in Chat Completions).
engine_phase_notes = {
    'systemd': ('16,385 (budget cap)', '6,570 (post-wrapup)'),
    'coding': ('16,387 (budget cap)', '4,286 (post-wrapup)'),
    'recovery': ('unavailable (~7,420 est.)', 'unavailable (~4,479 est.)')
}
rows = []
for r in results:
    req = max(r['metrics_after']['requests'], key=lambda q: q['time'])
    u = r['usage']
    rt_str, fin_str = engine_phase_notes[r['name']]
    total = u.get('completion_tokens')
    rows.append([
        r['name'],
        u.get('prompt_tokens', 'unavailable'),
        u.get('prompt_tokens_details', {}).get('cached_tokens', 0),
        rt_str,
        fin_str,
        total,
        r['finish'],
        f"{r['ttft_s']:.3f}",
        f"{r['first_final_s']:.3f}",
        f"{r['elapsed_s']:.3f}",
        r['timings'].get('prompt_per_second', 'unavailable'),
        req.get('decode_tok_s', 'unavailable')
    ])
table(['Test', 'Prompt tokens', 'Cached tokens', 'Reasoning tokens*', 'Final tokens*', 'Total generated', 'Finish', 'TTFT any (s)', 'First final (s)', 'Total time (s)', 'Prefill tok/s**', 'Decode tok/s'], rows)

add('''*Chat Completions in Strata 0.1.45 does not populate `completion_tokens_details.reasoning_tokens` in the JSON usage object. However, for `systemd` and `coding`, the native engine log explicitly records the two-segment generation split at the 16,384-token thinking-budget wrap-up boundary:
- **`systemd`**: initial segment read `131` prompt tokens in `4699 ms` (**27.9 tok/s**) and generated **16,385 thinking tokens** in `360,057 ms` (`45.5 tok/s`); the budget wrap-up continuation (`prompt 16533 tokens = 16515 reused + 18 read in 313 ms (57.4 tok/s)`) then generated **6,570 final-answer tokens** in `135,584 ms` (`48.5 tok/s`), for `22,955` engine-generated tokens (`22,972` API `completion_tokens` including 17 wrap-up tokens).
- **`coding`**: initial segment read `127` prompt tokens in `3163 ms` (**40.1 tok/s**) and generated **16,387 thinking tokens** in `368,498 ms` (`44.5 tok/s`); the budget wrap-up continuation (`prompt 16529 tokens = 122 reused + 16407 read in 11436 ms (1434.7 tok/s)`) then generated **4,286 final-answer tokens** in `94,953 ms` (`45.1 tok/s`), for `20,673` engine-generated tokens (`20,688` API `completion_tokens` including 15 wrap-up tokens).
- **`recovery`**: completed in a single uninterrupted segment (`prompt 110 tokens = 0 reused + 110 read in 2008 ms (54.8 tok/s), 11899 generated in 256132 ms (46.5 tok/s)`) without hitting the 16,384 thinking budget; because no wrap-up split occurred and reasoning text was discarded without retokenization, exact reasoning vs final token sub-counts are marked unavailable (multiplying the `159.974 s` reasoning stream span and `96.071 s` final stream span by `46.5 tok/s` yields approximately ~7,420 reasoning tokens and ~4,479 final tokens).

**Server `timings.prompt_per_second` for `systemd` (`26.1 tok/s`) and `coding` (`8.7 tok/s`) divides the initial `131` or `127` prompt tokens by the summed `prompt_ms` across both the initial prompt and the budget wrap-up continuation (`4699 + 313 = 5012.6 ms` for `systemd`; `3163 + 11436 = 14598.9 ms` for `coding`, where 16,407 tokens were re-read at `1434.7 tok/s`). Actual initial short-prompt prefill rates from the native engine log were **27.9 tok/s** (`systemd`), **40.1 tok/s** (`coding`), and **54.8 tok/s** (`recovery`).''')

rows = []
for r in results:
    req = max(r['metrics_after']['requests'], key=lambda q: q['time'])
    off = req.get('drafts_offered', 0); acc = req.get('drafts_accepted', 0)
    ss = r['samples']; g = [s['gpu'] for s in ss if s['gpu'].get('mem_info_vram_used')]
    rows.append([
        r['name'],
        off,
        acc,
        f'{100*acc/off:.2f}%' if off else 'unavailable',
        f"{100*req['hit_rate']:.2f}%" if req.get('hit_rate') is not None else 'unavailable',
        req.get('pcie_share', 'unavailable'),
        req.get('ram_blobs', 'unavailable'),
        req.get('file_blobs', 'unavailable'),
        req.get('file_mb', 'unavailable'),
        gib(max(int(x['mem_info_vram_used']) for x in g)),
        gib(min(int(x['mem_info_vram_total']) - int(x['mem_info_vram_used']) for x in g)),
        kibg(samples[0]['mem']['MemTotal'] - ss[0]['mem']['MemAvailable']),
        kibg(max(proc_total(s, 'VmRSS') for s in ss)),
        kibg(min(s['mem']['MemAvailable'] for s in ss)),
        req.get('reasoning_recoveries', 'unavailable'),
        req.get('reasoning_repeat_coverage', 'unavailable')
    ])
table(['Test', 'Drafts proposed', 'Accepted', 'Acceptance', 'Expert hit', 'PCIe share', 'RAM blobs', 'File blobs', 'File MB', 'Peak VRAM GiB', 'Min free VRAM GiB', 'Host RAM used before GiB', 'Peak Strata RSS GiB', 'Min available RAM GiB', 'Recoveries', 'Repeat coverage'], rows)

rows = []
for r in results:
    rd = r['reasoning_stream_duration_s']; fd = r['final_stream_duration_s']
    rows.append([r['name'], f'{rd:.3f}' if rd else 'unavailable', f'{fd:.3f}' if fd else 'unavailable', r['reasoning_characters_observed'], len(r['answer'])])
table(['Test', 'Observed reasoning stream span (s)', 'Final stream to request end (s)', 'Reasoning chars observed', 'Final answer chars'], rows)

rows = []
for r in results:
    ss = r['samples']
    def io(s, k):
        return sum(int(line.split()[1]) for p in s['processes'].values() for line in p['io'].splitlines() if line.startswith(k + ':'))
    rows.append([r['name'], io(ss[-1], 'read_bytes') - io(ss[0], 'read_bytes'), io(ss[-1], 'rchar') - io(ss[0], 'rchar'), max(r['metrics_after']['requests'], key=lambda q: q['time']).get('reused', 'unavailable')])
table(['Test', 'Sampled process read_bytes delta', 'Sampled process rchar delta', 'Prompt cached tokens'], rows)
add('''Process `read_bytes` deltas (`637–722 MiB` per test) reflect direct NVMe PLE row reads (`--ple-io direct` on the 28.8 GB `per_layer_token_embd.weight` table in shard 2), NOT expert paging: Strata's per-request expert file counters confirm `file_blobs = 0` and `file_mb = 0` on all three tests.''')

add('''## 13. 256K context readiness

Configured and API-advertised context agree at **262144** tokens (`native = 262144`, `max_positions = 262144`, `kv = int8`, `kv_resident = 32768`). The full **3.09 GiB** pinned host RAM buffer for the 262,144-token INT8 KV cache is already allocated and pinned at startup alongside the 71.73 GiB expert arena. With **44.778 GiB** minimum host `MemAvailable` and **520 MiB** minimum free VRAM (`832 MiB` reserve), the 262,144-token context remains viable in RAM and VRAM without enabling `--mmap-experts` or `--resident-budget-gib`.''')

add('## 14. Swap and memory pressure')
first = samples[0]; last = samples[-1]
table(['Measure', 'Before first test', 'After third test / delta'], [
    ['Host Swap used KiB', first['mem']['SwapTotal'] - first['mem']['SwapFree'], f"{last['mem']['SwapTotal'] - last['mem']['SwapFree']} ({last['mem']['SwapTotal'] - last['mem']['SwapFree'] - (first['mem']['SwapTotal'] - first['mem']['SwapFree']):+d} KiB)"],
    ['Native engine (PID 446232) VmSwap KiB', first['processes']['446232']['VmSwap'], f"{last['processes']['446232']['VmSwap']} (min 0, max 0 KiB)"],
    ['Python parent (PID 446197) VmSwap KiB', first['processes']['446197']['VmSwap'], f"{last['processes']['446197']['VmSwap']} ({last['processes']['446197']['VmSwap'] - first['processes']['446197']['VmSwap']:+d} KiB)"],
    ['pswpin pages', first['vmstat'].get('pswpin'), f"+{last['vmstat'].get('pswpin', 0) - first['vmstat'].get('pswpin', 0)} pages"],
    ['pswpout pages', first['vmstat'].get('pswpout'), f"+{last['vmstat'].get('pswpout', 0) - first['vmstat'].get('pswpout', 0)} pages (ZERO swap-outs)"],
    ['pgscan_kswapd', first['vmstat'].get('pgscan_kswapd'), f"+{last['vmstat'].get('pgscan_kswapd', 0) - first['vmstat'].get('pgscan_kswapd', 0)} pages"],
    ['pgscan_direct', first['vmstat'].get('pgscan_direct'), f"+{last['vmstat'].get('pgscan_direct', 0) - first['vmstat'].get('pgscan_direct', 0)} pages"],
    ['Cached KiB', first['mem'].get('Cached'), last['mem'].get('Cached')]
])
add('After-test `free -h`:\n\n```text\n' + (root / 'free-after.txt').read_text().strip() + '\n```\n\nAfter-test `vmstat`:\n\n```text\n' + (root / 'vmstat-after.txt').read_text().strip() + '\n```')
add('''Swap analysis:
- During the one-time startup load (`strata generate: expert arena read through the file cache`), reading 71.73 GiB of GGUF shards through the OS page cache while pinning the 71.73 GiB expert arena caused Linux to reclaim page cache and swap out ~25.3 MiB (`25,272 KiB`) of cold pages from the Python launcher parent (`PID 446197`) and ~220 MiB from other background host processes.
- Once loaded, **zero pages were swapped out during the entire 20.6-minute benchmark (`pswpout delta = 0` across all three trials)**, and the native C++/HIP inference engine (`PID 446232`, `76.741 GiB` RSS) had **`VmSwap = 0 KiB` at every single sample**. The `pswpin` delta of `+1,979` pages (~7.7 MiB) reflects the Python parent faulting a few of its own startup pages back into RAM (`PID 446197` `VmSwap` decreased from `25,272 KiB` to `20,548 KiB`).''')

add('## 15. Errors, warnings, and fidelity observations')
log = Path('/nvme/Strata-data/logs/strata-unsloth-ud-q4_k_xl.log').read_text()
current = log[log.rfind('[strata] 2026-10-18 17:56:08'):]
(root / 'current-engine-log.txt').write_text(current)
interesting = [l for l in current.splitlines() if re.search(r'WARNING|error|fail|unavailable|rounded|loop|budget|head|fallback|capturing|captured|PCIe share', l, re.I)]
add('Relevant exact current-process engine log lines:\n\n```text\n' + '\n'.join(interesting) + '\n```')
jlines = [l for l in (root / 'startup-after.txt').read_text().splitlines() if any(x in l.lower() for x in ['repeated reasoning detected', 'thinking budget reached', 'thinking capped', 'error', 'timeout', 'failed'])]
add('Relevant service-journal lines (all clients in the window):\n\n```text\n' + '\n'.join(jlines) + '\n```')
integrity = json.loads((root / 'integrity.json').read_text())
add('Integrity/service check:\n\n```json\n' + json.dumps(integrity, indent=2) + '\n```')
add('''Key fidelity and warning observations:
1. **Zero low-VRAM warnings**: Unlike the `IQ3_S` run at 700 MiB reserve (which logged `WARNING: only 392 MiB of VRAM free at the start of this request (below 512 MiB)`), `UD-Q4_K_XL` with `--vram-reserve-mib 832` kept free VRAM at `676 MiB` after loading and `520–644 MiB` across all verification-window graph captures (`1, 2, 3, 4, 5, 6` tokens).
2. **Zero reasoning-loop recoveries**: No `repeated reasoning detected` messages occurred in `journalctl -u strata`.
3. **Two thinking-budget wrap-ups**: `systemd` (`18:04:04`) and `coding` (`18:12:31`) reached the configured `16384`-token thinking budget and were cleanly wrapped up by Strata.
4. **Adaptive PCIe share**: Because `UD-Q4_K_XL` caches 8,131 of 24,576 expert slots in VRAM (~88% decode cache hit rate vs ~96% on `IQ3_S`), the CPU pool handles more cache misses per token (`132–142 us` per missed expert, `22.1–23.8 GB/s` of expert bytes), and Strata automatically adapted `pcie_frac` from `0.34 -> 0.25 -> 0.20`.
5. **Pack `--compat-bf16` conversion**: `unsloth-ud-q4_k_xl` converted 195 `Q8_0` small/hyper-connection tensors (`1.20 GiB`) to BF16 during pack build (`max |err| 0.0144`), while keeping `STRATA_HC_Q8=0` at runtime so RDNA4 does not re-quantize hyper-connection projections.''')

add('## 16. Behavioral assessment\n\n' + assessment_text)

add('''## 17. Loop / Reasoning Analysis

Explicit per-test loop and reasoning behavior for `UD-Q4_K_XL` compared against `IQ3_S`:''')

table([
    'Question / Metric',
    'Test A: Systemd (UD-Q4_K_XL vs IQ3_S)',
    'Test B: Python Coding (UD-Q4_K_XL vs IQ3_S)',
    'Test C: PostgreSQL Recovery (UD-Q4_K_XL vs IQ3_S)'
], [
    ['Did reasoning-loop detection activate?', 'No (`0.013` coverage) vs No (`0.015` coverage)', '**No (`0.158` coverage)** vs **Yes (`0.290` coverage)**', 'No (`0.018` coverage) vs No (`0.000` coverage)'],
    ['At what token count?', 'N/A vs N/A', '**N/A (never triggered)** vs **9,728 tokens**', 'N/A vs N/A'],
    ['Did recovery activate?', 'No (`0`) vs No (`0`)', '**No (`0`)** vs **Yes (`1`, low-effort resume)**', 'No (`0`) vs No (`0`)'],
    ['Did the model recover?', 'N/A vs N/A', '**N/A (uninterrupted thinking)** vs Yes (after recovery)', 'N/A vs N/A'],
    ['Did it hit the 16,384-token reasoning budget?', '**Yes (at 16,385 tokens)** vs No (finished in 16,282 total tokens)', '**Yes (at 16,387 tokens)** vs **Yes (after recovery)**', 'No (`11,899` total tokens) vs No (`12,141` total tokens)'],
    ['Did it repeat substantial passages?', 'Moderate (12 sections + section 12 summary checklist; less repetitive than `IQ3_S`)', 'Low in final answer; moderate internal deliberation (`0.158` repeat coverage)', 'Low (`0.018` repeat coverage; clean progression)'],
    ['Did it revisit already-rejected hypotheses?', 'No', 'No', 'No (rejects blind firewall disable and branches conditionally on evidence)'],
    ['Did it converge cleanly?', 'Converged via 16,384-token budget wrap-up (`finish=stop`)', 'Converged via 16,384-token budget wrap-up without loop recovery (`finish=stop`)', 'Converged naturally before budget (`finish=stop`)']
])

add('''Summary of loop/reasoning comparison:
- **Loop stability improved on Test B (Coding)**: `IQ3_S` entered a detected reasoning loop at `9,728` tokens (`coverage = 0.290 >= 0.25`) and required Strata's `reasoning_loop_recovery` intervention to escape. `UD-Q4_K_XL` kept repeated-passage coverage at `0.158` (well below `0.25`) and completed the coding task with **zero loop recoveries**.
- **Thinking-token consumption increased on Test A (Systemd)**: Whereas `IQ3_S` finished `systemd` naturally at `16,282` total tokens (reasoning + final combined), `UD-Q4_K_XL` used the entire `16,384`-token thinking budget on `systemd` before emitting `6,570` final answer tokens (`22,972` total tokens). Thus `UD-Q4_K_XL` avoided loop-recovery intervention (`0/3` vs `1/3`), but hit the `16,384`-token thinking budget ceiling more often (`2/3` tests vs `1/3` on `IQ3_S`).''')

add('''## 18. Systems Accuracy Comparison

Direct comparison of `UD-Q4_K_XL` against the factual and system-level findings from the `IQ3_S` benchmark:''')

table(['System / Technical Dimension', 'GSQ-RCO IQ3_S Baseline', 'UD-Q4_K_XL', 'Comparison'], [
    ['Systemd exit code `201` vs `217`', 'Mislabeled `201` as user/group setup (`201` is `NICE`; `217` is `USER`)', 'Correctly identifies `status=217/USER` and `status=216/GROUP` (along with `203/EXEC`, `200/CHDIR`)', '**UD-Q4_K_XL better** on `217/USER` & `216/GROUP`'],
    ['Other systemd exit codes (`214`, `211`)', 'Did not mislabel `214` or `211`', 'Mislabeled `status=214/PATH` (actual `214` is `SETSCHEDULER`) and `status=211/START_LIMIT_HIT` (actual `211` is `IOPRIO`; start-limit is `Result=start-limit-hit`)', '**UD-Q4_K_XL worse** (invented two wrong exit-code mappings)'],
    ['Systemd property semantics (`systemctl show`)', 'Presented `ExecMainCode=exited` where raw `systemctl show` property is numeric', 'Uses `systemctl show foo.service -p ExecStart --value | awk \'{print $1}\'` (which outputs `{` instead of the binary path), queries nonexistent `-p DropPaths` instead of `DropInPaths`, and passes `EnvironmentFiles --value` suffix into `systemd-run`', '**Tie / Mixed** (different `systemctl show` CLI/property bugs in both models)'],
    ['SELinux troubleshooting safety', 'Included a conditional global `setenforce 0`/`setenforce 1` experiment in initial troubleshooting guide', 'Explicitly warns against disabling SELinux or running mutating `restorecon` before evidence gathering; uses read-only `restorecon -n -v` dry-run', '**UD-Q4_K_XL better** (strictly read-only SELinux evidence posture)'],
    ['Transient unit (`systemd-run`) safety', 'Proposed running the production executable inside `systemd-run` without guarding against side effects', 'Uses `/usr/bin/env` for the environment probe and explicitly passes `--dry-run` with a safety caveat for executable/strace probes', '**UD-Q4_K_XL better**'],
    ['PostgreSQL / firewalld troubleshooting', 'Correctly rejected blind `firewalld` disable, checked Unix sockets, listeners, IPv4/IPv6, `pg_hba.conf`, SELinux, namespaces', 'Equally thorough: checks Unix sockets vs TCP, IPv4/IPv6 `localhost`, `ss`, `psql` as app user, `iptables`/`nft` packet counter deltas, `tcpdump -i lo`, `pg_hba.conf`, SELinux, namespaces, and time-bounded rules (`--timeout=300`)', '**Approximately equal (both Good)**']
])

add('''**Systems Accuracy Verdict: `approximately equal` overall** (with cleaner read-only safety discipline in `UD-Q4_K_XL`, offset by new `systemctl show` parsing and exit-code `214`/`211` hallucinations). Neither quantization eliminates the need for human review of generated `systemctl show` one-liners or systemd exit-code tables.''')

add('''## 19. Direct Performance & Resource Comparison (IQ3_S vs UD-Q4_K_XL)''')

iq3_decodes = [iq3_summary[t]['request_metrics']['decode_tok_s'] for t in ['systemd', 'coding', 'recovery']]
q4_decodes = [q4_summary[t]['request_metrics']['decode_tok_s'] for t in ['systemd', 'coding', 'recovery']]
avg_iq3_decode = statistics.mean(iq3_decodes)
avg_q4_decode = statistics.mean(q4_decodes)
decode_diff_pct = 100.0 * (avg_q4_decode - avg_iq3_decode) / avg_iq3_decode

iq3_times = [iq3_summary[t]['elapsed_s'] for t in ['systemd', 'coding', 'recovery']]
q4_times = [q4_summary[t]['elapsed_s'] for t in ['systemd', 'coding', 'recovery']]
avg_iq3_time = statistics.mean(iq3_times)
avg_q4_time = statistics.mean(q4_times)
time_diff_pct = 100.0 * (avg_q4_time - avg_iq3_time) / avg_iq3_time

table(['Metric', 'GSQ-RCO IQ3_S (Baseline)', 'UD-Q4_K_XL (Current)', 'Delta / Comparison'], [
    ['Model shard size on disk', '77.875 GiB (83.62 GB, 2 shards)', '103.688 GiB (111.33 GB, 4 shards)', '+25.813 GiB (+33.1%)'],
    ['VRAM reserve configured', '700 MiB (later updated to 832 MiB)', '832 MiB', '+132 MiB safety margin'],
    ['Model RAM (expert arena / peak Strata RSS)', '46.84 GiB arena / 52.096 GiB peak RSS', '71.73 GiB arena / 77.270 GiB peak RSS', '+24.89 GiB arena / **+25.174 GiB RSS (+48.3%)**'],
    ['Peak total host RAM used (total − avail)', '55.409 GiB', '80.947 GiB', '**+25.538 GiB (+46.1%)**'],
    ['Minimum available host RAM (`MemAvailable`)', '70.316 GiB', '44.778 GiB', '−25.538 GiB (still ample)'],
    ['VRAM peak / Minimum free VRAM', '31.563 GiB peak / 0.297 GiB (304 MiB) free', '31.352 GiB peak / 0.508 GiB (520 MiB) free', '+216 MiB min free VRAM (no warnings)'],
    ['GPU expert-cache residency', '13,103 / 24,576 slots (53.3%, 24.87 GiB)', '8,131 / 24,576 slots (33.1%, 23.73 GiB)', '−4,972 slots (−37.9% cached slots)'],
    ['Expert-cache hit rate (across 3 tests)', '95.70% – 97.00%', '87.10% – 88.80%', '−8.2 to −8.6 percentage points'],
    ['Expert residency in RAM', '100% resident in RAM (`file_blobs=0`)', '100% resident in RAM (`file_blobs=0`)', 'Identical (full arena in RAM)'],
    ['NVMe expert reads (`file_blobs` / `file_mb`)', '0 blobs / 0 MB (PLE direct I/O only)', '0 blobs / 0 MB (PLE direct I/O only)', 'Identical (zero NVMe expert paging)'],
    ['Systemd prefill (API / native initial)', '69.8 tok/s', '26.1 tok/s API* (27.9 tok/s initial + wrap-up)', '−62.6% API (−60.0% initial)'],
    ['Systemd decode', '70.2 tok/s', '46.3 tok/s', '−34.0%'],
    ['Systemd reasoning / total tokens', 'unavailable (<16,282; no budget hit) / 16,282 total', '16,385 (hit 16,384 budget) + 6,570 final / 22,972 total', '+41.1% total generated tokens'],
    ['Systemd total time', '233.694 s', '500.680 s', '+266.986 s (+114.2%)'],
    ['Systemd loop recovery', '0 (`coverage=0.015`)', '0 (`coverage=0.013`)', '0 in both (Q4 hit thinking budget)'],
    ['Python prefill (API / native initial)', '16.8 tok/s API* (138.1 initial + 1529.9 replay)', '8.7 tok/s API* (40.1 initial + 1434.7 replay)', '−48.2% API (both include replay)'],
    ['Python decode', '65.9 tok/s', '44.6 tok/s', '−32.3%'],
    ['Python reasoning / total tokens', '16,384 budget hit after loop recovery / 20,580 total', '16,387 (hit 16,384 budget) + 4,286 final / 20,688 total', '+0.5% total generated tokens'],
    ['Python total time', '319.470 s', '478.064 s', '+158.594 s (+49.6%)'],
    ['Python loop recovery', '**1 recovery (triggered at 9,728 tokens, `coverage=0.290`)**', '**0 recoveries (`coverage=0.158`)**', '**UD-Q4_K_XL eliminated loop recovery**'],
    ['Python tests passed', '4 / 4 container checks passed', '4 / 4 container checks passed', 'Identical (4 / 4 passed)'],
    ['PostgreSQL prefill', '126.5 tok/s', '54.8 tok/s', '−56.7%'],
    ['PostgreSQL decode', '68.7 tok/s', '46.5 tok/s', '−32.3%'],
    ['PostgreSQL reasoning / total tokens', 'unavailable (~7,770 est., no budget hit) / 12,141 total', 'unavailable (~7,420 est., no budget hit) / 11,899 total', '−2.0% total generated tokens'],
    ['PostgreSQL total time', '177.644 s', '258.147 s', '+80.503 s (+45.3%)'],
    ['PostgreSQL loop recovery', '0 (`coverage=0.000`)', '0 (`coverage=0.018`)', '0 in both'],
    ['**Average decode throughput (3 tests)**', f'**{avg_iq3_decode:.2f} tok/s**', f'**{avg_q4_decode:.2f} tok/s**', f'**{decode_diff_pct:+.1f}%**'],
    ['**Average task completion time (3 tests)**', f'**{avg_iq3_time:.2f} s** (`730.81 s` sum)', f'**{avg_q4_time:.2f} s** (`1236.89 s` sum)', f'**{time_diff_pct:+.1f}%** (mean of per-task % deltas: +69.7%)'],
    ['**Host RAM increase (Strata RSS / Host used)**', '52.096 GiB RSS / 55.409 GiB host used', '77.270 GiB RSS / 80.947 GiB host used', '**+25.174 GiB Strata RSS (+48.3%) / +25.538 GiB host used**']
])

add('''## 20. Quality Comparison & Final Verdict

### Side-by-Side Quality Assessment''')

table(['Dimension', 'GSQ-RCO IQ3_S', 'UD-Q4_K_XL', 'Evidence-Based Explanation'], [
    ['Coding correctness', 'Good', 'Good', 'Both models diagnosed all 3 failure modes and passed 4/4 isolated container tests. `UD-Q4_K_XL` reached its solution without triggering a reasoning-loop recovery (`0` vs `1`) and included a `pytest` suite, though both still hit the 16,384 thinking budget.'],
    ['Linux/system accuracy', 'Mixed', 'Mixed', 'Approximately equal overall: `UD-Q4_K_XL` fixed `IQ3_S`\'s `201` vs `217/USER` exit-code error and used safer read-only SELinux/transient-unit commands, but introduced `status=214/PATH` and `status=211/START_LIMIT_HIT` mislabels and `systemctl show -p ExecStart --value | awk \'{print $1}\'` (`{`) parsing errors.'],
    ['Troubleshooting structure', 'Good', 'Good', 'Both models produce logical, evidence-first decision trees and clear diagnostic tables; `UD-Q4_K_XL`\'s `systemd` final answer is slightly less redundant (`25.3 KB` vs `30.5 KB`).'],
    ['Evidence-first reasoning', 'Good', 'Good', 'Both models consistently gather logs, socket state, and configuration before mutating system files; `UD-Q4_K_XL` is stricter about avoiding temporary `setenforce 0` or live binary execution during evidence gathering.'],
    ['Wrong-hypothesis recovery', 'Good', 'Good', 'Both models immediately reject disabling `firewalld` without evidence, distinguish Unix sockets from TCP, check `ss`/`psql`/`pg_hba.conf`/`tcpdump`/counters, and converge without hitting the thinking budget.'],
    ['Reasoning efficiency', 'Mixed', 'Poor', '`UD-Q4_K_XL` hit the 16,384-token thinking budget on **2 of 3** tests (`systemd` and `coding`), generating `55,559` total tokens across the 3 tests (`1,236.9 s`) vs `49,003` tokens (`730.8 s`) for `IQ3_S`.'],
    ['Repetition', 'Mixed', 'Good', '`UD-Q4_K_XL` reduced final-answer checklist repetition in `systemd` (`25.3 KB` vs `30.5 KB`) and stayed below the `0.25` repeated-reasoning threshold on all 3 tests (`max 0.158` vs `0.290` on `IQ3_S`).'],
    ['Loop tendency', 'Mixed', 'Good', '`IQ3_S` triggered repeated-reasoning loop recovery on `coding` at 9,728 tokens (`coverage=0.290`); `UD-Q4_K_XL` had **0 loop recoveries** across all 3 tests (`coverage=0.013, 0.158, 0.018`).'],
    ['Instruction following', 'Good', 'Good', 'Both models satisfied all prompt constraints, covered every requested diagnostic factor, and avoided premature edits.'],
    ['Autonomous-agent suitability', 'Mixed', 'Mixed', '`UD-Q4_K_XL` avoids mid-stream loop-recovery intervention (`0/3` vs `1/3`), but its ~33% lower decode throughput (`45.8` vs `68.3` tok/s), higher rate of hitting the 16,384 thinking ceiling (`2/3` vs `1/3`), ~69% longer task wall-clock time, and subtle `systemctl show` CLI bugs keep autonomous suitability Mixed.']
])

add('''### Decision: Is `UD-Q4_K_XL` Worth It Over `GSQ-RCO IQ3_S`?

> **Question:** Does `UD-Q4_K_XL` provide enough improvement in coding/system reasoning, factual accuracy, and convergence to justify its additional RAM usage and lower tokens/sec compared with `GSQ-RCO IQ3_S`?

**Answer: Marginally for unassisted deep coding reasoning (where avoiding reasoning loops matters), but NO as an all-around replacement if turnaround time or Linux CLI precision is the primary goal — the two models are a true tradeoff rather than a clean sweep for `UD-Q4_K_XL`:**

1. **Where `UD-Q4_K_XL` genuinely improves over `IQ3_S`**:
   - **Reasoning-loop stability on complex coding (Test B)**: `IQ3_S` entered a verbatim/near-verbatim reasoning loop at 9,728 tokens (`coverage = 0.290`) during the Python scanner task and only succeeded because Strata's `reasoning_loop_recovery` intervened and downgraded effort. `UD-Q4_K_XL` never looped (`coverage = 0.158`, `recoveries = 0`), produced cleaner final prose with less section-by-section duplication, and included a unit test suite alongside the working implementation.
   - **Safer read-only operational posture (Test A)**: `UD-Q4_K_XL` avoided `IQ3_S`'s `setenforce 0` suggestion, used `restorecon -n -v` dry-runs, probed systemd environment via `/usr/bin/env`, and fixed the `217/USER` exit code.
2. **Where `UD-Q4_K_XL` does NOT improve (or regresses) relative to `IQ3_S`**:
   - **Linux/systemd factual accuracy is a wash (`Mixed` vs `Mixed`)**: While fixing `201`/`217`, `UD-Q4_K_XL` hallucinated `status=214/PATH` (actually `SETSCHEDULER`) and `status=211/START_LIMIT_HIT` (actually `IOPRIO`), and wrote `systemctl show foo.service -p ExecStart --value | awk '{print $1}'` (which extracts `{` instead of the binary path) and `-p DropPaths` (instead of `DropInPaths`).
   - **Thinking-budget exhaustion increased (`2/3` vs `1/3`)**: `UD-Q4_K_XL` exhausted the full 16,384-token thinking budget on both `systemd` and `coding` (whereas `IQ3_S` only hit the budget on `coding` after its loop recovery).
   - **Substantial speed and RAM cost**: Because `UD-Q4_K_XL`'s larger expert blobs reduce GPU expert-cache residency from **53.3% (13,103 slots)** to **33.1% (8,131 slots)** and lower decode cache hit rates from **~96.4%** to **~88.2%**, more expert evaluations fall back to the 6-core AVX2 CPU pool (`132–142 us` per missed expert). Average decode speed drops by **32.9%** (`68.27` → `45.80 tok/s`), total wall-clock completion time across the three tasks increases by **69.2%** (`730.81 s` → `1236.89 s`, from 12.2 minutes to 20.6 minutes), and host RAM usage rises by **+25.17 GiB** (`52.10 GiB` → `77.27 GiB` peak Strata RSS, leaving `44.78 GiB` available on this 128 GB host).

**Recommendation for `scar.lab`**:
- Host RAM (`125.7 GiB` usable, `44.8 GiB` still free under `UD-Q4_K_XL`) easily holds `UD-Q4_K_XL`'s full `71.73 GiB` expert arena in RAM with zero NVMe expert paging and zero active swap.
- Keep `UD-Q4_K_XL` active if your priority is **minimizing mid-reasoning loop interventions (`reasoning_loop_recovery`)** on hard coding prompts and you accept ~45–46 tok/s decode and ~8-minute deep-thinking turnarounds.
- Keep `IQ3_S` (with the new `832 MiB` VRAM reserve) ready for instant switchback whenever **~1.5× faster decode (`66–70 tok/s`) and ~40% shorter task completion times** outweigh `UD-Q4_K_XL`'s loop-coverage margin.''')

report_nvme = root.parent / (root.name.removesuffix('-data') + '.md')
repo_report = Path('/home/cpaquin/Workspace/Git/Containerized-VLLM-AMD-R9700-Strata/benchmarks') / report_nvme.name
add('Report file: `' + str(repo_report) + '` (also archived at `' + str(report_nvme) + '`).')
full_md = '\n'.join(text)
report_nvme.write_text(full_md)
repo_report.write_text(full_md)
h = hashlib.sha256(full_md.encode()).hexdigest()
(root / 'report.sha256').write_text(f'{h}  {report_nvme.name}\n')
print('WROTE:', repo_report, len(full_md), 'bytes, sha256:', h)
