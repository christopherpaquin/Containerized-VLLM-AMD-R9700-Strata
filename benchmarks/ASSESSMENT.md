# Baseline assessment

## Operational checks

Strata v0.1.45 runs the full IQ3_S model through HIP on the R9700. Authentication, the scar-coder alias, Chat Completions, a tool-call/result round trip, and Responses API generation passed. The full expert arena is loaded into RAM. These checks establish operational readiness, not the full definition of done.

## Executable coding trial

Both backends fixed the timeout-unit and CLI-error bugs and passed all five tests. vLLM: 23.34 seconds, five model steps, eight tool calls. Strata: 39.69 seconds, seven steps, nine tool calls. Strata ran tests before and after editing; its one repeated call was that legitimate test rerun. Neither trial required human rescue or showed a repeated failed-edit loop.

This is one small fixture with bounded tools. The tests cover only the specified examples. Strata added additional CLI validation, including a name-type check, that the tests do not establish as preserving every formerly valid input. A passing fixture therefore does not prove superior repository-level quality or minimal edits. Generated implementations and complete traces are retained in the JSON files; they were not corrected by the evaluator.

## Diagnostic responses

Of ten Strata diagnostic prompts, 7 ended normally and 3 reached the output cap. The vLLM baseline had one normal finish and nine length finishes. Requests capped total output at 2048 tokens and requested a 1024-token thinking budget. Strata implements the hard budget; the old vLLM server does not. This comparison reflects server policy as well as model behavior and is not an isolated intelligence benchmark.

Reviewed responses generally identified the executable-path, Python-environment, timeout-unit and permission evidence. The Bash response used NUL-delimited filename handling. The Ansible answer was mixed: its combined daemon_reload/restart handler was correct, but an earlier two-handler version did not arrange notification of the reload handler. The YAML repository answer added example schema requirements not supplied by the prompt and ended before completing its tests. These answers require review and are not counted as universally solved tasks.

## Performance

The short sampled decode median was 77.3 tokens/sec for Strata versus 122.4 for vLLM, about 37% lower. Both used temperature 1.0 and the same 256-token output cap. Tiny long-context marker answers are unsuitable for a robust decode-throughput comparison. Engine prompt timings and cached-token counts are retained for Strata. See NOTES.md for baseline interference and cache caveats.

## Remaining acceptance

All three marker-retrieval tests passed at 31,934, 127,946 and 257,951 prompt tokens. Strata TTFT was 17.45, 64.07 and 93.73 seconds. The latter two reused 16,384 and 114,688 tokens respectively; their lower latency must not be called a clean prefill-speed win. Fresh-token engine throughput was 1836.9, 1744.9 and 1532.6 tokens/sec. A repeated-record marker task does not establish general long-context reasoning quality. The performance gate is not met. Improved long-horizon engineering reliability, fewer real-world loops, and sustained stability over extended use have not been demonstrated. Keep the preserved vLLM backend available for rollback and subsequent A/B work.

## Additional reasoning probes

A subsequent synthetic reasoning suite passed 18/18 trials across nine task types with two seeds each. Reference rules and exhaustive enumeration checked answers; uncertainty examples also received human review. Both harder optimization runs reached the 15K effective thinking cap and took about 207 seconds. See [reasoning results](REASONING.md). These results do not resolve the comparative real-engineering acceptance gate.
