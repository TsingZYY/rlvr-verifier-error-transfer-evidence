# R12 最低成本 Identity-Switch Pilot 实现与识别审计 R1

审计日期：2026-08-05  
审计范围：`R12_IDENTITY_SWITCH_PILOT_PROTOCOL_DRAFT_R3.json`、现有 R10/R11 runner、reward-mask contract、R12 custody wrapper、所选两栈的 config/mapping/source/target 静态材料。  
明确未做：没有加载 tokenizer、模型或权重；没有 forward、gradient、optimizer 或 RL/RLVR；没有修改 result validator 或 release audit；没有产生科学结果。

## 结论先行

**当前 R3 不能执行，6-process / 12-process 也还不是一个真实可调用的 R12 实验。** 现有 `run_same_source_bridge_r12_custody.py` 只是给 R11 runner 加单次调用 custody；它仍解析 R11 cell、只接受 `BUG`/`GOLD_ONLY`，并要求 R11 result schema。要跑 R12 必须先写专用 R12 contract/runner/manifest，而不是继续包裹 R11。

R12 的核心 estimand `C` 在很窄的边界内是合理的：它可以描述“在固定七候选、固定 affine codebook、gold 同时得奖时，把第二个错误奖励从 latent identity `r` 切到 `pi(r)`，是否重定向相对 target readout”。它不能证明一般的“共享结果造成跨任务风险”，也不能证明相同 empirical/policy-weighted FPR。

但 R3 还有一个必须在 freeze 前修正的代数事实：

\[
U_P(s,r)=U_A(s,\pi(r))
\]

因为两个 BUG arm 使用相同 source rows、checkpoint、update recipe，而 `pi` 是五个 identity 上的双射。枚举全部五个 `r` 后，`BUG_ALIGNED` 与 `BUG_PERMUTED` 是**同一组五个 reward masks / updates 的循环重标记**，不是十个不同 design updates。对实际 `build_r4_a` 的两个所选 source panels 做 model-free mask 枚举，两栈均得到：aligned 有 5 个唯一 mask、permuted 有 5 个唯一 mask、两集合完全相等。

因此：

- R3 所写 A-only “22 unique design updates”不成立；正确是两栈共 **12 个唯一 intervention masks**（每栈 5 个 gold+wrong masks，加 1 个 Gold mask）。
- 若仍分别执行 aligned/permuted，A-only 可有 22 次技术执行和 110 次技术 readout，但其中 BUG 部分是重复执行；A/B 可有 44 次技术执行和 220 次 readout，但唯一 intervention masks 仍是 12。
- 真正最低计算版可把 permuted 作为 aligned matrix 的重索引，降为 **4 processes / 12 updates / 60 target reads**；A/B 为 **8 processes / 24 executions / 120 reads**。
- 若为检查 arm-label 或 runner 污染而保留 6/12 processes，则必须新增强制 alias gate：`mask_P(r)=mask_A(pi(r))`、`hash_P(r)=hash_A(pi(r))`、`Y_P(r,q)=Y_A(pi(r),q)`（容差内）。此时 permuted process 是技术负控，不是新增 treatment。

审计 verdict：**Reject current R3 as an execution contract; Accept the narrow identity-switch diagnostic with mandatory revisions.**

## 1. 审计依据与已验证静态事实

主要文件及本次读取到的 SHA-256：

