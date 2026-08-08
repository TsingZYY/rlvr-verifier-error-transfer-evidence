# R10 Latent-vs-Surface Diagnostic 独立审计 R1

日期：2026-08-05  
结论：**数值与代数结论成立；原实现存在 fail-closed 输入绑定缺口，已在 analyzer/tests 中加固。** 本审计只读取既有 R10 JSON；没有 tokenizer/model/forward/gradient/optimizer 或新 RL/RLVR 行为。

## 1. 结论

- `q_surface(r)=a_t^{-1}a_s r mod 7` 正确。若 source/target affine codebook 分别为 `K(a_s z+b_s)` 与 `K(a_t z+b_t)`，同一相对 gold 的 surface displacement 要求 `a_t q=a_s r (mod 7)`；intercept 在 displacement 中抵消。
- `q=6` 的重建正确：每个 trace 存有七个按 `FINAL=K0..K6` 排序的 raw candidate scores；结合被绑定的 target codebook 和 `canonical_z`，可直接读取 `(z+6) mod 7` 候选，未做外推。
- 5×6 双中心化实现正确：每行、每列 residual mean 均为 0，并且对任意 additive row/column nuisance 不变。
- A/B gate 正确覆盖完整 5×6 matrix，包括仅发生在 q6 的差异；现有 8 栈 A/B 最大绝对 effect-matrix difference 均为 `0.0`。
- `4/8 M0 fail` 正确：四个 M0 全部 `L<=1e-10`，四个 M1 全部 `L>1e-10`。独立实现重新读取 raw traces 后得到与 RESULT_R1 完全相同的八个 `L` 和失败集合。
- 因此，统一的“latent alignment 在全部八栈击败 surface competitor”已被现有 development data 否定，不能只报告 M1。保守 stop label 正确；它表示这一 competitor 未被击败，不表示 surface mechanism 已获因果证明。

## 2. 独立数值复核

| Stack | raw latent-surface | double-centered `L` | Gate |
|---|---:|---:|---|
| TP1-M0-A_TO_B | -0.007610048567 | -0.014388888223 | FAIL |
| TP1-M0-B_TO_A | -0.011420086452 | -0.006253234318 | FAIL |
| TP1-M1-A_TO_B | 0.027235255923 | 0.023152602060 | PASS |
| TP1-M1-B_TO_A | 0.017133644650 | 0.013296364376 | PASS |
| TP2-M0-A_TO_B | -0.000404970987 | -0.001189945766 | FAIL |
| TP2-M0-B_TO_A | -0.014648682731 | -0.012138143267 | FAIL |
| TP2-M1-A_TO_B | 0.015546607971 | 0.022433220999 | PASS |
| TP2-M1-B_TO_A | -0.004946681431 | 0.002734612056 | PASS |

四个 M0 在 raw contrast 下也全部为负，所以 M0 failure 不是双中心化制造的。`TP2-M1-B_TO_A` 的 raw contrast 为负、双中心化后为正，说明 M1 的“4/4 pass”严格依赖预注册的 interaction-residual estimand；不能把它改写成未经中心化也一致。

## 3. 修复的实现缺口

原 numeric core 没有发现错误，但原 analyzer 只检查 protocol schema，并会把重复 mapping stack 静默压成一个 dict entry；也没有强制每个 R10 result 声明的 mapping/target SHA 与当前输入一致。q6 又依赖外部 target codebook，因此这些属于证据绑定缺口。

已加固：

1. 验证 frozen protocol 的 status、identity/stack/replicate coverage、surface formula、contrast、tolerances、authorization boundary。
2. mapping stacks 必须精确 8 个且无重复；source/target codebook 都必须是 Z7 candidate permutation。
3. target JSONL 必须精确 `8×7=56` 个 `TARGET_CALIBRATION` rows、全局 row id 唯一、每栈七行、codebook 与 mapping stack 完全一致、gold 与 canonical z 一致。
4. 每个 R10 result 必须匹配 frozen result schema、runner SHA、mapping SHA、target SHA，并保持 non-formal/non-scientific boundary。
5. PRE/POST trace 的 state、source identity、initial/update hash 必须一致；evaluation cells 也必须绑定正确 update hash。
6. 测试从 5 个增至 11 个，新增 q6、additive nuisance invariance、q6-only A/B mismatch rejection、protocol mutation rejection、duplicate mapping rejection、result mapping-hash mutation rejection 和精确四-M0失败集合。

本次独立运行：`11/11 PASS`。加固没有改变八个 `L` 或最终 stop decision。既有 `R10_LATENT_VS_SURFACE_DIAGNOSTIC_RESULT_R1.json` 保留为原冻结产物，没有覆盖或重写。

## 4. 证据边界

这仍是看过 R10 outcomes 后冻结的 model-free post-hoc development diagnostic。两个 task pairs 才是顶层 cases；8 stacks、16 result files、五 source identities、六 target identities和 A/B 都不增加科学样本量。不得给 inferential p-value、population generalization、same empirical/policy FPR、sampled RLVR 或 semantic sharedness claim。

idea-evaluator 的 data-refuted gate 在这里适用：**“八栈统一 latent 优势”这一版本不应继续作为 headline mechanism。** 合理 pivot 是直接干预 target alignment，而不是在 reward arm 上重新标记同一组 source updates。

## 5. 下一步非冗余实验

已起草 `R13_TARGET_ALIGNMENT_PILOT_PROTOCOL_DRAFT_R1.json`：覆盖全部四个失败的 M0 stacks；每个 `(stack,r,replicate)` 只执行一次 target-blind source update，然后把同一 parameter hash 依次用于两套 matched target panels。H0 使用原 M0 target codebook；H1 改变 target multiplier，使 surface location 切换到 `q=3r`。主 estimand `F` 是 target-arm × old/new surface-location interaction。

最低 A-only 是 4 processes / 20 updates / 240 post target-identity cells；A/B 是 8 processes / 40 executions / 480 post cells。R13 当前 `run_eligible=false`，不授权任何模型动作。
