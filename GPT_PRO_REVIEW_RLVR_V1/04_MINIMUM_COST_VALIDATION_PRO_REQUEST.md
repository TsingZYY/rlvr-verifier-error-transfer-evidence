# GPT Pro 开跑前审核：最低成本验证是否可执行

日期：2026-07-30  
请求类型：否决式 pre-run audit  
用户授权：只授权最低成本验证；不授权直接进入多步 RLVR 或扩大性实验

## 1. 要回答的单一决策

请在以下三项中只选一项：

1. `RUN_ONE_STACK_METHOD_PILOT`
2. `BUILD_REAL_G1_G2_ASSETS_FIRST`
3. `STOP_DIRECTION`

不要因为工程代码可以运行就选择第 1 项。只有当一次运行能够增加可解释信息时才允许运行。

## 2. 当前已确认状态

### 工程状态

- P4-R1A CPU 合同预检已通过外部工程验收。
- 指定测试为 71 passed、1 warning。
- 双运行各产生精确 9 个输出且逐字节一致。
- 该验收没有调用模型、forward、gradient、optimizer、training、RL 或 RLVR。

### 当前 P4 fixture 状态

`tests/fixtures/p4_r1_contract_only_v1/CONTRACT.json` 明示：

- `synthetic_contract_only=true`
- `scientific_evidence=false`
- `experiment_status=NOT_RUN`
- `formal_experiment=false`
- `model_forward=false`
- `gradient=false`
- `training=false`
- `rl=false`
- `rlvr=false`

当前 8-stack/五臂 fixture 只检查：

- 8 个 mapping stacks 与 mirror 配对；
- 五臂名称和顺序；
- 同一 stack 内 source-bundle 哈希共享；
- calibration/audit 自声明分割不相交；
- synthetic byte fields 与 fail-closed schema。

它没有真实提供或检查：

- online FPR；
- accepted-wrong reward mass；
- initial wrong-target reachability；
- relative advantage；
- positive-count/exposure distribution；
- surface-form balance；
- Verifier 实现及哈希；
- 实际 reward vectors；
- bug-rule incidence matrix；
- 随机化回执；
- model/tokenizer hashes；
- 真实 target rows 与完整 probes；
- 实际 primary outcome 或 estimator 输出。

因此当前 fixture 不能直接升级为科学实验输入。

### 可用本机资源

- GPU：NVIDIA GeForce RTX 5060 Laptop GPU，8,151 MiB。
- 当前诊断时空闲显存约 5,702 MiB。
- 本地缓存：SmolLM2-360M-Instruct。
- 旧 restricted-action one-step 代码曾使用约 3.46 GiB 峰值显存。
- 旧代码和旧数据只实现早期 arithmetic restricted-action 设计，不实现新的 P2 five-arm causal estimand。

## 3. 三种可能的“最低成本验证”

### A. 再跑 CPU contract preflight

成本最低，但已经完成且不会增加科学信息。默认不应重复。

### B. 运行一个旧 arithmetic restricted-action one-step smoke

可以产生真实 model forward、gradient 与 LoRA update，但存在以下边界：

- 不是新的真实 8-stack 五臂资产；
- source/target 不是已证明机制不相交的任务族；
- 没有完整匹配 FPR、reward mass、reachability、advantage 等；
- 不能估计新的 sharedness causal estimand；
- 最多只能验证训练与评估 plumbing。

请判断：该 smoke 是否仍能增加足够的 method information，值得运行并交付审核；还是应因重复旧证据而跳过。

### C. 先构造真实 G1/G2，再运行五臂 8-stack screen

这是报告认为的最低成本 scientific go/no-go，但当前真实资产和 runner 尚不存在。

## 4. 必须解决的 selection-control estimand 问题

当前提案定义：

`Y = pre/post Δ[log p(bug) − log p(gold)]`

并定义：

`τ = (Y_shared,leaky − Y_shared,selection) − (Y_local,leaky − Y_local,selection)`

但当前 contract 又把：

- `shared_selection`
- `local_selection`

定义为 `frozen_selection_only`，即不进行参数更新。

若 selection 臂前后使用同一冻结模型和同一 panel，则：

`Y_shared,selection = Y_local,selection = 0`

这样 selection subtraction 不会扣除基座 shared/local 偏好；它只退化为：

`τ = Δ_shared,leaky − Δ_local,leaky`

请明确判断：

1. 这是合理的 baseline-adjusted estimand，selection arms 只是验证零漂移与测量稳定性；
2. 还是当前定义存在逻辑错误，需要重新定义 selection outcome；
3. 若需修改，请给出一个唯一、可计算的 selection-control 定义及最终 `τ` 公式。

禁止提出训练后重新匹配、删 stacks 或基于 outcome 改 mapping。

## 5. 希望 GPT Pro 输出

1. 单一 verdict：`RUN_ONE_STACK_METHOD_PILOT` / `BUILD_REAL_G1_G2_ASSETS_FIRST` / `STOP_DIRECTION`。
2. 对 selection-control estimand 的明确裁决和唯一公式。
3. 如果允许 one-stack pilot：列出它能回答和不能回答的各一项，并给出最小输入/输出清单。
4. 如果必须先构造资产：只列最多 8 个不可缺少的真实字段或文件。
5. 明确下一步是否允许 model forward、gradient、one-step optimizer update；分别回答 `YES/NO`。
6. 不得把 synthetic fixture、CPU preflight、旧 arithmetic smoke 或参数几何称为 sharedness 科学证据。
