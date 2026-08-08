# GPT Pro focused parser-boundary re-audit R8

Continue as the same final arbiter. Work only inside this ZIP and perform only
CPU/static checks. Do not load a tokenizer/model or execute forward, gradient,
optimizer, GPU, or sampled-RLVR work.

Verify the packet, clean-root tests, double contract tree, refreshed master, and
completion-only audit. Then independently exercise the real CLI (not a mocked
validator) with consistently supplied paths and hashes but malformed input at
each position:

1. A validation report containing a JSON integer above Python's configured
   parser digit limit, including at least 10,000 digits.
2. The same parser-limit value in the master and in a result anchor.
3. Malformed JSON, invalid UTF-8, missing/unreadable input, and a non-object JSON
   root where practical.

Every such case must return exit code 1 and write bounded canonical FAIL JSON
with `aggregate=null`; it must not echo the untrusted input, raise an uncaught
exception, emit NaN/Infinity, or overwrite a pre-existing output. Re-run the R7
finite-extreme/huge-integer attacks and sample the R5 four blocker families.

Return one final token on the first line, followed by compact JSON evidence:

- `REPAIR_AUDIT_PASS`
- `REPAIR_AUDIT_MORE_FIXES`
- `REPAIR_AUDIT_INVALID_PACKET`

PASS only permits asking the user for explicit model-run authorization. It is
not scientific evidence and does not itself authorize model work.
