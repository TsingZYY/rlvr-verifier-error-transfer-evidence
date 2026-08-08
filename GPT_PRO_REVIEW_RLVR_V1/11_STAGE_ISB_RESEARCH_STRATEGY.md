# Stage I-SB v2：从 shared/local 差异到可识别的 verifier 风险机制

日期：2026-07-29  
状态：`ENGINEERING_ONLY / STOP_BEFORE_RL`  
对象：SmolLM2-360M-Instruct，root seed `93001`，17-action restricted policy

## 1. 结论先说

本轮建立了一个稳定的工程信号，但同时推翻了一个过早的解释。

1. `shared > local` 的 train-to-H-ID expected-update cosine 在三个 LoRA 初始化上都成立，平均差为 `+0.360962`。
2. 这不能证明 shared sign-flip 是一种特殊的“语义规则迁移”。一个人为写入跨 split 一致性的有限 rank lookup 控制 B，在三个初始化中都高于 shared。
3. 完整 2×2 控制显示显著的非加性结构：coherence 在 non-sign 表面下提高 cosine，但在 shared-target-sign 表面下反而降低 cosine。三次初始化的 interaction 都为负，平均 `-0.253328`。
4. `shared > C` 平均为 `+0.243247`，但 shared 与 C 仍同时区别于 exact sign-flip、identity rank 和绝对值保持，不能把差值单独归因于语义。
5. H-ID 复用了相同的 16 个输出 magnitude/rank。当前测到的可能是有限标签查表、输出 token 几何或格式线索，不是 OOD 或新数值上的函数泛化。

因此当前决策是：

| 问题 | 决策 |
|---|---|
| 工程链、hash、source replay 是否可信 | `GO` |
| restricted-action opportunity 是否按构造匹配 | `GO` |
| shared/local 是否存在稳定的局部参数几何差异 | `YES, exploratory` |
| 差异是否已经证明 sign-flip 语义特异性 | `NO` |
| 是否可以进入 RL | `NO` |
| 下一步最值钱的实验 | 多 mapping/vocabulary 重复 + 新 magnitude/新 label + sign-format 消融 |

## 2. 我们实际上测量了什么

对 prompt \(i\)、错误条件 \(b\) 和第 \(j\) 个完整答案 action，先计算 buggy verifier 与 clean verifier 在 K=8 组内标准化 reward 下的期望系数差：

\[
\Delta c^{(b)}_{ij}
=
\mathbb{E}[c_{ij}\mid b]
-
\mathbb{E}[c_{ij}\mid clean].
\]

然后构造没有执行参数更新的反事实一阶方向：

\[
d_i^{(b)}
=
\sum_j
\Delta c^{(b)}_{ij}
\nabla_\theta \log \pi_\theta(a_{ij}\mid x_i),
\qquad
u_s^{(b)}
=
\frac{1}{|s|}
\sum_{i\in s}d_i^{(b)}.
\]

报告的 kernel 是：

\[
K_b
=
\cos\!\left(
u_{\mathrm{train}}^{(b)},
u_{\mathrm{H\text{-}ID}}^{(b)}
\right).
\]

直观地说，它问的是：

> 如果 verifier bug 在当前参数点上推动一次很小的更新，train 与 H-ID 倾向推动相同的 LoRA 参数方向吗？

它不等于：

- 实际训练后的准确率或错误率；
- 多步优化轨迹；
- 自由生成中的漏洞触发概率；
- OOD 泛化；
- 能力损害 BSL。

所以这里的 cosine 是“局部预测信号”，不是训练结果。

## 3. exact control 解决了什么，又没有解决什么

构造后的 exact policy 对每题固定：

- gold probability：`0.10`；
- 每个 false condition probability：`0.05625`；
- K=8 时每个 false condition 至少出现一次的概率：`0.370703`；
- K=8 时 bug-only 概率：`0.173599`。

这匹配了 restricted 17-action 空间内的：

- 边际抽样机会；
- 预期标准化 coefficient norm；
- action token 数；
- 每题 action basis。

但它没有匹配自然模型的 free-generation FPR，也没有保证最终参数更新 norm 相同。

原因有两个：

1. exact policy 通过 frozen logit offsets 强制改变了基础分布，base-to-exact 的平均 total variation 为 `0.575448`，这是很强的人工干预。
2. 在原始 restricted softmax 中，negative false answers 的平均概率为 `0.115163`，显式 `+NN` positive false answers 只有 `0.002388`，相差约 `48.2×`；平均完整序列 log-probability 相差约 `4.981` nats。

