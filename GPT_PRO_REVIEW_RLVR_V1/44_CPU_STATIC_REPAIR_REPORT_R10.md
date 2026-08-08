# CPU/static ancestor-TOCTOU repair report R10

Status: `CPU_STATIC_REPAIR_CANDIDATE_AWAITING_GPT_PRO_REAUDIT`

No tokenizer, model weight, forward, gradient, optimizer, GPU, or sampled-RLVR action occurred. Scientific evidence and model authorization remain false.

R9's only supported remaining blocker was `R9_TOCTOU_ANCESTOR_SWAP`. GPT Pro withdrew `R9_EXCLUSIVE_CREATE_FAILURE_CLASS` as `UNREPRODUCED` after a direct request for an actual command, exception, stdout, and stderr.

R10 removes output ancestor traversal from the validator interface:

1. The output must be one safe leaf in the process current working directory. An absolute output is allowed only when its lexical parent is exactly the current working directory.
2. Nested relative outputs, `..`, and any absolute output outside the current working directory fail before `os.open`.
3. The exclusive open receives only the leaf name. It never receives a parent or ancestor component, so swapping a previously checked ancestor cannot redirect creation.
4. Callers that need a different destination must launch the validator with that existing directory as cwd and pass a leaf output name.
5. Pre-existing leaf entries remain protected by `lexists`, `O_EXCL`, and `O_NOFOLLOW` where available.

Local CPU/static results:

- Focused completion-validator tests: `20/20` pass.
- Full packet suite: `57/57` pass.
- Non-cwd absolute and nested relative outputs are rejected before `os.open` and emit bounded canonical FAIL JSON.
- Existing regular file, broken symlink, symlinked parent, parser, numeric, hash-chain, double-tree, and authorization regressions remain passing.
- Two refreshed R10 contract trees are byte-identical: `27/27` files.
- R10 master SHA-256: `a80b0ee7b3c7582d40596a9f7af93a889022224a7a6f772e6e89818b18e82131`.
- Completion validator SHA-256: `76f08682c28c5cd8801a76dad64df396473b3aa7d9dccd63ba2839a876453a23`.
- The completion-only refresh audit passes; all 24 execution contract files are unchanged and no model bytes were read.

This remains static infrastructure evidence only, not same-FPR, sampled-RLVR, hidden-audit, formal-G1, or scientific evidence.
