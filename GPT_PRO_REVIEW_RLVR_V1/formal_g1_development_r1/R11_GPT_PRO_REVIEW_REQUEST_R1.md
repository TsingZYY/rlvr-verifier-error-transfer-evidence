# R11 gold-only DiD bridge: GPT Pro review request

## Evidence boundary

This is a static design review. Do not authorize or simulate tokenizer loading,
model loading, forward passes, gradients, optimizer steps, sampled RLVR, or
hidden-audit access. The R10 data are already observed development-calibration
outputs and are not confirmatory evidence.

## Decisive R10 facts

- R10 completion validator: PASS, with 8/8 stacks and substantively identical
  A/B replicates.
- Equal-weight mean diagonal excess: `+0.006579572175230297`.
- Task-pair effects: TP1 `+0.004522390450750074`; TP2
  `+0.008636753899710518`.
- Minimum stack effect: `-0.01097992828914098`; two of eight stacks are
  negative.
- Minimum identity effect: `-0.007168473941939224` for `Z7_PLUS4`.
- A-to-B mean: `+0.013858263407434734`; B-to-A mean:
  `-0.0006991190569741415`.
- M0 mean: `-0.0017495214939117457`; M1 mean:
  `+0.01490866584437234`.
- Only 2/8 stacks rank in the top 5% of all 120 deterministic identity
  bijections. The common global identity alignment ranks 6/120, but its
  absolute identity-matched effect is `-0.019027140310832434`.
- Therefore the frozen reviewer sign-consistency gate is INCONCLUSIVE, and no
  practical margin was frozen before R10 outcomes.

## Proposed minimum bridge

Keep all eight stacks and all five source/target identities. Reuse the immutable
R10 pre traces and bug-update traces. Add exactly one gold-only update per stack,
from the identical checkpoint and source rows, and evaluate that update on all
five target identities. Use two independent process replicates.

For stack `s`, source identity `r`, and target identity `q`:

```text
bug_effect(s,r,q) = post_bug(s,r,q) - pre(s,q)
gold_effect(s,q)  = post_gold(s,q) - pre(s,q)
adjusted(s,r,q)   = bug_effect(s,r,q) - gold_effect(s,q)

primary(s,r) = adjusted(s,r,r)
             - mean_{q != r} adjusted(s,r,q)
```

Aggregate with equal weights: off-diagonal targets, then source identities,
then mapping/direction, then task pair. Realized update norm is diagnostic only
and cannot change learning rate, steps, identities, stacks, or weights.

## Review tasks

1. Causal identification: Does this DiD isolate the wrong-reward contribution
   under the exact one-step, unclipped manual-SGD setup? Is a wrong-only arm
   also required?
2. Cross-release validity: May hash-bound R10 bug arms be reused, or must both
   bug and gold-only arms be rerun in one counterbalanced release?
3. No-op validity: Is the immutable pre state a sufficient no-op control, or is
   a separately executed no-op process necessary?
4. Interface confounding: Must a non-affine codebook control that removes the
   shared affine anchor be added before this bridge?
5. Statistics: Specify an outcome-independent practical margin and exact
   GO/INCONCLUSIVE/STOP rule, or state that no defensible margin is currently
   available.
6. Cost audit: Identify the minimum design that resolves the causal ambiguity
   without granting sampled-RLVR or hidden-audit access.

## Required single verdict

Return exactly one headline verdict:

- `RUN_GOLD_ONLY_DID_BRIDGE`
- `REDESIGN_BEFORE_MODEL_ACTION`
- `STOP_DIRECTION`

Then list CRITICAL, MAJOR, and MINOR findings. Explicitly keep all model-action
authorization fields false.
