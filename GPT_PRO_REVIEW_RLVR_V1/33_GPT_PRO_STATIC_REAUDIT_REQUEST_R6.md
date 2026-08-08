# GPT Pro focused completion re-audit R6

Continue as the same final arbiter. Work only inside this ZIP and perform only
CPU/static checks. Do not load a tokenizer/model or execute forward, gradient,
optimizer, GPU, or sampled-RLVR work.

Independently verify ZIP hash/path/CRC/payload, clean-root unittest, both frozen
27-file trees, and the new master hash. Then reproduce the four exact R5 attacks:

1. Reuse one UUIDv4 run nonce across two different stacks while keeping 16
   distinct result and invocation hashes and re-anchoring everything.
2. Separately replace result, invocation, or authorization hashes with unique
   malformed strings; replace authorization ID or run nonce with malformed
   strings; re-anchor and rehash consistently.
3. Use `positive_diagonal_excess_count` values 999, -1, 3.7, and `true`; also use
   non-finite/bool means or non-integer source-rule counts.
4. Mark one report FAIL, give it a contaminated summary, rehash it, and verify the
   final output is FAIL with `aggregate=null`. Repeat the null-aggregate assertion
   for old R2, duplicate paths/inodes/hashes, invalid labels, malformed IDs, and
   any other completion error.

Also confirm the valid exact-eight fixture still recomputes the same aggregate
and that no authorization/evidence status changed.

Return one final token:

- `REPAIR_AUDIT_PASS_REQUEST_USER_AUTHORIZATION_ALLOWED`
- `REPAIR_AUDIT_MORE_FIXES`
- `REPAIR_AUDIT_INVALID_PACKET`

A PASS only permits asking the user for an exact externally hashed authorization
receipt. It does not authorize model work and is not scientific evidence.
