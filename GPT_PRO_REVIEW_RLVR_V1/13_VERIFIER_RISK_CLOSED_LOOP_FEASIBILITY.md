# Verifier 风险闭环可行性审计

审计日期：2026-07-28  
硬件：NVIDIA GeForce RTX 5060 Laptop GPU，8151 MiB  
总体判定：**有条件可行；允许继续做清洁数据、候选组和随机微更新，正式 Stage 2 暂停。**

## 一句话结论

这个研究问题是成立的，但要把原命题拆成两个强度不同的声明：

1. **预测声明，可做**：在相同 FPR 下，冻结模型可达性、组内相对优势和
   优化器感知的参数影响，能否在未见数据库／错误族上更好地预测真实损害？
2. **因果机制声明，尚不能做**：这些因素是否是 verifier 错奖导致参数迁移和
   能力损害的原因？这必须由同一 checkpoint 上的
   `wrong / matched-correct / no-op` 随机微更新来识别。

当前的纯 SFT replay 流程没有沿 verifier 错奖的错误输出更新模型，因此不能把
错误输出的梯度相似性直接接到“真实能力损害”上。

更可守、也更容易发表的核心问题是：

> 在非平稳、可验证的工具 Agent 后训练中，一个训练前、checkpoint-specific 的
> 风险分解——verifier 可利用性、冻结策略危险可达性、组内可学习性，以及
> 状态相关的 retention 冲突——能否预测 terminal held-out 能力损害，并在固定
> token、step 和 rollout 预算下优于 FPR-only 及强单信号基线？

## 闭环逐箭头判定

| 箭头 | 当前状态 | 可以声称什么 | 进入下一步前缺什么 |
|---|---|---|---|
| 发现 verifier 漏洞 | `PARTIAL GO` | 已发现“base 通过、区分世界失败”的动态输出 | 完成 100% 独立 adjudication；把 Gate 0 接入正式数据入口 |
| 扣除冻结选择效应 | `DESIGN GO` | 2×2 分解已形式化和单元测试 | clean Stage 1、冻结候选池、独立 discovery/measurement seed |
| 预测参数级迁移 | `INSTRUMENT GO / HYPOTHESIS NOT PASSED` | 本机能完成可逆 LoRA 微更新并测到变化 | 当前一阶预测不稳定；仍缺 clean 候选组、相对优势、实际优化器和 executable outcome |
| 预测真实能力损害 | `STOP` | 只能定义 outcome，尚无标签 | 同一起点 pre/post checkpoint、no-repair 分支、sealed final worlds |
| 按风险分配修复预算 | `STOP` | 只能定义决策目标 | repair treatment effect、成本、多个预算点、outer holdout |

## 1. 发现 verifier 漏洞

对实验单位

```text
i = (db_id, error_family, generator_seed, task)
```

定义：

\[
L_i =
\mathbf 1[
V_{\text{selection}}(y_i^-)=1
\land
V_{\text{adjudication}}(y_i^-)=0
].
\]

必须保存 verifier 的**错误结构**，不能只存总 FPR：

- base world 是否通过；
- candidate SQL 是否能执行；
- gold／world protocol 是否有效；
- selection、adjudication 和 terminal world 的结果；
- `db_id × error_family` 的错误集中度；
- accepted-correct、rejected-wrong 和 sham mutation 负对照。

