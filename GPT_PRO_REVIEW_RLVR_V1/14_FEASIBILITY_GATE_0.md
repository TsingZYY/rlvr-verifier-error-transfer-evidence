# Gate 0 可行性结论：同 FPR、三重对照与 held-out 同构迁移

更新时间：2026-07-28

## 一句话结论

**技术上可行，但当前 v3 在科学上不能进入 Stage 2。**

| 范围 | 判定 | 理由 |
|---|---|---|
| 1.5B LoRA 与 SQLite 多世界执行 | GO | 单次 Stage 1 实测峰值 5.02 GiB，8 GB GPU 可运行 |
| 当前 replay-pilot-v3 | STOP | Stage 1 候选池已被静态 witness 标签富集，不是 clean |
| 修订后的多世界协议 | REVISE | 先补 clean pool、参数化同构 world、动态 replay 闭环与统计规模 |
| Stage 2 | 暂停 | Gate 0 全部通过前不得启动 |

## 当前本机证据

1. [Stage 1 训练报告](./outputs/replay-pilot-v3/stage1-adapter/training_report.json)记录了
   72 个样本、2 epochs、36 optimizer steps、134.56 秒和 5.02 GiB 峰值显存。
2. [v3 manifest](./data/derived/six-gym-counterexample-replay-pilot-v3/manifest.json)
   显示 Stage 1 由 8 个静态 counterexample、8 个匹配 control 和 56 个 context
   组成。只有前 16 个有可执行的 shift world；其余 56 个无法产生动态 witness。
3. 当前 30/72 的[修正后动态诊断](./outputs/replay-pilot-v3/stage1-current-predictions/dynamic-witness-partial-v2.json)
   得到 11 个 both-correct、4 个 dynamic witness、5 个 candidate execution
   failure 和 10 个 both-wrong。4 个 witness 全都来自预先富集的 8 个静态
   counterexample：`TRAIN_2950`、`TRAIN_3362`、`TRAIN_3141`、`TRAIN_4017`。
4. 该诊断覆盖不完整，所以代码强制输出空的 `selection_eligible_ids`。它只证明
   scorer 可以回收富集池中的一部分已知反例，不能证明 clean dynamic selection。
5. evaluator 已把 candidate SQL 失败与 gold/world 协议失败分开。模型生成无效
   SQL 现在保留在错误分母中；只有 gold 或 world 本身失败才标为 protocol error。

因此，现有 v3 只保留为 `static-label-oracle / selection-contaminated`
诊断，不进入正式方法比较。

## 四个核心变量

对当前 checkpoint 在样本 `i` 上生成的一条候选 SQL，定义：

- `B_i = 1`：候选在 base/template world 上与 gold 等价；
- `Y_i = 1`：候选在训练侧 adjudication worlds 中至少失败一次；
- `S_i = 1`：训练侧 selector 将该样本选入 priority replay；
- `H_i = 1`：候选在最终 sealed held-out 同构 worlds 中至少失败一次。

必须区分两个不同概念：

- 单实例错误接受率：`FAR_spurious = P(H=1 | B=1)`；
- selector 假阳性率：`FPR_v = P(S=1 | B=1, Y=0)`。

同时报告：

- `TPR_v = P(S=1 | B=1, Y=1)`；
- witness 同构迁移率：`P(H=1 | B=1, S=1)`；
- Stage 2 前后的 held-out multiworld accuracy、forgetting/BWT、安全放弃率、
  危险调用率、ECE/Brier/AURC。

本实验所说的“相同 FPR”专指 `FPR_v`，不能用
`FAR_spurious` 或“单数据库碰巧通过率”替代。

## 五世界协议与 held-out 同构迁移

每个独立机制实例都必须有以下五个 world：

| world role | 用途 | selector 是否可见 |
|---|---|---|
| `base` | 生成一次模型输出并检查单实例通过 | clean/selection/leaky 可见 |
| `selection` | clean dynamic witness 与 replay priority | clean/selection/leaky 可见 |
| `adjudication` | 训练侧估计 TPR/FPR；只用于受控噪声消融 | 主 clean selector 不可见 |
| `leak_probe` | 故意制造 leaky-oracle 上界 | 仅 leaky 可见 |
| `final_holdout` | 最终同构迁移和论文主指标 | 所有 selector 永远不可见 |

