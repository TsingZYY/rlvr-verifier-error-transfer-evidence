# GPT Pro 二次审核：最低成本真实 Task-Pair 与 G1/G2 资产选择

日期：2026-07-30  
当前状态：`NOT_RUN`  
权限边界：CPU-only、只读审计和资产设计；禁止模型加载、forward、gradient、optimizer、training、RL/RLVR。

## 1. 本轮只解决一个问题

在不把旧数据或 synthetic fixture 冒充新证据的前提下，给出能够支持

`general sharedness update-effect`

的最低成本真实 task-pair 方案。

必须最终返回一个单一裁决：

1. `USE_EXISTING_ASSETS_WITH_EXACT_NEW_CONSTRUCTION`
2. `BUILD_TWO_CONTROLLED_TASK_PAIRS_FROM_SCRATCH`
3. `NO_LOW_COST_IDENTIFIABLE_DESIGN`

不要授权模型运行。本轮只裁决资产构造方案。

## 2. 已冻结的 estimand

\[
L_{s,m,t}
=
\frac{1}{N_s}\sum_i
\left[
\log p_{\theta_t}(b_{s,m,i}\mid x_{s,i})
-
\log p_{\theta_t}(g_{s,i}\mid x_{s,i})
\right]
\]

\[
\Delta^{\mathrm{leaky}}_{s,m}
=
L^{\mathrm{leaky}}_{s,m,\mathrm{post}}
-
L^{\mathrm{leaky}}_{s,m,\mathrm{pre}}
\]

\[
\tau_s
=
\Delta^{\mathrm{leaky}}_{s,\mathrm{shared}}
-
\Delta^{\mathrm{leaky}}_{s,\mathrm{local}}
\]

Frozen controls 只作 replay/measurement stability gate，不进入 \(\tau_s\)：

\[
D_s
=
\max_m
\left|
L^{\mathrm{frozen}}_{s,m,\mathrm{post}}
-
L^{\mathrm{frozen}}_{s,m,\mathrm{pre}}
\right|
\le \epsilon_{\mathrm{replay}}.
\]

8-stack screen 的独立单位固定为：

`2 task pairs × 2 mappings × 2 mirrors`

## 3. 可复用资产的真实边界

### A. Arithmetic：一个真实 READY 工程 block

源：

`outputs/arithmetic-gate1-stage-ISB/engineering/block-b000-root93001-real-v2`

可复用：

- train/H-ID 各 32 个真实 prompts；
- 每个 prompt 有 17 个完整 restricted actions，共 544 actions/split；
- action 含 canonical text、token ids、base log-probability 和 SHA256；
- exact-control 含 reference probability、restricted logit、frozen offset、reward-coefficient contract；
- arithmetic verifier 实现存在：
  `src/sqlite_agent_research/arithmetic_bugs.py`；
- shared rule 是 `flip_sign_v2`；local target 是 prompt-specific mapping。

不能声称：

- H-ID 是 task-disjoint target；
- 这是新的五臂设计；
- 已有 mirror 或 8 个独立 mapping stacks；
- verifier 已被前瞻 hash receipt 绑定；
- 这个 block 授权了训练或 transfer claim。

H-ID 与 train 使用同一个 arithmetic generator、grammar 和 target vocabulary，只是不同 numbers/lineage。

### B. Arithmetic natural Gate-1 pool：存在但 G1 失败

源：

`outputs/arithmetic-gate1-stratified/audit-v2`

已观测：

- local FPR = `0.0073439412`
- shared FPR = `0.0072215422`
- local exposure = `0.00732421875`
- shared exposure = `0.0072021484375`

但：

- effective-advantage SMD = `0.3113`
- target-property max SMD = `0.5205`
- 原阈值为 `0.20`
- gate `passed=false`

因此不可当作 passing G1，也不得复跑旧 smoke 冒充新验证。

### C. SQLite repair：真实 rows，但不是可直接复用的 G2

源：

`data/derived/six-gym-read-only-pilot-v1`

可复用：