| 文件 | SHA-256 | 本次用途 |
|---|---|---|
| `formal_g1_development_r1/R12_IDENTITY_SWITCH_PILOT_PROTOCOL_DRAFT_R3.json` | `25371a38eea9ea56ecbb8c9ea7dd0f2b001b2469365861e651967ba2822d0377` | arms、estimands、计数、边界、停止规则 |
| `mvp_same_source_v1/run_same_source_mvp.py` | `439cc675cf8b53c1c0ec2e70a85866a6b87a1a9eaf3539478fe087ded35c6f4d` | 五-offset 循环、checkpoint restore、现有 gold+wrong update |
| `mvp_same_source_v1/run_same_source_bridge_r11.py` | `7d9ebe5a2ede65525daa8caff794f47a5e712ac48e11059d69fa6f0c455348eb` | R11 protocol/arm 绑定及五-update 后置条件 |
| `mvp_same_source_v1/run_same_source_bridge_r12_custody.py` | `cd021411ffc251a0265e4af197c84f8f3cff2be8658c695953c1376203fd0c3c` | 证明当前所谓 R12 wrapper 仍调用 R11 runner |
| `mvp_same_source_v1/r11_bridge_contract.py` | `d0327fd3aea5f72ac998ec9c86a586c31f89bc58548904cf8d0bd78caae44fc5` | 现有 arm 仅为 `BUG`,`GOLD_ONLY` |
| `mvp_same_source_v1/r11_static_contract.py` | `4f9d829aae7eb94a5c70eb2cae13f094a9f96a3b5a815d7ea27a1b2458f7fb6e` | R11 的 8-stack/2-arm/2-replicate cell space |
| `mvp_same_source_v1/r12_invocation_custody.py` | `47b189e362aeca36b587ca1ae46f60b3ddb04d60923cdba43ad7d967329920b3` | 单次 claim 的静态 custody 能力；不是 R12 science runner |
| `mvp_same_source_v1/MVP_CONFIG_V1.json` | `8d3288f8dfe30dcbf009779299b2927ec87e296288bc41c41ec21aa0498a8924` | 正确 local model revision 的来源 |
| `real_assets/build_r4_a/REAL_SOURCE_BUNDLES_V1.jsonl` | `b4d1d34379869fdc4dec7770682cbb9682b55b2d3259bb2a3a4d53e8864db5c0` | 两栈实际 source rows 的 mask 枚举 |
| `real_assets/build_r4_a/TARGET_CALIBRATION_REAL_V1.jsonl` | `5df581f7ff10180d36fd57f3d7ab2df8bee8f3c6dc449af2fa4972062960a55d` | 固定 calibration readout panel |
| `real_assets/build_r4_a/REAL_MAPPING_STACKS_V1.jsonl` | `0a76d1ca1b214ce82e2a3c31b3ffdce448f28e48377610fe5f8120c74616fb1a` | task-pair/codebook/人工审查状态 |

R3 自身已正确标注 `DRAFT_STATIC_DESIGN_NOT_AUTHORIZED_NO_MODEL_RUN`、`run_eligible=false`、`scientific_evidence=false`。两个固定开发栈是：

- `TP1-M0-A_TO_B`: `MOD7_SUM_V1 -> DFA7_FINAL_V1`；
- `TP2-M0-A_TO_B`: `MARKED_RANK7_V1 -> PAREN_MAX_DEPTH7_V1`。

两栈使用相同的 M0 affine codebook 对：source `K((1*z+0) mod 7)`，target `K((2*z+1) mod 7)`；它们共享同一 builder 和 verifier implementation。mapping 记录仍是 `PENDING_HUMAN_REVIEW` / `MACHINE_COMPUTED_PENDING_HUMAN_REVIEW`。

## 2. 证据分层

### 2.1 已有证据

1. R3 的书面 treatment 边界明确：Gold 为 `0/6` design candidate-panel FPR；两个 BUG arm 均为 `1/6`，每行 binary reward mass 均为 2。这个“matched FPR”只对 aligned-versus-permuted 成立。
2. `C` 的公式是明确、可重算的 treatment-by-target interaction；Gold 在 `C` 中不直接出现，Gold 只用于 `A` 的 adverse/clean anchor。
3. 现有 parent runner 每个 process 固定循环 offsets 1..5；R11 Gold 虽然把 wrong reward 置零，仍会运行五次 update。它不能实现 R3 要求的“每 `(k,s)` 只运行一次 Gold”。
4. 当前 R12 custody wrapper 明确 import `run_same_source_bridge_r11`，解析 R11 cell id，并要求 `r11-same-source-bridge-result-r1`。这不是三臂 R12 runner。
5. 实际 source rows 上 aligned/permuted mask 集合相等；这不是模型结果，而是 treatment 定义和双射 `pi` 导出的静态事实。
6. R3 绑定的 local revision `a10cc1512eabd3dde888204e902eca88bddb4951` 与当前 M0 configs 一致；不能继承 R11 protocol 中冲突的 revision。

### 2.2 合理推断（尚非运行证据）

1. 写一个专用 R12 runner 后，一台现有 CUDA 环境从计算结构上可以完成 6/12-process 调度；每个 BUG process 执行 5 次 one-step LoRA update，每个 Gold process执行 1 次。这里没有运行时、显存或成功率证据。
2. 在 consistency、同初始状态、无 arm-label 副作用、无跨 update interference 的条件下，`C` 可识别“rewarded latent offset 的切换对两个对应 target readout 的相对总效应”。
3. 若同硬件/软件和确定性算法确实给出 bitwise-stable 参数，A/B 可以作为技术重现性检查；但这不增加 task-pair 样本量。
4. A-only 可作为最便宜的工程 smoke test，但没有独立复跑证据。若目标是形成可解释的 development result，而不仅是冒烟，12-process A/B（或去重后的 8-process A/B）更稳妥。

