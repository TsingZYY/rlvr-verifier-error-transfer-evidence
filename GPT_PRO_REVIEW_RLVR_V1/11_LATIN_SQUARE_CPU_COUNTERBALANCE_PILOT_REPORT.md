# Latin-square CPU Counterbalance Pilot Report

## Outcome

`PASS_COUNTERBALANCE_FEASIBILITY_ONLY`

The five-block cyclic Latin-square construction resolves the specific static
aliasing and reward-geometry problem in an isolated CPU-only feasibility pilot.
It does **not** make the frozen v5 release run-eligible and does not authorize a
tokenizer, model, forward pass, optimizer, training, RL, or RLVR action.

## Evidence boundary

- `experiment_status=NOT_RUN`
- `scientific_evidence=false`
- `formal_experiment=false`
- `run_eligible=false`
- `human_review_status=PENDING_HUMAN_REVIEW`
- `design_review_status=PENDING_POST_REDESIGN_REVIEW`
- `audit_access_mode=NONBLIND_LOCKED_AUDIT_REFERENCE`
- `model_execution_performed=false`

The pilot reads the existing 280 frozen CPU rows from `build_v4_a` only as a
rejected baseline reference. Each base row is deliberately reused as a paired
repeated measure across five rule-identity blocks. The 1,400 derived records are
not 1,400 independent scientific observations.

## Frozen counterbalance

| Rule block | Shared | MOD7 local | DFA7 local | Rank7 local | Paren7 local |
|---|---:|---:|---:|---:|---:|
| RB0 | +1 | +2 | +3 | +4 | +5 |
| RB1 | +2 | +3 | +4 | +5 | +1 |
| RB2 | +3 | +4 | +5 | +1 | +2 |
| RB3 | +4 | +5 | +1 | +2 | +3 |
| RB4 | +5 | +1 | +2 | +3 | +4 |

Every `Z7_PLUSk` rule identity serves exactly once as shared and exactly once as
each task's local rule. Shared and local offsets never coincide within a block.

## Strict geometry definition

For every derived row and arm:

```text
vector_key(row, arm)
  = tuple(reward_by_arm[arm][candidate]
          for candidate in [FINAL=K0, ..., FINAL=K6])
```

The pilot requires both of the following counters to be exactly equal:

```text
Counter(candidate-aligned reward vector)
Counter(gold_candidate -> wrong_candidate)
```

The equality is checked separately for `SOURCE`, `TARGET_CALIBRATION`, and
`TARGET_AUDIT` at these scopes:

- global split;
- task pair;
- each of the eight base mapping stacks aggregated across all five rule blocks.

This produces `3 × (1 + 2 + 8) = 33` required geometry records.

## Results

### Rejected v5 baseline

- Base rows: 280
- Geometry records: 27
- Failed geometry records: 27
- Exact geometry pass: `false`
- Expected failure observed: `true`

### Five-block counterbalanced pilot

- Base rows: 280
- Rule blocks: 5
- Derived paired records: 1,400
- Future formal design cells represented: `5 × 8 = 40`
- Required geometry records: 33
- Failed geometry records: 0
- Per-base-row rule-identity counterbalance: `true`
- Rule-identity role matrix: `true`
- All required static geometry checks: `true`

The result demonstrates that the proposed counterbalancing method can remove
the deterministic rule-identity and candidate-aligned reward-geometry aliasing
that invalidated v5.

## Determinism and regression checks

Two independent builds were generated:

- `real_assets/latin_square_pilot_v1/build_a`
- `real_assets/latin_square_pilot_v1/build_b`

Each build contains five files and 2,450,883 bytes. The two trees are
byte-for-byte identical (`difference_count=0`).

Full regression suite:

```text
49 passed in 62.63s
```

This includes five new pilot tests covering the Latin square, strict geometry,
independent deterministic builds, fail-closed tamper detection, and
non-overwrite behavior.

## Build A artifact hashes

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `BASELINE_REWARD_VECTOR_PAIR_GEOMETRY_AUDIT_V1.json` | 73,751 | `931BC273C90D5517C5B9D6D3412B4F78F606705CEA12D8A0E1CCA5A6E4DD6BE2` |
| `LATIN_SQUARE_ASSIGNMENT_V1.json` | 2,721 | `83354291B03AC6E8358DF28DBD744976105DFE44086059E744CCF47A658A334A` |
| `LATIN_SQUARE_DERIVED_ROWS_V1.jsonl` | 2,280,880 | `BF72D2949A532B3911672640ED9A10592F8D29A9539C11B6070469D64B0FF324` |
| `LATIN_SQUARE_PILOT_BUILD_MANIFEST_V1.json` | 2,265 | `B79F39BC7F66DD345CF7A74CE8A93750E6DD389270E8F24E8662D7AD76183669` |
| `REWARD_VECTOR_PAIR_GEOMETRY_AUDIT_V1.json` | 91,266 | `AED44AD87B314126B67F4953868FA5AC2F4B8268FD057468676DA2ADA79F3281` |

## What remains blocked

The isolated feasibility pass is not a production asset-gate acceptance. Before
any model work, the following still must happen:

1. Migrate the formal builder, native validator, manifests, protocol, package
   validator, and hash chain from 8 frozen cells to the 5 × 8 design.
2. Generate a new versioned release; the rejected v5 hashes and audit seal
   cannot be reused.
3. Freeze the exact G1 model-dependent metrics, thresholds, global stop rule,
   update-norm diagnostic-only rule, model/tokenizer/runtime/update/scoring
   decisions, and analysis hierarchy.
4. Regenerate a truly hidden audit only after those blocking fields are frozen,
   or explicitly retain the current audit as nonblind diagnostic material.
5. Complete human row review and a post-redesign independent review.

Until those steps pass, `L`, `D`, `tau`, transfer, and the causal research claim
remain `NOT_RUN` or unverified.