- train/calibration/test 分别 96/16/32 rows；
- database-disjoint；
- prompt、gold SQL、数据库与来源 lineage 存在。

不能声称：

- database-disjoint 等同 task-disjoint；
- rows 有 shared/local bug probes、完整 candidates、reward vectors、base logits 或 mirrors；
- test 仍 untouched：旧 frozen 与 LoRA evaluation 已经使用全部 32 test rows；
- 存在 seal-before-run receipt。

当前 SQL evaluator 只有 clean execution oracle，没有 shared/local verifier bug。

### D. 当前明确缺失

- 两个真实 task pairs；
- 两个 mappings 和两个 mirrors；
- 五臂真实 candidate/reward panels；
- shared/local frozen probe controls；
- task-disjoint sealed target dual probes；
- calibration/audit seal 与零 lineage 交集回执；
- shared/local 在两个任务间含义一致、且 local 不跨任务共享的 bug-rule incidence matrix；
- G1 的真实 FPR、accepted-wrong mass、reachability、relative advantage、positive-count、exposure 与 surface/token balance；
- 前瞻 verifier/code/model/randomization hash chain。

## 4. 请做的否决式判断

### Q1. “任务不相交”的最低可接受定义

请给一个可执行的定义，并判定以下是否足够：

- 不同 arithmetic operator/template；
- arithmetic 与 SQL repair；
- 同一 SQL repair task 的 database-disjoint split；
- 两个新构造、输入语义不同但输出接口相同的受控任务。

只选一个最便宜、仍能支持 `cross-task sharedness` 的定义。

### Q2. 两个 exact task pairs

若裁决不是 `NO_LOW_COST_IDENTIFIABLE_DESIGN`，请给出：

- `task_pair_1`: source task、target task；
- `task_pair_2`: source task、target task；
- 为什么 source/target task-disjoint；
- A→B / B→A 是否算两个 task pairs，还是同一 pair 的 mirror/direction；
- 哪些现有 rows 可复用，哪些必须新生成；
- target calibration 和 untouched audit 各需多少 rows（最低数量，不做 power 外推）。

### Q3. 一个 exact shared rule 与 local construction

请定义一个在所有任务中稳定、重复、可学习的 shared verifier error，并给出：

- 每种 task 的 gold candidate；
- shared wrong candidate 的 deterministic transform；
- local wrong candidate 的 deterministic construction；
- `task × bug-rule` incidence matrix；
- local rule 如何在 task 内稳定但不跨 task 共享；
- shared/local 如何保持 wrong-label multiset、surface/token、positive-count、FPR、reward mass、reachability、relative advantage 和 exposure 可匹配；
- 哪些规则必须由人工标注验证。

若 arithmetic 与 SQL 无法在不引入表面混杂的条件下共享同一错误结构，请明确否决，不要勉强拼接。

### Q4. 最低资产构造顺序

在禁止模型 forward 的当前权限下，请把下一步压缩为不超过 5 个 CPU-only deliverables。每项写：

- exact filename；
- 输入来源；
- 必须字段；
- 可计算内容；
- 必须保持 `NOT_RUN` 的字段；
- fail-closed 条件。

### Q5. 下一轮授权门

明确写出：

- 哪些 CPU verifier/FPR 检查可立即运行；
- 哪些字段必须等 model-forward 授权；
- 满足什么精确条件后，才值得把一个 one-stack 方法学 pilot 再提交给 PRO 审核；
- 本轮仍须逐项返回：
  `GPU / weight load / forward / gradient / optimizer = YES/NO`。

## 5. 禁止事项

- 不得用旧 H-ID 代替 task-disjoint target；
- 不得用旧 SQLite test 代替 untouched audit；
- 不得把 synthetic task names 当真实 rows；
- 不得把 frozen controls 解释为 selection-exposure adjustment；
- 不得仅凭相同 FPR 宣称 exchangeability；
- 不得把 A→B 与 B→A、mirror、mapping、seed 混成独立单位；
- 不得授权任何模型运行，除非你先给出可复核的真实资产闭合条件；即便给出条件，本轮权限仍保持 NO。
