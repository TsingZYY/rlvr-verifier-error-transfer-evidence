# GPT Pro：真实 Task-Pair 与资产构造裁决

日期：2026-07-30  
审核角色：因果识别与资产设计  
状态：`PRE_REVIEW / NOT_RUN`

## 单一裁决

`BUILD_TWO_CONTROLLED_TASK_PAIRS_FROM_SCRATCH`

旧 arithmetic H-ID、旧 natural Gate-1 pool、SQLite test 和 P4 synthetic
fixture 均不能作为本轮 scientific rows。可以复用的只有 canonical
serialization、SHA-256、schema、lineage 和 overlap checker 等任务无关工程组件。

本裁决只证明存在一套可构造的最低成本真实资产方案，不授权模型运行：

```text
protocol_status:       PRE_REVIEW
experiment_status:     NOT_RUN
scientific_evidence:   false
run_eligible:          false
G1:                    NOT_RUN
G2:                    NOT_RUN
```

## Task-disjoint 的最低定义

Source 与 target 必须由不同的 task oracle、input schema、generator code path、
prompt grammar 和 seed namespace 产生；不得共享实例、模板 lineage 或语义求解
函数。唯一允许共享的是预先声明的七类输出接口、候选序列格式和 bug-rule
framework。

附加规则：

- source、target、calibration、audit 和不同 stack 的 row ID、seed、prompt bytes
  与 lineage 必须零交集；
- audit 必须在任何模型动作前字节级 seal，且不能参与设计、匹配、调参或 debug；
- A→B 与 B→A 是一个 task pair 的两个 direction/mirror，不是两个 task pairs；
- 两个 mappings 是独立 codebook constructions；
- 独立科学单位是 mapping stack；LoRA seeds 只作技术重复。

## 统一输出接口

四个任务都产生 canonical class：

`z ∈ {0,1,2,3,4,5,6}`

每个 prompt 的七个候选固定为：

```text
FINAL=K0
FINAL=K1
FINAL=K2
FINAL=K3
FINAL=K4
FINAL=K5
FINAL=K6
```

不得增加解释、空格或换行差异。

## 两个 exact task pairs

### TP1_MOD7SUM_DFA7

`MOD7_SUM_V1`

- 输入：`a,b ∈ {0,...,20}`
- oracle：`z=(a+b) mod 7`
- 按 z 分层生成，七类严格平衡

`DFA7_FINAL_V1`

- 输入：start state、长度 2–6 的 `{x,y}` sequence、完整 transition table
- transition：

| state | x | y |
|---:|---:|---:|
| 0 | 2 | 4 |
| 1 | 5 | 0 |
| 2 | 1 | 6 |
| 3 | 6 | 2 |
| 4 | 0 | 5 |
| 5 | 3 | 1 |
| 6 | 4 | 3 |

- oracle：执行完整 sequence 后的 final-state index

Directions：

- `MOD7_SUM_V1 → DFA7_FINAL_V1`
- `DFA7_FINAL_V1 → MOD7_SUM_V1`

### TP2_RANK7_PARENDEPTH7

`MARKED_RANK7_V1`

- 输入：七个互异整数，其中一个被标记
- oracle：严格小于被标记值的 listed values 数量

`PAREN_MAX_DEPTH7_V1`

- 输入：合法 balanced-parentheses string，最大深度 1–7
- oracle：`z=maximum_depth−1`
- 每个深度必须有多个非同构字符串，不能只替换长度

Directions：

- `MARKED_RANK7_V1 → PAREN_MAX_DEPTH7_V1`
- `PAREN_MAX_DEPTH7_V1 → MARKED_RANK7_V1`

## 两个 mappings 与 8 stacks

对每个 task pair，第一项为 A，第二项为 B：

```text
mapping_0:
  c_A(z) = K_z
  c_B(z) = K_((2z+1) mod 7)

mapping_1:
  c_A(z) = K_((3z+2) mod 7)
  c_B(z) = K_((5z+4) mod 7)
```