“同构”在主实验中严格指：

- schema、主外键约束、查询意图和错误触发机制相同；
- nuisance rows、主键值、插入顺序、重复项、`NULL`、ties 和非锚定 literals
  由独立 seed 生成；
- 每个 world 保存 generator ID、seed、schema hash 和 database hash；
- `selection`、`adjudication`、`leak_probe`、`final_holdout` 的 seed 与数据库
  hash 均不同。

因为 prompt 和 schema 不变，同一条模型 SQL 只生成一次，再在所有 value-world
上执行；held-out 同构迁移几乎只增加 CPU/SQLite 成本。schema/table/column 改名会
改变 prompt，需要重新生成，因而只作为较小的二级 OOD 实验。

## clean / selection / leaky 三重对照

三种 provenance 条件使用相同基础任务、模型 seed、训练 token、optimizer step
和最终 held-out 集：

### `clean`

- Stage 1 候选池在任何静态或动态 witness outcome 被查看前冻结；
- priority 只能读取 `base + selection`；
- `adjudication`、`leak_probe`、`final_holdout` 均不可见；
- 这是主实验。

### `selection`

- manifest 中名称为 `selection`，语义是 `selection-only`；
- Stage 1 候选池按训练侧静态 witness outcome 富集；
- Stage 2 在该池内做 uniform、等预算 replay，不再用 witness priority；
- 只用于测量“先挑池”本身能制造多少表面收益。

### `leaky`

- 使用与 clean 相同、未富集的 Stage 1 候选池；
- priority 故意读取 `leak_probe` outcome；
- 它是泄漏造成的乐观上界，不是可部署方法；
- `final_holdout` 仍完全 sealed，用来检查泄漏收益能否真正迁移。

当前 v3 同时做了静态富集和静态 priority，比 `selection-only` 污染更强，
所以不能冒充上述三个正式条件中的任何一个。

预注册主要比较：

1. `leaky - clean`：未来式标签能制造多大表面增益；
2. `selection - clean`：候选池富集本身能否解释结果；
3. `(leaky-clean)_leak_probe - (leaky-clean)_final_holdout`：泄漏收益是否在
   untouched transfer 上消失。

## 相同 FPR、不同错误结构

这是独立的机制消融，不与三重 provenance 和全部 replay baseline 做一次性全交叉。

1. 在训练侧 `adjudication` worlds 上冻结 `Y`，最终 `final_holdout` 继续 sealed。
2. 两个条件共享完全相同的真阳性集合。
3. 从同一个 `Y=0, B=1` 分母中各注入恰好 `m` 个假阳性，因此：

   `FPR_v(iid) = FPR_v(clustered) = m / N_negative`。

4. `iid-dispersed`：假阳性尽量分散到不同 `(db_id, error_family)` cluster。
5. `structure-clustered`：相同数量的假阳性集中到尽可能少的 cluster。
6. 两条件还必须匹配：

   - TPR 和真阳性 ID；
   - replay 样本数；
   - supervised token，允许差异不超过 1%；
   - dense token、optimizer steps、学习率和候选生成预算。

7. 错误结构的主标签来自参数化 generator provenance，例如
   `missing_outer_join`、`duplicate_set_semantics`、`null_aggregate`、
   `predicate_boundary`、`topk_tie`、`correlated_subquery`。AST 差异标签只作
   诊断，不能充当因果类别。
8. 必须报告 cluster 数、最大 cluster share 和 HHI；至少要求
   `HHI_clustered > HHI_iid`。如果不能同时匹配 FPR/TPR/token 并形成结构差异，
   Gate 0 失败，不能训练后再挑一组“看起来匹配”的样本。

[Gate 0 检查模块](./src/sqlite_agent_research/gate0.py)已经实现五世界 manifest
检查、selector role 防泄漏、相同 FPR 构造和 token/TPR/FPR/结构浓度匹配检查。
它使用 adjudication truth 的分支只能解释为受控机制 stress test，不能包装成
部署时可用的 oracle selector。

## 数据与统计门槛

当前 32 条、2 个 test DB 的 pilot 不可用于方法结论。正式比较前要求：

