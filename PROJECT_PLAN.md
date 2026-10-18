# Scar.lab Strata project plan

## Objective

Replace the active Scar inference backend with stable Strata and the full Qwen3.8-Flash-Next GSQ-RCO IQ3_S model. Preserve the current vLLM deployment for rollback. Quality and task completion take priority over throughput.

## Implementation sequence

1. Inventory hardware, active containers, endpoints and rollback configuration. Record a same-day vLLM baseline before stopping it.
2. Install stable Strata v0.1.45 under `/opt/strata`, using the existing ROCm 7.2.2 installation. Store downloads, packs, MTP and logs under `/nvme/Strata-data`. Build HIP for gfx1201. Keep vLLM online during installation.
3. Review and enforce the generated configuration: IQ3_S, 262144 native context, INT8 KV, 32768 resident KV window, full expert arena in RAM, automatic expert cache, 700 MiB VRAM reserve, direct PLE I/O, no vision, one request, exact-match MTP depth 4. Disable route-tail skipping, speed projection and probabilistic speculation.
4. Configure thinking at xhigh with preserved reasoning, 16384 thinking tokens, loop recovery, and Qwen's requested sampling defaults. Document any unsupported setting rather than silently substituting it.
5. Install a systemd service. Stop vLLM before starting Strata. Retain the old container, image, configuration and models. Restore vLLM if Strata cannot serve correctly.
6. Validate API health, chat, tool calls, multi-turn state and engineering tasks. Measure prompt throughput, decode throughput and first-token latency. Exercise approximately 32K, 128K and near-256K inputs, leaving output room. Check resource usage, swap and kernel errors.
7. Record version, configuration, measurements, limitations and rollback commands. Send a Slack notification only after successful live inference, with remaining validation limits stated.

## Acceptance gates

- A healthy API alone is insufficient: an actual generation and tool-call round trip must pass.
- No configuration fallback to a smaller model, lower KV precision, mmap experts or reduced context.
- Record measured performance and task outcomes; do not claim improvement without comparative evidence.
- Separate operational readiness from full long-running quality acceptance. Any incomplete goals remain explicitly open.

## Current inventory

- Host: scar.lab, Ubuntu, kernel 7.0.0-38-generic; user cpaquin.
- GPU: AMD Radeon AI PRO R9700, gfx1201, 32 GB; ROCm `/opt/rocm-7.2.2`.
- RAM: 125 GiB usable; swap 31 GiB, unused at inventory.
- NVMe: `/nvme`, 460 GiB initially available.
- Active backend: Docker `radlight-vllm`, model alias `scar-coder`, host port 8080, native context 262144.
- Existing dashboard: port 8088. Hermes containers may be stopped for cutover.
- Rollback compose: `/home/cpaquin/Workspace/Git/Radiance-vLLM-AMD-R9700/compose.radlight.yaml`.
- Full rollback container paths and restart policy: `docs/rollback-inventory.json`.
- Slack delivery uses the existing Hermes bot configuration; readiness delivered to `#hermes-local` after live generation and tool checks.

## Validation record

Implementation and controlled checks are complete. Operational readiness, API/tool use, the executable coding fixture, and retrieval through 257,951 tokens passed. Comparative decode performance is about 37% below the old backend; improved long-horizon quality and extended stability remain open acceptance gates. Full definition of done is not claimed. Results are recorded under `benchmarks/` and `docs/DEPLOYMENT.md`.

## Follow-up implementation

Completed the medium/xhigh reasoning comparison and restored xhigh after medium missed one of 18 cases. Adapted and deployed the sibling inference dashboard for native Strata, authenticated benchmarks, GGUF shard discovery, and public runtime settings. Browser validation and all 56 dashboard tests passed. Comparative reasoning results are in `benchmarks/REASONING_COMPARISON.md`.
