# RLVR verifier-error cross-task structure

This repository is a curated evidence snapshot for the research question:

> Can verifier errors with similar aggregate false-positive rates produce different cross-task transfer risk because their error structure interacts differently with a target task or interface?

The title is a **research question, not an established finding**.

## Current evidence status — 2026-08-09

- The old uniform **shared-latent-identity** mechanism is stopped. The R10 model-free development diagnostic found that the surface/codebook competitor was not defeated in all four M0 stacks.
- The R13 scientific batch is **0/8 processes complete**. There is no positive or negative R13 scientific result.
- A local engineering canary passed. It loaded the model and exercised 16 forward calls, 14 backward calls, and one manual parameter step, while producing zero scientific source or target cells. This validates the local execution chain only.
- The R13 execution manifest is prepared but remains `LOCAL_DEVELOPMENT_EIGHT_PROCESS_MANIFEST_READY_MODEL_NOT_RUN`.
- The design value `FPR = 1/6` is an unweighted fixed-candidate-panel quantity. It is not empirical, natural, or policy-weighted FPR.
- The repository does not establish sampled or multi-step RLVR transfer, population-level task generalisation, shared semantic identity, deployment risk, or a causal effect of shared verifier errors.
- A/B order repeats are technical reproducibility checks, not independent scientific samples.
- GPT Pro outputs are AI-assisted design reviews, not human peer review or independent replication.

See [EVIDENCE_STATUS_2026-08-09.md](EVIDENCE_STATUS_2026-08-09.md) for the evidence tiers and authoritative pointers.

## Included scope

- `GPT_PRO_REVIEW_RLVR_V1/formal_g1_development_r1/`: frozen protocols, analyzers, validators, R10 diagnostic artifacts, R13 design artifacts, engineering canary, and execution manifest.
- `GPT_PRO_REVIEW_RLVR_V1/mvp_same_source_v1/`: development MVP code, tests, and bounded diagnostic outputs.
- `GPT_PRO_REVIEW_RLVR_V1/authorized_runs/`: machine-readable run records and integrity receipts retained for auditability.
- `GPT_PRO_REVIEW_RLVR_V1/real_assets/`: generators, protocols, tests, manifests, and validation reports; large generated build/release trees are excluded.
- `literature/`: source indexes, manifests, maps, validation summaries, and acquisition/validation tools. Third-party PDFs are excluded.
- Root Python utilities used to assemble, compare, verify, or execute bounded artifacts.

## Intentionally excluded

- model weights and local virtual environments;
- third-party paper PDFs whose redistribution rights were not established;
- generated ZIP archives and duplicate packet-clean-test trees;
- the 7,010-file full real-asset build/release corpus;
- machine-local preflight receipts containing local paths and hardware details;
- internal ChatGPT/GPT Pro thread links and raw reviewer responses;
- caches, bytecode, and transient logs.

These exclusions reduce privacy, licensing, repository-size, and evidence-interpretation risk. They do not delete the local source material.

## Closest prior-work boundary

`Delay, Plateau, or Collapse` already establishes the broad point that systematic verifier-error patterns, initial trigger frequency, and conditional advantage can change within-task RLVR outcomes. A non-redundant continuation here must test a source-to-target alignment effect after controlling target-side initial frequency, conditional advantage, policy-weighted FPR, erroneous-reward exposure, and update strength.

Until that experiment exists, this repository should be read as a reproducible research-design and development-evidence package rather than a completed scientific result.
