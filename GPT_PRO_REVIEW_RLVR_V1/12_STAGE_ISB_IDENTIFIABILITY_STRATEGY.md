# Stage-I-SB v3：从“有趣的 cosine”转向可识别的 verifier-risk 证据

## 当前结论

旧 v2 实验必须停止扩展，但不是因为它已经证明了“模型利用 rank shortcut”。准确结论是：

> 旧生成器允许一个非语义机制解释同一结果，因此 semantic transfer 不可识别。

与此同时，旧能力口径被 `+NN` 输出格式和 H-ID 位数变化严重混杂。修正后，SmolLM2 在旧任务上的能力确实偏弱，但应由普通 `NN` 重打分和自由生成支持，而不能再用“负号概率质量”支持。

首个 Gate-LC-A 候选后来被一个组合反例推翻：旧 cyclic decoy 可由算术答案直接解码，本地两 block 均为 `256/256`；ChatGPT Pro 首轮补丁把它改成 `semantic_rank × nuisance_band`，仍是 `256/256`。因此 v1–v4 只保留为开发诊断。

当前 v5 将 train/H-ID decoy 改成两个不共享点位的固定随机 derangement，并新增三个具有完整共同支持的组合解码硬门。两 block 上三项均为 `0/256`。这只说明已冻结的 fixed-and-crossed decoder registry 没有发现泄漏，不等于已经排除所有模型可学 shortcut。Gate-LC-B、geometry、training 和 confirmatory evidence 仍全部未授权。

## 证据状态

| 环节 | 当前证据 | 判定 |
|---|---|---|
| follow-up ZIP 完整性 | 7/7 声明文件 SHA-256 匹配；外层 ZIP CRC/path/duplicate 检查通过 | 通过 |
| 旧 v2 rank surface | true split namespace oracle 32/32 + 32/32 | generator-side failure |
| blind rank transfer | affine raw 0/32，clip 2/32；centroid 1-NN 2/32 | 未证明模型跨 namespace 利用 |
| signed restricted policy | gold top-1 2/32 + 2/32；top-1 全负 | 被格式混杂 |
| conventional `NN` policy | gold top-1 2/32 + 2/32；top-1 全正 | 能力仍弱，符号解释被推翻 |
| `+NN → NN` | 正数平均 +8.8379 / +9.0898 nats | 严重格式效应 |
| greedy strict | train 0/32，H-ID 0/32 | 当前任务不在理想能力边界 |
| greedy loose last integer | train 6/32，H-ID 0/32 | H-ID 位数/难度变化严重 |
| saved factorial interaction | 三 seed mean -0.253328 | 单 block/mapping/vocabulary 描述性现象 |
| Gate-LC-A v1–v4 | fixed/cyclic 或 semantic×nuisance decoy 可 train→H-ID 256/256 | 被反例推翻 |
| Gate-LC-A v5 | 2 blocks、16 data files、4096 rows、136 fixed comparisons + 6 crossed comparisons；crossed 0/256 | 冻结工程 registry 通过 |
| Gate-LC-B | 未运行 | NO-GO |
| 新 geometry | 未授权 | NO-GO |
| RL/SGD/AdamW | 未运行、未授权 | NO-GO |

## ChatGPT Pro 首轮工程审查的采纳结果

外部补丁的可执行部分已在隔离副本中复验：

- baseline SHA 与 patch SHA 匹配；
- patch clean apply；
- focused `20 passed`；
- 本机补齐既有依赖后相关旧测试 `46 passed`；
- runner 两次 19 文件字节一致；
- audit 与 READY 可重现。

但整体判定为 `FAIL / 不合并`，原因不是测试失败，而是测试未覆盖两个 critical 反例：

1. `support < 16 → __RARE__` 会把 256 行全部折叠成常数多数分类器；构造的“32 类、每类 support 8、跨 split 完美复用”特征从 raw `256/256` 被折叠成 `16/256`。
2. Pro decoy 为 `semantic_rank + shift[nuisance]`；算术 gold 与首操作数 band 可从 prompt 直接恢复，train→H-ID 为 `256/256`。

安全 verifier 也不能整体采纳。它不执行 ZIP 内代码，这是正确的；但实测接受：

- `CON.txt`、`AUX.json`、`LPT1.log`；
- NTFS ADS 冒号；
- trailing dot/space；
- file/file-prefix collision；
- 高压缩比 payload。

本地 verifier 已升级为 v2：增加 NFC/casefold、Windows device/ADS/trailing、prefix collision、encrypted entry、entry/size/ratio 上限与分块 CRC/hash；focused `16 passed`。ChatGPT Pro 首轮交付 ZIP 的内容经该 verifier 核验为 7/7 完整，但 Pro 自己的 verifier 不支持其 `SHA256SUMS.txt` 文件名，因此“自验证通过”声明仍不成立。

## 对 follow-up 报告必须作的三项更正

### 1. “100% rank decoding”不是 blind transfer

旧审计使用各 split 的真实 namespace：

```text
round((first_operand - namespace) / 200)
```