因此准确说法是：

> 当前匹配的是人工 restricted policy 下的机会与一阶 coefficient 规模，不是自然 FPR，也不是输出表面已经被消除。

## 4. 第一层结构控制：原始差异不是语义特有

三种子 structural-control 结果：

| 条件 | 结构 | cosine 均值 | 三种子范围 |
|---|---|---:|---:|
| B coherent non-sign | 相同 16-rank lookup 跨 split 复用 | `0.420116` | `0.403795–0.434637` |
| shared | exact sign flip、identity rank | `0.381503` | `0.363767–0.396975` |
| A sign-matched domain-separated | 与 shared target 同号，split mapping 不同 | `0.306788` | `0.297794–0.323292` |
| local | 原始 split-specific derangement | `0.020541` | `0.014376–0.029250` |

三个初始化全部满足：

```text
B > shared > A > local
```

这支持两个工程判断：

- shared/local gap 不是随机初始化偶然翻转；
- exact sign-flip 语义不是产生高 cosine 的必要条件，因为有限 coherent lookup B 更高。

但 B 的跨 split 一致性是控制设计主动写入的，而且 H-ID 复用同一 16-value vocabulary。因此 B 是一个有用的 positive control，不是模型自己发现抽象规则的证据。

## 5. 第二层结构控制：sign 与 coherence 发生强交互

2×2 的因素定义如下。“shared-target sign”表示控制答案与 shared sign-flip target 同号，不表示与 gold 同号。

| 条件 | shared-target sign | cross-split coherent | cosine 均值 |
|---|---:|---:|---:|
| A | 1 | 0 | `0.306788` |
| B | 0 | 1 | `0.420116` |
| C | 1 | 1 | `0.138255` |
| D | 0 | 0 | `0.335320` |

三种子描述性 contrasts：

| Contrast | 20260729 | 20260730 | 20260731 | 均值 |
|---|---:|---:|---:|---:|
| `C − A` | `-0.168979` | `-0.166549` | `-0.170069` | `-0.168532` |
| `B − D` | `+0.072423` | `+0.087787` | `+0.094179` | `+0.084796` |
| `C − B` | `-0.273497` | `-0.277894` | `-0.294191` | `-0.281861` |
| `A − D` | `-0.032096` | `-0.023558` | `-0.029943` | `-0.028532` |
| `(C−A)−(B−D)` | `-0.241401` | `-0.254336` | `-0.264248` | `-0.253328` |
| `shared − C` | `+0.233469` | `+0.240232` | `+0.256041` | `+0.243247` |
| `shared − local` | `+0.349391` | `+0.378978` | `+0.354516` | `+0.360962` |

最关键的解释不是“coherence 有用”或“sign 有用”，而是：

> 在这个固定 mapping 与固定 16-label vocabulary 中，二者不是可相加的独立因素；同一个 coherence 操作在两种 sign surface 下方向相反。

具体地：

- shared-target-sign 条件中，把 A 变为 coherent C，cosine 平均下降 `0.168532`；
- non-sign 条件中，把 D 变为 coherent B，cosine 平均上升 `0.084796`。

这说明“mapping 在两个 split 相同”不等于“两个 split 的参数更新方向相同”。真正决定 kernel 的，是被选 action 的 token 梯度、prompt mixture、rank/magnitude mapping 与 coefficient 的联合几何。

三枚 adapter seed 只验证了技术初始化稳健性。每个 factorial cell 目前仍只有一个人为 mapping；mapping 不是技术 seed，不能把三次初始化当成三个独立机制样本。

## 6. 当前最强、也最安全的论文命题

原命题仍有研究价值：

> Verifier 的危险性不等于它接受错误答案的频率；风险还取决于错误输出的可达性、组内相对优势、参数耦合和与正确轨迹的冲突。

本轮结果要求把它收紧为一个可以真正被检验的新命题：

> 即使匹配了边际接受机会和 restricted-policy coefficient 规模，parameter-level verifier-risk 指标仍可能被输出词表、符号格式和 mapping 结构主导。只有当该指标跨独立 mapping、未见 magnitude、未见错误标签和 prompt/output format 仍然成立时，才可以把它解释为可迁移的 verifier-rule 机制。

