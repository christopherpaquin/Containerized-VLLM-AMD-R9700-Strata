import json,hashlib,re,statistics
from pathlib import Path
from datetime import datetime
root=Path(Path('/tmp/strata-report-data-path').read_text());load=lambda n:json.loads((root/n).read_text())
results=[load(n+'.json') for n in ['systemd','coding','recovery']]
props=load('props-before.json');engine=load('metrics-before.json')['engine'];cfg=load('config.json');samples=[s for r in results for s in r['samples']]
gib=lambda x:f'{x/2**30:.3f}'
kibg=lambda x:f'{x/2**20:.3f}'
text=[]
def add(s):text.append(s.strip()+'\n')
def table(headers,rows):
 add('| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+'\n'.join('| '+' | '.join(str(x) for x in row)+' |' for row in rows))
def proc_total(s,k):return sum(p.get(k,0) for p in s['processes'].values())
add(f'''# scar.lab Strata runtime, reasoning, and performance benchmark

Observation date: {datetime.fromtimestamp(results[0]['start_unix']).astimezone().isoformat()}. Host: scar.lab, user cpaquin. All times are seconds unless specified. GiB/MiB are binary units. This report concerns the running **Qwen3.8-Flash-Next GSQ-RCO IQ3_S** deployment.

Three sequential, single-turn streaming requests used the exact supplied prompts, one trial each, seed 42, max_tokens 24576. No system message, tool definitions, sampling overrides, reasoning overrides, tuning, restart, replacement components, or context stress test was applied. The running defaults were inherited. The max_tokens request cap leaves the configured 16384 thinking budget intact (unlike a 20000 cap, which would reduce thinking to 15000 under Strata's answer-room rule). Sampling seed does not guarantee deterministic results across quantizations/backends.

Telemetry sampled approximately every 0.5 seconds; short prefill phases can be missed. Existing loaded caches were retained, not flushed. This measures a warm running service, not cold startup. Completed test requests at 2026-10-18T16:59:22.387952-04:00. Snapshot files, public configuration, exact requests, answers, metrics, sampled resource data, and reproduction procedure are saved in `{root}`. Reasoning text was observed only for stream boundary timing and discarded; this report does not reproduce chain-of-thought. Report generation and telemetry add modest CPU/disk activity. Other host services were left running. The service was not made exclusive: queued activity from another request was observed during the coding trial; short foreign-request entries in the journal are not scored as benchmark trials. Queueing is included in client timings when present. Native parallel request count remained 1.''')
add('Key findings: the requested GSQ-RCO IQ3_S quality-first configuration is active. All three requests finished normally at 65.9–70.2 decode tokens/s. Coding triggered one reasoning-loop recovery and then a thinking-budget close; four executable scanner checks passed. Strata used no swap, with at least 70.316 GiB host RAM available. VRAM headroom was only about 304 MiB at the sampled minimum, and Strata issued a low-free-VRAM warning. These three trials provide preliminary evidence, not a broad model ranking.')
add('''## 1. Model identity

- Model: **Qwen3.8-Flash-Next GSQ-RCO**, IQ3_S distribution from `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF`. GGUF metadata identifies ISTA DASLab as quantizer and includes GSQ/RCO tags. This is specifically the requested GSQ-RCO IQ3_S artifact, not UD-Q4_K_XL.
- API canonical name: `qwen3.8-flash-next-iq3_s`; alias `scar-coder`.
- First shard: `/nvme/Strata-data/models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00001-of-00002.gguf` (54,817,524,224 bytes).
- Second shard: `/nvme/Strata-data/models/IQ3_S/Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-00002-of-00002.gguf` (28,800,138,432 bytes).
- Recorded model revision: `ed59f92082b1e93c0e96d60a8b11aab089b52f09`. Deployment-verified SHA-256: shard 1 `4c1eb2ceb4915e1192f4f386021897bde56a97f40a0bb78bb86465e0f7d2aca3`; shard 2 `316b46f3a2dbd68c900f43136ab9449f9dcc3725dfd8c794847c204bc161e113`. These are prior verified manifest values; this observation task read GGUF headers/sizes and did not rehash 84 GB during inference.
- IQ3_S is a mixed-precision quant label: inspected tensors include IQ3_S, IQ3_XXS, IQ2_S, Q2_0, IQ4_XS/IQ4_NL, Q4_K/Q5_K/Q6_K/Q8_0, BF16/F16/F32. It does not mean every tensor is 3-bit. PLE is IQ4_NL; startup reports a native Q5_K output head.
- API/engine version: **Strata 0.1.45**. Deployment provenance records tag `v0.1.45`, commit `477ac13cbe8e582c20484871ce459d20d82df09d`. `/opt/strata` has no `.git`, so commit/tag are recorded provenance, not independently recovered by `git rev-parse` on the live installation. Build manifest source fingerprint: `b95137167bd9b751` (not a Git commit).
- ROCm **7.2.2**, native HIP Release build, architecture `gfx1201`, AMD HIP platform, clang++ from `/opt/rocm/llvm/bin/clang++`. Runtime GPU: **AMD Radeon AI PRO R9700**. Some API hardware labels only say “AMD Radeon”; startup gives the full device identity.
- MTP/speculation enabled: `--mtp /nvme/Strata-data/mtp/rt --spec 4 --spec-min-p 0.5`. Engine advertises `mtp_max=4`, verification window `spec=6`, lookup depth `lookup=3`; those counters are different from the configured MTP draft depth, not evidence that it was changed to 6.
- Vision disabled in configuration/build and API (`vision.enabled=false`, `modalities.vision=false`).''')
add('''## 2. Context and KV configuration

Configured maximum, `/props` n_ctx, engine max_context, and `/v1/status` native/max_positions all report **262144**. INT8 KV is active; **32768** cells per QSA layer are resident in VRAM. KV streaming is active with **3.09 GiB pinned host RAM allocated at startup** for the full authoritative K/V. This is measured allocation, not current populated-token usage.

Resident K/V payload can be calculated from the inspected architecture and source: 12 QSA layers × 32768 cells × 2 KV heads × 256 dimensions × 2 (K and V) × (1 byte + 2/64 scale bytes) = **396 MiB**. Full 262144-cell payload is **3168 MiB = 3.09375 GiB**, matching Strata’s rounded **3.09 GiB** startup allocation. Treat the startup allocation as authoritative; 396 MiB is a calculated resident payload, not a measured complete GPU allocation. Page maps, indexing, RoPE tables, state, MTP KV, and scratch buffers add overhead and are not individually exposed by the API.

Native context is active; no `--rope-scaling`, `--rope-scale`, or YaRN options are present. GGUF metadata has native context 262144 and RoPE base 10000000 with no scaling keys. No context extension is indicated. No deviation from the intended context/KV baseline was found.''')
# calculation above verify arithmetic later
add('## 3. System RAM usage\n\nLoaded idle snapshot (`free -h`):\n\n```text\n'+(root/'free-before.txt').read_text().strip()+'\n```')
add('''The CPU is Intel Core i7-8086K, 6 cores/12 threads, AVX2 (no AVX-512), four 32 GB DDR4 DIMMs configured at 3200 MT/s (dmidecode). Physical RAM is 134,996,344,832 bytes (125.725 GiB). The 46.84 GiB expert arena is loaded into RAM, registered/pinned for GPU access; engine reports arena_mib=47962. There is no reduced resident-expert budget or mmap-expert option configured. Startup confirms the full arena load. Memory-mapped token embeddings (322 MiB) are separate and do not imply mmap expert loading.

Idle native engine RSS was 54,045,104 KiB (51.541 GiB), VSZ 94,131,664 KiB (89.771 GiB), 40.9% RAM. Python parent RSS was 179,304 KiB (0.171 GiB), VSZ 561,732 KiB (0.536 GiB), 0.1% RAM. Summed idle RSS is approximately 51.712 GiB. The engine smaps_rollup reports approximately 51.4 GiB anonymous/private memory and 50.9 GiB AnonHugePages; explicit hugepages are not required for residency. The host has substantial filesystem/page cache; low `MemFree` alone is not memory pressure when `MemAvailable` remains large. Process RSS includes pinned memory and can overlap driver accounting; summing VSZ is not physical RAM usage. Full process/status/maps/smaps evidence is stored beside the report.''')
add('Loaded process snapshot:\n\n```text\n'+'\n'.join(l for l in (root/'processes-before.txt').read_text().splitlines() if l.strip().startswith(('PID','298535 ','298542 ')))+'\n```')
table(['Observed quantity','Value'],[
 ['Peak summed Strata parent + engine RSS',kibg(max(proc_total(s,'VmRSS') for s in samples))+' GiB'],
 ['Minimum host MemAvailable during tests',kibg(min(s['mem']['MemAvailable'] for s in samples))+' GiB'],
 ['Peak whole-host used RAM (total − available)',kibg(max(s['mem']['MemTotal']-s['mem']['MemAvailable'] for s in samples))+' GiB'],
 ['Maximum summed Strata VmSwap',str(max(proc_total(s,'VmSwap') for s in samples))+' KiB']])
add('''All experts appear RAM resident: full arena loaded, substantial available RAM, no Strata VmSwap, and per-request expert file counters below. NVMe PLE reads remain expected even when all experts are resident. Exact CPU/GPU expert execution counts and a direct physical-residency census of every arena page are not exposed; evidence supports residency without claiming such a census.''')
add('## 4. GPU / VRAM usage')
base=load('metrics-before.json')['hardware']
gpus=[s['gpu'] for s in samples if s['gpu'].get('mem_info_vram_used')]
table(['Quantity','Observed'],[
 ['Total VRAM',gib(base['gpu_mem_total'])+' GiB ('+str(base['gpu_mem_total'])+' bytes)'],
 ['Idle used / free before tests',gib(base['gpu_mem_used'])+' / '+gib(base['gpu_mem_total']-base['gpu_mem_used'])+' GiB'],
 ['Peak sampled VRAM',gib(max(int(g['mem_info_vram_used']) for g in gpus))+' GiB'],
 ['Minimum sampled free VRAM',gib(min(int(g['mem_info_vram_total'])-int(g['mem_info_vram_used']) for g in gpus))+' GiB'],
 ['GPU utilization range',str(min(int(g['gpu_busy_percent']) for g in gpus))+'–'+str(max(int(g['gpu_busy_percent']) for g in gpus))+'%'],
 ['Peak edge / junction temperature',str(max(int(g.get('temp1_input',0)) for g in gpus)/1000)+' / '+str(max(int(g.get('temp2_input',0)) for g in gpus)/1000)+' °C'],
 ['Peak sampled package power',str(max(int(g.get('power1_average',0)) for g in gpus)/1e6)+' W'],
 ['Configured power cap',str(max(int(g.get('power1_cap',0)) for g in gpus)/1e6)+' W'],
 ['Configured reserve','700 MiB; not a guaranteed steady-state free amount'],
 ['Expert cache','13103 slots, 25465 MiB (24.87 GiB), fully prefilled at startup'],
 ['Cache policy','PROFILE, ranked by routing frequency, no eviction']])
for phase in ['reading the prompt','thinking','answering']:
 ss=[s for s in samples if (s.get('live') or {}).get('phase')==phase]
 if ss:add(f"Sampled phase `{phase}`: {len(ss)} samples; GPU utilization {min(int(s['gpu'].get('gpu_busy_percent',0)) for s in ss)}–{max(int(s['gpu'].get('gpu_busy_percent',0)) for s in ss)}%.")
rows=[]
for r in results:
 ss=r['samples'];g=[x['gpu'] for x in ss]
 active=lambda k:[int(m.group(1)) for x in g for line in x.get(k,'').splitlines() if '*' in line for m in [re.search(r'(\d+)Mhz',line)] if m]
 sc=active('pp_dpm_sclk');mc=active('pp_dpm_mclk');pw=[int(x.get('power1_average',0))/1e6 for x in g];util=[int(x.get('gpu_busy_percent',0)) for x in g]
 rows.append([r['name'],f'{min(sc)}/{statistics.median(sc):.0f}/{max(sc)}',f'{min(mc)}/{statistics.median(mc):.0f}/{max(mc)}',f'{min(pw):.0f}/{statistics.median(pw):.0f}/{max(pw):.0f}',f'{min(util)}/{statistics.median(util):.0f}/{max(util)}'])
table(['Test','Active sclk MHz min/median/max','Active mclk MHz min/median/max','Power W min/median/max','GPU use % min/median/max'],rows)
add('Clock values are the active (*) entries reported by sysfs pp_dpm_sclk/mclk, not configured limits. The raw state listings remain in sampled JSON. Power peaks include a 399 W reading despite a 300 W reported cap; these are unvalidated driver telemetry samples, not proof of sustained electrical draw above the cap. Median power is 281–287 W across trials. The 99 °C junction maximum is a sampled sensor result; no throttle-state evidence was collected.')
add('''Idle edge/junction/memory temperatures were 34/36/32 °C; idle clocks sclk 42 MHz, mclk 96 MHz; idle package power 14 W. Performance mode auto. Peak memory-sensor temperature was not sampled (only idle 32 °C); edge/junction peaks are shown above. No direct thermal-throttle counter was captured, so absence of throttling cannot be proven solely from temperatures. No desktop-sized reservation is evident; most VRAM is explained by the expert cache, model weights, MTP, KV and buffers. Startup says prefill borrows 3886 cache slots (7.38 GiB); that temporary prompt-path use is distinct from a dynamic eviction policy. 13103 cached layer/expert pairs out of 24576 total is approximately 53.3% by count; the full pool remains in RAM. The configured 700 MiB reserve is reduced by later allocations: startup logged 562 MiB free, and the loaded idle device had approximately 405 MiB free before these tests. See warning section for actual request-time warnings.''')
add('## 5. NVMe / storage\n\n```text\n'+(root/'df-before.txt').read_text().strip()+'\n```')
add('''Data root: `/nvme/Strata-data`. Total model shard logical size: **83,617,662,656 bytes (77.875 GiB)**; on-disk model directory approximately 78 GiB. Whole Strata data tree approximately 86 GiB before this report; XFS `/dev/nvme0n1p1`, 374 GiB available in `df -hT`.

PLE is tensor `per_layer_token_embd.weight` in shard 2, shape [160, 320001536], IQ4_NL, **28,800,138,240 bytes** excluding its 192-byte GGUF header. `--ple-io direct` reads selected rows directly from NVMe, rather than copying the entire table into RAM. Startup direct-read probe: 99241 rows/s. SSD keepalive reads one table page after 100 ms idle, until 60 seconds after the last request. Thus NVMe reads during inference are not by themselves evidence of expert paging.

Pack: `/nvme/Strata-data/packs/iq3_s` (~1.5 GiB; dense.bin 1,538,035,200 bytes, tokenizer and native expert index). `/nvme/Strata-data/cache` was empty. MTP runtime files: `/nvme/Strata-data/mtp/rt/experts.bin` 707,788,800 bytes, `dense.bin` 116,099,072 bytes, `draft_vocab.bin` 425,196 bytes. MTP source/intermediate files remain under `/nvme/Strata-data/mtp` (~6.5 GiB total), including `mtp-q2_0.gguf` 889,017,216 bytes and `tensors/` (~4.9 GiB). Loaded MTP layer reports 835 MiB VRAM plus a 212.9 MiB draft head.

Existing storage baseline supplied for this host: **3272 MiB/s sequential read**. It was not remeasured. No destructive disk benchmark was run. Expert file-read counters and process I/O deltas are in the performance sections; total process reads also include PLE and cannot be attributed entirely to experts.''')
add('## 6. Actual running configuration and quality baseline\n\nService: `strata.service`; Python parent 298535, native engine child 298542. Actual config: `/opt/strata/strata-iq3_s.json`. API key is deliberately omitted.\n\n```json\n'+json.dumps(cfg,indent=2)+'\n```')
add('Explicit STRATA_ environment variables from the live engine:\n\n```text\n'+(root/'pid-298542-strata-env.txt').read_text()+'\n```')
add('''Baseline comparison: model/quant, native context, INT8 KV, 32768 resident KV, auto expert cache, full RAM arena, configured reserve 700 MiB, disabled vision, direct PLE, MTP depth 4, route-tail skip 0, disabled speed projection and parallel=1 all match. No meaningful configuration deviation was found. Engine cvec=0 and request projection=null confirm speed projection inactive; it is a separate feature from the disabled HC_Q8 path. Sampled MTP uses the default exact-match verification path; spec_min_p=0.5 is a draft gate, not probabilistic acceptance mode. Operational caveats: actual free VRAM is below the reserve value; configured `high` resolves to template `xhigh`; `spec=6` is the verification window rather than MTP depth.

Runtime defaults reported at startup, not explicit process environment entries: RDNA4 switches described by Strata as exact: GDN_HEAD, GDN_PP, GDN_CONVL2, GDN_NOY, CVEC_FUSE enabled; prompt chunk auto up to 16384. STRATA_HIP_WMMA remains off (startup says nothing enabled); STRATA_HC_Q8=0 prevents the optional Q8 hyper-connection projection path/re-quantization; probabilistic/coupled/Gumbel speculation flags are zero. Native Q5_K output head is part of the quant, not a Q8 compatibility conversion. CPU pool: 5 workers plus host thread. PCIe probe 12.2 GB/s; default pcie_frac 0.34 adapted to 0.30 after the earlier short request, as logged by Strata. No explicit user tuning was applied during this benchmark.''')
add('''## 7. Sampling configuration

Verified server configuration and `/props`: temperature **1.0**, top_p **0.95**, top_k **20**, min_p **0.0**, presence_penalty **0.0**, repetition_penalty **1.0**. No configured frequency_penalty; engine request parsing defaults it to **0.0**. No configured repetition window; engine window is zero unless a penalty is enabled, where server uses a fallback 64-token window. With all penalties neutral, the window has no active effect.

`/props` n_predict=-1 and source default max_tokens=0 mean no smaller fixed server output cap: available context limits unspecified requests. These benchmark requests explicitly use **max_tokens=24576** and **seed=42**. No custom stop strings were sent; normal model stop behavior applies, plus output/context cap, the thinking-budget boundary and repetition protection. The model's end-of-message stop can end a reply normally.

Verified precedence in serve/server.py: model-card defaults → configured sampling block → shared Chat settings → API request. GET `/settings` returned shared=false and defaults={} before testing. This harness sends no sampling overrides besides seed and output cap. A separate coding-agent client may override settings per request; its private/current settings were not inspected, and no claim is made that this benchmark reproduces that client's settings. The desired Qwen Thinking sampling baseline matches the observed server values.''')
add('''## 8. Reasoning configuration and semantics

- Thinking enabled by template default: `enable_thinking` undefined resolves to true. There is no explicit stored enable_thinking=true field; effective behavior is verified from the template and reasoning stream. Distinct non-thinking mode exists (`reasoning_effort=none`/enable_thinking=false); not used here.
- Config reasoning_effort=`high`; the model template maps high to **xhigh**. xhigh is an instruction sentence, not a different weight set or a guaranteed thinking-token count.
- `preserve_thinking` is unspecified; template default is **true**. Prior assistant reasoning is retained when supplied as reasoning_content by the client. A stateless chat API cannot preserve text the client omits. These tests are single-turn and do not independently test multi-turn preservation.
- Configured thinking budget **16384 tokens**. No separate soft budget was found. The budget is enforced at a clean parser/token boundary by injecting a wrap-up and closing think tag; it is not an exact hard allocator boundary. A request output cap can reduce it to leave answer room. With max_tokens 24576, the answer-room ceiling is 18432 and does not reduce 16384. Final request usage below shows whether this limit was reached.
- Repetition guard: repeat_stop_tokens default **256** consecutive identical tokens. Reasoning-loop recovery=`recover`, with checks every **512** output tokens, 0.25 repeated coverage threshold over the last 2000 words using 12-word passages appearing three times; history bounded to 30000 words. Tail-period detection also considers the last 2048 characters and periods up to 64. On detection, Strata can continue with a low-effort instruction; this changes prompt behavior and is not pure untouched model reasoning.
- Reasoning is returned in a separate `reasoning_content` stream field. It is not merely hidden internal retained state. This harness discards its text and retains metrics only. Final answers are saved separately. Model-generated reasoning and final tokens count against the output cap and context; retained prior reasoning also consumes later input context.
- Strata normalizes effort aliases, renders the supplied template, separates reasoning from final content, enforces budget/answer room and loop recovery. Those are the relevant runtime modifications. No client reasoning override was sent in these tests. Unsupported exact hard/soft thinking-duration limits are marked unavailable rather than approximated.''')

for r in results:
 title={'systemd':'9. Reasoning behavior test — systemd','coding':'10. Coding reasoning test','recovery':'11. Wrong-hypothesis recovery test'}[r['name']]
 add('## '+title+'\n\nExact prompt (unchanged):\n\n> '+r['prompt']+'\n\nFull final response is archived in `'+str(root/(r['name']+'.json'))+'` under `answer`; reasoning text was discarded. Behavioral characterization is in section 16; request metrics are in section 12.')
table(['Prompt','SHA-256 of exact UTF-8 text'],[[r['name'],hashlib.sha256(r['prompt'].encode()).hexdigest()] for r in results])
add('## 12. Per-test performance measurements')
rows=[]
for r in results:
 req=max(r['metrics_after']['requests'], key=lambda q:q['time']);u=r['usage'];rt=u.get('completion_tokens_details',{}).get('reasoning_tokens');total=u.get('completion_tokens');fin=total-rt if rt is not None and total is not None else None
 r['_reasoning_tokens']=rt;r['_final_tokens']=fin
 ss=r['samples'];g=[s['gpu'] for s in ss if s['gpu'].get('mem_info_vram_used')]
 rows.append([r['name'],u.get('prompt_tokens','unavailable'),rt if rt is not None else 'unavailable',fin if fin is not None else 'unavailable',total,r['finish'],f"{r['ttft_s']:.3f}",f"{r['first_final_s']:.3f}",f"{r['elapsed_s']:.3f}",r['timings'].get('prompt_per_second','unavailable'),req.get('decode_tok_s','unavailable')])
table(['Test','Prompt tokens','Reasoning tokens','Final tokens*','Total generated','Finish','TTFT any','First final','Total time','Prefill tok/s','Decode tok/s'],rows)
add('''*Final tokens = completion_tokens − API reasoning_tokens when that detail exists; includes any non-reasoning structural/terminating tokens and is not guaranteed to be exact visible-answer tokenization. Missing counts remain unavailable. Prefill and decode rates are server timing fields; TTFT is client time to the first nonempty reasoning or content chunk, not an initial role-only SSE chunk. First-final latency includes thinking. Decode covers reasoning plus final answer, not final alone. Short prefills have substantial fixed overhead and are not bulk-prompt throughput estimates.''')
rows=[]
for r in results:
 req=max(r['metrics_after']['requests'], key=lambda q:q['time']);off=req.get('drafts_offered',0);acc=req.get('drafts_accepted',0);ss=r['samples'];g=[s['gpu'] for s in ss if s['gpu'].get('mem_info_vram_used')]
 rows.append([r['name'],off,acc,f'{100*acc/off:.2f}%' if off else 'unavailable',f"{100*req['hit_rate']:.2f}%" if req.get('hit_rate') is not None else 'unavailable',req.get('pcie_share','unavailable'),req.get('ram_blobs','unavailable'),req.get('file_blobs','unavailable'),req.get('file_mb','unavailable'),gib(max(int(x['mem_info_vram_used']) for x in g)),kibg(max(proc_total(s,'VmRSS') for s in ss)),kibg(min(s['mem']['MemAvailable'] for s in ss)),req.get('reasoning_recoveries','unavailable')])
table(['Test','Drafts proposed','Accepted','Acceptance','Expert hit','PCIe share','RAM blobs**','File blobs','File MB','Peak VRAM GiB','Peak Strata RSS GiB','Min available GiB','Recoveries'],rows)
add('''**These are Strata request counters, not a count of all CPU-executed experts. Cache residency is fixed at 13103/13103 profile slots; no per-request live occupancy gauge is exposed. CPU versus GPU expert execution counts are unavailable; expert cache hit/PCIe-share counters and the startup GPU-hit-path statement characterize placement without inventing counts. All expert file counters are interpreted separately from PLE/process disk I/O.''')
rows=[]
for r in results:
 rt=r['_reasoning_tokens'];ft=r['_final_tokens'];rd=r['reasoning_stream_duration_s'];fd=r['final_stream_duration_s']
 rows.append([r['name'],f'{rd:.3f}' if rd else 'unavailable',f'{fd:.3f}' if fd else 'unavailable',f'{rt/ft:.3f}' if rt is not None and ft else 'unavailable','unavailable'])
table(['Test','Observed reasoning stream span','Final stream to request end','Reasoning/final ratio','Separately exposed reasoning tok/s'],rows)
add('Coding timing caveat: the server aggregates prefill/replay work across loop recovery and budget wrap-up. Its 16.8 prompt tok/s field divides the original 127 prompt tokens by 7553.6 ms of total prompt work, including replay; it is not actual fresh long-prompt throughput. Native log segments report 138.1 tok/s for the initial 127-token prompt and 1529.9 tok/s for a 9843-token recovery replay. The combined 65.9 decode tok/s also spans these automatic continuations. API completion_tokens=20580 versus engine_generated=20565 reflects 15 runtime-added/accounting tokens; neither is a final-answer-only count.')
add('''Stream spans are client phase-boundary measurements, not separately instrumented GPU compute durations. They include buffering/transfer and small request-completion overhead. Chat Completions in this build does not serialize the internally tracked reasoning_tokens into usage, although the Responses API can expose them. The protocol was kept constant for all three tests; no counts were reconstructed by retokenizing discarded reasoning. Reasoning tokens, final-only tokens and their ratio are therefore unavailable here. Separately exposed reasoning/final decode rates are unavailable; do not compare a derived phase quotient to server decode throughput as if they were the same metric.''')
rows=[]
for r in results:
 ss=r['samples']
 def io(s,k):
  return sum(int(line.split()[1]) for p in s['processes'].values() for line in p['io'].splitlines() if line.startswith(k+':'))
 rows.append([r['name'],io(ss[-1],'read_bytes')-io(ss[0],'read_bytes'),io(ss[-1],'rchar')-io(ss[0],'rchar'),max(r['metrics_after']['requests'],key=lambda q:q['time']).get('reused','unavailable')])
table(['Test','Sampled process read_bytes delta','Sampled process rchar delta','Prompt cached tokens'],rows)
add('Process I/O deltas span sampled boundaries, not the exact full request. read_bytes represents storage-layer reads and rchar includes logical reads; neither isolates experts from PLE. No expert file blobs were reported if the request file counters above are zero.')
add('''## 13. 256K context readiness

Configured and API-advertised context agree at **262144**; native model metadata agrees. The already loaded streaming KV allocation (3.09 GiB pinned host RAM) supports the full configured window; the resident window stays 32768. Startup allocated those resources successfully without a KV pin/allocation failure. Substantial host MemAvailable and no Strata swap support feasibility without evicting the expert arena.

Previously saved same-day diagnostic `benchmarks/strata-256k.json` recorded **257951 input tokens + 10 completion tokens = 257961 total**, successful marker retrieval, TTFT 93.728 seconds, 143263 fresh tokens at 1532.6 tok/s with 114688 cached tokens. This was an earlier run of the same model/configuration class, not this benchmark's current process. It does not prove an exactly full 262144-token conversation with long output. No hundreds-of-thousands-token prompt was resent here.

Caution: free VRAM is only a few hundred MiB after dynamic buffers, so “should fit” is supported by allocations and the historical near-limit trial, not a guarantee against every future graph/workload allocation. No expert eviction or swap is required by the observed allocations; maximum sustained context stability remains untested here.''')
add('## 14. Swap and memory pressure')
first=samples[0];last=samples[-1]
table(['Measure','Before','After / delta'],[
 ['Swap used KiB',first['mem']['SwapTotal']-first['mem']['SwapFree'],last['mem']['SwapTotal']-last['mem']['SwapFree']],
 ['pswpin pages',first['vmstat'].get('pswpin'),last['vmstat'].get('pswpin',0)-first['vmstat'].get('pswpin',0)],
 ['pswpout pages',first['vmstat'].get('pswpout'),last['vmstat'].get('pswpout',0)-first['vmstat'].get('pswpout',0)],
 ['pgscan_kswapd',first['vmstat'].get('pgscan_kswapd'),last['vmstat'].get('pgscan_kswapd',0)-first['vmstat'].get('pgscan_kswapd',0)],
 ['pgscan_direct',first['vmstat'].get('pgscan_direct'),last['vmstat'].get('pgscan_direct',0)-first['vmstat'].get('pgscan_direct',0)],
 ['Cached KiB',first['mem'].get('Cached'),last['mem'].get('Cached')]])
add('After-test `free -h`:\n\n```text\n'+(root/'free-after.txt').read_text().strip()+'\n```\n\nAfter-test `vmstat` (first row is since-boot average; subsequent rows are interval samples):\n\n```text\n'+(root/'vmstat-after.txt').read_text().strip()+'\n```')
add('''The pre-existing small swap allocation is not evidence that Strata swapped. Per-process VmSwap and test-window swap counter deltas above are the relevant checks. There was measurable reclaim scanning during the first/systemd trial: pgscan_kswapd increased by 82268 pages and pgscan_direct by 11936 pages. These counters did not increase during the coding or recovery trials. Cached memory fell by about 310636 KiB across the full window. Thus it would be incorrect to claim zero reclaim. With >70 GiB available, zero swap traffic and stable expert RSS, the samples do not establish sustained global RAM exhaustion; the reason for the initial reclaim is not isolated (driver/zone allocation or other host activity may contribute). Global major faults increased by 301; they are not process-specific and do not prove expert paging. Whole-host disk reads include the benchmark and other services, not just the inference engine.''')
add('## 15. Errors, warnings, and fidelity observations')
log=Path('/nvme/Strata-data/logs/strata-iq3_s.log').read_text();current=log[log.rfind('[strata] 2026-10-18 16:37:54'):];(root/'current-engine-log.txt').write_text(current)
interesting=[l for l in current.splitlines() if re.search(r'WARNING|error|fail|unavailable|rounded|loop|budget|head|fallback',l,re.I)]
add('Relevant exact current-process engine log lines:\n\n```text\n'+'\n'.join(interesting)+'\n```')
jlines=[l for l in (root/'startup-after.txt').read_text().splitlines() if any(x in l.lower() for x in ['repeated reasoning detected','thinking budget reached','thinking capped','error','timeout','failed'])];add('Relevant service-journal lines (all clients in the window):\n\n```text\n'+'\n'.join(jlines)+'\n```')
integrity=load('integrity.json');add('Integrity/service check:\n\n```json\n'+json.dumps(integrity,indent=2)+'\n```')
add('''MAP_HUGETLB unavailability is a hugepage optimization limitation, not failure to load/pin the arena: startup reports successful portable host registration and requests transparent huge pages. CPU AVX2 is the expected host kernel path for this processor, not a quant fallback. The “experimental native Q5_K head” is the original model head; conversions.json has compat_bf16=false and an empty tensors conversion list. RDNA4 cache GPU/CPU rounding differences are explicitly noted in startup; native cache execution is enabled.

Review window: current service started 16:37:53 EDT, engine 16:37:54, through completion of these tests. Kernel and service journal snapshots are saved. Historical warnings from earlier deployments are not attributed to this test process. No HIP/ROCm failure, GPU reset, OOM, model-corruption report, expert/PLE loading failure, context allocation failure, service restart or benchmark request timeout was observed in the captured window. The low-free-VRAM warning, unavailable explicit hugepages, native Q5_K head notice and coding reasoning-loop recovery/budget wrap-up are the material findings. Successful graph-capture lines containing “no error” are success messages, not errors. Automatic loop recovery is described below.''')
add('## 16. Behavioral assessment\n\n'+(root/'assessment.md').read_text())
add('''## 17. Future model comparison procedure and limitations

Use exactly the three quoted prompts in the saved request JSON and this report, unchanged and in the same order. Keep seed=42, max_tokens=24576, streaming usage enabled, one sequential request, no tools/system prompt, same inherited quality-first sampling, xhigh/16384 thinking budget, INT8 KV/32768 resident/native 262144 context, same reserve and MTP policy. Record actual effective settings and flag differences rather than silently forcing unsupported settings. Save each comparison to a new timestamped directory/report. `procedure.py` is the exact harness used here, including telemetry; paths/model label/config location need explicit adaptation for a different deployment and should be documented. It is not an installer and must not overwrite or restart a live configuration.

For UD-Q4_K_XL, record the real model publisher, quant tensor mix, file hashes/provenance, pack conversion policy and MTP artifact identity. Do not assume it is GSQ-RCO. The GPU's auto expert-cache capacity and CPU/GPU execution share may change with quant size; those are measured comparison variables. Report versions, CPU/GPU clocks/power/temperature, RAM/swap, context allocation, prompt cache reuse, finish status, recovery count and actual draft acceptance alongside throughput. Include TTFT to any token and latency to final answer separately.

These are three stochastic, unblinded single trials, not a robust quality ranking. The recovery test assesses conditional rejection of a misleading hypothesis; no follow-up evidence was supplied, so actual multi-turn belief revision was not tested. No real RHEL/PostgreSQL host was changed, and generated troubleshooting advice was not executed against production. A coding execution fixture, if performed, is described in the assessment. Repeat trials and blind scoring would be needed to answer whether UD-Q4_K_XL measurably improves correctness, convergence, or agent completion enough to justify any speed/RAM/cache tradeoff. This IQ3_S report alone cannot answer that comparison.''')
report=root.parent/(root.name.removesuffix('-data')+'.md');add('Report file: `'+str(report)+'`. The complete Markdown file is printed verbatim to the terminal after final validation.')
report.write_text('\n'.join(text));print(report)
