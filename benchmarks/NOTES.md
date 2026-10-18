# Measurement notes

- Short vLLM runs used streaming, thinking disabled and a 256-token output cap. They are throughput probes, not task-quality scores.
- The 32K/128K/near-256K inputs repeat diagnostic records with a marker at the beginning; retrieval is checked from the recorded response. This does not establish general long-context quality.
- `prompt_tokens_per_ttft_s` includes queueing, tokenization, prompt processing and first-token generation; it is an end-to-end approximation, not isolated kernel prompt throughput.
- The vLLM engineering suite was inadvertently submitted during the long-context baseline. It was cancelled to restore single-request measurement. The 128K timing may include this interference; near-256K also experienced some overlap. Do not treat these as clean comparative timing results.
- GPU compilation/download activity also overlapped baseline collection; results are operational observations.
- The vLLM engineering suite was rerun serially after the overlapping requests completed. Many diagnostic responses hit the 2048-token cap; the old server does not implement Strata's hard reasoning budget. Compare completion behavior with this difference in mind.
- The executable coding trial uses five unit tests, a 12-step limit and four bounded tools. It is one small coding task, not a general SWE benchmark. Repeated-call counts include legitimate repeated checks and should not alone be called loops.
- Files ending `-clean.json` were rerun without another evaluation request in flight. Model downloading still used CPU/network/storage, and normal clients could still reach the old endpoint.
