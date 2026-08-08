# R10 latent-vs-surface diagnostic: independent model-free review

## Audit boundary

- The packet contains 16 already-recorded R10 development-calibration result JSON files, their mapping/target assets, a frozen post-hoc diagnostic protocol, the pure-stdlib analysis implementation and tests, and the generated result.
- Do not load a tokenizer or model, and do not run forward, gradient, optimizer, training, sampled RLVR, or hidden-audit actions.
- Recomputing JSON-derived quantities and running the included pure-CPU unit tests is allowed.
- The diagnostic is outcome-aware development work and cannot become confirmatory evidence.

## Claimed result to verify

The frozen analysis compares double-centered latent-identity alignment with the affine candidate-displacement competitor

`q_surface(r) = inverse_mod7(a_target) * a_source * r mod 7`.

It reports exact A/B agreement and `latent_minus_surface <= 1e-10` in four of eight stacks, specifically all four M0 stacks, leading to:

`STOP_SHARED_IDENTITY_CLAIM_SURFACE_COMPETITOR_NOT_DEFEATED`.

## Required checks

1. Verify packet member hashes and strict input coverage.
2. Independently verify the `q_surface` derivation, q=6 reconstruction from seven stored candidate scores, effect reconstruction, double-centering, A/B gate, and eight stack values.
3. Look for an indexing, codebook, centering, weighting, or data-selection error that would reverse the stop decision.
4. Decide whether the evidence really refutes the old causal claim, merely narrows it, or is inconclusive. Keep the development-only boundary.
5. Audit the proposed next design: a fixed source update evaluated under independently manipulated target-codebook alignment. Reject any design that only relabels the same set of updates.

## Output contract

Start with exactly one verdict:

- `STOP_OLD_CLAIM_AND_PIVOT`
- `REPAIR_ANALYSIS_BEFORE_DECISION`
- `OLD_CLAIM_NOT_REFUTED`

Then state the independently recomputed values or the exact blocking discrepancy, followed by the lowest-cost non-redundant next experiment. Do not authorize a model run.
