# Scar.lab Strata Deployment Goals

## Objective

Deploy **Strata** on `scar.lab` as a quality-first local inference platform using:

- **Model:** Qwen3.8-Flash-Next GSQ-RCO IQ3_S
- **GPU:** AMD Radeon AI Pro R9700, 32 GB VRAM
- **System RAM:** 128 GB DDR4-3200
- **Storage:** 512 GB NVMe mounted at `/nvme`
- **OS:** Headless Linux server; no desktop environment
- **Primary use cases:** coding, repo-level software engineering, Linux/system administration, troubleshooting, log analysis, tool use, and long-running agentic tasks

The main goal is **higher usefulness, reasoning quality, and task completion reliability than the current vLLM deployment**. Raw speed is secondary, but the new deployment should not be materially slower than the current Scar inference stack if avoidable.

---

## Core Priorities

In order of importance:

1. **Model quality / correctness**
2. **Agentic reliability and convergence**
3. **Avoid reasoning/tool-call loops**
4. **Long-context usefulness**
5. **Stable AMD R9700 execution**
6. **Performance at least comparable to the current Scar deployment**
7. **Efficient use of VRAM, system RAM, and NVMe**

Do not trade model quality for small throughput gains unless the change is clearly proven safe.

---

## Hardware Baseline

Treat this hardware as authoritative for the deployment:

### GPU

- AMD Radeon AI Pro R9700
- 32 GB VRAM
- Headless system
- No desktop compositor or GUI workload
- Strata may use essentially all VRAM except a small runtime safety reserve

### System RAM

- 128 GB total physical RAM
- 4 x 32 GB matched DDR4-3200 DIMMs
- All four DIMMs are currently configured at 3200 MT/s
- Linux reports approximately 125 GiB usable

Use the system RAM aggressively for the full MoE expert pool.

Do **not** deliberately mmap/offload experts to NVMe when the complete expert set can remain resident in RAM.

### NVMe

Mounted at:

```text
/nvme
```

Measured sequential read performance:

```text
~3272 MiB/s
~3.43 GB/s
```

The NVMe may be used fully for Strata/model storage.

Preferred data location:

```text
/nvme/Strata-data
```

---

## Model Selection

Primary model:

```text
Qwen3.8-Flash-Next GSQ-RCO IQ3_S
```

Reasons:

- Full Qwen3.8-Flash-Next MoE architecture
- Approximately 125B total parameters
- Approximately 6B active parameters per token
- Full expert population retained
- GSQ-RCO quantization has shown near-BF16 benchmark quality
- IQ3_S has a substantially smaller expert footprint than UD-IQ4_XS while retaining very high quality
- Better expert-cache density in 32 GB VRAM
- Strong fit for Strata's native execution path

Do not substitute a smaller IQ2 quant or the reduced Coder/expert-pruned model unless explicitly requested.

Keep these as possible later A/B candidates, not initial deployment targets:

```text
Qwen3.8-Flash-Next GSQ-RCO IQ3_XXS
Qwen3.8-Flash-Next UD-IQ4_XS
```

---

## Context Window

Target the model's full native context:

```text
262144 tokens
```

This is the desired "256K" configuration.

Do not use YaRN or other context-extension mechanisms for the initial deployment.

The model should use its native context window only.

---

## KV Cache

Use:

```text
KV cache: INT8
```

Do not use Q4 KV for the initial quality baseline.

Desired behavior:

- Keep a useful active KV region in VRAM
- Stream the remainder through host RAM when necessary
- Preserve as much R9700 VRAM as possible for model runtime and hot MoE experts

Initial target:

```text
KV resident window: 32768 tokens
Maximum context:     262144 tokens
```

Adjust only if Strata's current release requires different syntax or if measured VRAM pressure makes another value clearly preferable.

---

## VRAM Policy

Because Scar is headless, maximize useful R9700 VRAM utilization.

Initial target:

```text
VRAM reserve: 700 MiB
Expert cache: auto
```

Do not reserve several GB for a desktop environment that does not exist.

Do not set VRAM reserve to zero. Maintain enough safety margin for transient runtime allocations and HIP stability.

Allow Strata to use remaining available VRAM for:

- Dense model components
- Runtime buffers
- KV working set
- MTP/speculative decoding
- Hot MoE expert cache

---

## System RAM Policy

The full GSQ-RCO IQ3_S expert pool should remain resident in RAM.

Expected expert footprint is roughly:

```text
~47 GiB
```

With approximately 125 GiB usable RAM, Scar has ample headroom.

Do not enable low-memory expert modes such as:

```text
--mmap-experts
```

unless required to work around a real defect.

Do not artificially constrain expert residency with a small resident RAM budget.

Leave enough RAM for:

- Linux
- Strata process/runtime
- KV cache
- filesystem cache
- SSH/admin tools
- agent clients
- observability/debugging

A reasonable operational objective is to keep at least roughly 10-16 GiB genuinely available under normal inference load, although more is expected with IQ3_S.

Avoid swapping model/runtime pages during performance testing.

