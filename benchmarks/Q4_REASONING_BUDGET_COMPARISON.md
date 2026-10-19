# Qwen3.8-Flash-Next UD-Q4_K_XL Reasoning-Budget Comparison (`16K` vs `12K` vs `8K` at `xhigh`)

Date: 2026-10-18. Host: `scar.lab` (AMD Radeon AI PRO R9700 32 GB, ROCm 7.2.2, Strata v0.1.45, `--vram-reserve-mib 832`).

- **16K Baseline (`reasoning_budget_tokens = 16384`)**: [`20261018-1757-Qwen3.8-Flash-Next-UD-Q4_K_XL.md`](20261018-1757-Qwen3.8-Flash-Next-UD-Q4_K_XL.md) (`20261018-1757-Qwen3.8-Flash-Next-UD-Q4_K_XL-data/`)
- **12K Test (`reasoning_budget_tokens = 12288`)**: [`20261018-1846-Qwen3.8-Flash-Next-UD-Q4_K_XL-xhigh-12K.md`](20261018-1846-Qwen3.8-Flash-Next-UD-Q4_K_XL-xhigh-12K.md) (`20261018-1846-Qwen3.8-Flash-Next-UD-Q4_K_XL-xhigh-12K-data/`)
- **8K Test (`reasoning_budget_tokens = 8192`)**: [`20261018-1905-Qwen3.8-Flash-Next-UD-Q4_K_XL-xhigh-8K.md`](20261018-1905-Qwen3.8-Flash-Next-UD-Q4_K_XL-xhigh-8K.md) (`20261018-1905-Qwen3.8-Flash-Next-UD-Q4_K_XL-xhigh-8K-data/`)
- **IQ3_S 16K Reference**: [`20261018-1647-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S.md`](20261018-1647-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S.md) (`20261018-1647-Qwen3.8-Flash-Next-GSQ-RCO-IQ3_S-data/`)

## 6. Three-Way Q4 Reasoning-Budget Comparison (`16K` vs `12K` vs `8K`)

| Metric | Q4 `16K` Baseline (`16384`) | Q4 `12K` (`12288`) | Q4 `8K` (`8192`) | IQ3_S `16K` Reference |
| --- | --- | --- | --- | --- |
| Systemd reasoning tokens | 16,385 (pass 1) + 17 wrap-up | ~9,522 est. (single pass) | 8,194 (pass 1) + 16 wrap-up | ~7,630 est. (single pass) |
| Systemd budget hit | Yes (`16,384`) | No | Yes (`8,192`) | No |
| Systemd loop recovery | 0 (`coverage=0.013`) | 0 (`coverage=0.006`) | 0 (`coverage=0.032`) | 0 (`coverage=0.011`) |
| Systemd total tokens | 22,972 | 18,922 | 15,949 | 16,133 |
| Systemd total time | 500.680 s | 398.078 s | 344.230 s | 229.607 s |
| Systemd accuracy | Mixed (`214/211` & `ExecStart` `{` bug) | Mixed (`ExecStart` `{` & `--scope -p User=` bug) | Mixed (`-p ExecStart=` & `-p Path` bug) | Mixed (`201/USER` & `setenforce 0`) |
| Coding reasoning tokens | 16,387 (pass 1) + 15 wrap-up | 12,291 (pass 1) + 15 wrap-up | 8,193 (pass 1) + 17 wrap-up | 16,388 (pass 1+2) + 15 wrap-up |
| Coding budget hit | Yes (`16,384`) | Yes (`12,288`) | Yes (`8,192`) | Yes (`16,384`) |
| Coding loop recovery | 0 (`coverage=0.158`) | 0 (`coverage=0.024`) | 0 (`coverage=0.063`) | **1** (at 9,728 tokens, `coverage=0.290`) |
| Coding repeat coverage | 0.158 | 0.024 | 0.063 | 0.290 |
| Coding total tokens | 20,688 | 17,803 | 10,046 | 20,729 |
| Coding total time | 478.064 s | 408.213 s | 222.072 s | 323.556 s |
| **Coding tests passed** | **4 / 4** | **1 / 4** (`>=` bug + `NotImplementedError` stub) | **2 / 4** (`visited_dirs` root bug + no external sort) | **4 / 4** |
| PostgreSQL reasoning tokens | ~7,420 est. (single pass) | ~7,582 est. (single pass) | ~6,232 est. (single pass) | ~7,770 est. (single pass) |
| PostgreSQL budget hit | No | No | No | No |
| PostgreSQL loop recovery | 0 (`coverage=0.018`) | 0 (`coverage=0.035`) | 0 (`coverage=0.044`) | 0 (`coverage=0.000`) |
| PostgreSQL total tokens | 11,899 | 13,042 | 11,847 | 12,141 |
| PostgreSQL total time | 258.147 s | 283.312 s | 255.765 s | 177.644 s |
| PostgreSQL quality | Good (Complete) | Good (Complete) | Good (Complete) | Good (Complete) |
| **Average decode tok/s** | **45.80 tok/s** | **46.60 tok/s** | **46.93 tok/s** | **68.27 tok/s** |
| **Total benchmark time** | **1,236.89 s** (20.6 min) | **1,089.60 s** (18.2 min) | **822.07 s** (13.7 min) | **730.81 s** (12.2 min) |
| **Loop recoveries total** | **0 / 3** | **0 / 3** | **0 / 3** | **1 / 3** |
| **Budget hits total** | **2 / 3** (`systemd`, `coding`) | **1 / 3** (`coding`) | **2 / 3** (`systemd`, `coding`) | **1 / 3** (`coding`) |

