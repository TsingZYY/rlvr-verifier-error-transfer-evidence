# GPT Pro 最低成本验证裁决

日期：2026-07-30  
阶段：开跑前否决式审核  
科学状态：`NOT_RUN`

## 1. 单一裁决

`BUILD_REAL_G1_G2_ASSETS_FIRST`

当前不运行 one-stack method pilot，也不停止研究方向。

否决开跑的直接理由：

- 当前 fixture 明确是 `synthetic_contract_only=true`、`scientific_evidence=false`；
- 它只证明 8-stack / 五臂 schema、hash、mirror 和 fail-closed 工程合同；
- 它没有真实 FPR、accepted-wrong reward mass、reachability、relative advantage、exposure distribution、verifier reward vectors、真实 target rows、模型哈希或 estimator 输出；
- 旧 arithmetic smoke 最多重复证明旧 runner 可以 forward、backward 和更新，不能增加关于新 sharedness estimand 的信息。

因此：

```text
Synthetic fixture scientific-run eligibility: NO
Old arithmetic smoke authorization:          NO
Experiment status:                           NOT_RUN
```

这不是 `STOP_DIRECTION`：目前没有反驳核心假设的实验结果；阻塞项是真实资产和 estimand 冻结缺失。

## 2. Primary estimand 的最终裁决

对 mapping stack \(s\)、mapping \(m\in\{\mathrm{shared},\mathrm{local}\}\)，在 sealed panel 上定义：

\[
L_{s,m,t}
=
\frac{1}{N_s}\sum_{i=1}^{N_s}
\left[
\log p_{\theta_t}(b_{s,m,i}\mid x_{s,i})
-
\log p_{\theta_t}(g_{s,i}\mid x_{s,i})
\right].
\]

leaky arm 的 pre/post change：

\[
\Delta^{\mathrm{leaky}}_{s,m}
=
L^{\mathrm{leaky}}_{s,m,\mathrm{post}}
-
L^{\mathrm{leaky}}_{s,m,\mathrm{pre}}.
\]

每个 mapping stack 的 primary paired contrast：

\[
\tau_s
=
\Delta^{\mathrm{leaky}}_{s,\mathrm{shared}}
-
\Delta^{\mathrm{leaky}}_{s,\mathrm{local}}.
\]

P3 的 GO / STOP / INCONCLUSIVE 规则只能作用于 stack-level \(\tau_s\)，不能把 LoRA seeds 当独立统计单位。

## 3. Frozen control 的正确角色

原 `frozen_selection_only` 不能解释为 selection-exposure control，也不能声称已经扣除了 selection exposure。它只能作为：

`frozen_measurement_control`

建议真实协议使用：

- `shared_frozen_probe_control`
- `local_frozen_probe_control`

并明确：

```json
{
  "control_role": "frozen_measurement_control",
  "selection_exposure_adjustment": false
}
```

运行有效性 gate：

\[
D_s
=
\max_{m\in\{\mathrm{shared},\mathrm{local}\}}
\left|
L^{\mathrm{frozen}}_{s,m,\mathrm{post}}
-
L^{\mathrm{frozen}}_{s,m,\mathrm{pre}}
\right|.
\]

必须在参数更新前冻结 \(\epsilon_{\mathrm{replay}}\)，并要求：

\[
D_s \le \epsilon_{\mathrm{replay}}.
\]

若不满足，则该 stack / run 无效，不得计算科学 verdict。Frozen controls 不进入 \(\tau_s\) 的减法。

当前设计能支持的表述：

- general sharedness update-effect estimand

当前设计不能支持的表述：

- selection-adjusted effect
- selection-exposure causal decomposition
- semantic-specific transfer

## 4. 运行权限

| 动作 | 授权 |
|---|---:|
| 构造真实协议、rows、manifests、seal receipt | YES |
| CPU-only schema/hash/lineage/overlap/randomization 静态检查 | YES |
| CPU-only verifier 在冻结且人工标注的候选表上生成 reward vectors/FPR | YES |
| 使用 synthetic fixture 做科学 pilot | NO |
| 重跑旧 arithmetic smoke | NO |
| GPU | NO |
| 加载模型权重 | NO |
| Model forward | NO |
| Backward / gradient | NO |
| One-step optimizer update | NO |
| Training / RL / RLVR | NO |
| Core gate 或 GO/STOP 科学决策 | NO |

Tokenizer-only 检查也需先冻结 exact tokenizer hash、版本和输入 rows，再单独审核。

## 5. 开跑前必须构造的八项真实资产

1. `P4_R1_REAL_PROTOCOL_V1.json`
2. `REAL_MAPPING_STACKS_V1.jsonl`
3. `REAL_SOURCE_BUNDLES_V1.jsonl` 及原始字节文件
4. `VERIFIER_REWARD_MANIFEST_V1.jsonl`
5. `G1_OPPORTUNITY_AUDIT_V1.json`
6. `TARGET_CALIBRATION_REAL_V1.jsonl`
7. `TARGET_AUDIT_REAL_V1.jsonl` 与 `TARGET_AUDIT_SEAL_RECEIPT.json`
8. `RANDOMIZATION_MODEL_ENV_PREREG_V1.json`

这些资产必须真实绑定任务、候选、verifier、reward vector、lineage、随机化、模型/代码哈希和 sealed target；不能把 synthetic 字节或自声明布尔值改名后当作真实证据。

## 6. 当前结论

```text
Pre-run verdict:   BUILD_REAL_G1_G2_ASSETS_FIRST
Experiment status: NOT_RUN
G1:                NOT_RUN
G2:                NOT_RUN
Core gate:         NOT_RUN
```

最低成本验证已经完成的是“能否值得开跑”的否决式审查，而不是模型实验。结果为：现在开跑不值；先构造真实 G1/G2 资产，再复审。