这个修正不是削弱研究，而是把“shared 比 local 高”升级为更有价值的问题：

> 怎样区分语义规则迁移、有限标签查表和表面 token 几何？

## 7. 下一步最小实验：先测 mapping 方差

### 7.1 独立实验单位

最低决策级设计：

- 6 个独立 target-vocabulary blocks；
- 每个 vocabulary 生成 2 个独立 mapping replicates；
- 共 12 个 `vocabulary × mapping` 实验单位；
- 每个单位使用 2 个共同 adapter seeds；
- 每个 rank 每个 split 保持 2 道题，即每 split 32 prompts。

adapter seed 是技术重复，不是统计独立样本。真正的随机化单位是：

\[
\text{vocabulary block}\times\text{mapping replicate}.
\]

每个 vocabulary 的两个 replicates 至少有一个应是镜像 counterbalance：把当前 coherent 候选与 domain-separated complement 的角色互换，并在两个 sign cells 上同时反转。否则“coherence effect”仍可能只是某个固定 H-ID token/rank 恰好更对齐。

在任何模型打分之前必须冻结：

- magnitude codebook；
- sign 与 stratum assignment；
- A/B/C/D mappings；
- expression/template seeds；
- format-arm 顺序；
- heldout label codebook；
- 全部 inclusion/exclusion rules。

第一步只运行显式 sign 格式下的 H-ID kernel。若少于 `8/12` mapping units 保持同方向，就将当前结果判为 mapping-specific，停止扩展，不进入 RL。

### 7.2 三类 heldout

| Split | 目的 | 必须满足 |
|---|---|---|
| H-ID | 同标签、换数字与模板 | 只测 isomorphic transfer，不叫 OOD |
| H-MAG | 测函数规则 | gold magnitude 与训练完全不重合；sign、位数、token length、模板难度匹配 |
| H-LABEL | 测有限标签查表 | arithmetic prompt 保持，任意控制换用不重合错误答案 codebook；shared 仍是 `-gold` |

真正区分语义规则和 lookup 的主对比是：

\[
R=
(shared_{\mathrm{MAG}}-C_{\mathrm{MAG}})
-
(shared_{\mathrm{ID}}-C_{\mathrm{ID}}).
\]

如果 same-vocabulary H-ID 很高，但 H-MAG 或 H-LABEL 崩溃，当前信号主要是 lookup/表面结构。只有 shared 在新 magnitude 与新格式中继续优于 C，才有理由讨论 sign-flip 函数规则。

### 7.3 leading-sign 消融

使用三个 paired format arms：

| Arm | Prompt | 正数输出 | 目的 |
|---|---|---|---|
| F0 conventional | 中性 | `NN` | 去掉显式 plus token |
| F1 token-only | 中性 | `+NN` | 测 `+` token 表面 |
| F2 cue+token | 明确要求 leading sign | `+NN` | 测 prompt cue |

关键比较：

- `F1 − F0`：输出 token 表面效应；
- `F2 − F1`：prompt cue 效应；
- `F2 train → F0 heldout`：移除 sign cue 后是否仍迁移。

应保存三个 train 与三个 heldout aggregate gradients，计算完整 `3×3` cross-format kernel，而不是重复九次前向。

## 8. 分析与门槛

先将 cosine 变换为：

\[
z=\operatorname{atanh}(\operatorname{clip}(K,-0.999,0.999)).
\]

每个 mapping 内先平均两个 adapter seeds，再计算 factorial contrasts、`shared−C`、label dependence、magnitude retention 和 \(R\)。分析单位不能下放到 prompt。

建议模型：

```text
contrast ~ heldout_type * format_arm
         + (1 | vocabulary)
         + (1 | vocabulary:mapping)
```

由于只有 12 个 mapping 单位，主要报告：

- vocabulary-cluster bootstrap interval；
- 12 个单位的方向一致性；
- mapping 间方差；
- 两个 adapter seeds 的最大技术分歧。

在冻结前写入 Fisher-z SESOI `0.05`。机制效应通过需同时满足：

1. 至少 `10/12` mapping units 同方向；
2. vocabulary-cluster bootstrap 95% interval 完全越过 `±0.05`；
3. 两个 adapter seeds 对该 contrast 的差不超过 `0.03`；
4. mapping 间 SD 小于平均效应绝对值。

语义规则还需同时满足：