因此它证明 generator 把 rank 写进了 operand，但不能证明：

- train 学到的 decoder 可直接迁移 H-ID；
- 基础模型实际使用该 decoder；
- 该 decoder 因果造成保存 kernel 的高 alignment。

论文表述必须停在：

```text
generator-side identifiability failure
```

不能写成：

```text
proved rank lookup caused false semantic alignment
```

### 2. “负号偏好”主要是 `+NN` 格式效应

prompt 只要求普通 integer，但旧 action contract 强制正数为 `+NN`。重打分后：

- positive top-1 从 0/32 变成 32/32；
- negative probability mass 从约 0.98 降到约 0.025–0.037；
- gold top-1 仍只有 2/32。

因此：

- “模型不在理想能力边界”仍成立；
- “模型不理解正号/负号”不能由旧 mass 直接推出；
- 所有后续能力门必须以 F0 conventional 为 primary。

### 3. H-ID 不是纯同构 held-out

train first operands 全是四位数，H-ID 全是五位数；greedy loose gold 从 6/32 降到 0/32。H-ID 同时改变：

- namespace；
- digit count；
- tokenization；
- 算术难度；
- prompt length；
- answer vocabulary则保持不变。

所以旧 H-ID 不能区分“语义未迁移”和“模型对五位数直接失效”。

## Gate-LC-A v5 当前候选

当前候选目录：

```text
outputs/arithmetic-gate1-stage-ISB/gate-lc/engineering-root95001-v5
```

核心构造：

1. `semantic_rank`、`target_rank`、`cue_code_rank` 分离。
2. 四格共享 base expression，每格都有等宽 `Reference code Cxx`。
3. numeric codebook 全为 25 的倍数。
4. `{0,25,50,75}` residue subgroup 让单个 operand suffix 可结构性平衡。
5. neutral offset 固定 100，不破坏 suffix 正交。
6. train/H-ID 交换 within-band offsets，保持 aggregate distribution，并公开每个 categorical probe 的共同支持率。
7. block 0/1 使用不相交的 numeric base domain。
8. decoy 在 train/H-ID 使用两个不同的固定随机 derangement；旧 semantic-only 与 semantic×nuisance 规则不能迁移。
9. placebo code shift 在 train/H-ID 不复用同一 nuisance slot。
10. informative cue 必须 256/256 解码；placebo 与 nuisance 以 Wilson U95 ≤ 0.125 为门。
11. 三个 mandatory crossed decoders 必须具有 100% train/H-ID support；无共同支持不能被写成“无泄漏”。

当前产物：

```text
audit SHA-256:
6f13683284069e4994463ce59753c31629e98ad8668777c3c098dd21b778b1ae

artifact manifest SHA-256:
8a1e1e0986a0bf97bfe413d4affe8f3565c74c7d70177ac77df64030c11b9ab9

READY SHA-256:
fd95d5520a27c7c88165525195f369ac33450c93db9c48e898f5049e584f82f7
```

独立重放产生 20/20 相同文件，字节差异为 0。三项 crossed decoder 在两个 block 上均为：

```text
arithmetic semantic rank:                 0/256
arithmetic semantic rank × nuisance:      0/256
placebo reference code × nuisance:        0/256
shared support:                            256/256
Wilson U95:                                0.014784
```

所有 authorization：

```text
confirmatory=false
gate_lc_b_capability_passed=false
source_scoring_authorized=false
geometry_authorized=false
training_authorized=false
```

## 当前 Gate-LC-A 仍不能解决的四个问题

### A. 固定 categorical probes 不是 shortcut 完备证明

v5 已为每个 feature 输出 train→H-ID row support，并把不完整支持标为 `diagnostic only`。这修复了“unseen category fallback 被误写成无泄漏证据”的口径错误，但仍不能排除：

- 数值归一化后的线性规则；
- character n-gram；
- BPE token pattern；
- small tree/rule list；
- 多个单特征组合；
- 基础模型自带的数字结构表示。

所以允许的表述只能是：

> passed the frozen fixed-and-crossed engineering decoder registry

不能写：

> proved no model-learnable surface shortcut exists

### B. v5 decoy 修复了 transfer，但不是 label-coherence-matched control

v5 用 split-crossed derangement 阻断了旧 `256/256` 查表路径。代价是：

- valid arm 的 semantic mapping 跨 split 保持；
- decoy arm 的 hidden mapping 跨 split 刻意不同。

因此 raw `valid - decoy` 仍同时包含 semantic rule 与 mapping coherence。v5 decoy 只可作为泄漏压力控制，不能直接支持 semantic geometry。主要分析必须使用：

1. mirror mapping；
2. full prompt×label crossed matrix；
3. prompt main effect 与 label main effect double centering；
4. H-MAG/H-LABEL 与 format arms。

### C. codebook 正交与自然能力存在张力

25 的倍数 codebook 有利于 suffix 正交，却把答案变成三位数；当前 base operands 为五位数。这可能让 SmolLM2 再次离开能力边界。

