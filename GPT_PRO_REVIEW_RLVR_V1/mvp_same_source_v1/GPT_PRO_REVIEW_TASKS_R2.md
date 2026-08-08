# GPT Pro review tasks for the same-source diagnostic MVP

## Common evidence rules for every reviewer

You are reviewing a diagnostic MVP for the research proposition:

> Same false-positive rate, different transfer risk: cross-task structure of
> verifier errors in RLVR.

Read the uploaded files directly. Recompute or cross-check material quantities
instead of trusting `MVP_RUN_REPORT_R2.md`. Keep these evidence levels separate:

1. implementation/integrity evidence;
2. feasibility evidence;
3. directional mechanism evidence;
4. confirmatory or population evidence.

The current run is deliberately labelled `DIAGNOSTIC_MVP_NOT_FORMAL`. It uses
one mapping stack, development calibration data, a fixed seven-candidate policy,
one exact expected-reward LoRA/SGD step, and five source-rule identities. It is
not sampled RLVR, not hidden audit, and not a significance test. Do not propose
tuning the observed stack, rule identities, learning rate, or endpoint based on
the observed signs. Identify unsupported claims and status washing explicitly.

## Reviewer 1: causal identification and mechanism

Audit whether the same-source 5x5 design successfully separates source update
from target identity. Determine exactly what causal contrast it identifies and
what it still cannot identify. Construct at least two alternative mechanisms
that can generate the observed 5x5 effect matrix without a shared semantic
verifier-error representation. Assess candidate-token, codebook, task,
low-rank-update, and finite-rule artifacts. Decide whether running the remaining
seven frozen stacks is causally informative and give a minimum no-tuning
decision rule.

End with exactly one token:

`CAUSAL_PROCEED_7_STACKS`, `CAUSAL_REPAIR_BEFORE_7_STACKS`, or `CAUSAL_STOP`.

## Reviewer 2: RLVR-surrogate fidelity

Audit the fixed-candidate exact expected-reward update against sampled RLVR.
Check the candidate normalization, reward vector, log-odds target endpoint,
one-step manual SGD, LoRA initialization/reset, absence of optimizer state, and
diagnostic opportunity gates. Explain which differences are harmless for a
smoke test and which could reverse the sign or magnitude under real RLVR.
Specify the cheapest bridge experiment that would make the surrogate result
meaningfully predictive of sampled RLVR, without using the audit split.

End with exactly one token:

`SURROGATE_ADEQUATE_FOR_7_STACK_SCREEN`, `SURROGATE_NEEDS_BRIDGE_FIRST`, or
`SURROGATE_INVALID`.

## Reviewer 3: statistical interpretation and next decision rule

Recompute the five diagonal-excess values and aggregate. Assess the evidential
meaning of mean `+0.001435`, sign count `3/5`, sample SD about `0.0324`, one
mapping stack, and deterministic replication. Decide whether the current result
is null-like, merely underpowered, heterogeneous, or mechanically
noninterpretable. Pre-register a low-cost eight-stack descriptive decision rule
that cannot be tuned using the observed first stack. State what outcomes would
justify sampled RLVR, redesign, or stopping. Do not manufacture p-values or
treat rules/cells as independent scientific samples.

End with exactly one token:

`STATS_PROCEED_FROZEN_7_STACKS`, `STATS_REDESIGN_FIRST`, or `STATS_STOP`.

## Reviewer 4: code, integrity, and adversarial audit

Read `run_same_source_mvp.py`, `validate_mvp_replicates.py`, the configuration,
raw R2 results, input assets, and formal Repair-B protocol. Look for coding
errors, accidental target leakage, incorrect response-token scoring, invalid
gradient calculations, misleading determinism checks, hash/reference loopholes,
calibration/audit contamination, and status washing. Recompute at least the
effect matrix aggregation and verify 5x5 coverage from raw JSON. Classify every
finding as CRITICAL, MAJOR, or MINOR and propose executable negative fixtures.

End with exactly one token:

`IMPLEMENTATION_PASS_FOR_DIAGNOSTIC_EXPANSION`,
`IMPLEMENTATION_REPAIR_BEFORE_EXPANSION`, or `IMPLEMENTATION_REJECT`.

## Reviewer 5: synthesis arbiter

This task is dispatched only after the four independent reviews are returned.
Read the original evidence packet and the four verbatim reviewer responses.
Resolve disagreements using the raw artifacts. Choose one minimum-cost action:

1. run the remaining seven frozen stacks unchanged;
2. run a bridge experiment before stack expansion;
3. repair implementation/estimand before any further model runs;
4. stop the direction.

Provide an executable action contract: exact allowed changes, forbidden
adaptation, gate/stop rule, evidence label, and what would justify sampled RLVR.

End with exactly one token:

`ARBITER_RUN_7_STACKS`, `ARBITER_BRIDGE_FIRST`, `ARBITER_REPAIR_FIRST`, or
`ARBITER_STOP_DIRECTION`.

