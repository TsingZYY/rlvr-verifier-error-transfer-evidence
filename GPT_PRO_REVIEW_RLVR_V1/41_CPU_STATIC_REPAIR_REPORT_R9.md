# CPU/static output-path fail-closed repair report R9

Status: `CPU_STATIC_REPAIR_CANDIDATE_AWAITING_GPT_PRO_REAUDIT`

No tokenizer, model weight, forward, gradient, optimizer, GPU, or sampled-RLVR
action occurred. Scientific evidence and model authorization remain false.

R9 changes only the output-path boundary identified by the R8 arbiter:

1. `os.path.lexists` rejects every pre-existing output directory entry,
   including a broken symlink.
2. Any symlink in the output ancestor chain is rejected.
3. Rejection preserves the existing file/link and any link target, emits bounded
   canonical FAIL JSON to stdout, and returns exit code 1 without traceback.
4. A fresh output is created with exclusive-create and no-follow flags where
   available, closing the check/write race at the final path.
5. Any exclusive-open/write failure also becomes bounded canonical stdout FAIL.

Local CPU/static results:

- 56/56 focused tests pass.
- Existing regular output is byte-preserved and produces canonical stdout FAIL.
- Broken output symlink is preserved and its target is not created.
- Symlinked output parent is rejected and no file is created through it.
- R8 parser, R7 numeric, and R5 completion regressions remain passing.
- Two refreshed R9 contract trees are byte-identical: 27/27 files.
- R9 master SHA-256:
  `0f738610c6bfb0386ae37a2f7f5a4d7abba89cb36b5c96d39aa230d3e6bf860a`.
- Completion validator SHA-256:
  `55873e35bddef3383dcb5c9c79dda20c9cb8934a26a61e8a19720937c7942743`.

This remains static infrastructure evidence only, not same-FPR, sampled-RLVR,
hidden-audit, formal-G1, or scientific evidence.
