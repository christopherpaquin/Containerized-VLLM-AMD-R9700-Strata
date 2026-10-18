# Reasoning quality probes

Run date: 2026-10-18. Model: Qwen3.8-Flash-Next GSQ-RCO IQ3_S, Strata v0.1.45 on scar.lab.

## Method

Novel synthetic tasks with answers checked by executable reference rules, not an LLM judge. Two trials per task, seeds 42 and 43, temperature 1.0, top_p 0.95, top_k 20, xhigh effort. No tools or hints were supplied to the model. No service settings or model weights were changed.

Requests allow 20000 total output tokens and request a 16384-token thinking budget. The server reserves answer room and reports an effective 15000-token thinking cap. This avoids the earlier diagnostic suite's restrictive 1024-token thinking cap.

The uncertainty task has an automatic label/format check plus human review of both possible-world examples. Both trials described one causal and one non-causal world consistent with the facts. Multi-turn scoring requires both the original uncertainty set and the correct revision after new evidence.

## Screening results

| Task | Correct / trials | Elapsed seconds |
| --- | --- | --- |
| constrained-optimization | 2/2 | 19.8, 24.1 |
| dependency-order | 2/2 | 37.3, 57.6 |
| aliasing-code-trace | 2/2 | 16.0, 10.1 |
| base-rate-reasoning | 2/2 | 6.8, 6.1 |
| fault-isolation-xor | 2/2 | 10.1, 10.9 |
| insufficient-evidence | 2/2 | 7.0, 8.0 |
| critical-path | 2/2 | 7.6, 6.5 |
| update-hypothesis | 2/2 | 5.9, 6.6 |

Screening total: **16/16**. All screening requests ended normally. Raw responses, references and timings: `strata-reasoning.json`.

## Harder optimization check

The 12-job problem has two resource limits, prerequisite jobs, exclusions and a cardinality constraint. An exhaustive search over all 4096 subsets provides the optimum; any feasible optimal selection is accepted.

**2/2 correct.** Both runs selected D, F, I and L: CPU 23, RAM 13, benefit 65. Exhaustive enumeration confirms benefit 65 is globally optimal; there are two optimal selections. Both answers ended normally after the server wrapped up thinking at the 15000-token cap. Elapsed times were 206.7 and 207.3 seconds. Raw responses and reference selections: `strata-reasoning-hard.json`.

Combined result: **18/18 trials across nine task types**, using two seeds per task. The two multi-turn trials each require both stages to pass. No answer was corrected or retried after being scored. The optimum reference was never sent to the model.

## Interpretation and limits

These results support accuracy on the supplied constraint, code-state, probability, uncertainty, scheduling, fault-isolation and evidence-update problems. The harder optimizer also reached a verified global optimum in both trials. The ordering puzzle took 37–58 seconds despite a small state space, indicating excessive deliberation on at least one easy task. The harder optimizer exhausted its thinking cap in both trials, so correct final answers should not be confused with efficient convergence.

This is a small synthetic suite with two trials per task. It does not establish superiority to the previous vLLM model, broad engineering reliability, resistance to long agent loops, or general long-context reasoning. A controlled comparison on the same cases and real bug-fix tasks is needed for those claims.

## Reproduce

```bash
export STRATA_API_KEY="$(cat /home/cpaquin/.config/strata/api-key)"
python3 scripts/reasoning_eval.py --output benchmarks/reasoning-new-run.json
```

The current script includes the harder case in its default suite. The initial screening and harder case were run separately and retained in separate result files.
