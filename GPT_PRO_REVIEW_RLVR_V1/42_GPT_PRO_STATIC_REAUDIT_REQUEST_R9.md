# GPT Pro focused output-path re-audit R9

Continue as the same final arbiter. Work only inside this ZIP and perform only
CPU/static checks. Do not load a tokenizer/model or execute forward, gradient,
optimizer, GPU, or sampled-RLVR work.

Verify packet anchors, clean-root tests, double contract tree, refreshed master,
and completion-only audit. Then use the real CLI to attack output handling:

1. Pre-existing regular file, directory, hard link, valid symlink, and broken
   symlink as the exact output path.
2. A symlinked immediate parent and a symlink at another existing ancestor.
3. Where practical, race a new output entry between checking and creation.
4. Make exclusive creation/write fail without placing untrusted text in output.

For every rejected path: return 1; preserve every existing directory entry and
target byte-for-byte; create no link target; emit one bounded canonical FAIL JSON
object to stdout with `aggregate=null`; emit no traceback or NaN/Infinity. A
fresh ordinary path must still create exactly one canonical result file.

Re-run the R8 parser-limit/bad-input cases, R7 extreme numerics, and the R5 four
blocker families. Return one first-line token followed by compact JSON:

- `REPAIR_AUDIT_PASS`
- `REPAIR_AUDIT_MORE_FIXES`
- `REPAIR_AUDIT_INVALID_PACKET`

PASS only permits asking the user for explicit model-run authorization. It does
not itself authorize model work and is not scientific evidence.