For benchmarks, swap may be temporarily disabled if needed:

```bash
sudo swapoff -a
```

Do not permanently remove swap without a specific reason.

---

## NVMe / PLE Policy

Use the NVMe for:

- Strata data
- model files
- packs
- MTP data
- PLE / n-gram data
- temporary model installation artifacts

Initial PLE policy:

```text
PLE I/O: direct / NVMe
```

Do not preload the PLE table into RAM initially.

The NVMe has already measured approximately 3.27 GiB/s sequential reads and is fast enough to establish the baseline with PLE on disk.

RAM-backed PLE may be tested later as an A/B optimization.

---

## Vision

Disable vision initially.

Target:

```text
vision = off
```

Scar's primary purpose is coding, systems work, troubleshooting, and agentic engineering tasks.

Do not spend VRAM/RAM on vision components unless vision becomes an explicit requirement.

---

## Sampling / Reasoning Baseline

Use Qwen's recommended Thinking Mode behavior as the initial baseline.

Target behavioral settings:

```text
enable_thinking      = true
reasoning_effort     = xhigh
preserve_thinking    = true

temperature          = 1.0
top_p                = 0.95
top_k                = 20
min_p                = 0.0
presence_penalty     = 0.0
repetition_penalty   = 1.0
```

Do not use greedy decoding for the baseline.

Do not aggressively lower temperature merely because the workload is coding.

The primary problem being addressed is poor long-horizon reasoning and repeated/looping attempts, so preserve enough sampling flexibility for the model to abandon failed hypotheses.

---

## Reasoning Budget / Loop Protection

Use a bounded reasoning budget rather than allowing effectively unlimited reasoning.

Initial target:

```text
reasoning budget: 16K-32K tokens per turn
```

Prefer starting around:

```text
16384 tokens
```

and increase only if real tasks consistently need more.

If supported by the installed Strata release, enable reasoning-loop recovery behavior.

The goal is:

```text
inspect
→ hypothesize
→ act
→ test
→ interpret
→ finish or genuinely change strategy
```

Avoid:

```text
modify
→ test
→ repeat same theory
→ modify again
→ rediscover prior failure
→ loop indefinitely
```

Do not initially modify repetition penalties unless loops are observed under the recommended Qwen sampling configuration.

If loops persist, test small changes such as:

```text
repetition_penalty = 1.03
```

then:

```text
repetition_penalty = 1.05
```

Change one behavioral parameter at a time.

---

## Quality-First Strata Settings

Explicitly avoid optimizations that intentionally change model behavior in exchange for small speed gains.

### Route-tail skipping

Disable route-tail skipping for the quality baseline.

Target:

```text
STRATA_ROUTE_TAIL_SKIP=0
```

Do not skip lower-ranked routed experts simply to improve decode speed.

### Experimental Speed Projection

Disable experimental speed projection.

The initial baseline must prioritize output fidelity and reproducibility over speculative speed enhancements.

### Approximate/Probabilistic Speculation

Do not enable experimental probabilistic speculative sampling modes that can alter output distribution.

---

## MTP / Speculative Decoding

MTP/speculative decoding may remain enabled if it uses Strata's normal exact-match verification path.

Initial target:

```text
MTP enabled
speculative depth approximately 4
```

The purpose is to recover speed without intentionally reducing model quality.

If MTP causes instability, output corruption, or behavioral differences, disable it temporarily and compare against a non-speculative baseline.

---

## Concurrency

Initial optimization target:

```text
1 active inference request
```

Scar is intended primarily for interactive agent/coding work rather than multi-user throughput.

Do not sacrifice single-request quality or context capacity to maximize concurrent requests.

Concurrency can be revisited after the primary workload is validated.

---

## Storage Layout

Preferred structure:

```text
/opt/strata
    Strata source/runtime

/nvme/Strata-data
    models/
    packs/
    mtp/
    cache/
    logs/
```

Use Strata's supported data-directory configuration rather than symlinks where possible.

---

## Existing Services

Scar currently hosts:

- vLLM
- Hermes

Hermes is not materially used and may be shut down.

Do not run vLLM and Strata simultaneously if they compete for the R9700.

Preserve the existing vLLM deployment/configuration so it can be restored for comparison or rollback.

Do not destroy the current model environment until the Strata deployment has been validated.

---

## Installation Strategy

Use the current stable Strata release rather than an arbitrary development commit.

Target backend:

```text
HIP / AMD
```

Target model family:

```text
Qwen
```

Target quant:

```text
GSQ-RCO IQ3_S
```

Target context:

```text
262144
```

Target vision:

```text
off
```

Target data directory:

```text
/nvme/Strata-data
```

Target VRAM reserve:

```text
700 MiB
```

Prefer an installation workflow that completes setup/download/configuration without automatically launching the service until the generated configuration has been reviewed.

Conceptual setup target:

```bash
./setup.sh   --backend hip   --family qwen   --model IQ3_S   --context 262144   --vision no   --data-dir /nvme/Strata-data   --vram-reserve-mib 700   --no-start
```

IMPORTANT:

