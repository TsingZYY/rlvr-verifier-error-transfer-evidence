# Same-source diagnostic MVP run report R2

Date: 2026-08-04  
Execution owner: Codex  
Run status: `MVP_COMPLETED_DIAGNOSTIC_ONLY`  
Validation status: `PASS`  
Scientific evidence: `false`  
Formal experiment: `false`

## Decision

The minimum same-source model chain is technically viable and causally wired as
intended, but this one-stack diagnostic does **not** provide directional support
for a stable same-identity transfer excess.

The frozen stack-level result was:

```text
mean diagonal excess = +0.0014351197651454317 log-probability units
positive source rules = 3/5
mean absolute per-rule diagonal excess = 0.0251601253237043
sample SD across the five rule identities = 0.0323871574271811
range = [-0.0469221046992711, +0.0362737859998431]
```

The aggregate is small relative to the identity-level dispersion and the signs
are mixed. The correct interpretation is `PIPELINE_PASS_EFFECT_UNRESOLVED`, not
evidence that the research hypothesis is true and not a fatal falsification of
the eight-stack mechanism screen.

## Frozen MVP

- Model: `HuggingFaceTB/SmolLM2-360M-Instruct`
- Revision: `a10cc1512eabd3dde888204e902eca88bddb4951`
- Weight SHA-256:
  `e6bffe7435d7ddc10fd3b9a9efd429dafbacb1cb17015fb5562664e7532bf86e`
- Runtime: PyTorch `2.8.0+cu128`, Transformers `4.55.4`, PEFT `0.17.1`
- Mapping stack: `TP1-M0-A_TO_B`
- Development data only: 14 source rows and 7 target-calibration rows
- Audit access: forbidden and unused
- Five source identities: `Z7_PLUS1` through `Z7_PLUS5`
- Five target identities evaluated under each identical source update
- Update: one manual-SGD step on rank-4 LoRA `q_proj` and `v_proj`
- Learning rate: `0.1`; gradient clipping: `1.0`
- Objective: exact expected verifier reward over seven fixed candidates
- Reward: gold plus the source-rule wrong candidate receive 1; all others 0
- Target metric: mean wrong-versus-gold candidate log odds

This is a fixed-candidate RLVR surrogate. It does not include sampled rollouts,
an optimizer state, multiple policy steps, or a formal G1 contract.

## Diagnostic gates

All five source identities passed the frozen low-cost opportunity gates.

```text
mean wrong-candidate probability mass: 0.124899 to 0.168305
mean wrong relative advantage:          0.676354 to 0.719760
candidate entropy:                      1.803990 nats
supervised candidate token count:       exactly 7 for every candidate
```

These are feasibility checks. They establish that the base model supplied
nondegenerate candidate probability and learning signal; they are not evidence
about cross-task transfer.

## Per-source result

| Source identity | Diagonal excess |
|---|---:|
| `Z7_PLUS1` | +0.0213746684 |
| `Z7_PLUS2` | -0.0123904092 |
| `Z7_PLUS3` | +0.0088396583 |
| `Z7_PLUS4` | -0.0469221047 |
| `Z7_PLUS5` | +0.0362737860 |

The identity heterogeneity is the decisive result. Averaging it into one small
positive number would conceal two negative identities, especially the large
negative `Z7_PLUS4` result.

## Causal and integrity checks

- Exactly 5 source updates and 25 ordered source-target evaluation cells.
- Each source update has one parameter hash reused by all five target readouts.
- Five source identities have five distinct update hashes.
- Every update begins from the same initial LoRA parameter hash.
- No optimizer state or warm start is carried across source identities.
- Trainable parameter count: 409,600.
- Realized update norms: 0.004092 to 0.007941.
- Restoring the initial LoRA state reproduced every base target score with
  maximum absolute error `0.0`.
- Two R2 CUDA runs with `CUBLAS_WORKSPACE_CONFIG=:4096:8` were identical in all
  substantive fields after excluding timestamp, runtime, and peak-memory
  diagnostics.
- Substantive replicate SHA-256:
  `bb544e35de848c629cb6c905fd995572997f27bb1bb0ef24422ef7b910af5da1`.
- Runtime: 81.86 and 81.35 seconds.
- Peak allocated GPU memory: 3.750 GiB in both deterministic replicates.

## Evidence boundary

### Demonstrated

- The same-source intervention graph can be executed on the available GPU.
- One physical source update can be reused across all five target identities.
- The fixed candidate policy has nondegenerate wrong mass and relative
  advantage for all five identities in this stack.
- The implementation and measured outputs are deterministically reproducible.

### Not demonstrated

- A positive or stable diagonal transfer excess.
- Cross-stack, cross-direction, or cross-task-pair robustness.
- Sampled RLVR behavior.
- Hidden-audit performance.
- Statistical significance, confirmation, or population generalization.
- That a shared representation is the unique causal mediator.

## Lowest-cost next experiment

Do not tune this stack or learning rate using the observed signs. Preserve this
run as development-only and execute the identical frozen MVP on the other seven
mapping stacks. The resulting eight stack summaries would answer whether the
mixed identity pattern is stack-specific or structurally repeatable. Only if
that screen is coherent should the project pay the cost of sampled RLVR and a
fully frozen G1 contract.

