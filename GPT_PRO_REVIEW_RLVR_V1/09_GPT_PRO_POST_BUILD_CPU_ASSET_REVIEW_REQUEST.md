# GPT Pro 审核请求：CPU-only 最低成本验证资产门

日期：2026-07-30  
审核对象：上传兼容审查投影所绑定的 CPU-only 五件套  
当前状态：`PRE_REVIEW / NOT_RUN`

## 你的角色

请作为独立、对抗性的研究设计与因果识别审稿人，实际检查上传投影中的完整协议、
预注册、bundle manifests、任务代码、全部 source/target rows 和分层 verifier
样本，而不是只复述本请求。

本轮不是请你运行模型，也不是请你批准科学结论。请只判断：当前 CPU-only 资产是否
足以进入“真人逐行复核 + 冻结模型实验 blocking fields”的下一阶段。

## 待审研究命题

“在 online verifier FPR、accepted-wrong reward mass、candidate exposure 和
reward-vector multiset 相同的条件下，shared verifier error 是否比
task-local verifier error 造成更高的跨任务迁移风险？”

关键识别量仍预注册为：

```text
L = mean(log p(bug | x) - log p(gold | x))
Delta = L_post - L_pre
tau = Delta_shared - Delta_local
```

冻结的 `D` 只可作为 gate/诊断量，不可用于事后挑选或调整样本。

## 上传文件及外部哈希

1. `P4_R1_REAL_PROTOCOL_V2_PRE_REVIEW.json`  
   SHA-256 `ad2d018fb74b1b7a4800e79c293bf6648cb4644c80e7d4406762d9cdbb49ca1b`
2. `REAL_SOURCE_STACK_BUNDLE_V1.zip`  
   SHA-256 `e322675a405fcce9e37804e9c6c4592b6c03171cf71f5d068acbe8c8859d83eb`
3. `VERIFIER_G1_CPU_BUNDLE_V1.zip`  
   SHA-256 `52e2cf3cb0603ba7c78224f309a4cb305ac81b21766f9378aac440fcaba5783c`
4. `TARGET_G2_REAL_BUNDLE_V1.zip`  
   SHA-256 `3017292380eb6344b0beb74358f7217d3bafa6ad9d95c0c72de0b82c44ec0ae4`
5. `RANDOMIZATION_MODEL_ENV_PREREG_V1.json`  
   SHA-256 `5d260acf548804abb150ee8425470d2145218d47d3b6427f0319b29edbb72bae`

## 上传边界

ChatGPT 当前上传端拒绝直接附加 `.json` 和 `.zip`；在新会话逐文件重试也复现。因此
本轮上传两个只读 Markdown 审查投影，而不是伪称原 ZIP 已成功上传：

- `PRO_UPLOAD_A_CPU_PROTOCOL_AND_PROVENANCE_V5.md`
  - 228,147 bytes
  - SHA-256
    `a66c22a2ae175c12ecdbc20ab8870998c5cc63dabf98b739d7f92579a9fe10fb`
  - 完整协议、final prereg、三份 bundle manifest、mapping stacks、任务代码、
    native provenance、G1 audit、机器验证报告、label index 与 audit seal。
- `PRO_UPLOAD_B_CPU_ROWS_AND_REWARD_SAMPLE_V5.md`
  - 1,328,159 bytes
  - SHA-256
    `580073737beaec61ece4aa91c4294ace75252ca6ab0d00e85b2737c18280e560`
  - 全部 8 个 source bundle、全部 56 条 calibration rows、全部 112 条 sealed
    audit rows；verifier reward manifest 为 24 条确定性
    `mapping_stack_id × split_role` 分层样本。

投影中每个完整 section 均记录原 deliverable/member 的 byte length 与 SHA-256；唯一
抽样部分被明确标记，完整 verifier manifest 仍由 bundle manifest hash 绑定，并已由
本地独立 validator 全量检查。

由于你没有直接收到原始 ZIP，请把“亲自重算原始 ZIP 外部哈希”标为
`UNDECIDABLE_FROM_UPLOAD_ADAPTER`，不要把本地机器 PASS 冒充你的亲自复核。这不妨碍
你审查 task-disjointness、因果识别、reward 结构、seal 设计及剩余 blocking fields。

## 机器验证事实

- 四个从零构造且 task-disjoint 的任务；
- 2 task pairs × 2 mappings × 2 directions = 8 mapping stacks；
- source 112、target calibration 56、sealed target audit 112；
- 280 prompts × 7 candidates = 1,960 candidate records；
- shared/local static online FPR 均为 `112/672 = 1/6`；
- 两次独立构建的 574 个原生文件逐字节一致；
- 两次最终五件套逐字节一致；
- 独立 oracle 复算、seal/lineage/overlap/hash/ZIP-chain 检查通过；
- 编译通过，完整测试为 `44 passed`；
- 定向篡改、malformed JSON、跨 build report、状态洗白、输出污染、
  非原子发布、provenance 混淆与校验后临时文件变化均被拒绝。

这些是工程验证事实，不是模型结果或科学证据。

## 请重点审计

1. 四个任务是否真的在 oracle、输入 schema、generator path、prompt grammar 与
   seed namespace 上足够 task-disjoint，还是仍存在会伪造迁移的共享捷径。
2. 两个 mappings 与双向 mirrors 是否形成 8 个合理科学单位，还是存在
   pseudoreplication、方向依赖或 codebook 混淆。
3. shared/local 的相同静态 FPR、相同 reward mass 和相同候选暴露，是否足以隔离
   “错误结构”；还缺少哪个必须在模型运行前冻结的混杂量。
4. calibration 与 sealed audit 的边界、lineage、overlap 和 seal 是否足以阻止
   事后选择；任何 audit leakage 是否仍可能发生。
5. G1 的 model-dependent reachability/balance gate 应如何精确定义，才能避免
   用 G1 结果选择有利样本或重新匹配 update norm。
6. 当前 8-stack screen 能否支持最低成本的机制筛查；它不能支持哪些统计或外推结论。
7. 五件套的状态、哈希链、native/final prereg 区分与 fail-closed 发布契约中，
   是否存在会让一次失败或修改被误当成已冻结实验的致命缺陷。

## 强制证据边界

当前必须保持：

```text
human_review_status = PENDING_HUMAN_REVIEW
experiment_status   = NOT_RUN
scientific_evidence = false
run_eligible        = false
G1/G2/core          = NOT_RUN
```

本轮没有运行 tokenizer、GPU、权重加载、forward、backward、gradient、
optimizer、training、RL 或 RLVR；也没有请求你授权这些动作。

## 输出格式

先给且只给以下一个裁决 token：

```text
CPU_ASSET_GATE_ACCEPTED__FREEZE_BLOCKING_DECISIONS_NEXT
REVISE_CPU_ASSETS_BEFORE_HUMAN_REVIEW
STOP_LOW_COST_DESIGN
```

随后依次给出：

1. 最严重缺陷（没有则写 `NONE FOUND AT CPU-ASSET GATE`）；
2. 逐项 `PASS / FAIL / UNDECIDABLE_UNTIL_MODEL_RUN` 审计表；
3. 当前文件已经证明、尚未证明的边界；
4. 若需修改，给出最小补丁与受影响文件；
5. 若接受，列出下一步必须冻结的最小 blocking fields；
6. 下一项最低成本、且不越过当前权限边界的动作。

接受该 gate 只表示 CPU 资产可以进入真人复核和 blocking-field 冻结，不表示
科学 GO，不表示模型实验已获授权。
