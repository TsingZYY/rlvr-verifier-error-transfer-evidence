# CPU/static repair report R5

Status: `CPU_STATIC_REPAIR_CANDIDATE_AWAITING_GPT_PRO_REAUDIT`

No tokenizer load, model-weight load, model forward, gradient, optimizer step,
or sampled-RLVR action occurred while producing R5. Scientific evidence remains
false.

## R4 blockers repaired

1. Replica independence now requires different resolved result paths, different
   filesystem objects, ordered replicate IDs `A`/`B`, distinct UUIDv4 run
   nonces, and two independently hashed invocation-start receipts.
2. Gradient/update norms are explicitly labeled
   `RUNNER_REPORTED_INTERNAL_CONSISTENCY_ONLY` and
   `NOT_AN_INDEPENDENT_VALIDATION_INPUT`; their boundary states that they are
   used for neither diagnostic gates nor scientific explanation.
3. Model-action preflight now requires an externally supplied exact
   authorization-receipt SHA-256. The receipt has one exact schema/version/action
   ID, UUIDv4 authorization ID, RFC3339 UTC issue/expiry times, exact eight-stack
   hashes, exact runner/pair-validator/completion-validator/static-contract/model
   inventory hashes, and exact allowed/forbidden operations.
4. `validate_eight_stack_completion.py` requires one externally anchored master,
   exactly eight distinct R5 result-pair anchors, exactly eight distinct PASS
   validation reports, all eight frozen stack IDs, 16 distinct result hashes,
   16 distinct invocation-receipt hashes, one shared authorization, no missing or
   extra stack, and no old R2 schema before recomputing the aggregate.
5. Critical R5 objects carry the exact evidence boundary:
   `NOT_SAME_FPR_EVIDENCE`, `NOT_SAMPLED_RLVR`, `CALIBRATION_ONLY`,
   `NOT_HIDDEN_AUDIT`, `NOT_FORMAL_G1`, and
   `SCIENTIFIC_EVIDENCE_FALSE`; completion additionally requires
   `OLD_R2_EXCLUDED`.

## Local CPU/static checks

- 41 focused contract, validator, completion, and native-asset tests pass.
- Two independently frozen R5 contract trees are byte-identical: 27/27 files.
- R5 master inclusion-contract SHA-256:
  `4a1caf5ebdae91d04952010e09124e52ff7ac71fe27d739bc22f49247c206ca8`.
- Both portable asset validators report 280/280 prompt rows, 0 parse errors,
  0 prompt-visible semantic mismatches, and identical critical asset hashes.
- The authorization template is rejected and all model-action flags remain
  false.

These are infrastructure and fail-closed contract results only. They are not
same-FPR evidence, sampled-RLVR evidence, hidden-audit evidence, formal G1
evidence, or scientific support for the research claim.