Do not assume these exact flags are still valid.

Before executing installation, inspect the current stable Strata documentation and `setup.sh --help` for the installed version and adapt syntax as necessary while preserving the intended configuration.

---

## Generated Configuration Review

Before first launch, inspect the generated Strata configuration.

Verify that the resulting configuration is functionally equivalent to:

```text
Model              GSQ-RCO IQ3_S
Backend            HIP
Maximum context    262144
KV                 INT8
KV resident        ~32768
Expert cache       auto
Expert pool        fully RAM-resident
VRAM reserve       ~700 MiB
Vision             disabled
PLE                 NVMe/direct
MTP                 enabled
Spec depth          ~4
Route-tail skip    disabled
Speed projection    disabled
Parallel requests   1
```

Do not start the service if setup silently chose a lower-memory model, smaller context, Q4 KV, mmap experts, large VRAM reserve, or another quality-reducing fallback.

---

## API

Expose an OpenAI-compatible API suitable for:

- Codex-style clients
- OpenCode
- PI
- other coding agents
- custom scripts

Bind only to the network interfaces required by the lab environment.

Do not expose the API publicly to the Internet.

Preserve compatibility with existing Scar clients where practical.

---

## Performance Goal

Speed is secondary to usefulness, but the new deployment should not be materially slower than Scar's current model.

Record baseline measurements from the existing vLLM deployment if still available.

At minimum measure:

- prompt processing tokens/sec
- decode/generation tokens/sec
- time to first token
- VRAM usage
- RAM usage
- expert-cache residency/hit behavior if available
- behavior at small context
- behavior around 32K
- behavior around 128K
- behavior approaching 256K

Do not optimize against tokens/sec alone.

---

## Quality Validation

The primary success criterion is whether the model solves real engineering work more reliably than the current Scar model.

Test tasks should include:

1. Repository-level coding changes
2. Multi-file debugging
3. Linux service troubleshooting
4. systemd failures
5. container/runtime troubleshooting
6. Ansible/configuration-management work
7. shell/Bash tasks
8. Python development
9. log analysis
10. dependency/environment failures
11. tool calls
12. tasks where the initial hypothesis is deliberately wrong
13. tasks requiring the model to stop editing and gather more evidence
14. long-context repository/document work
15. multi-turn agent tasks

Track:

```text
Task solved successfully
Number of tool calls
Number of failed attempts
Repeated commands/actions
Repeated file edits
Unnecessary changes
Whether failed hypotheses are abandoned
Whether the model recognizes lack of progress
Whether human intervention is required
Total task completion time
```

The primary metric is:

```text
successful task completion without looping or human rescue
```

not benchmark score or raw tokens/sec.

---

## Stability Validation

Before declaring deployment complete:

- Confirm ROCm/HIP sees the R9700 correctly
- Confirm Strata is using the R9700
- Confirm the complete expert set is resident in system RAM
- Confirm no unintended swap activity during inference
- Confirm NVMe data path is `/nvme/Strata-data`
- Confirm context can be configured to 262144
- Confirm 32 GB VRAM is used aggressively without OOM
- Confirm API remains stable during long agent runs
- Confirm no kernel/HIP errors under sustained use
- Confirm memory configuration remains stable at DDR4-3200

If unexplained crashes occur, investigate memory and HIP stability before reducing model quality.

---

## Rollback

Keep the existing vLLM deployment recoverable.

A rollback must not require rebuilding Scar from scratch.

Document:

- old service names
- old model path
- old configuration
- how to stop Strata
- how to restore/start vLLM
- any ports changed
- any client endpoint changes

---

## Documentation Requirement

As changes are made, keep the deployment documented.

At minimum capture:

- Strata version
- commit/tag
- ROCm version
- kernel version
- model filename/version
- quant
- context size
- KV configuration
- expert-cache configuration
- VRAM reserve
- environment variables
- systemd service configuration
- API endpoint
- observed VRAM/RAM usage
- benchmark results
- stability notes
- known issues
- rollback procedure

Do not leave important tuning decisions only in shell history.

---

## Definition of Done

The deployment is complete when all of the following are true:

- Qwen3.8-Flash-Next GSQ-RCO IQ3_S runs reliably on the R9700 through Strata
- Full native 262144-token context is available
- INT8 KV is in use
- Full expert pool is RAM-resident
- R9700 VRAM is efficiently utilized
- PLE/model data is stored on `/nvme`
- No desktop-related VRAM reservation is wasting capacity
- No quality-reducing route-tail or speed-projection optimization is enabled
- OpenAI-compatible API is usable by coding agents
- Performance is not materially worse than the previous Scar deployment
- Real coding/system tasks show improved usefulness and fewer reasoning/tool loops
- Existing vLLM deployment remains recoverable
- Final configuration and validation results are documented

## Guiding Principle

When choosing between two configurations:

> Prefer the configuration that improves correctness, reasoning consistency, coding/system-task success, and agent convergence, even if it costs some throughput.

Only sacrifice model fidelity when a performance problem is demonstrated and the quality impact has been measured.
