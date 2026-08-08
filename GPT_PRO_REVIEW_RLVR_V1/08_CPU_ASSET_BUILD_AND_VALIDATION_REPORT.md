# CPU-only 最低成本验证资产：构造与验证报告

日期：2026-07-30  
阶段：`PRE_REVIEW`  
实验状态：`NOT_RUN`  
科学证据：`false`  
可运行状态：`run_eligible=false`  
人工逐行复核：`PENDING_HUMAN_REVIEW`

## 结论

已完成 GPT Pro 上一轮裁决要求的 CPU-only 五件套，并通过两次独立构建、独立 oracle
复算、原生资产验证、篡改测试、发布链回读和打包器对抗测试。

这只证明实验输入、Verifier 几何和冻结机制已经被机器一致地构造；它不证明
“shared verifier error 导致更高跨任务迁移风险”，也不构成 G1、G2 或核心因果命题的
任何结果。

## 构造覆盖

- 四个从零构造、task-disjoint 的任务：
  `MOD7_SUM_V1`、`DFA7_FINAL_V1`、`MARKED_RANK7_V1`、
  `PAREN_MAX_DEPTH7_V1`。
- 两个 task pair、两个独立 mapping、双向 mirror，共 8 个 mapping stacks。
- 112 条 source rows、56 条 target calibration rows、112 条 sealed target audit
  rows，共 280 prompts。
- 每个 prompt 固定 7 个候选，共 1,960 条 candidate records。
- shared 与 local arm 均为每个 prompt 接受一个错误候选：
  `112 / 672 = 1/6`，候选暴露、reward-vector multiset 和正奖励质量一致。
- target audit seal：
  - 字节数：457,076
  - SHA-256：
    `7ae1a5e80ce40b29195328311e1fa097423d331d2b96c7e3101c0231021df300`
  - 112 个 semantic row hashes
  - 112 个 exact-LF line hashes
  - 112-step chain

## 独立验证

原生 validator 不导入 builder 或任务实现，独立复算四个任务 oracle，并检查：

- task、split、stack、row ID、seed、prompt bytes 与 lineage 零交集约束；
- 七类候选、canonical serialization、reward contract 与精确 FPR；
- audit seal、manifest、代码哈希和协议哈希闭合；
- 所有模型、参数更新、selection adjustment 与科学状态均保持禁用或
  `NOT_RUN`；
- malformed JSON 与四类定向篡改均 fail-closed。

最终测试：

```text
python compile: PASS
pytest: 44 passed in 53.77s
native validation build_v4_a: valid=true, error_count=0
native validation build_v4_b: valid=true, error_count=0
```

两次原生构建均为 574 个文件、4,937,095 bytes，逐字节差异数为 0。两次最终发布的
五件套也逐字节一致，差异数为 0。

## 发布链与打包器审计

早期 `release_v1_*`、`release_v2_a`、`release_v3_a` 和 `release_v4_*` 不是交付物。
对抗审计曾发现并修复以下发布层缺陷：

1. 跨 build validation report 可能被误接受；
2. 危险状态可能被浅层状态检查遗漏；
3. 输出目录与 build root 重叠可能污染输入；
4. 失败发布可能遗留部分五件套；
5. native prereg/build manifest 与 final prereg 的身份可能混淆。
6. 校验后、封包前临时替换文件并恢复，可绕过首尾 hash snapshot；
7. ZIP member path `.` 未被拒绝。

最终打包器另行实施严格 JSON、路径、symlink/junction、完整目录快照、工具字节快照、
确定性 ZIP、ZIP 回读、精确五文件清单、sibling staging 和原子发布检查。所有原生
输入和工具代码现在只捕获一次；后续状态检查、manifest/hash、JSON/JSONL、成员收集、
provenance 和封包均消费同一冻结字节快照。新增并发文件变化回归与完整打包测试为
`28 passed`，发现缺陷的独立复核代理再次确认 `28 passed, 0 failed`。

最终回读结果：

```text
REAL_SOURCE_STACK_BUNDLE_V1.zip members: 232
VERIFIER_G1_CPU_BUNDLE_V1.zip members: 9
TARGET_G2_REAL_BUNDLE_V1.zip members: 345
manifest member hashes: PASS
protocol hash bindings: PASS
prior-bundle hash chain: PASS
native provenance bindings: PASS
ZIP metadata and deterministic ordering: PASS
release_v5_a vs release_v5_b: difference_count=0
```

## 最终五件套

唯一用于 GPT Pro 审核的主副本是 `real_assets/release_v5_a`：

| 文件 | bytes | SHA-256 |
|---|---:|---|
| `P4_R1_REAL_PROTOCOL_V2_PRE_REVIEW.json` | 19,696 | `ad2d018fb74b1b7a4800e79c293bf6648cb4644c80e7d4406762d9cdbb49ca1b` |
| `REAL_SOURCE_STACK_BUNDLE_V1.zip` | 1,296,012 | `e322675a405fcce9e37804e9c6c4592b6c03171cf71f5d068acbe8c8859d83eb` |
| `VERIFIER_G1_CPU_BUNDLE_V1.zip` | 2,285,081 | `52e2cf3cb0603ba7c78224f309a4cb305ac81b21766f9378aac440fcaba5783c` |
| `TARGET_G2_REAL_BUNDLE_V1.zip` | 1,809,833 | `3017292380eb6344b0beb74358f7217d3bafa6ad9d95c0c72de0b82c44ec0ae4` |
| `RANDOMIZATION_MODEL_ENV_PREREG_V1.json` | 5,611 | `5d260acf548804abb150ee8425470d2145218d47d3b6427f0319b29edbb72bae` |

`release_v5_b` 是独立重建的复现副本，不另作科学重复。

## 仍未验证

- 人工逐行复核尚未完成；
- 模型、权重、tokenizer、运行环境、随机化与一步更新方案尚未冻结；
- base logits、错误目标可达性、相对优势、pre/post log probabilities 尚未测量；
- `L`、`D`、`tau` 尚未计算；
- G1、G2、core/scientific GO-STOP 均为 `NOT_RUN`。

下一步只提交 GPT Pro 做资产门审核。即使 GPT Pro 接受，也只意味着可以冻结剩余
blocking fields 并安排真人复核；任何 tokenizer、权重加载、forward、backward、
optimizer、training、RL 或 RLVR 仍需单独、明确授权。
