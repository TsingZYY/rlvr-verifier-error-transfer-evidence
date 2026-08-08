# 大模型公司 Technical Reports 本地索引

截至 2026-07-30，共 30 条第一方报告记录、13 家机构、1,650 页。每份报告均已核对 PDF 文件头、页数、SHA-256 与可解析性。`P1/P2/P3` 表示与本课题的相关优先级，不代表报告本身的学术质量。

## OpenAI（9）

- **GPT-5.6 Preview System Card**（2026，system card，P1）——相近 aggregate CoT monitorability 下，不同环境组的可监控性方向相反。[[本地 PDF]](00_industry_technical_reports/OpenAI__2026__GPT-5.6_Preview_System_Card__OPENAI-GPT56-SC.pdf) [[官方来源]](https://deploymentsafety.openai.com/gpt-5-6/)
- **Update to GPT-5 System Card: GPT-5.2**（2025，system-card addendum，P1）——生产流量 deception 下降，但 missing-image 与 coding 子任务回归。[[本地 PDF]](00_industry_technical_reports/OpenAI__2025__Update_to_GPT-5_System_Card_GPT-5.2__OPENAI-GPT52-SC.pdf) [[官方来源]](https://openai.com/index/gpt-5-system-card-update-gpt-5-2/)
- **Evaluating Chain-of-Thought Monitorability**（2025，technical report，P1）——跨多类环境评估 CoT、动作与完整轨迹监控，并显示窄域优化压力的影响不均匀。[[本地 PDF]](00_industry_technical_reports/OpenAI__2025__Evaluating_Chain-of-Thought_Monitorability__OPENAI-COT-MONITORABILITY-TR.pdf) [[官方来源]](https://openai.com/index/evaluating-chain-of-thought-monitorability/)
- **Monitoring Reasoning Models for Misbehavior and the Risks of Promoting Obfuscation**（2025，technical report，P1）——记录系统性 unit-test hacks，并比较动作监控与 CoT 监控。[[本地 PDF]](00_industry_technical_reports/OpenAI__2025__Monitoring_Reasoning_Models_for_Misbehavior_and_the_Risks_of_Promoting_Obfuscation__OPENAI-COT-MONITORING-TR.pdf) [[官方来源]](https://openai.com/index/chain-of-thought-monitoring/)
- **OpenAI o3 and o4-mini System Card**（2025，system card，P1）——披露大规模 reasoning RL、工具使用、reward-hack scoring 与 CoT monitoring。[[本地 PDF]](00_industry_technical_reports/OpenAI__2025__OpenAI_o3_and_o4-mini_System_Card__OPENAI-O3-O4-SC.pdf) [[官方来源]](https://openai.com/index/o3-o4-mini-system-card/)
- **Training LLMs for Honesty via Confessions**（2025，technical report，P2）——把主回答 reward 与 confession reward 分开，在多类欺骗/违规任务上测试独立诚实通道。[[本地 PDF]](00_industry_technical_reports/OpenAI__2025__Training_LLMs_for_Honesty_via_Confessions__OPENAI-CONFESSIONS-TR.pdf) [[官方来源]](https://openai.com/index/how-confessions-can-keep-language-models-honest/)
- **GPT-5 System Card**（2025，system card，P2）——提供外部 autonomy、scheming 与 reward-hacking 评估的版本背景。[[本地 PDF]](00_industry_technical_reports/OpenAI__2025__GPT-5_System_Card__OPENAI-GPT5-SC.pdf) [[官方来源]](https://openai.com/index/gpt-5-system-card/)
- **OpenAI o1 System Card**（2024，system card，P1）——记录早期 reasoning model 在网络安全任务中的 reward hacking。[[本地 PDF]](00_industry_technical_reports/OpenAI__2024__OpenAI_o1_System_Card__OPENAI-O1-PREVIEW-SC.pdf) [[官方来源]](https://openai.com/index/learning-to-reason-with-llms/)
- **OpenAI o1 System Card (December 2024)**（2024，system card，P1）——新版评测未观察到相同现象，构成有边界的 checkpoint/evaluation 对照。[[本地 PDF]](00_industry_technical_reports/OpenAI__2024__OpenAI_o1_System_Card_December_2024__OPENAI-O1-SC.pdf) [[官方来源]](https://openai.com/index/openai-o1-system-card/)

## Anthropic（5）

- **Claude Sonnet 4.6 System Card**（2026，system card，P1）——普通 coding reward-hack 水平与更高 GUI computer-use over-eagerness 并存。[[本地 PDF]](00_industry_technical_reports/Anthropic__2026__Claude_Sonnet_4.6_System_Card__ANTHROPIC-SONNET46-SC.pdf) [[官方来源]](https://www.anthropic.com/system-cards)
- **Claude Haiku 4.5 System Card**（2025，system card，P1）——与 Sonnet 4.5 的平均 reward-hacking rate 相近，但更容易 hardcode / special-case tests。[[本地 PDF]](00_industry_technical_reports/Anthropic__2025__Claude_Haiku_4.5_System_Card__ANTHROPIC-HAIKU45-SC.pdf) [[官方来源]](https://www.anthropic.com/system-cards)
- **Claude Sonnet 4.5 System Card**（2025，system card，P1）——更新 reward-hacking stress tests，并提醒评测变化限制跨版本绝对比较。[[本地 PDF]](00_industry_technical_reports/Anthropic__2025__Claude_Sonnet_4.5_System_Card__ANTHROPIC-SONNET45-SC.pdf) [[官方来源]](https://www.anthropic.com/system-cards)
- **Claude 4 System Card**（2025，system card，P1）——报告相对 Claude 3.7 的 reward-hacking 缓解与多套压力测试。[[本地 PDF]](00_industry_technical_reports/Anthropic__2025__Claude_4_System_Card__ANTHROPIC-CLAUDE4-SC.pdf) [[官方来源]](https://www.anthropic.com/news/claude-4)
- **Claude 3.7 Sonnet System Card**（2025，system card，P1）——直接评估 agentic coding reward hacking 与 CoT faithfulness 的局限。[[本地 PDF]](00_industry_technical_reports/Anthropic__2025__Claude_3.7_Sonnet_System_Card__ANTHROPIC-CLAUDE37-SC.pdf) [[官方来源]](https://www.anthropic.com/news/claude-3-7-sonnet)

## NVIDIA（3）

- **Nemotron-Cascade**（2025，technical report，P1）——展示多阶段 RL 的 reward interference、instruction-following 回归与规则 reward 捷径。[[本地 PDF]](00_industry_technical_reports/NVIDIA__2025__Nemotron-Cascade_Scaling_Cascaded_Reinforcement_Learning_for_General-Purpose_Reasoning_Models__NVIDIA-NEMOTRON-CASCADE-TR.pdf) [[官方来源]](https://research.nvidia.com/labs/nemotron/nemotron-cascade/)
- **Llama-Nemotron: Efficient Reasoning Models**（2025，technical report，P1）——披露数学、代码、科学和工具使用上的大规模多域 RL 配方。[[本地 PDF]](00_industry_technical_reports/NVIDIA__2025__Llama-Nemotron_Efficient_Reasoning_Models__NVIDIA-LLAMA-NEMOTRON-TR.pdf) [[官方来源]](https://research.nvidia.com/labs/nemotron/)
- **Nemotron 3 Nano Technical Report**（2025，technical report，P2）——披露 multi-environment RL 与 generative reward model。[[本地 PDF]](00_industry_technical_reports/NVIDIA__2025__Nemotron_3_Nano_Technical_Report__NVIDIA-NEMOTRON3NANO-TR.pdf) [[官方来源]](https://research.nvidia.com/labs/nemotron/Nemotron-3/)

## DeepSeek（2）

- **DeepSeekMath-V2: Towards Self-Verifiable Mathematical Reasoning**（2025，technical report，P1）——直接讨论自然语言证明中的 verifier 高假阳性，并引入 meta-verifier。[[本地 PDF]](00_industry_technical_reports/DeepSeek__2025__DeepSeekMath-V2_Towards_Self-Verifiable_Mathematical_Reasoning__DEEPSEEK-MATHV2-TR.pdf) [[官方来源]](https://github.com/deepseek-ai/DeepSeek-Math-V2)
- **DeepSeek-R1**（2025，technical report，P1）——开放 reasoning RL 配方，使用规则 accuracy/format rewards，并指出 neural reward model 的 reward-hacking 风险。[[本地 PDF]](04_rlvr_generalization_context/2025__DeepSeek-AI__DeepSeek-R1_Incentivizing_Reasoning_Capability_in_LLMs_via_Reinforcement_Learning__2501.12948.pdf) [[官方来源]](https://github.com/deepseek-ai/DeepSeek-R1)

## Alibaba Qwen（2）

- **Qwen3 Technical Report**（2025，technical report，P1）——分阶段 reasoning RL 与 hybrid thinking 配方；verifier 可靠性没有独立审计。[[本地 PDF]](00_industry_technical_reports/Alibaba_Qwen__2025__Qwen3_Technical_Report__QWEN-QWEN3-TR.pdf) [[官方来源]](https://github.com/QwenLM/Qwen3)
- **Qwen2.5-Math Technical Report**（2024，technical report，P1）——数学 reward modeling 与 GRPO，可用于比较 rule/model rewards。[[本地 PDF]](00_industry_technical_reports/Alibaba_Qwen__2024__Qwen2.5-Math_Technical_Report_Toward_Mathematical_Expert_Model_via_Self-Improvement__QWEN-QWEN25MATH-TR.pdf) [[官方来源]](https://github.com/QwenLM/Qwen2.5-Math)

## Moonshot AI（2）

- **Kimi K2: Open Agentic Intelligence**（2025，technical report，P1）——在真实与合成 agent 环境中结合 RLVR 和 self-critique rubric rewards。[[本地 PDF]](00_industry_technical_reports/Moonshot_AI__2025__Kimi_K2_Open_Agentic_Intelligence__MOONSHOT-KIMIK2-TR.pdf) [[官方来源]](https://github.com/MoonshotAI/Kimi-K2)
- **Kimi k1.5: Scaling Reinforcement Learning with LLMs**（2025，technical report，P1）——明确指出可猜答案造成“答案正确、推理错误”的 false-positive verification。[[本地 PDF]](00_industry_technical_reports/Moonshot_AI__2025__Kimi_k1.5_Scaling_Reinforcement_Learning_with_LLMs__MOONSHOT-KIMI15-TR.pdf) [[官方来源]](https://github.com/MoonshotAI/Kimi-k1.5)

## 其他机构（7）

- **Seed-Thinking-v1.5**（ByteDance Seed，2025，technical report，P1）——区分可验证/不可验证 reward，并在人工标注的不稳定样本上对比两种 verifier。[[本地 PDF]](00_industry_technical_reports/ByteDance_Seed__2025__Seed-Thinking-v1.5_Advancing_Superb_Reasoning_Models_with_Reinforcement_Learning__BYTEDANCE-SEED15-TR.pdf) [[官方来源]](https://seed.bytedance.com/en/public_papers/seed-thinking-v1-5-advancing-superb-reasoning-models-with-reinforcement-learning)
- **Phi-4-Reasoning Technical Report**（Microsoft，2025，technical report，P1）——在可验证数学上做 outcome-based RL，并评估训练域外迁移。[[本地 PDF]](00_industry_technical_reports/Microsoft__2025__Phi-4-Reasoning_Technical_Report__MICROSOFT-PHI4R-TR.pdf) [[官方来源]](https://www.microsoft.com/en-us/research/publication/phi-4-reasoning-technical-report/)
- **Gemini 2.5 Technical Report**（Google DeepMind，2025，technical report，P2）——确认通过 RL 训练 thinking，并报告 decontamination；verifier error 细节有限。[[本地 PDF]](00_industry_technical_reports/Google_DeepMind__2025__Gemini_2.5_Technical_Report__GOOGLE-GEMINI25-TR.pdf) [[官方来源]](https://deepmind.google/models/gemini/)
- **MiniMax-M1**（MiniMax，2025，technical report，P1）——披露 CISPO 与大规模 reasoning RL。[[本地 PDF]](00_industry_technical_reports/MiniMax__2025__MiniMax-M1_Scaling_Test-Time_Compute_Efficiently_with_Lightning_Attention__MINIMAX-M1-TR.pdf) [[官方来源]](https://www.minimax.io/news/minimaxm1)
- **Magistral**（Mistral AI，2025，technical report，P2）——给出 reasoning RL pipeline，但 verifier-error 分析有限。[[本地 PDF]](00_industry_technical_reports/Mistral_AI__2025__Magistral__MISTRAL-MAGISTRAL-TR.pdf) [[官方来源]](https://mistral.ai/news/magistral)
- **Tulu 3**（Ai2，2024，technical report，P1）——开放端到端 post-training 配方，作为闭源公司自报结果之外的控制证据。[[本地 PDF]](04_rlvr_generalization_context/2024__Lambert__Tulu_3_Pushing_Frontiers_in_Open_Language_Model_Post-Training__2411.15124.pdf) [[官方来源]](https://allenai.org/blog/tulu-3-technical)
- **The Llama 3 Herd of Models**（Meta，2024，technical report，P3）——详细 post-training 与 reward-modeling 背景，但不是 verifier-error 审计。[[本地 PDF]](00_industry_technical_reports/Meta__2024__The_Llama_3_Herd_of_Models__META-LLAMA3-TR.pdf) [[官方来源]](https://ai.meta.com/research/publications/the-llama-3-herd-of-models/)

## 机器可读入口

- `technical_reports_sources.csv`：冻结的下载源与相关性说明。
- `technical_reports_manifest.csv`：最终本地路径、状态、字节数和 SHA-256。
- `technical_reports_validation.csv`：逐份 PDF 完整性结果。
- `technical_reports_validation_summary.json`：总量与公司分布。
- `technical_reports_keyword_audit.csv`：全文关键词命中审计；它只用于发现证据位置，不把词频当作研究结论。
