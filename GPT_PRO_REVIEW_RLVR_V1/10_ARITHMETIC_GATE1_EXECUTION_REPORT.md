# Arithmetic Gate 1 Execution Report

Run date: 2026-07-28  
Decision: **STOP_BEFORE_RL — independent matching gate failed**  
Core hypothesis status: **not evaluated**

## Outcome first

The local and shared verifiers were implemented and measured on the exact same
frozen SmolLM2 candidate groups. Their natural false-positive frequencies were
successfully brought very close:

| Final independent audit | Local | Shared | Local / shared |
|---|---:|---:|---:|
| False positives | 60 | 59 | 1.017 |
| Natural FPR | 0.007344 | 0.007222 | 1.017 |
| Candidate exposure | 0.007324 | 0.007202 | 1.017 |
| Non-zero-advantage groups | 52 | 55 | relative difference 0.055 |

However, the pre-registered independent gate did not pass:

- effective-advantage SMD: `0.311` (required `<= 0.20`);
- maximum target-property SMD: `0.520` (required `<= 0.20`).

The target-property comparison includes magnitude, decimal width, sign,
completion token length, and base-model mean log-probability. The failure means
that equal accepted-wrong counts did not imply equal update opportunity.
Running local/shared RL after this result would confound shared error structure
with base probability scale and completion form.

Therefore:

- clean/local/shared experimental RL was **not run**;
- held-out transfer \(X_b\) was **not measured**;
- boundary skill loss (BSL) was **not measured**;
- the question “does the shared bug cause more transfer or damage?” remains
  unanswered rather than receiving a negative answer.

## Frozen implementation

The experiment now has runnable, tested components for:

- exact integer AST generation and evaluation;
- strict integer-only parsing;
- true, local, and shared verifiers;
- construction-only reachable local-target calibration;
- common local/shared trigger-set matching;
- fixed local SmolLM2 rollout pools;
- \(d_b\), \(\tau_b\), sequential first-pass Best-of-N, and \(f_b\);
- independent hard matching gates;
- fresh-rollout online group-relative REINFORCE with BF16 LoRA;
- base/adapter evaluation for \(X_b\) and BSL;
- identity-preconditioned LoRA \(\kappa_b\) and reversible one-step \(m_b\).

The trainer fixes:

- LoRA rank 8, alpha 16, dropout 0 on q/k/v/o projections;
- group size 8;
- temperature 1, top-p 1, top-k 0, maximum 8 generated tokens;
- AdamW learning rate `1e-5`, betas `(0.9, 0.95)`, weight decay 0;
- fixed-reference KL coefficient `0.04`;
- one update per fresh rollout group.

Bug arms require a common trigger manifest. Local and shared cannot use
different prompt subsets.

## Model and data

Local model:

- `HuggingFaceTB/SmolLM2-360M-Instruct`;
- frozen revision
  `a10cc1512eabd3dde888204e902eca88bddb4951`;
- no inference API was used;
- local weight and tokenizer hashes are recorded in
  `models/SmolLM2-360M-Instruct/MODEL_MANIFEST.json`.

The final engineering dataset contains 32 subtraction problems. True answers
are negative and \(|gold|\) is stratified uniformly from 5 through 20, twice
per value. This replaced an earlier diagnostic dataset over-concentrated at
`-1` and `-2`, where the shared sign-drop target collapsed into the model's
unrelated prior for output `1`.

For every problem:

\[
w_{\mathrm{shared}}=|gold|.
\]

The local target is a frozen table lookup, not a shared transformation. It is
selected only from naturally observed wrong integers, constrained to the same
positive sign and decimal width as the shared target, with bounded target
reuse. The selection objective matches its observed construction frequency to
the shared target's frequency. A common 19-problem trigger set is used by both
arms.

## Selection versus independent evidence

The two-seed design pool contains 12,288 candidate records, 768 groups, and 16
candidates per group. It passed the design gate:

| Design-pool quantity | Local | Shared | Comparison |
|---|---:|---:|---:|
| False positives | 89 | 86 | ratio 1.035 |
| Effective advantage | 3.476 | 3.463 | SMD 0.0218 |
| Non-zero groups | 77 | 73 | relative difference 0.052 |
| Max target-property SMD | — | — | 0.0118 |

The final independent pool uses unseen seed `36003`, 8,192 records, 512
groups, and the exact frozen targets/trigger set. It failed as reported above.
No target, subset, threshold, or decoding parameter was retuned after viewing
this final audit.

## Frozen selector diagnostics

These values are descriptive diagnostics despite the failed matching gate:

| Metric | Local | Shared |
|---|---:|---:|
| \(d_b\) | 0.10156 | 0.10742 |
| \(\tau_b\) | 0.02556 | 0.02640 |
| \(f_b\) | 0.03613 | 0.04102 |

Sequential first-pass harmful selection \(H_b(N)\):

| \(N\) | Local | Shared |
|---:|---:|---:|
| 1 | 0.00391 | 0.00781 |
| 2 | 0.01172 | 0.01758 |
| 4 | 0.02930 | 0.02930 |
| 8 | 0.05078 | 0.06055 |
| 16 | 0.10156 | 0.10547 |

This is a fixed sequential tie-break for binary verifier rewards. It is a
selector effect, not parameter learning.

## Counterfactual diagnostic

A fresh, identical LoRA initialization was used for both arms. Protected gold
NLL used 16 disjoint new-number problems. The update was reversed exactly
after each arm.

| Diagnostic | Local | Shared |
|---|---:|---:|
| Source events | 60 | 59 |
| \(\kappa_b=-\cos(g_P,g_b)\) | -0.20786 | -0.18623 |
| \(m_b\), protected NLL change | -0.01372 | -0.00669 |
| Restore error | 0 | 0 |

Both \(m_b\) values are negative, so this one-step probe predicts improvement,
not absolute protected-capability harm. Shared is larger only in the relative
sense that it predicts less improvement than local. With two bug mechanisms
and a failed matching gate, this cannot validate a general risk predictor.

## Verification

- Full test suite: `132 passed`.
- Final rollout pool audit: passed, 8,192 records, 32 prompts, 512 complete
  groups, 16 candidates per group.
- Counterfactual parameter restoration error: exactly zero.
- Artifact canonical inventory SHA-256:
  `881049155ad42b11314abd649e1c73b30881c2b8905281043045355e0c3d461d`.

## Evidence boundary and next experiment

This run establishes that the implementation and falsifiable stop gate work.
It does **not** establish the proposed shared-bug damage mechanism.

The next valid experiment should change the data/action design before any RL:

1. use substantially more independent problem identities, rather than mainly
   more samples from the same prompts;
2. cross-fit local-target selection across multiple construction seeds;
3. retain same-sign/same-width/base-log-probability matching as hard gates;
4. consider a separately labelled integer-constrained policy experiment if
   free-form generation remains too sparse;
5. reserve a new untouched audit seed and do not run RL unless it passes.

Relaxing the current `0.20` advantage gate after seeing this result would not
be a valid continuation of the frozen experiment.