因此 Gate-LC-A 的 codebook 不能自动成为 Gate-LC-B/geometry 的最终 codebook。必须先用 clean-only calibration：

```text
model × difficulty × format
```

选择唯一候选，再在 untouched audit 上锁定。

### D. 当前工程 blocks 不是 confirmatory replicates

两个 blocks 共享：

- 同一 codebook；
- 同一 generator family；
- 同一 probe family；
- 同一 root；
- 同一分析阈值。

它们只能检测实现漂移与 numeric-domain 稳定性，不能当成两个独立科学样本。

## 最清晰的下一步路线

### Step 1：冻结“停止旧设计”的更正结论

保留旧 v2 源码、数据、score 和 kernel 作为反例，不修写、不覆盖。更正报告成为新的解释入口。

通过条件：

- 所有旧数字可从保存文件重聚合；
- format forward 使用相同本地模型 hash；
- 明确标记无 gradient、无 training。

### Step 2：完成 Gate-LC-A 的 adversarial revision

已完成：

1. exact/category support 显式报告；
2. operand exact/band/prefix/within-band/suffix 和 tokenizer length；
3. deliberately injected rank surface；
4. split-crossed balanced decoy；
5. semantic、semantic×nuisance、reference×nuisance crossed hard gates；
6. v5 byte-identical replay。

仍未完成：

1. character/token n-gram 与低容量树/规则列表；
2. full crossed prompt×label matrix 的静态完整性检查；
3. family-level uncertainty across independent generator families；
4. externally frozen prospective blocks。

若任何预冻结 probe 失败：生成 `FAILED.json`，不继续 source scoring。

### Step 3：Gate-LC-B 只看 clean capability

使用互斥数据：

```text
calibration set
→ 按冻结顺序选择唯一 model×difficulty
→ untouched audit
→ 锁定或停止
```

三个格式臂：

- F0：`NN/-NN`，中性 instruction；
- F1：`+NN/-NN`，中性 instruction；
- F2：`+NN/-NN`，显式 leading-sign instruction。

primary 必须是 F0。Gate 至少覆盖：

- strict parseability；
- restricted gold top-1/rank；
- free-generation pass@1；
- sign-conditional reachability；
- natural FPR/exposure/effective advantage；
- exact-policy TV。

若 exact TV 失败，exact arm 只能当 geometry ablation。

### Step 4：先做 full crossed interaction geometry

对每个 base prompt 保存所有 candidate labels：

```text
g[i,r]
```

再计算：

```text
interaction[i,r]
= g[i,r]
- mean_r g[i,r]
- mean_i g[i,r]
+ grand_mean g
```

这比直接比较同标签 raw cosine 更接近真正问题：

> arithmetic prompt 与 verifier-accepted target 是否有超出 prompt/label main effect 的特异耦合？

四格主要量：

```text
S = K_valid,placebo - K_decoy,placebo
L = 0.5 * [(K_valid,info - K_valid,placebo)
         + (K_decoy,info - K_decoy,placebo)]
I = (K_valid,info - K_valid,placebo)
  - (K_decoy,info - K_decoy,placebo)
```

### Step 5：mirror mapping + 新 vocabulary + heldout ladder

最低 confirmatory 单位：

```text
6 vocabularies
× 1 mirrored mapping pair
= 12 mapping roles
```

每个 mapping 使用 2 个 LoRA initialization，仅作技术重复。

heldout：

- D-ID：相同 digit count/difficulty；
- H-TEMPLATE；
- H-MAG：answer-string intersection=0；
- H-LABEL：lookup codebook intersection=0；
- H-FORMAT；
- H-JOINT。

只通过 H-ID 时，分类必须是：

```text
same-vocabulary lookup compatible
```

### Step 6：只有 geometry 通过后才考虑 dynamics

顺序：

1. frozen Best-of-N；
2. counterfactual one-step；
3. deterministic expected SGD；
4. AdamW state-aware virtual update；
5. short actual training；
6. natural K8；
7. free-generation RLVR。

每一层都必须有独立 operation authorization。当前没有任何 training authorization。

## 当前决策

```text
旧 v2：STOP
Gate-LC-A v1–v4：SUPERSEDED BY 256/256 COMPOSITIONAL COUNTEREXAMPLE
Gate-LC-A v5 fixed+crossed registry：LOCAL ENGINEERING PASS
Gate-LC-A model-class completeness：NOT PROVED
raw valid-minus-decoy geometry：NOT IDENTIFIED
Gate-LC-B：NOT RUN
source scoring：NOT AUTHORIZED
geometry：NOT AUTHORIZED
training/RL：NOT AUTHORIZED
94001–94008：UNTOUCHED IN CURRENT WORKSPACE
```

最值得继续的研究问题已经从：

> shared bug 的 raw cosine 是否更高？

收缩成：

> 在 prompt leakage、label main effect、format、capability、mapping 和 frozen selection 都被扣除后，是否仍存在可跨新 magnitude/label 迁移的 prompt×target interaction，并且它是否预测真实学习损害？

这条问题比当前 raw cosine 更难，但也更接近一篇能够站得住的论文。
