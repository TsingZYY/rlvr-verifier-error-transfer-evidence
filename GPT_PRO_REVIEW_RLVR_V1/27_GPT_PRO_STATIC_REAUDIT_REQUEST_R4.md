# GPT Pro independent re-audit request R4

Please independently extract and audit this packet. Do not trust the included
report or reference hashes without recomputation. Do not run a real tokenizer,
model, forward pass, gradient, or optimizer step.

## Mandatory checks

1. Run `python -m unittest discover -v` from the clean extraction root. Report
   the exact discovered/pass/fail/error count and any path dependency.
2. Rebuild the controlled assets twice in new temporary directories. Verify
   byte-identical trees and independently solve all 280 visible prompt texts.
3. Recompute critical asset hashes and check the portable validation report.
4. Recompute all eight config/manifest hashes and the master contract hash.
   Confirm exact 8/8 inclusion, that only `mapping_stack_id` differs across
   configs, and that all authorization fields are false.
5. Confirm the non-authorizing template is rejected before tokenizer/model
   load and cannot be promoted by a manifest boolean.
6. Mutation-test LF removal, row reorder, row field mutation, source/target
   relabeling, target-offset drift, semantic config drift, recursive model-file
   addition/deletion, row-semantics drift, coherent matrix forgery, update-hash
   swapping/duplication, and coordinated manifest/result rewriting.
7. Inspect the raw-trace validator. Decide whether source gates, expected
   reward, target metrics, effects, diagonal excess, summaries, token counts,
   and update norms are independently recomputable and fail closed.
8. Confirm the result-pair anchor must live outside result directories and is
   itself checked against an externally supplied SHA-256.
9. Confirm the current audit is labeled exposed/static-only and cannot be used
   as hidden evaluation.
10. Confirm the packet supports no same-FPR, sampled-RLVR, causal, hidden-audit,
    or scientific-success claim.

## Decision requested

Return exactly one final token after your detailed audit:

- `REPAIR_AUDIT_PASS_REQUEST_USER_AUTHORIZATION`
- `REPAIR_AUDIT_MORE_FIXES`
- `REPAIR_AUDIT_FATAL`

If and only if you choose the first token, specify the exact scope that may be
put to the user for authorization. The code owner will still require an
explicit user instruction before creating any authorization receipt or running
any model action.