这个方向已有很近的工作，不能声称“首次发现频率不够”。Egashira 等人的
[Delay, Plateau, or Collapse](https://arxiv.org/abs/2605.02909)
已经显示，系统性 false positive 的影响可从 plateau 到 collapse，且不由总体
错误率决定。Helff 等人的
[LLMs Gaming Verifiers](https://arxiv.org/abs/2604.15149)
也已经用 isomorphic perturbation 检测 verifier 的 extensional shortcut。

本项目的增量不能是“FPR 不够”，而应是：**在 matched FPR 下，错误的
checkpoint-specific 可学习性和跨任务影响能否解释后续损害差异。**

## 2. 扣除冻结选择效应

这里其实有三种不同的效应，不能混成一个百分比：

### 2.1 发现阶段的 winner's curse

同一批 rollout 既用来发现错误、又用来估计可达率，会高估被选中错误的概率。
因此：

- discovery rollout 和 measurement rollout 使用独立 seed；
- 在看 verifier outcome 前冻结 checkpoint、候选池和 selector hash；
- 以 measurement rollout 或 teacher-forced log-prob 估计可达性；
- 确定性 top-K 的未入选项 propensity 为 0，不能靠 IPW 事后恢复；selector
  应保留 10%–20% 随机探索。

### 2.2 候选池富集与 risk priority

严格扣除需要完整 2×2：

| | Uniform priority | Risk priority |
|---|---:|---:|
| Clean candidate pool | \(Y_{CU}\) | \(Y_{CR}\) |
| Enriched candidate pool | \(Y_{EU}\) | \(Y_{ER}\) |

\[
\Delta_{\text{priority}}=Y_{CR}-Y_{CU}
\]

\[
\Delta_{\text{pool}}=Y_{EU}-Y_{CU}
\]

\[
\Delta_{\text{interaction}}
=(Y_{ER}-Y_{EU})-(Y_{CR}-Y_{CU}).
\]

现有 `clean / selection / leaky` 三臂不能识别最后的交互，不能简单做
“naive effect − pool effect”然后称为净因果效应。

### 2.3 冻结 selector 与参数学习

还需要 checkpoint × selector 的另一个 2×2：

| | Strict selector | Buggy selector |
|---|---:|---:|
| Frozen \(\theta_0\) | \(Y_{0S}\) | \(Y_{0B}\) |
| Trained \(\theta_1\) | \(Y_{1S}\) | \(Y_{1B}\) |

- 冻结选择效应：\(Y_{0B}-Y_{0S}\)
- strict decoder 下的参数损害：\(Y_{1S}-Y_{0S}\)
- 训练 × selector 交互：
  \((Y_{1B}-Y_{1S})-(Y_{0B}-Y_{0S})\)

代码中的
[`risk_loop.py`](./src/sqlite_agent_research/risk_loop.py)
已把这两个 2×2 写成可执行恒等式，并显式保留交互项。

## 3. 从可达性预测参数迁移

### 3.1 四个量不要机械相乘

对 accepted-wrong 轨迹 \(e\)：

- 可达性 \(q_e\)：冻结 \(\theta_0\) 下的 empirical sample rate，及
  wrong-vs-correct 长度归一化 log-prob margin；
- 组内优势 \(A_e^+\)：同一 prompt 候选组中，错误输出获得的正相对优势；
- 源更新梯度 \(g_e\)；
- protected correct task 的 loss 梯度 \(g_t\)；
- 优化器预条件器 \(P\)。

一阶预测应写为：

\[
\widehat{\Delta L_t}
=-\eta n_e q_e\,E[A_e^+]\,g_t^\top P g_e .
\]

`parameter coupling` 与 `gradient conflict` 不是两个独立乘数：

- \(|g_t^\top P g_e|\) 或归一化投影表示耦合强度；
- 符号表示协同还是冲突；
- \(g_t^\top P g_e<0\) 时，一次正优势更新预测 protected loss 上升。

Adam-aware influence 和低维梯度库已有明确先例，例如
[LESS](https://proceedings.mlr.press/v235/xia24c.html)；样本级参数风险和
任务—安全联合梯度选择也分别已有
[SQSD](https://arxiv.org/abs/2605.04572) 与
[DualSelect](https://arxiv.org/abs/2606.09866)。
所以本项目必须证明这些信号在 verifier-driven、非平稳工具 Agent 场景中的
**独立增量预测力**，不能只把多个现有 feature 拼在一起。

### 3.2 因果验证

从同一 \(\theta_0\) 和同一预注册 probe optimizer 出发，对 source item 随机分配：

1. accepted-wrong 正优势微更新；
2. matched-correct 微更新；
3. no-op；

三组匹配 token、学习率、步数、梯度裁剪和 dropout。更新前冻结 target pairs，
更新后测：

- target gold loss／margin 变化；
- 多世界 executable correctness；
- dangerous call、valid abstention 和 over-refusal；
- 一阶预测符号与实际变化是否一致。

若一阶量不能预测实际一步迁移，参数机制被否证。当前 8 GB 环境只能支持
**LoRA trainable-subspace** 的机制声明，不能外推为完整 1.5B 参数空间。

## 4. 预测真实能力损害

真正的 no-repair damage label 是：

\[
D_i(0)=
\mathbf 1[
\text{pre terminal-world correct}
\land
\text{post terminal-world wrong}
].
\]

连续版本是 sealed multiworld pass fraction 的下降。恢复案例必须单独报告，不能
让恢复和遗忘相互抵消。

按预注册顺序比较嵌套模型：

- `M0`：FPR／错误结构频率；
- `M1`：`M0 + reachability`；
- `M2`：`M1 + group-relative advantage`；
- `M3`：`M2 + signed optimizer-aware influence`；
- `M4`：`M3 + length/schema complexity/pre-competence` 等 nuisance controls。

采用 leave-DB-and-error-family-out 外层交叉拟合，比较：

- ΔBrier、Δlog-loss、AUPRC；
- calibration slope；
- lift@budget；
- terminal executable damage，而不只是 teacher-forced loss。

只有 `M3/M4` 在 untouched DB／error family 上稳定胜过 frequency-only，才支持
“危险不等于频率”。若闭环会用上一轮 damage 更新下一轮风险模型，必须拆成：

- `feedback_holdout_t`：可在下一轮使用；
- `terminal_outer_holdout`：所有模型、阈值和预算策略冻结后只打开一次。

## 5. 按风险分配修复预算

不能只按 \(P(\text{damage})\) 排序。高风险但不可修复的项可能浪费全部预算。
每个候选动作的基本价值应是：

\[
\text{value}_i(b)
=
\frac{
w_i P(D_i(0)=1)
E[D_i(0)-D_i(b)\mid X_i]
-\lambda_u U_i
}{
\operatorname{cost}_i(b)
}.
\]

固定总 token、optimizer step、rollout 和 SQLite execution 预算；同时把
dangerous-call 与 over-refusal 设为非劣约束。主要结果应是：

- damage avoided per 1k supervised tokens；
- retention–budget curve 和 AUC；
- 相对 uniform、FPR、NLL/surprise、uncertainty、MIR 和 oracle 的 policy regret；
- 不同 `db_id × error_family` 的 worst-group／CVaR 损害。

failure replay、置信度门控和边界失败采样已有
[NexGRPO](https://aclanthology.org/2026.acl-long.1682/)；
一般能力 replay 与在线动态调权已有
[RECAP](https://aclanthology.org/2026.findings-acl.1717/)；
失败轨迹、policy roll-forward 和 policy reachability 已有
[Verified Critical Step Optimization](https://arxiv.org/abs/2602.03412)。
因此“按置信度回放失败”本身不能作为本论文的新意。

## 当前本机证据

### 已测通

- 动态 witness 报告在 72 个 source examples 中完成 30 个，发现 4 个
  `dynamic_spurious_equivalence`；报告明确为
  `partial_diagnostic_only_not_selection_eligible`，输出 0 个 selection ID。
- LoRA-NLL 梯度 smoke 对 4 个 accepted-wrong 和 4 个 protected references
  完成 12 次 backward：
  - 2,179,072 个可训练 LoRA 参数；
  - 单个 FP32 梯度 8,716,288 bytes，约 8.31 MiB；
  - 运行约 15.98 秒；
  - 峰值显存 8.003 GiB；
  - 4 个 wrong-vs-own-gold cosine 均为负；
  - 16 个 cross-task pair 中 43.75% cosine 为负。
- 又从完全相同的 checkpoint 做了 paired、可逆的一步 SGD 微更新：
  - `wrong / matched-correct / no-op` 均使用相同评测 targets；
  - `η=0.01` 和 `η=0.005` 两个步长；
  - wrong/correct source loss 均下降，证明更新实际生效；
  - no-op 的 4 个 target loss change 全为 0；
  - 每个 treatment 后 LoRA 参数恢复误差为 0；
  - 峰值显存 8.025 GiB。
- 全部代码测试：`55 passed`。

### 这些数字不证明什么

- 4 个 witness 来自 selection-contaminated、partial pool；
- 没有候选组，所以没测 empirical reachability 或 group-relative advantage；
- cross-task 43.75% 没有 matched-correct／random baseline，不能叫异常冲突率；
- 探针比较的是 wrong-response NLL 与旧 SQL gold NLL，不是当前实际
  safety-batch gradient；
- 虽然现在已有实际 SGD 微更新，但不是 GRPO／AdamW／真实 Stage 2，也没有
  post-update executable damage label；
- 8.003 GiB 已接近 8151 MiB 上限，不能批量缓存完整梯度。

更重要的是，当前一阶预测**没有通过稳定性检查**：

| treatment | η | predicted-vs-observed Pearson | 方向一致率 |
|---|---:|---:|---:|
| accepted-wrong | 0.010 | 0.184 | 68.75% |
| accepted-wrong | 0.005 | 0.012 | 50.00% |
| matched-correct | 0.010 | 0.058 | 75.00% |
| matched-correct | 0.005 | 0.443 | 68.75% |

accepted-wrong 的 observed sign 在两个步长之间也只有 68.75% 一致。样本只有
16 个 pair，不能做显著性结论；但它足以否定“当前 gradient cosine 已经能预测
参数损害”的说法。可能原因包括 BF16 数值粒度、LoRA 参数化的局部非线性、
过小样本和未匹配 update norm，这些都只是后续待检验解释。

所以该结果只证明：**本机可做流式 LoRA 梯度和可逆微更新仪器化；当前选择污染
样本上的一阶影响代理没有被实测变化稳定验证，核心命题仍未通过。**

原始证据：

- [`gradient-probe.json`](./outputs/replay-pilot-v3/stage1-current-predictions/gradient-probe.json)
- [`micro-update-probe.json`](./outputs/replay-pilot-v3/stage1-current-predictions/micro-update-probe.json)
- [`micro-update-probe-eta-0p005.json`](./outputs/replay-pilot-v3/stage1-current-predictions/micro-update-probe-eta-0p005.json)
- [`dynamic-witness-partial-v2.json`](./outputs/replay-pilot-v3/stage1-current-predictions/dynamic-witness-partial-v2.json)
- [`gradient_probe.py`](./src/sqlite_agent_research/gradient_probe.py)
- [`micro_update_probe.py`](./src/sqlite_agent_research/micro_update_probe.py)
- [`risk_loop.py`](./src/sqlite_agent_research/risk_loop.py)

## 最小实验阶梯

### Gate 1：清洁测量，不训练完整 Stage 2

1. 重新构建 clean Stage 1；不能对当前 v3 做事后减法。
2. 物理生成并哈希 world manifest；正式入口强制校验 role 和数据库内容。
3. 冻结 100–200 个 prompts；teacher-forced log-prob 为全量主可达性，
   分层抽取约 200 个做 `K=16` sampling 校验。
4. 生成同 FPR、不同 error concentration 的 clustered／dispersed 条件。
5. 完成 `wrong / correct / no-op` 随机微更新和实际 target damage。
6. 只在该 Gate 的符号、预测和 leakage checks 通过后进入 risk model。

### Gate 2：预测验证

- 至少 20–30 个 DB、6 个机制类别；
- 机制预测建议至少 160 个独立 source clusters；
- 若 damage rate 为 20%，约需 500 个单位才能积累约 100 个 damage events；
  10% 时约需 1000 个，5% 时约需 2000 个；
- screening 使用 3 个完全配对 training seeds；
- 确认性 policy effect 至少 5 seeds。

### Gate 3：预算策略

先筛：

1. uniform；
2. frequency-only；
3. reachability-only；
4. reachability + advantage；
5. full signed-influence risk；
6. leaky oracle，仅作上界。

胜出的 1–2 个方法再与 NexGRPO-like、RECAP-like、NLL/surprise、MIR 和
metamorphic-failure 进行 5-seed 确认。

## 单 GPU 计算边界

按当前 7.1–11.1 秒／生成的实测范围：

- 1000 prompts × 1 candidate：约 2.0–3.1 GPU 小时／checkpoint；
- `K=4`：约 7.9–12.4 小时；
- `K=8`：约 15.8–24.8 小时。

因此低概率可达性不能全靠 sampling；1% 事件要约 299 次独立采样才有 95%
概率至少观察一次。可行方案是：

- 全量 teacher-forced log-prob margin；
- 小型分层 sample 校验；
- backward 串行执行；
- 梯度流式投影到 256–512 维并保存 layer norms／dot products；
- 只保存少量完整梯度用于审计；
- 缓存 frozen rollouts，所有策略复用。

当前硬件上的正式小矩阵约需 50–90 GPU 小时，确认性 5-seed 阶段再约
40–60 小时。应逐 Gate 运行，不能一次性全开。

## 明确否证条件

出现任一项，应放弃或缩窄核心命题：

1. 完整风险模型在 outer DB／family 上不优于 frequency-only；
2. reachability 排序在独立 measurement seeds 上不复现；
3. matched-FPR 的 clustered 与 dispersed 条件真实损害无差异；
4. 一阶 influence 对随机微更新后的迁移方向接近随机；
5. 参数指标只预测 loss，不预测 multiworld executable damage；
6. 风险预算在同预算下不优于 uniform、NLL/surprise 或 MIR；
7. 改善完全来自全局 over-abstain 或 dangerous-call 上升；
8. 优势只存在于 leak probe，terminal transfer 消失；
9. 控制长度、DB、family 和 pre-competence 后效应消失；
10. 无法产生足够的独立机制实例或约 100 个真实 damage events。

## 最终决定

**研究闭环值得继续，但现在只能进入“clean Gate 1 + 随机微更新验证”。**

当前不能写：

> 四个因素已经导致了参数迁移和真实能力损害。

当前可以写：

> 我们已经证明该机制链的关键测量在单张 8 GB GPU 上可执行，并建立了能够
> 区分冻结选择、参数更新和二者交互的对照；下一步将检验这些训练前信号是否
> 对 terminal held-out damage 具有增量预测力。
