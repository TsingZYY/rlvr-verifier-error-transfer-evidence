# R13 causal-identification audit R1

## Bottom line

R13 is a valid low-cost intervention on the specific ambiguity exposed by R10:
it holds the exact source update fixed and changes only the matched target
codebook interface.  It can therefore test whether the *relative location* of
the transfer hotspot follows that target interface transformation within the
four frozen affine M0 development stacks.

R13 cannot prove that shared semantic identity caused the old pattern, cannot
attribute every transfer effect uniquely to the verifier false positive, and
cannot support a population claim about tasks or sampled RLVR.

## What is the causal unit?

For each technical replicate `k`, frozen stack `s`, and source identity `r`, the
unit is the one parameter state produced by the source-only update `U(k,s,r)`.
That update is computed once after a fresh reset.  The two potential readouts
are then:

- `Y_H0(U)`: read the fixed update through the original M0 target codebook;
- `Y_H1(U)`: read the same fixed update through the switched target codebook.

No target arm, target row, target score, or evaluation order is permitted to
enter `U`.  The trainable-parameter hash must remain identical before, between,
and after the two target reads.  This removes the earlier ambiguity in which a
different reward arm could silently create a different source update.

## The identifying contrast

For every `(k,s,r)`, R13 reconstructs all six non-gold target offsets from the
seven raw candidate scores, computes the 5-by-6 double-centered response `R`,
and compares the old and new surface-aligned locations:

`F = [R(H1,q_H1)-R(H1,q_H0)] - [R(H0,q_H1)-R(H0,q_H0)]`.

Positive `F` means that switching only the target codebook moved the relative
hotspot away from the old surface location and toward the new surface location.
The arm-specific `G` contrasts additionally require the surface location to
exceed the latent location in each arm.  All frozen stack-level gates must pass;
technical cells, rows, identities, and processes are not independent task
samples.

## Correct interpretation of "one by one"

Execution is one update at a time because every identity requires a fresh reset
and a single source update.  Scientific selection is not one result at a time:
the four stacks, five identities, two panels, estimands, tolerances, and stop
rules are frozen before any outcome is inspected.  A failed early cell cannot
be used to change later multipliers, identities, or thresholds.

## Remaining threats and hard boundaries

1. **Treatment meaning.** H1 changes the complete codebook interface presented
   in the target prompt and its mechanically implied labels.  A pass identifies
   sensitivity to that interface transformation; it does not by itself reveal
   an internal neural representation or prove a semantic mechanism.
2. **Outcome-aware development set.** The four M0 stacks were chosen because
   they failed R10.  There are only two task-pair clusters.  No standard error,
   confidence interval, p-value, or population generalization is licensed.
3. **Verifier-error decomposition.** The source BUG update rewards both gold
   and one wrong candidate.  Double-centering removes a purely identity-invariant
   Gold main effect, but it does not rule out nonlinear Gold-by-wrong-update
   interactions.  R13 is not a BUG-versus-Gold decomposition.
4. **Mechanism family.** Only affine codebooks over Z7 are intervened on.
   Formatting, aliases, non-affine permutations, semantic paraphrases, and
   other surface channels remain outside the gate.
5. **Training regime.** One exact-expected-reward LoRA step over a fixed
   candidate panel is a development surrogate, not sampled or multi-step RLVR.
6. **Asset and execution custody.** Machine allowlist validation is not human
   approval.  The matched panels need an independently signed review receipt;
   the production backend, complete dependency content, one-shot invocation,
   external launch signature, and result validation must all be hash-bound.

## Frozen decision tree

- Any missing/tampered row, non-finite value, parameter mutation, clipping,
  duplicate execution, A/B mismatch, or custody failure: `TECHNICAL_STOP`, with
  no scientific interpretation.
- Every required stack passes `F`, `G_H0`, and `G_H1`: report only
  `R13_DEVELOPMENT_SURFACE_ALIGNMENT_SWITCH_PASS_ONLY`.
- Any stack fails `F`: stop the target-alignment causal mechanism claim.
- `F` passes but an arm-specific `G` gate fails: report mixed/inconclusive
  representation evidence; do not resurrect shared latent identity.

No outcome automatically authorizes a later experiment.
