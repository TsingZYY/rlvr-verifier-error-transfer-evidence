# GPT Pro final static re-audit request R5

Act as the final adversarial arbiter. Work only inside the attached ZIP. Do not
request, load, or execute a tokenizer or model and do not perform forward,
gradient, optimizer, GPU, or sampled-RLVR work.

First independently verify the ZIP SHA-256 reported in the accompanying chat,
path safety, CRC, payload manifest, and clean-root `python -m unittest discover
-v`. Then independently re-run or inspect the two portable asset validations,
the 27-file R5 contract-tree determinism check, the eight manifest/master hash
chain, and every fail-closed mutation below.

Required adversarial mutations:

1. Use the same result path as replicas A and B.
2. Use two paths to the same inode/hard link.
3. Duplicate a run nonce or invocation-start receipt.
4. Swap replicate IDs or replace an invocation receipt after its external hash
   is fixed.
5. Promote a local authorization template, omit/wrong the externally supplied
   authorization SHA-256, replace the receipt, expire it, change its version,
   action ID, authorization ID, allowed operations, or any bound hash.
6. Change or remove any exact evidence-boundary label.
7. Treat runner-reported norms as a gate, independent validation input, or
   scientific explanation.
8. Complete with seven stacks, nine inputs, a duplicate stack, duplicate result
   hashes, duplicate invocation hashes, a FAIL report, an old R2 schema, or a
   report not bound to its result anchor.
9. Recompute the valid synthetic eight-stack aggregate independently and verify
   that only the exact eight PASS reports contribute.

Judge whether these five R4 blockers are now closed:
`REPLICA_INDEPENDENCE_CONTRACT`, `UPDATE_NORM_EVIDENCE_BOUNDARY`,
`USER_AUTHORIZATION_EXTERNAL_BINDING`, `EIGHT_STACK_COMPLETION_VALIDATOR`, and
`EXACT_EVIDENCE_BOUNDARY_LABELS`.

Return one final token:

- `REPAIR_AUDIT_PASS_REQUEST_USER_AUTHORIZATION_ALLOWED`
- `REPAIR_AUDIT_MORE_FIXES`
- `REPAIR_AUDIT_INVALID_PACKET`

Even a PASS only permits asking the user for explicit model-action
authorization. It is not itself authorization and is not scientific evidence.