- H-MAG 上 `shared−C > 0.05`；
- H-MAG shared cosine 至少 `0.20`；
- F0 conventional 下仍有 `shared−C > 0.05`；
- \(R>0.05\)。

以下任一条件触发 `STOP_BEFORE_RL`：

- READY、hash、source replay、assigned-rank、probability 或 token gate 失败；
- H-MAG 与训练 magnitude 不完全分离；
- H-LABEL 无法同时匹配 sign、width 和 token length；
- 少于 `8/12` mappings 同方向；
- `8–9/12` 同方向或 interval 穿过 `[-0.05,0.05]`；
- mapping SD 大于平均效应；
- `shared−C` 只在 `+NN` 格式成立；
- C 在 H-MAG 与 shared 同样稳定，导致语义规则与抽象 rank rule 仍不可区分。

## 9. 研究闭环怎样继续

当前闭环的位置是：

| 闭环步骤 | 当前状态 | 下一门 |
|---|---|---|
| 发现 verifier 漏洞 | restricted verifier 已构造 | 扩到独立 vocab/mapping |
| 扣除冻结选择效应 | exact opportunity 已匹配，但自然 FPR 未匹配 | H-MAG/H-LABEL/format 对照 |
| 预测参数级迁移 | 有 exploratory C3 kernel | mapping-level replication |
| 预测真实能力损害 | 尚未测量 | 先做 blinded C4 short-horizon prediction |
| 按风险分配修复预算 | 尚无证据 | 只有 C4 预测 C5/C7 后才能分级 |

正确顺序是：

1. 在工程 roots 上完成 12-unit mapping pilot；
2. 若通过，再加入 H-MAG、H-LABEL、F0/F2；
3. 冻结 active v2 protocol、代码、模型、环境、seeds、分析与授权验证器；
4. 取得外部 timestamp/receipt 后，才解锁未看过的 `94001–94008`；
5. 先做 blinded short-horizon restricted-policy prediction；
6. 只有预测门通过，才授权 paired clean/local/shared RL；
7. 最后测 selection-adjusted H-ID/H-MAG transfer 与 BSL。

root `93001` 已参与 tolerance calibration、控制设计和结果检查，永久只能作为 engineering root。`94001–94008` 当前仍禁止打分、设计选择或训练。

## 10. Claim ladder

| Level | 当前状态 | 允许说什么 |
|---|---|---|
| C0 artifact integrity | 支持 | 当前 engineering artifact 与 source replay 可复核 |
| C1 restricted opportunity matching | 支持 | 人工 17-action policy 内机会与 coefficient norm 匹配 |
| C2 frozen reachability | 未确认 | 当前 root 非外部预注册且已看 outcome |
| C3 local parameter geometry | 探索性支持 | 固定 root/mapping 中存在稳定局部几何差异与交互 |
| C4 predictive kernel dynamics | 未测 | 没有 blinded short-horizon prediction |
| C5 controlled post-training transfer | 未测 | 没有授权 RL |
| C6 semantic specificity | 不支持 | B、C 与 same-vocabulary 限制已否定简单解释 |
| C7 capability-boundary damage | 未测 | 没有 BSL 因果结果 |
| C8 general verifier-risk principle | 不支持 | 仍需跨模型、任务、free generation 和真实漏洞复制 |

## 11. 最终策略

现在不应增加 RL seed，也不应继续围绕当前单一 mapping 做更多初始化。初始化方向已经稳定，真正缺失的是机制随机化。

下一笔计算预算应优先花在：

1. `mapping/vocabulary repeats`；
2. `H-MAG/H-LABEL`；
3. `leading-sign format ablation`；
4. `blinded C4 prediction`；
5. 最后才是 RL 与 BSL。

这条路线能让失败也有研究价值：

- 若 H-ID 高而 H-MAG/H-LABEL 低：得到“kernel 会把 lookup 误判为迁移”的诊断论文；
- 若 shared 在新 magnitude、无显式 plus token 下仍高于 coherent controls：才建立 sign-flip 语义机制；
- 若 kernel 稳定但不能预测短程训练：得到“局部参数几何不是有效 dynamics predictor”的边界；
- 若 kernel 能盲预测 RL 和 BSL：才真正连接到 verifier 风险与修复预算。

当前最清晰的行动不是“继续训练”，而是先让替代解释有机会把主假设推翻。