Exact stacks：

```text
TP1-M0-A_TO_B
TP1-M0-B_TO_A
TP1-M1-A_TO_B
TP1-M1-B_TO_A
TP2-M0-A_TO_B
TP2-M0-B_TO_A
TP2-M1-A_TO_B
TP2-M1-B_TO_A
```

每个 stack：

- source rows：14（每类 2）
- target calibration rows：7（每类 1）
- untouched target audit rows：14（每类 2）

合计：

- source 112 rows
- target calibration 56 rows
- target audit 112 rows
- 280 prompts × 7 candidates = 1,960 candidate records

这些是结构覆盖下限，不是 power 声明。

## Shared 与 local rules

Shared rule：

```text
S_Z7_PLUS1: z -> (z+1) mod 7
```

Local rules：

| task | rule | transform |
|---|---|---|
| `MOD7_SUM_V1` | `L_MOD7_PLUS2` | `z -> z+2 mod 7` |
| `DFA7_FINAL_V1` | `L_DFA7_PLUS3` | `z -> z+3 mod 7` |
| `MARKED_RANK7_V1` | `L_RANK7_PLUS4` | `z -> z+4 mod 7` |
| `PAREN_MAX_DEPTH7_V1` | `L_PAREN7_PLUS5` | `z -> z+5 mod 7` |

所有 non-zero shifts 都是无 fixed-point 七循环。逐 row 必须满足：

`gold != shared_wrong != task_local_wrong`

## Reward contract

每个 prompt 的七个 candidates 和顺序在所有 arm 间完全相同。

```text
clean:
  gold=1; six wrong=0

shared_leaky:
  gold=1; shared_wrong=1; remaining five wrong=0

local_leaky:
  gold=1; task_local_wrong=1; remaining five wrong=0

shared_frozen_probe_control / local_frozen_probe_control:
  parameter_update=false
  selection_exposure_adjustment=false
```

FPR 定义：

`accepted wrong candidates / all wrong candidates`

因此 shared/local 均为：

```text
online_FPR = 1/6
accepted_wrong_reward_mass = 1 per prompt
wrong_positive_count = 1 per prompt
total_positive_count = 2 per prompt
candidate_count = 7 per prompt
candidate exposure = identical
reward-vector multiset = identical
```

不得事后匹配 update norm。

## 仍为 NOT_RUN 的量

- base logits
- initial wrong-target reachability
- model-conditioned log probability
- tokenizer-level tokenization
- model-conditioned relative advantage
- pre/post target log probabilities
- `L`, `D`, `tau`
- G1、G2、core 和 scientific GO/STOP

## 人工复核边界

PRO 要求全部 source、calibration 和 audit rows 逐行人工复核。Codex 可以生成
machine-oracle labels、独立算法复核与待审清单，但不得把 AI 或算法检查写成
`human_review=PASS`。在真人完成逐行复核前，必须保持：

`human_review_status=PENDING_HUMAN_REVIEW`

任何 disagreement 都必须生成新版本并重新 seal；不得原位修改已 seal 的 rows。

## CPU-only deliverables

1. `P4_R1_REAL_PROTOCOL_V2_PRE_REVIEW.json`
2. `REAL_SOURCE_STACK_BUNDLE_V1.zip`
3. `VERIFIER_G1_CPU_BUNDLE_V1.zip`
4. `TARGET_G2_REAL_BUNDLE_V1.zip`
5. `RANDOMIZATION_MODEL_ENV_PREREG_V1.json`

## 本轮权限

允许：

- 构造四个新任务生成器；
- 生成 source/calibration/audit rows；
- machine oracle、待人工复核清单；
- CPU schema/hash/lineage/overlap/randomization；
- CPU verifier/reward-vector/FPR；
- 创建 audit seal receipt。

禁止：

```text
tokenizer-only = NO
GPU = NO
weight load = NO
forward = NO
backward = NO
gradient = NO
optimizer = NO
training/RL/RLVR = NO
core/scientific GO-STOP = NO
```
