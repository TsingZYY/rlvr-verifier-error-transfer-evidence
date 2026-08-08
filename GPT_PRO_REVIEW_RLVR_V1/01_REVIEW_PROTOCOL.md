# Four-Reviewer Protocol

The reviewers have non-overlapping responsibilities.

## P1: novelty and claim boundary

Answer only: Is the narrowed claim still novel relative to the latest primary
literature? Compare concrete estimands and settings, not titles. The shared
ChatGPT report is only a search lead.

Required verdict:

- `NOVEL_WINDOW_DEFENSIBLE`
- `DEFENSIBLE_ONLY_AFTER_NARROWING`
- `NOVELTY_NOT_DEFENSIBLE`

## P2: causal identification and construct validity

Answer only: Does the candidate one-step experiment identify cross-task error
sharedness rather than token, difficulty, reachability, or update-opportunity
effects? Freeze treatment, outcome, estimand, randomization unit, target
task-disjointness, and the minimum necessary controls.

Required verdict:

- `IDENTIFIED_AS_WRITTEN`
- `IDENTIFIABLE_WITH_SPECIFIC_FIXES`
- `NOT_IDENTIFIED`

## P3: statistical decision and independent replication

Use the P2-frozen design. Specify aggregation, paired contrast, interval/test,
practical-effect threshold, multiplicity, missing-unit rules, and exact
`GO / STOP / INCONCLUSIVE` criteria. LoRA seeds and prompt rows are not
independent scientific units.

Required verdict:

- `DECISION_CAPABLE`
- `UNDERPOWERED_BUT_INFORMATIVE`
- `NON_DECISION_CAPABLE`

## P4: execution evidence and cheapest run gate

Use P1-P3 and the repository artifacts. Distinguish CPU/tokenizer leakage
checks, the one-step causal core gate, and unapproved short/full RLVR. Issue
only the cheapest next executable task and the required return-artifact
manifest.

Required verdict:

- `RUN_NOW`
- `REPAIR_THEN_RUN`
- `STOP_AND_PIVOT`

Experiment status:

- `CORE_GATE_PASS`
- `CORE_GATE_FAIL`
- `INCONCLUSIVE`
- `NOT_RUN`

## Authorization rule

P4 may authorize a one-step core run only if:

1. P1 is not `NOVELTY_NOT_DEFENSIBLE`;
2. P2 is not `NOT_IDENTIFIED`;
3. P3 is not `NON_DECISION_CAPABLE`;
4. prerequisite leakage and matching gates are satisfied prospectively.

