# CPU/static repair R3

## Decision

The blocking asset and integrity defects are mechanically repaired, but model
execution remains forbidden. The current state is:

`CPU_STATIC_REPAIR_PASS_AUTHORIZATION_PENDING`

Any runner invocation must terminate before tokenizer/model load with:

`STOP_BEFORE_TOKENIZER_OR_MODEL_LOAD`

## What was repaired

- `MARKED_RANK7_V1` no longer renders unmarked positive values with a minus
  prefix. The prior assets had 36/42 model-visible label mismatches.
- An independent prompt-text solver parses and solves all four tasks from the
  final model-visible bytes without using `canonical_z` as its answer.
- Two regenerated asset trees are byte-identical across 574 files.
- The CPU validator reports 0 parse errors, 0 semantic mismatches, and 0 total
  validation errors over 280 prompts.
- The immutable manifest binds eight stacks, source/calibration assets,
  mapping, model root files, the full tokenizer surface, chat template,
  runner, R3 validator, runtime/package versions, deterministic environment,
  and the pre-existing audit seal receipt without using audit content.
- The runner consumes source and target offsets separately, rejects unknown
  configuration drift through the static contract, and uses
  `warn_only=False`.
- The R3 validator recomputes every effect, each within-update diagonal
  excess, the stack aggregate, exact source-identity/update-hash bindings, and
  provenance.

## Verification

- Real-asset suite: 29/29 passed.
- R3 contract/validator mutation suite: 11/11 passed.
- Forged matrix, swapped update hash, fake provenance, relabeled target,
  target-offset mismatch, unknown schema, and warn-only determinism fixtures
  are rejected.
- The old R2 pair is substantively identical but fails R3 provenance and
  integrity validation. It cannot be combined with repaired runs.
- A real runner invocation with the static-only manifest exited nonzero with
  `model action authorization missing` and created no result file.

## Evidence boundary

Current evidence is only:

`CPU_STATIC_REPAIR_PASS_ONLY`

It is not scientific evidence, not same-FPR evidence, not sampled-RLVR
evidence, and not hidden-audit evidence. No tokenizer/model execution occurred
in this repair stage.

## Remaining blockers

1. Freeze eight stack-specific configs/manifests under one unchanged inclusion
   contract.
2. Obtain action-specific authorization for the repaired eight-stack
   fixed-candidate diagnostic.
3. If the paper is to use “same FPR”, freeze non-adaptive G1 formulas,
   aggregation units, equivalence bounds, and tolerances before seeing any new
   model-dependent values.
4. A sampled-RLVR bridge remains forbidden until the repaired eight-stack
   bridge gate passes.
