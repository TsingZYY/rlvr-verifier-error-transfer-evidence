# R11 GPT Pro static-custody review request

## Required role

Act as an adversarial pre-execution reviewer. Review the attached files only. Do not infer that any model action ran, do not treat development calibration as hidden audit, sampled RLVR, same-FPR evidence, formal G1 evidence, or scientific evidence.

## Current claim

The earlier fatal blocker `R11_STATIC_CONTRACT_SCHEMA_CONFLICT` is claimed resolved by a dedicated R11 custody layer. The package freezes an 8 stack x 2 arm x 2 replicate design (32 OS processes). `BUG` and `GOLD_ONLY` must differ only through a signed reward mask. The R10 runner is retained only as a frozen computation path behind an R11 adapter.

The package is deliberately **not run eligible**. It contains a non-authorizing receipt template, no user authorization receipt, no invocation receipts, and no result files.

## Files to inspect first

1. `formal_g1_development_r1/R11_CURRENT_STATE_R5.json`
2. `mvp_same_source_v1/r11_static_contract.py`
3. `mvp_same_source_v1/run_same_source_bridge_r11.py`
4. `mvp_same_source_v1/r11_bridge_contract.py`
5. `mvp_same_source_v1/frozen_r11_bridge_r1/R11_32_CELL_MASTER_INCLUSION_CONTRACT_R1.json`
6. `mvp_same_source_v1/frozen_r11_bridge_r1/R11_RELEASE_INDEX_R1.json`
7. `mvp_same_source_v1/frozen_r11_bridge_r1/R11_AUTHORIZATION_RECEIPT_TEMPLATE_R1.json`
8. `mvp_same_source_v1/R11_32_CELL_STATIC_AUDIT_R1.json`
9. `mvp_same_source_v1/R11_PORTABLE_STATIC_AUDIT_R1.json`
10. `formal_g1_development_r1/validate_r11_bridge_results.py`
11. All `test_r11_*.py` and `test_validate_r11_bridge_results.py` files.

## Frozen facts to verify, not assume

- Master SHA-256: `9842f7b7fe3c6183d277274ba7461a4de38e07207db353c65630e3a92cb496d6`.
- Local and clean-directory audits each report 32/32 cells and have SHA-256 `dcc92ec617a68e84546a39a2ef2cbcead386e6a59f424eead3ad66b50e4ea3ae`.
- Regression tests report 74/74 pass.
- Model execution performed: false.
- Model execution authorized: false.
- Scientific evidence: false.

## Questions requiring explicit answers

1. Does the R11 custody layer close the exact-schema conflict without allowing arm, stack, replicate, manifest, authorization, or invocation swapping? Cite exact code locations.
2. Can the monkey-patched R11 adapter fall back to the R10 authorization or invocation verifier, fail to restore global functions, or create a concurrency/reentrancy hazard? Treat any such route as potentially fatal.
3. Can any checked-in static file self-authorize tokenizer/model load, forward, gradient, optimizer step, or the 32-process bridge? Verify the template is rejected by the executable verifier.
4. Is the 32-cell master exact and non-selective? Check omissions, duplicates, ordering, shared bindings, and the BUG/GOLD_ONLY unique-treatment-difference claim.
5. Does the result validator correctly preserve the two-task-pair scientific clustering and development-only boundary, or can 32 processes be misrepresented as 32 independent scientific samples?
6. Identify all remaining CRITICAL, MAJOR, and MINOR flaws. Distinguish implementation blockers from scientific-design limitations.
7. Give one exact verdict:
   - `STATIC_CUSTODY_PASS_EXTERNAL_AUTHORIZATION_PENDING`
   - `STATIC_CUSTODY_REPAIR_REQUIRED`
   - `FATAL_DESIGN_FLAW_STOP`

If choosing repair, specify the minimum patch and the negative test that proves it. Do not recommend model execution until all required external custody and exact authorization artifacts exist.
