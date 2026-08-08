# CPU/static pre-validation parse repair report R8

Status: `CPU_STATIC_REPAIR_CANDIDATE_AWAITING_GPT_PRO_REAUDIT`

No tokenizer, model weight, forward, gradient, optimizer, GPU, or sampled-RLVR
action occurred. Scientific evidence and model authorization remain false.

R8 changes only the outer CLI input boundary identified by the R7 arbiter:

1. Input files are hashed and parsed inside a bounded fail-closed block.
2. File I/O, Unicode, JSON parser-limit, and numeric conversion errors produce a
   fixed-schema structured FAIL report with `aggregate=null`.
3. The untrusted input and exception message are never echoed; only the bounded
   exception type is recorded.
4. The canonical output path is shared with ordinary validator failures.

Local CPU/static results:

- 53/53 focused tests pass.
- A real 10,000-digit JSON integer triggers Python's parser limit but the CLI
  returns exit code 1 and writes canonical FAIL JSON with `aggregate=null`.
- Malformed JSON, invalid UTF-8, non-object roots, and missing input files follow
  the same structured FAIL path.
- R7 extreme-float and 1000-digit-integer repairs remain passing.
- Two refreshed R8 contract trees are byte-identical: 27/27 files.
- The 24 execution-contract files are unchanged; only the completion validator
  and dependent master/template/reference hashes changed.
- R8 master SHA-256:
  `a1fb96e7de1d5d147c6540c524c325a91ba79225d60df9d91efb0ffcd18b6aa1`.
- Completion validator SHA-256:
  `2f4127c91e33b5eeb138f463a961f4577b4eb402ebeef563c37bf3eb8458ea34`.

This remains static infrastructure evidence only, not same-FPR, sampled-RLVR,
hidden-audit, formal-G1, or scientific evidence.
