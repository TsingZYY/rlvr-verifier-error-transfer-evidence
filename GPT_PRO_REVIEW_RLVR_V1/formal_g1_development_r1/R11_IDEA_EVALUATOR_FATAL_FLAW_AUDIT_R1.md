# Idea-evaluator fatal-flaw audit: R11/R12 shared-result-identity claim

## 1. First impression

- **Paper type:** New Problem / New Setting.
- **One-sentence old story:** At the same fixed candidate-panel false-positive rate, verifier errors that share a result identity across tasks cause greater cross-task migration than errors that do not.

## 2. Fatal-flaws audit (early gate)

| # | Flaw | Severity | Evidence | Defense |
|---|---|---|---|---|
| 1 | The core shared-latent-identity mechanism is beaten by its simple candidate-surface-displacement competitor in half of the frozen stacks. | **CRITICAL** | The frozen model-free diagnostic reconstructs all 16 R10 A/B result files from seven-candidate traces. A/B maximum matrix difference is exactly 0. `S_latent - S_surface <= 1e-10` in 4/8 stacks, exactly all four M0 stacks; only all four M1 stacks pass. The pre-frozen all-eight rule therefore returns `STOP_SHARED_IDENTITY_CLAIM_SURFACE_COMPETITOR_NOT_DEFEATED`. | No defense is permitted for the old mechanism claim. The premise is retired rather than rescued by a new threshold, an overall positive average, or more repetitions. |
| 2 | The proposed R12 reward-identity switch is a relabelling, not a non-redundant intervention. | **MAJOR** | With common source rows and initialization, `BUG_PERMUTED(r) = BUG_ALIGNED(pi(r))`. Enumerating all five identities gives the same set of updates under a different index. The draft's 22 unique updates reduce to 12 unique intervention masks, and the current R12 custody wrapper still calls the two-arm R11 runner. | Retire R12 R3. Any new experiment must manipulate a matched target codebook while reusing the exact same source update, not permute a fully enumerated reward arm. |

The CRITICAL data-refutation flaw triggers the early-stop rule. No five-dimension scoring, paradigm-shift score, or feasibility score is used to decorate the rejected version.

## 7. Verdict

**Reject and Pivot.**

The old paper-level claim that shared latent result identity is the causal channel is rejected. The surviving research observation is narrower and different: under the same fixed candidate-panel reward error rate, transfer structure changes with mapping/codebook representation. That observation motivates a new representation-conditioned migration-risk hypothesis; it does not rehabilitate the rejected latent-identity mechanism.

Top three actions:

1. Retire the frozen R11 32-process bridge and R12 R3 reward-permutation draft; preserve them only as invalidated development artifacts.
2. Freeze a new target-alignment intervention in which every source update is reused unchanged across two matched target panels whose codebooks differ by a precommitted non-affine permutation.
3. Keep the new study development-only until it passes strict custody, exact input binding, and an outcome-independent set of fresh task-pair clusters; do not call fixed `1/6` candidate-panel reward mass empirical or policy-weighted FPR.