## 7. Ranking by Decision Criteria & Direct Answers to Experimental Questions

### Ranking of Q4 Reasoning-Budget Configurations (Priority Order)

1. **`Q4 16K` (`reasoning_budget_tokens = 16384`) — Rank #1 (Winner)**:
   - **Only Q4 configuration that achieved `4/4` executable coding correctness** (implementing strict `> 1 GiB` filtering, root-seeded symlink-cycle protection, `PermissionError` reporting, and a complete working external merge-sort generator).
   - Maintained `0/3` reasoning-loop recoveries, complete wrong-hypothesis recovery, and equivalent systemd troubleshooting accuracy.
2. **`Q4 8K` (`reasoning_budget_tokens = 8192`) — Rank #2**:
   - Passed `2/4` coding checks (got strict `> 1 GiB` threshold and `PermissionError` handling right, but forgot to seed `root` into `visited_dirs` on symlink-cycle traversal and omitted external merge sort after hitting the `8,192` thinking cap), maintained `0/3` loop recoveries, and cut total benchmark time by **33.5%** (`822.07 s` vs `1,236.89 s`).
3. **`Q4 12K` (`reasoning_budget_tokens = 12288`) — Rank #3**:
   - Passed only `1/4` coding checks because its `coding` implementation used `st.st_size >= min_size` (failing strict `> 1 GiB` filtering on both regular and symlink checks) and explicitly raised `NotImplementedError` in `find_large_files_stream_sorted()`, while saving only 11.9% wall-clock time (`1,089.60 s` vs `1,236.89 s`).

### Direct Answers to the 7 Experimental Questions

1. **Does reducing the budget from 16K to 12K reduce wasted reasoning without harming answer quality?**
   - **No.** Reducing the budget from `16,384` to `12,288` saved only `147.29 s` (`11.9%`) across the three tasks, and it **materially harmed coding answer quality**: on Test B (`coding`), `12K` still hit the `12,288`-token ceiling, left its streaming external sort as a `NotImplementedError` stub, and used `>= min_size` instead of `> min_size`, dropping container test pass rate from `4/4` (`16K`) to `1/4` (`12K`).
2. **Does 12K still avoid the coding reasoning loop seen with IQ3_S?**
   - **Yes.** `12K` had `0` reasoning-loop recoveries across all three tests (`coding` `reasoning_repeat_coverage = 0.024`, well below the `0.250` loop threshold and `IQ3_S`'s `0.290`).
3. **Does 8K produce noticeably weaker, rushed, or incomplete answers?**
   - **Yes on complex coding (Test B); No on diagnostic prose (Tests A and C).** On `coding`, hitting the `8,192` budget caused the model to emit a truncated/rushed code implementation (`1,836` final tokens vs `4,286` at `16K`) that omitted external merge sort (leaving a 1-line signature stub) and missed seeding `visited_dirs` with `root`'s `(st_dev, st_ino)` (`2/4` tests passed). On `systemd` and `recovery`, `8K` remained thorough and well-structured (`30,049` and `19,908` chars).
4. **Do all three Q4 configurations still pass 4/4 coding tests?**
   - **No.** Only **`16K` passed `4/4`**. `12K` passed **`1/4`** (`>=` threshold bug + `NotImplementedError` external-sort stub), and `8K` passed **`2/4`** (missing `root` in `visited_dirs` cycle guard + omitted external-sort implementation).
5. **Does the systemd factual accuracy improve, worsen, or remain essentially unchanged as the reasoning budget shrinks?**
   - **Essentially unchanged (`Mixed` at all three budgets).** `12K` and `8K` avoided `16K`'s fabricated `status=214/PATH` and `status=211/START_LIMIT_HIT` exit-code labels, but `12K` kept the broken `systemctl show -p ExecStart --value | awk '{print $1}'` (`{`) command and added invalid `systemd-run --scope -p User=` examples, while `8K` introduced broken `systemctl show custom.service -p ExecStart= --value custom.service` (trailing `=`) and invalid `-p Path` flags.
6. **Which budget provides the best balance for coding and Linux/sysadmin agent use?**
   - **`16384` (`16K`)** is the only budget that preserves `UD-Q4_K_XL`'s `4/4` coding implementation completeness and edge-case verification under `reasoning_effort = xhigh`. At `xhigh` effort, the model spends its first ~8K–12K thinking tokens exploring diagnostic prose and architecture; cutting off thinking at `8K` or `12K` interrupts the final code-verification phase of its chain of thought.
7. **Would you recommend `16384`, `12288`, or `8192` as the default reasoning budget for Scar?**
   - **Recommend keeping `16384` (`16K`)** as the default reasoning budget when `reasoning_effort = xhigh`. Both `12288` (`1/4` coding checks) and `8192` (`2/4` coding checks) caused concrete regressions in executable coding correctness because `xhigh` prompts the model to think expansively before drafting code; truncating that thinking mid-flight via a lower `reasoning_budget_tokens` forces `REASONING_WRAP_UP` before the model finishes verifying its implementation details.