### 2.3 尚未验证的核心假设

1. SmolLM2 在这两个栈上是否产生有限、无 clipping、超过 dead-zone 的 `A` 与 `C`；当前模型执行数是 0。
2. 不同 wrong candidate 的 pre-policy mass、expected reward、gradient norm、update norm 是否严重不平衡；R3 只要求记录，不要求匹配。
3. `1e-12` A/B tolerance 是否在当前 GPU/runtime 上可达；尚无 R12 treatment 的技术校准。
4. machine-generated row labels、prompt semantics 和 intended task answers是否通过独立人工审查。
5. affine latent offset 的 effect 是否能推广到非-affine codebooks、自然错误、sampled/multi-step RLVR 或真实 policy-weighted FPR。
6. 选定的 cyclic derangement 与两条 M0/A_TO_B 栈虽然声明 outcome-independent，但 R12 是看过 R10 development outcomes 后设计的；只能保持 development-only。

## 3. Fatal / Major 缺陷审计

| 级别 | 缺陷 | 为什么会让当前版本停止 | 修复条件 |
|---|---|---|---|
| **FATAL-RUN-1** | 没有 R12 science runner/contract | 当前 custody 最终调用 R11 runner；arms、protocol hash、cell schema、Gold 次数、result schema 都与 R3 冲突。任何当前调用都不能声称执行了 R12。 | 专用 R12 contract、runner、manifest、cell space、custody binding 和静态测试完成并重新 freeze。 |
| **FATAL-RUN-2** | aligned/permuted treatment alias 与“22 unique updates”冲突 | `P(r)=A(pi(r))`；R3 把重复 treatment 当作不同 design updates。若 implementation 让两者不同，差异来自 arm-label/seed/order contamination；若相同，当前 uniqueness 叙述为假。 | R4 选择“去重重索引”或“保留重复作 alias negative control”，纠正计数并加 exact alias gate。 |
| **MAJOR-1** | `C` 不是纯 sharedness mechanism | 它识别切换 rewarded latent offset 的总效应；policy mass、expected reward 和 gradient geometry 会随 candidate 一起变。 | 把 claim 限于 fixed-panel total identity-switch effect；若要归因 sharedness，增加 dose/probability-matched control 或预注册 mediation/gradient 分析。 |
| **MAJOR-2** | 科学样本量只有两个 outcome-aware development cases | 五 identities、6/12 processes、110/220 reads 和 A/B 都是嵌套 treatment 或技术重复，不能当独立样本。 | 禁止 CI/SE/p/population claim；后续另冻 fresh task-pair clusters。 |
| **MAJOR-3** | 资产语义仍 pending human review | 两个 mapping stack 明示 machine labels pending human review。技术 hash 完整不等于题意和 gold label 正确。 | 在授权前冻结独立人工审查 receipt，并由 manifest 绑定。 |
| **MAJOR-4** | 单一 M0 affine mapping、单向 A_TO_B | 两 task pairs 共享同一 codebook 对、builder 和 verifier；不能排除 codebook/template-specific effect。 | 本 pilot 只写 affine M0/A_TO_B development boundary；general claim 前加入 non-affine、反向与新构造簇。 |
| **MAJOR-5** | A/B tolerance 与 practical margin 未校准 | `epsilon=1e-10` 只是 numerical dead zone；即使通过也可能科学上可忽略。`1e-12` 重现阈值也尚未用 R12 pipeline 校准。 | 用非研究资产做 outcome-blind determinism calibration；未来 inference 冻结 practical margin。 |

## 4. 识别命题：正确部分与严格边界

令 `T_j` 表示在同一 source panel 上给 gold 和 wrong latent offset `j` 各 reward 1 的 one-step intervention，`G` 表示只给 gold reward 1。记 `Y_j(q)` 为 `T_j` 后 target identity `q` 的 readout。

aligned/permuted 只是：

\[
Y_A(r,q)=Y_r(q),\qquad Y_P(r,q)=Y_{\pi(r)}(q).
\]

因此：

\[
C(r)=[Y_r(r)-Y_{\pi(r)}(r)]-[Y_r(\pi(r))-Y_{\pi(r)}(\pi(r))].
\]

