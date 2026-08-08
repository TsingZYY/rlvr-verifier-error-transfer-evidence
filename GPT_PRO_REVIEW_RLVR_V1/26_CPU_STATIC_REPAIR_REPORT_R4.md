# CPU/static repair report R4

Status: `EXECUTION_INTEGRITY_REPAIR_CANDIDATE_AWAITING_GPT_PRO_REAUDIT`

This packet contains no new model output. No tokenizer was loaded, no model
weights were loaded, and no forward, gradient, or optimizer operation was run.
All scientific-evidence and formal-experiment flags remain false.

## Decisive CPU/static results

- Two independent repaired asset builds contain 574 files each and are byte
  identical. `BUILD_R4_DETERMINISM.json` reports no missing or mismatched file.
- Independent prompt-text solving covers 280/280 visible prompts with 0 parse
  errors and 0 semantic mismatches.
- Counts are source 112, target calibration 56, current audit 112, total 280,
  and fixed-candidate verifier records 1,960.
- Source/calibration/audit remain disjoint by row id and lineage. The current
  audit was read during CPU validation and is therefore explicitly labeled
  `CURRENT_AUDIT_EXPOSED_AND_USED_FOR_CPU_STATIC_INTEGRITY_ONLY` and
  `NOT_ELIGIBLE_AS_HIDDEN_EVALUATION`.
- A root-level clean-discovery command, `python -m unittest discover -v`, runs
  exactly 28 standard-library tests and all 28 pass.

## Repairs against the previous Pro blockers

1. A single `commitment_core.py` now defines canonical JSON bytes with exactly
   one trailing LF. Builder, runner, static contract, and validator share it.
   Runner preflight and result validation both compare selected source/target
   row commitments to the manifest.
2. Results now require row-level pre-source, pre-target, and per-update
   post-target score traces, token counts, gold/offset candidate mappings,
   parameter-state labels, and update hashes. The validator recomputes source
   gates, expected reward, target metrics, effects, diagonal excess, summaries,
   token ranges, and update-norm consistency from those traces.
3. The model inventory is a recursive exact sorted list of relative path,
   byte size, and SHA-256. Additions, deletions, symlinks, weight-index changes,
   and shard drift fail closed.
4. Config validation is exact for the full frozen model, runtime, device,
   candidate scoring, update, gate, estimand, and claim-boundary semantics.
5. Each manifest requires an externally supplied expected hash. Result-pair
   validation requires a separately stored result-anchor receipt plus an
   externally recorded receipt hash. The helper refuses to store that receipt
   inside either result output directory.
6. Audit labels no longer claim hidden eligibility. Runner receives only the
   seal receipt and calibration path; audit-row access is forbidden.
7. Tests construct fresh temporary assets and a synthetic model inventory, so
   they do not depend on the author's absolute path or real model. The packet
   has a root-level discovery entry point and uses only the standard library.
8. Eight stack-specific configs, determinism addenda, and manifests are bound
   by one master inclusion contract. Only `mapping_stack_id` may differ across
   configs. The rule is run all eight with no selection/deletion/adaptation.
9. The authorization template is intentionally non-authorizing and is rejected
   by the preflight. Every manifest and the master keep all model actions false.
10. The claim boundary says fixed-candidate expected-reward optimization is a
    diagnostic RLVR surrogate, not sampled RLVR, not same-FPR evidence, not
    hidden evaluation, and not a formal G1 result.

## Frozen anchors

- Master inclusion contract SHA-256:
  `243dcbbea90753e339c2506d3229a5f6d9b8f54f39ea61450d991f48708ce6fc`
- Static eight-stack audit SHA-256:
  `1e2a3b3d93a38a98d55c2e6bd6b8dec31edb280e64e5733297be55ee78fc64b4`
- Portable validation A SHA-256:
  `df6e88596f62e5909572c4a2531be654f0ddad5e9faa3c4e892dd6c36e188348`
- Portable validation B SHA-256:
  `59486ac7dcd62ff0c5d0f2913c803729e8f307ea65b196f6c855232bd24762f0`

These hashes inside the packet are reference values, not a trusted external
anchor by themselves. The master hash must be copied to independent custody
before any later authorization.

## Evidence boundary

`CPU_STATIC_ASSET_AND_EXECUTION_CONTRACT_PASS` is a local static result only.
`SCIENTIFIC_EVIDENCE_FALSE`, `FORMAL_EXPERIMENT_FALSE`,
`MODEL_EXECUTION_PERFORMED_FALSE`, and `MODEL_ACTIONS_AUTHORIZED_FALSE` remain
the only valid scientific/execution status. Old R2 outputs are invalid under
this repaired contract and may not be merged with any future repaired run.
