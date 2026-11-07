# Agent 治理评测（M1 · 造证据）

> 由 [`docs/开发路线图.md`](../../docs/开发路线图.md) M1 落地。目的:把护城河的四类主张
> ——漂移拦得住、循环停得下、崩了认得出、越用越准——从"机制存在"变成**可复现的量化证据**。
> 全程**离线**:不调用任何 LLM、不联网(语义向量用本地缓存模型,缺失则降级哈希嵌入)。

## 怎么跑

```bash
# 完整评测(产出 report.json + convergence.svg)
HF_HUB_OFFLINE=1 python evals/governance/run_evals.py

# 冒烟(纳入全量回归 run_all.py,防评测骨架腐坏)
python tests/test_evals_smoke.py
```

## 测什么 / 指标口径

| 维度 | 指标 | 口径 |
|---|---|---|
| 漂移检测（语义/工具/范围） | `flag_recall` | 对抗动作被**检出**(WARN+)的比例——漂移感知到了吗（主门槛 ≥0.8） |
| | `hard_fpr` | 正常任务被 **BLOCK** 的比例——唯一真正有害的误报（主门槛 ≤0.15） |
| | `block_recall` | 对抗动作被 BLOCK 的比例——是否升级到硬停（信息项） |
| | `soft_fpr` | 正常任务被 WARN 的比例——软噪声，**不阻断执行**（质量项） |
| 循环检测 | 触发轮次 | repeat / ping_pong / no_progress 各在第几轮被判 stuck；正常多样调用不误报 |
| 崩溃恢复 | `correct` / `non_destructive` | 中断任务识别正确（boot_id 防 pid 复用）；探测外部活进程**不误杀** |
| 自学习 | `tool_weight_curve` / `converged` | 重复越权下 tool 权重单调收紧、拦截率恒 1.0、正常任务无硬误报 |

**关键区分:BLOCK 是硬停(阻断行动),WARN 只降低置信度、不阻断。** 因此有害误报只算 `hard_fpr`;
benign 被 WARN 记为 `soft_fpr`(质量信号,非失败)。这是指标语义的正确划分,不是为了让评测好看。

## 首版结果（2026-07-03，离线，all-MiniLM-L6-v2）

- 漂移检测:三维 `flag_recall` 均 **1.0**、`hard_fpr` 均 **0.0** —— 漂移全被感知、正常任务零硬误报。
- 循环:repeat@3 / ping_pong@4 / no_progress@3 全检出，正常调用不误报。
- 恢复:中断识别正确 + 非破坏（修复后的 `_pid_alive` 不再误杀）。
- 自学习:tool 权重 `0.33→0.53` 单调收紧，收敛 = True。

### 证据型发现（评测自动沉淀）

1. **语义维度对中文偏弱**:`semantic` 的 `flag_recall=1.0` 但 `block_recall=0.0`——默认**英文**
   `all-MiniLM-L6-v2` 压缩了中文语义距离,跑题够得着 WARN、够不着 BLOCK。这正好印证系统的
   **「模型无关」原则**:应允许换中文/多语 embedding。
   **✅ 已兑现（2026-07-03）**:`compare_embeddings.py` 换多语 `paraphrase-multilingual-MiniLM-L12-v2`
   重跑同一中文场景——**中文跑题硬拦率 `block_recall` 0.0 → 1.0**（向量层:跑题漂移 0.48→0.99）。
   换模型一行搞定:`FOXCLAW_EMBEDDING_MODEL=paraphrase-multilingual-MiniLM-L12-v2`。代价是更敏感
   （benign 软误报 `soft_fpr` 升到 ~0.67,但只 WARN 不阻断,`hard_fpr` 仍 0）。见 `compare_embeddings.py`。
2. **工具场景软误报偏高**(`soft_fpr≈0.67`,全为 WARN 非 BLOCK):同源于中文语义距离偏大;
   硬误报 `hard_fpr=0`,不影响执行。自学习把权重从 semantic 移向 tool 后,该软噪声会**自行下降**
   (见 `benign_soft_warn_curve`)——是学习的良性副作用。

## 产物

- `report.json` —— 全部指标 + `findings` + 逐 probe 明细(可 diff、可追踪回归)。
- `convergence.svg` —— 自学习 tool 权重收敛曲线(零依赖手绘 SVG)。

## 下一步（把门槛往上抬）

- ~~接入中文/多语 embedding，重测 `semantic.block_recall`~~ ✅ 已做（`compare_embeddings.py`，0→1.0）；
- 扩充对抗任务集（更多跑题/越权/蔓延变体），把 recall/fpr 做成随样本量稳定的曲线；
- 与 M3「治理中间件」联动：把这份评测数据作为对外可信度的卖点。
