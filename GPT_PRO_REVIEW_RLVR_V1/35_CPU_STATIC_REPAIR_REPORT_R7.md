# CPU/static numeric fail-closed repair report R7

Status: `CPU_STATIC_REPAIR_CANDIDATE_AWAITING_GPT_PRO_REAUDIT`

No tokenizer, model weight, forward, gradient, optimizer, GPU, or sampled-RLVR
action occurred. Scientific evidence and model authorization remain false.

R7 changes only the adjacent numeric-overflow boundary found by the R6 final
arbiter:

1. `mean_diagonal_excess` is converted through an exception-safe parser that
   rejects bool, non-numeric values, non-finite floats, and integers that cannot
   be represented as finite floats.
2. The fixed-eight average divides each finite input by eight before summing,
   avoiding overflow from a naive intermediate total.
3. The derived aggregate mean is explicitly required to be finite. Numeric
   conversion or aggregation failure returns structured `FAIL` with
   `aggregate=null`.
4. The CLI serialization path is tested with an overflowing integer input and
   writes valid canonical FAIL JSON instead of raising.

Local CPU/static results:

- 51/51 focused tests pass.
- Eight `1e308` inputs produce a finite PASS aggregate of `1e308`.
- A `10**1000` mean produces structured FAIL with `aggregate=null`.
- Two refreshed R7 contract trees are byte-identical: 27/27 files.
- The 24 execution-contract files are byte-identical to R6; only the completion
  validator hash and its dependent master/template/reference chain changed.
- R7 master SHA-256:
  `b000369bc28c464fa625627e38faf21fb9637c9a5fe29e23d26173f6d122f279`.
- Completion validator SHA-256:
  `90686a347ff851df08623697bd06460e6ebb5b321a4dd5657021f04a56feb889`.

This is static infrastructure evidence only: `NOT_SAME_FPR_EVIDENCE`,
`NOT_SAMPLED_RLVR`, `CALIBRATION_ONLY`, `NOT_HIDDEN_AUDIT`, `NOT_FORMAL_G1`,
and `SCIENTIFIC_EVIDENCE_FALSE`.