- 约 800–1,300 个共享的 paired test units；
- 至少 20–30 个 held-out DB；
- 至少 160 个独立 `(DB, mechanism, generator seed)` 单位；
- 至少 6 个有 generator provenance 的互斥错误类别；
- 动态 witness 至少 50 个，最好 100 个以上；
- 统计单位是 DB 或独立 mechanism instance，不把同一模板的多个 value-world
  当成独立样本；
- Gate A 用 3 个训练 seed 看方向；正式确认至少 5 个 seed；
- 二元结果用 paired McNemar，区间用按 DB/机制分层的 paired bootstrap。

“两组 FPR 没有显著差异”不等于“相同 FPR”。除训练侧精确构造外，locked test
还应做 paired equivalence/TOST。粗略样本需求为：

| FPR 等价界限 | paired negatives |
|---|---:|
| ±5 pp | 248–495 |
| ±3 pp | 687–1,374 |
| ±2 pp | 1,546–3,092 |

首轮建议预注册 ±3 pp；若数据达不到，只能声称“训练侧 FPR 按构造相同”，不能
声称 held-out FPR 已统计等价。

## 计算可行性

- 当前 Stage 2 训练估计约 2.4–3.0 分钟/branch；
- 1.5B 逐题生成实测约 7.1–11.1 秒/题，评估而非训练是主要瓶颈；
- clean Gate A 的 6 方法 × 3 seed，加 3 个共同 Stage 1 checkpoint，对
  500 条生成约需 20.7–32.5 GPU 小时；
- 三重 provenance 与 6 方法完整交叉会变成 54 个 Stage 2 branch，对 500 条
  约需 56–88 GPU 小时；
- 如果同构迁移只换数据库值，缓存的一次 greedy SQL 可在所有 worlds 复用，
  不需要把 GPU 时间翻倍。

因此采用分阶段矩阵：

1. Gate 0：不训练，只验证数据、动态 witness 数量、FPR matching 和防泄漏；
2. Gate A1：仅 clean，6 方法 × 3 seed；
3. Gate A2：只让胜出的 1–2 个方法进入 clean/selection/leaky；
4. Gate A3：只让胜出方法进入 iid/clustered 同 FPR 消融；
5. Gate B：方向稳定后再扩至 5 seed、第二模型家族和 schema-renamed OOD。

这保留所有因果问题，但避免无信息的 `3 × 6 × 2` 全笛卡尔积。

## Gate 0 自动与人工通过条件

Stage 2 之前必须全部满足：

- [ ] clean Stage 1 pool 的构造日志证明未读取 witness outcome；
- [ ] 每个实例五个 world 齐全，schema 相同，seed/hash 独立；
- [ ] `final_holdout` manifest 在 Stage 2 前 sealed 并保存 SHA-256；
- [ ] 当前 checkpoint 对候选池 100% 完整生成，无重复或缺失 ID；
- [ ] 生成上限预注册为至少 384，并逐条保存 `generation_hit_cap`；
- [ ] 所有纳入样本的 gold/world protocol 成功；candidate execution error 保留在分母；
- [ ] replay 容量为 `K` 时至少有 `max(2K, 50)` 个 dynamic witnesses；
- [ ] witness 至少覆盖 6 个机制类别和 20 个 DB；小 pilot 最低也要 3 类、4 DB；
- [ ] iid/clustered 的 FPR、TPR、样本数完全相同，supervised token 差异 ≤1%；
- [ ] clustered 的错误结构浓度明确高于 iid；
- [ ] 所有 branch 的 dense token、optimizer steps、学习率和生成预算匹配；
- [ ] evaluation purpose 永远不输出 replay selection IDs；
- [ ] 43 个本地测试全部通过。

任何一项失败都先修数据或协议，不启动 Stage 2。

## 停止规则

- 动态 witness 少于 `max(2K, 50)`：停止方法比较，扩充 generator；
- 无法在同一 FPR/TPR/token 下制造结构差异：删除该消融，不事后放宽定义；
- selector 读取过 `final_holdout`：该 run 永久作废并重新 seed；
- clean dynamic replay 不优于 uniform、NLL/surprise 和 hard non-witness：
  报告负结果或将贡献改为 benchmark，不包装成新方法；
- safety 提升来自全局过度 abstain：判定方法失败。
