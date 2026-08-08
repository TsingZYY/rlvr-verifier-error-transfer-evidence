# GPT Pro focused numeric fail-closed re-audit R7

Continue as the same final arbiter. Work only inside this ZIP and perform only
CPU/static checks. Do not load a tokenizer/model or execute forward, gradient,
optimizer, GPU, or sampled-RLVR work.

Independently verify ZIP hash/path/CRC/payload, clean-root unittest, both frozen
27-file trees, the R7 master hash, and the completion-only refresh audit. Then
reproduce the two exact R6 blockers with consistent re-anchoring:

1. Give all eight valid reports individually finite means near the largest
   representable float (including `1e308` and `sys.float_info.max`). The result
   must never PASS with a non-finite aggregate and must always serialize.
2. Give one or more reports arbitrarily large JSON integer means (for example
   `10**1000`). The validator and CLI must not raise; they must return/write
   structured FAIL with `aggregate=null` and finite/canonical JSON only.

Also attack mixed-sign extreme floats, booleans, strings, NaN/Infinity where the
in-memory API permits them, and any numeric conversion or derived-aggregation
exception path. Every invalid path must be fail-closed and serializable. Confirm
the original four R5 blockers remain fixed and the valid exact-eight fixture
still recomputes its original aggregate.

Return one final token on the first line:

- `REPAIR_AUDIT_PASS`
- `REPAIR_AUDIT_MORE_FIXES`
- `REPAIR_AUDIT_INVALID_PACKET`

Always follow the token with compact JSON evidence. A PASS only permits asking
the user for explicit model-run authorization; it does not authorize model work
and is not scientific evidence.