这个 interaction 的因果解释成立需要：相同初始 checkpoint、相同 source/target rows、相同 update recipe；arm 名、nonce、执行次序和 seed 不得进入数值计算；每个 update 从相同 snapshot 重置；没有 clipping/non-finite/跨 update 状态残留。

若通过，它最多支持：固定七候选、两个已看过 development family、M0 affine codebook、one-step exact-expected-reward surrogate 中，rewarded latent identity 的切换会改变相对 target transfer，且这一结果以 gold 同时得奖为条件。

它不支持：

- `shared result identity` 是唯一机制；
- 同 empirical、natural 或 policy-weighted FPR；
- wrong-only additive effect 或 gold×wrong decomposition；
- sampled/multi-step RLVR；
- capability damage、实际部署风险或 practical magnitude；
- codebook-invariant/semantic sharedness；
- task-population generalization或 confirmatory inference。

R3 对这些禁区大体写对了；需要补上的关键一句是：**permuted 不是第二组 treatment，而是同一五-treatment panel 的预注册重配对；分开执行时只承担 contamination/determinism negative-control 角色。**

## 5. Runner 与冻结材料的最小 patch 清单

以下均须在任何模型授权之前完成。

### P0：先出 R4 protocol

1. 二选一并冻结：
   - **去重版（真正最低成本）**：每栈 `GOLD_ONLY + BUG_PANEL` 两进程；从五个 `Y_j(q)` 重索引得到 permuted potential outcomes。A-only 4 processes/12 updates/60 reads；A/B 8 processes/24 executions/120 reads。
   - **alias-control 版（保留原 6/12 process 外形）**：三 arm 分开执行，但把 permuted 明确写成重复负控；唯一 design masks=12，A-only executions=22/reads=110，A/B executions=44/reads=220。
2. 修正所有 `unique_design_updates`、count check、process semantics、结果 validator 要求和贡献措辞。
3. 将 `C` 与 `A` 作为两条独立解释轴：`C` 是 identity-switch；`A` 是相对 shared Gold 的 adverse anchor。只有二者都过才能给 full development pass；`C` 过而 `A` 不过只能报告 redirection-without-positive-adverse-anchor。
4. 选定 A-only 或 A/B 后 freeze；不允许看 A 再补 B。

### P0：专用 R12 treatment contract 与 runner

1. 新建 R12 arm/treatment schema，不能复用 `r11_bridge_contract.ARMS` 或 R11 protocol hash。
2. runner 的 Gold 路径必须 `wrong_offset=None`，每 `(k,s)` 只更新一次、读取一个五-target vector；禁止构造五个 per-r Gold updates。
3. BUG 路径显式记录 `nominal_r` 与 `rewarded_wrong_identity`; aligned 使用 `r`，permuted 使用 `pi(r)`。
4. 每个 treatment 从同一初始 trainable snapshot 恢复并检查 initial hash；seed/row order 只可由 stack、replicate 和实际 treatment mask 决定，不能由 arm label 或 nominal `r` 污染 alias。
5. 输出 raw mask rows、canonical mask hash、pre-policy mass、expected reward、raw gradient norm、update norm、parameter hash、五个 raw target metrics；不在 runner 内信任或生成最终 scientific verdict。
6. Gold、aligned、permuted 的 initial parameter hash、source/target commitments、optimizer、precision、chat template 和 model inventory必须逐臂一致。

### P0：alias、Gold 和计数静态测试

1. 对两个冻结 source panels逐行重算 FPR 与 reward mass。
2. Gold 在五个 hypothetical `r` 下得到同一 canonical mask；实际只生成一份 update/readout。
3. 对所有 `(s,r)` 验证 `mask_P(r)=mask_A(pi(r))`。
4. alias-control 版在完成后验证 parameter hash 与 target vector 同样 alias；任何不一致都标 `TECHNICAL_CONTAMINATION_STOP`，不得计算 `C`。
5. 构造 synthetic raw results，验证 A/C、dead-zone、缺 cell、重复 cell、错 mapping、NaN/Inf、clipping、跨 arm drift 和错误计数均 fail closed。

### P0：custody / manifest / authorization

