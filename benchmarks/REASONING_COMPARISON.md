# Medium versus xhigh reasoning

2026-10-18. The live default was changed to medium and tested, then restored to high (the template maps high to xhigh) to retain the quality-first baseline. Only reasoning effort changed; the configured 16384-token budget, sampling, model, KV and fidelity settings stayed fixed.

The same nine task types ran twice with seeds 42 and 43. Reasoning probes requested 16384 thinking tokens with 20000 total output tokens; the server reserved answer room and applied an effective 15000-token thinking cap. The executable coding fixture retained its original 1536-token request thinking budget and 4096-token output limit in both arms.

| Task | xhigh correct | Medium correct | xhigh median time | Medium median time |
| --- | --- | --- | --- | --- |
| constrained-optimization | 2/2 | 2/2 | 21.9 s | 19.4 s |
| dependency-order | 2/2 | 2/2 | 47.4 s | 36.1 s |
| aliasing-code-trace | 2/2 | 1/2 | 13.1 s | 10.7 s |
| base-rate-reasoning | 2/2 | 2/2 | 6.4 s | 6.9 s |
| fault-isolation-xor | 2/2 | 2/2 | 10.5 s | 9.7 s |
| insufficient-evidence | 2/2 | 2/2 | 7.5 s | 8.5 s |
| critical-path | 2/2 | 2/2 | 7.1 s | 7.1 s |
| update-hypothesis | 2/2 | 2/2 | 6.3 s | 6.3 s |
| multi-resource-optimization | 2/2 | 2/2 | 207.0 s | 105.1 s |

**Reasoning accuracy: xhigh 18/18; medium 17/18.** Both harder optimization trials passed under medium, with faster completion. The failed medium trial was a Python aliasing trace: it failed to propagate an append through two references to the same list. The expected rows were `[[1,3,6],[2],[1,3,6]]`; medium returned `[[1,3],[2],[1,3,6]]`. This was a wrong final answer with a normal finish, not truncation or a JSON-format failure.

Both coding trials passed all five executable tests. Xhigh took 39.69 s with 9 tool calls; medium took 27.73 s with 9 tool calls. The medium trace gathered evidence, ran failing tests, edited the implementation, and reran passing tests. Neither trace required human rescue.

## Decision

Restore xhigh as the quality-first default. The observed faster medium responses did not establish a safe correctness tradeoff. Medium remains available through a per-request `reasoning_effort: medium` override. No temperature, penalty, precision or thinking-budget reduction was applied.

The sample is small and synthetic; one observed regression is enough to avoid replacing the verified default, but it does not establish a population-wide accuracy difference. Timing is descriptive and includes cache/hardware/runtime variation. A broader blind comparison on real engineering tasks remains open.

## Reproduce medium tests

```bash
export STRATA_API_KEY="$(cat /home/cpaquin/.config/strata/api-key)"
python3 scripts/reasoning_eval.py --effort medium --output benchmarks/strata-reasoning-medium.json
python3 scripts/agent_eval.py --url http://scar.lab:8080 --effort medium --output benchmarks/strata-agent-medium.json
```
