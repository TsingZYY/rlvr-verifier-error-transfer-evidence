# GPT Pro focused cwd-bound output re-audit R10

Continue as the same final arbiter. Work only inside this ZIP and perform only CPU/static checks. Do not load a tokenizer/model or execute forward, gradient, optimizer, GPU, or sampled-RLVR work.

First verify packet anchors, manifest, clean-root tests, double contract tree, refreshed master, and completion-only audit. Confirm the correction that `R9_EXCLUSIVE_CREATE_FAILURE_CLASS` was withdrawn as `UNREPRODUCED` and that `R9_TOCTOU_ANCESTOR_SWAP` was the only supported R9 blocker.

Attack the new output capability boundary with the real CLI:

1. From an ordinary existing output directory used as process cwd, pass a fresh leaf output and confirm exactly one canonical result file is created.
2. Try absolute output paths outside cwd, nested relative paths, `..` paths, and a symlink alias or junction alias to cwd. They must fail before `os.open` and create nothing outside cwd.
3. Race creation of the leaf entry after the pre-check. `O_EXCL` must preserve the racing entry and return one bounded canonical FAIL JSON object without traceback.
4. Re-run exact-leaf attacks using a regular file, directory, hardlink, valid symlink, and broken symlink.
5. Attempt the former ancestor-swap attack. Decide whether it can still redirect `os.open` now that the open receives only the leaf name relative to the process cwd and no parent pathname.
6. Re-run R8 parser-limit/bad-input, R7 extreme numeric, and R5 hash-chain/double-tree/authorization regressions.

Every rejection must return 1, preserve pre-existing entries and targets, emit one bounded canonical FAIL JSON object with `aggregate=null`, and emit no traceback or NaN/Infinity.

Return one first-line token followed by compact JSON:

- `REPAIR_AUDIT_PASS`
- `REPAIR_AUDIT_MORE_FIXES`
- `REPAIR_AUDIT_INVALID_PACKET`

PASS only permits asking the user for explicit model-run authorization. It does not itself authorize model work and is not scientific evidence.