1. 定义 R12 的 2-stack × selected arms × selected replicates exact cell set；master、plan、signed launch、claim ledger 和 output path 全部绑定 R4 protocol及专用 runner hash。
2. 生产运行必须有外部 trusted signer policy 和真实签名；当前 HMAC 测试 verifier 不能当生产授权。
3. 绑定正确 model revision `a10cc1512eabd3dde888204e902eca88bddb4951` 与清理后的语义模型 inventory；排除 cache/pyc。
4. 绑定独立 human-review receipt、source/target/mapping bytes、R12 validator 以及选定 A-only/AB variant。

### P1：运行前技术校准

1. 在非研究 stack 或 synthetic tensor 上校准 bitwise/absolute determinism，不接触 R12 outcome。
2. 若 `1e-12` 不可达，必须在看到 R12 结果前发布新 protocol revision；不能 outcome-dependent 放宽。
3. 估算单 BUG/Gold process 的时间和显存，仅作为资源计划，不当科学证据。

## 6. 成功规则与停止规则

### 6.1 授权前停止规则

以下任一未满足，模型执行数必须保持 0：R4 尚未 freeze；专用 runner/validator 未通过静态 adversarial tests；variant 未选定；Gold 不是真正一次更新；alias/count 仍冲突；生产 trust root/signature 缺失；human review receipt 缺失；revision/inventory/asset bindings 不完整。

### 6.2 技术成功规则

1. 精确完成所选 design 的 cells/executions/reads；所有值有限、无 clipping、无 overwrite/replay。
2. Gold 每 `(k,s)` 只有一个 mask/update hash/五-target vector，并被五个 `A(r)` 共同引用。
3. 两 BUG treatments 每行均为 design FPR `1/6`、reward mass 2；唯一差异是实际 rewarded wrong identity。
4. alias-control 版必须通过 `P(r)=A(pi(r))` 的 mask、parameter hash 和 target vector gate；不通过即技术污染，无科学解释。
5. A/B 版必须先逐 treatment/arm 重现，再平均；补偿性差异不能靠最终 `C` 抵消。

### 6.3 Development mechanism 结果规则

- **Identity-switch signal**：两个 task-pair mean 的 `C > epsilon`，且 overall descriptive mean `C > epsilon`；报告全部 10 个 stack×identity `C`，不把 identities 当样本。
- **Adverse clean-anchor signal**：两个 task-pair mean 的 `A > epsilon`，且 overall descriptive mean `A > epsilon`。
- **Full R12 development pass**：technical success + 上述两条都满足。标签只能是 `R12_DEVELOPMENT_IDENTITY_SWITCH_PASS_ONLY`。
- `C` 不过：禁止 identity-switch/sharedness claim；即使 `A` 过，也只能说明在固定 setup 中存在 BUG-plus-Gold versus Gold 的差异。
- `C` 过、`A` 不过：最多报告相对 identity redirection，没有 positive adverse anchor；不得称为更高迁移风险。
- 任一技术 gate、dead-zone gate 或 task-pair sign gate失败：此版本停止；禁止换 derangement、stack、epsilon、seed 或超参数后重跑同一 development claim。

## 7. 科学样本量解释

当前 pilot 的描述性顶层单位是 **2 个 task-pair cases**。每个 case 内的五个 identities 是固定 treatment levels/readouts，不是五个独立任务；2/6/12 OS processes、12/22/44 updates、60/110/220 reads 都不是科学样本；A/B 是技术重复，也不是新 cluster。

此外，R12 是看过 R10 development outcomes 后设计的，所以即使技术上成功，本轮 confirmatory sample size 仍应视为 **0**；两 task pairs只能作为 outcome-aware development cases。不得给 SE、CI、inferential p-value 或 population task claim。

未来若用预注册、独立、新 task-pair clusters做最简单的一侧全正 sign test，5 个全正 clusters 的零假设概率是 `2^-5=0.03125`；这是数学下限而不是推荐 power。更稳妥的是至少 6 个 fresh clusters，并在允许异质性时另做 power/sample-size 设计。当前两个 pairs不能回收成 fresh held-out clusters。

## 8. 最低成本建议

1. **先不要运行 6/12。** 先发布 R4，解决 treatment alias 与错误 unique-count。
2. 工程烟雾测试选择去重 A-only：4 processes / 12 updates / 60 reads；它只能回答 pipeline 是否工作。
3. 若目标是形成可审核的 development mechanism result，选择去重 A/B：8 processes / 24 executions / 120 reads。若坚持 12-process 版本，明确多出的 aligned/permuted executions 是 alias negative control，并把任何差异当技术失败。
4. 只有 static、custody、human-review 和 production-signature gates 全通过后，才讨论实际模型授权；本审计不提供授权。

