# GPT Pro R5 final re-audit summary

Final token: `REPAIR_AUDIT_MORE_FIXES`

The same final arbiter independently verified the 63-member ZIP, all 62 payload
entries, CRC/path safety, 41/41 clean-root tests, two fresh 574-file asset trees,
280/280 visible prompt semantics, recursive model-inventory mutation rejection,
two 27-file byte-identical frozen contract trees, the exact eight-stack master
hash, authorization receipt attacks, A/B path/inode/nonce/receipt attacks, norm
evidence labels, current-audit boundaries, and most completion negative cases.

Authorization, replica-pair independence, norm evidence boundaries, asset
semantics, and the eight-stack hash chain passed. The 727 MB model omission was
classified only as an evidence gap, not a vulnerability or the reason for the
negative verdict.

Four completion-validator vulnerabilities remained:

1. A run nonce could be reused across different stacks, yielding only 15 unique
   invocations while completion still passed.
2. Re-anchored malformed result/invocation/authorization hashes, authorization
   IDs, and run nonces passed because equality/uniqueness was checked without
   exact SHA-256 or UUIDv4 format validation.
3. `positive_diagonal_excess_count` accepted 999 and 3.7; the latter was silently
   truncated with `int()` despite each stack having exactly five source rules.
4. A FAIL report was detected but still contributed to a non-null aggregate,
   allowing contaminated means/counts to be emitted downstream.

The arbiter required 16 globally distinct UUIDv4 nonces, exact internal
identifier formats, finite/count-domain validation without coercion, and
`aggregate = null` whenever any completion error exists. Scientific evidence and
authorization request status both remained false.
