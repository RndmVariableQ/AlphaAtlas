# 实验记录

## 实验总览

已完成期货 AlphaPROBE、MCTS-LLM、ReAct 与 AlphaAtlas+Annealing V1 Async 的
fold1 OOS 评估。
仓库目前没有美股数据适配或美股实验记录。

结果表按研究模型分组：DeepSeek、Haiku 5.5、GPT6-luna；Fold1 另列 GPT5.5。
同一模型内依次排列 AlphaAtlas、AlphaPROBE、MCTS-LLM、ReAct、AlphaSchema，各独立运行的 500／1000 次结果相邻。

---

## 数据

### 期货（原生 5m，既有实验）

- **来源 / 频率**：RiceQuant 原生 5 分钟数据；使用主力真实合约，按品种汇总。
- **请求区间**：2015-01-01 至 2026-09-01（含端点）；记录的实际交易日覆盖为
  2015-01-05 至 2026-09-01。
- **实验快照**：`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。

### 期货 Research Kernel 四字段

- **来源 / 频率**：ChaoticTrader_Future research kernel 的 RiceQuant final 主力真实合约
  1m 数据，按此前指定口径合并为 5m；使用 kernel 已有的换月累计比例复权价格。
- **字段 / 目标**：`adj_close`、`adj_vwap`、`volume`、`amount`；目标为未来 12 根基础
  5m 的 `adj_close` 对数收益，因子观测频率可用 5m、15m、30m、60m、1d。
- **实际覆盖**：2012-01-04 至 2026-09-28，13,559,126 行、97 个品种；fold1 预热从
  2015-01-01 开始，搜索与测试只读取各自允许的日期范围。
- **运行记录的 MarketData 快照**：
  `8f689ef3680e43b4f8f1c21f92bd21ad5f4c6a308e19ad31530f60bda4b4b49a`。
- **比较范围**：AlphaPROBE 的 deepseek-flash 500、1000 次结果、V1 Async 和 ReAct 四字段结果
  使用该复权派生快照；ReAct 八字段使用下方独立快照，其余期货实验使用上方原生 5m 数据；
  不同快照上的实验差异不能单独归因于方法或语言模型。
  聚合、缺失与来源详见
  [数据记录](docs/records/data.md#2026-10-19-research-kernel-复权五分钟导出)。

### 期货 Research Kernel 完整字段

- **资产 / 目录**：`futures_kernel_5m_full` / `data/futures_kernel_5m_full/`。
- **字段 / 目标**：`adj_open`、`adj_high`、`adj_low`、`adj_close`、`adj_vwap`、`volume`、
  `amount`、`open_interest`；目标仍为未来 12 根基础 5m 的 `adj_close` 对数收益。
- **实际覆盖**：2012-01-04 至 2026-09-28，13,559,126 行、97 个品种，177 个月度分区。
- **MarketData 快照**：
  `73db06c2d7223ce5829b8d3d9846a27b35e59e0d621a11a4a94d99dcc6ec0e87`。
- **使用范围**：ReAct 八字段的 500 次结果使用此快照；AlphaPROBE、V1 Async 和 ReAct 四字段
  的 Kernel 结果使用上方四字段快照。
  聚合、缺失与审计见[完整字段记录](docs/records/data.md#2026-10-23-research-kernel-完整字段导出)。

### A 股

- **来源 / 频率**：RiceQuant 原生日线及 PIT 基本面；union1800 是 HS300、ZZ500、ZZ1000
  历史成员的逐日并集。
- **请求区间**：2015-01-01 至 2026-09-01（含端点）；记录的实际交易日覆盖为
  2015-01-05 至 2026-09-01。
- **实验快照**：`abfc6d04d953633b82f844406443a774fb45ced8a6d301a2837084a6849c6710`。
- **基本面时点**：财务数据延后一交易日使用；历史修订限制见[数据快照说明](docs/records/data.md)。

### 美股

尚未确定数据来源、频率和日期范围；仓库没有对应的数据配置或快照。

---

## 已完成实验

### 期货因子搜索

**Fold / seed**：fold1 / 42。

- **目标**：预测未来 12 根 5 分钟 Bar 的对数收益。
- **搜索设置**：开放 `futures_cta` 参考库，首轮不预置因子，seed 42；各组尝试次数见表。
- **Fold1**：train 2016-01-01–2018-12-31，val 2019-01-01–2020-12-31，test
  2021-01-01–2022-12-31。
- **结果汇总日期**：gpt-5.5 版 AlphaPROBE / MCTS-LLM 为 2026-09-18；MCTS + DeepSeek
  500 次为 2026-10-10、1000 次为 2026-10-15；AlphaPROBE + DeepSeek 1000 次为 2026-10-16；
  Kernel V1 Async + DeepSeek 500 次为 2026-10-21，1000 次为 2026-10-22；
  Kernel AlphaPROBE + DeepSeek 500 次为 2026-10-22，1000 次为 2026-10-23；
  Kernel 四字段与八字段 ReAct + DeepSeek 500 次均为 2026-10-23，四字段 1000 次为 2026-10-24。

各实验设置：

- **AlphaPROBE**：gpt-5.5 为 500 次，deepseek-flash 为 500、1000 次；
  embedding 模型均为 Qwen3-Embedding-0.6B。
- **MCTS-LLM**：gpt-5.5 为 500 次，deepseek-flash 为 500、1000 次。
- **ReAct**：AgentScope 单 Agent，deepseek-flash；四字段和八字段分别预设 1000 次预算，
  在 500 次暂停并评估当时完整的已准入因子库。四字段 1000 次保留前 500 次的 65 个成员，
  在第 500 次清空模型上下文后继续搜索；搜索与测试沿用未裁剪口径。
- **AlphaAtlas+Annealing V1 Async**：deepseek-flash；最多并行 3 个研究计划，
  每计划最多 5 个候选；Explorer 对话上限 40 轮，三个 Agent 均可调用共同的五项只读研究工具。

| 实验人 | 方法 | 数据 | 模型 | 尝试次数 | TEST 单因子加权 Pearson（均值 / 中位数） | 正 IC | 单因子加权 Spearman 均值 | Linear 加权 Pearson | LightGBM 加权 Pearson |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 汤子逸 | AlphaAtlas+Annealing V1 Async | Kernel 四字段 | deepseek-flash | 500 | 0.003563 / 0.003538 | 52 / 64（81.25%） | 0.002211 | 0.013123 | 0.015892 |
| 汤子逸 | AlphaAtlas+Annealing V1 Async | Kernel 四字段 | deepseek-flash | 1000 | 0.004134 / 0.004646 | 147 / 166（88.55%） | 0.004777 | 0.016481 | 0.019924 |
| 汤子逸 | AlphaPROBE | 原生 5m | deepseek-flash | 1000 | 0.002365 / 0.002410 | 51 / 66（77.3%） | 0.000062 | 0.012144 | 0.020982 |
| 汤子逸 | AlphaPROBE | Kernel 四字段 | deepseek-flash | 500 | 0.002922 / 0.003593 | 38 / 48（79.17%） | 0.002988 | 0.011288 | 0.016138 |
| 汤子逸 | AlphaPROBE | Kernel 四字段 | deepseek-flash | 1000 | 0.002679 / 0.003072 | 60 / 78（76.92%） | 0.002439 | 0.014138 | 0.015396 |
| 汤子逸 | MCTS-LLM | 原生 5m | deepseek-flash | 500 | 0.002512 / 0.001987 | 26 / 35（74.3%） | 0.000301 | 0.007910 | 0.008754 |
| 汤子逸 | MCTS-LLM | 原生 5m | deepseek-flash | 1000 | 0.001567 / 0.001223 | 38 / 60（63.3%） | 0.000212 | 0.004354 | 0.009233 |
| 汤子逸 | [ReAct](artifacts/runs/futures_kernel_5m-all-fold1-react-42-145195d3de-prefix500/report.md) | Kernel 四字段 | deepseek-flash | 500 | 0.002494 / 0.001603 | 44 / 65，67.69% | 0.001632 | 0.012126 | 0.019378 |
| 汤子逸 | [ReAct](artifacts/runs/futures_kernel_5m-all-fold1-react-42-6990ca0c5f/report.md) | Kernel 四字段 | deepseek-flash | 1000 | 0.001763 / 0.001166 | 88 / 136，64.71% | 0.000621 | 0.012771 | 0.021948 |
| 汤子逸 | [ReAct](artifacts/runs/futures_kernel_5m_full-all-fold1-react-42-af44aa3bd0-prefix500/report.md) | Kernel 八字段 | deepseek-flash | 500 | 0.004672 / 0.005051 | 53 / 68（77.94%） | 0.001008 | 0.008176 | 0.015305 |
| 汤子逸 | AlphaPROBE | 原生 5m | gpt-5.5 | 500 | 0.002393 / 0.001559 | 27 / 37（73.0%） | 0.003421 | 0.006413 | 0.011228 |
| 汤子逸 | MCTS-LLM | 原生 5m | gpt-5.5 | 500 | 0.002785 / 0.003165 | 32 / 44（72.7%） | 0.002229 | 0.008493 | 0.017879 |

单因子沿用训练方向。已评估的下游模型使用冻结因子库在 train+val 拟合，LightGBM 固定 50 轮。
ReAct 四字段 500 次行对应运行 `futures_kernel_5m-all-fold1-react-42-145195d3de` 的
[500 次评估快照](artifacts/runs/futures_kernel_5m-all-fold1-react-42-145195d3de-prefix500/report.md)。
1000 次行对应 `futures_kernel_5m-all-fold1-react-42-6990ca0c5f`，
见[单因子报告](artifacts/runs/futures_kernel_5m-all-fold1-react-42-6990ca0c5f/report.md)和
[模型报告](artifacts/runs/futures_kernel_5m-all-fold1-react-42-6990ca0c5f/model/report.md)。
与首轮四字段 `ac32f1e3dc` 的结果见[复跑对照](artifacts/comparisons/react_kernel_four_repeat_145195d3de/results_comparison.html)。


### 期货库内因子相似度：统一 product 窗口

**Fold / seed**：fold1 / 42。

截至 2026-11-02，下表 13 组结果的相似度均按 **product 窗口**，
在 **Val 2019–2020** 上评估。
每对因子取共同有限观测重新排名，计算绝对 Spearman；全部不同因子对等权平均，排除自身相关，
不重复计数、不抽样、不填补缺失。此处沿用库内去重的 pooled 行口径，不使用品种成交额权重。
Kernel 四字段的 AlphaPROBE、V1 Async 与 ReAct 使用四字段 Research Kernel 复权派生快照，
ReAct 八字段使用八字段 Kernel 快照；
原生 5m 各组使用对应快照；
不同快照间的数值差异不能作为同数据条件下的方法排名。

| 方法 / 模型 | 数据 | 尝试次数 | 因子数 | 因子对数 | 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AlphaAtlas+Annealing V1 Async / deepseek-flash | Kernel 四字段 | 500 | 64 | 2016 | 0.115394 | 0.072774 | 0.288826 | 0.695866 | 0 |
| AlphaAtlas+Annealing V1 Async / deepseek-flash | Kernel 四字段 | 1000 | 166 | 13695 | 0.127824 | 0.073150 | 0.332904 | 0.695866 | 0 |
| AlphaPROBE / deepseek-flash | 原生 5m | 1000 | 66 | 2145 | 0.109710 | 0.053192 | 0.294732 | 0.695848 | 0 |
| AlphaPROBE / deepseek-flash | Kernel 四字段 | 500 | 48 | 1128 | 0.155238 | 0.096048 | 0.418874 | 0.699248 | 0 |
| AlphaPROBE / deepseek-flash | Kernel 四字段 | 1000 | 78 | 3003 | 0.149145 | 0.091602 | 0.397867 | 0.699248 | 0 |
| MCTS-LLM / deepseek-flash | 原生 5m | 500 | 35 | 595 | 0.184539 | 0.131507 | 0.431388 | 0.699873 | 0 |
| MCTS-LLM / deepseek-flash | 原生 5m | 1000 | 60 | 1770 | 0.131032 | 0.069709 | 0.355669 | 0.699873 | 0 |
| [ReAct](artifacts/runs/futures_kernel_5m-all-fold1-react-42-145195d3de-prefix500/report.md) / deepseek-flash | Kernel 四字段 | 500 | 65 | 2080 | 0.208221 | 0.170754 | 0.463688 | 0.695703 | 0 |
| [ReAct](artifacts/runs/futures_kernel_5m-all-fold1-react-42-6990ca0c5f/report.md) / deepseek-flash | Kernel 四字段 | 1000 | 136 | 9180 | 0.179021 | 0.140757 | 0.411079 | 0.695703 | 0 |
| [ReAct](artifacts/runs/futures_kernel_5m_full-all-fold1-react-42-af44aa3bd0-prefix500/report.md) / deepseek-flash | Kernel 八字段 | 500 | 68 | 2278 | 0.119819 | 0.042138 | 0.355628 | 0.699055 | 0 |
| AlphaPROBE / gpt-5.5 | 原生 5m | 500 | 37 | 666 | 0.193626 | 0.149755 | 0.425736 | 0.688010 | 0 |
| MCTS-LLM / gpt-5.5 | 原生 5m | 500 | 44 | 946 | 0.145717 | 0.091692 | 0.352664 | 0.700253 | 1 |

ReAct 四字段的逐对结果与准入核对见[相似度汇总](artifacts/comparisons/react_kernel_four_reset500_6990ca0c5f/similarity_summary.json)。


### 美股

仓库尚无美股资产配置、数据适配或 TEST 实验。开展前需先确定数据来源与使用范围、股票池、频率和
独立的 fold 设计。

### 期货因子搜索：fold2

AlphaAtlas 的 DeepSeek 与 GPT6-luna 的 1000 次完整结果分别入库 143 与 36 个因子；
MCTS-LLM + GPT6-luna 的 1000 次完整结果入库 7 个因子；
AlphaPROBE + DeepSeek 的 1000 次完整结果入库 31 个因子。结果与相似度见下方两表。

**数据设置**：Kernel 八字段 / all / 裁剪 1%–99%。

**Fold / seed**：fold2 / 42。

Train：2019-01-01–2021-12-31；Val：2022-01-01–2023-12-31；Test：2024-01-01–2025-12-31。

500 次为同一 1000 次预算运行的前 500 次提交截面；组合模型分别使用各截面的因子库拟合。

DeepSeek＋MCTS-LLM（79ad3e5e40）已完成1000次；500／1000次截面分别入库26／45个因子，组合模型均按本表统一参数评估。

本表 Linear／LightGBM 均采用 Train+Val 全局特征及标签 1%–99% 裁剪、品种全拟合期去均值；Test 复用特征边界，标签保持原值。LightGBM 统一为 `num_boost_round=50, min_data_in_leaf=2000, max_depth=5`。

AlphaSchema 的搜索诊断与历史对照存在差异，适配边界和统一评估配置见
[复现记录](docs/records/experiments.md#2026-11-02-alphaschema真实试跑与-fold2-luna-比较配置)。

既有 AlphaAtlas Haiku 5.5 运行（ef96a68059）使用英文提示词和公共 JSON 兼容逻辑，第 444 次后启用证据驱动回访，
第 887 次后修正补位不足的调度与近期研究证据交接。
提示词、搜索策略及 CLI 采样后端与 DeepSeek 存在差异，比较范围见[实验记录](docs/records/experiments.md)。
本轮 Haiku 运行 531d4676f6 的 500／1000 次结果独立列出，分别入库 25／67 个因子；原轮结果保留。

| 实验人 | 方法 | 模型 | 尝试次数 | TEST 单因子加权 Pearson（均值 / 中位数） | 正 IC | 单因子加权 Spearman 均值 | Linear 加权 Pearson | LightGBM 加权 Pearson |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| — | [AlphaAtlas+Annealing（4e3f0e7900）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900-prefix500/report.md) | deepseek-flash | 500 | 0.003478 / 0.002727 | 18 / 23，78.26% | 0.002806 | [0.013321](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/deepseek_old/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900-prefix500-uniform50-leaf2000-depth5/model/report.md) | [0.011558](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/deepseek_old/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900-prefix500-uniform50-leaf2000-depth5/model/report.md) |
| — | [AlphaAtlas+Annealing（4e3f0e7900）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900/report.md) | deepseek-flash | 1000 | 0.002062 / 0.001921 | 38 / 52，73.08% | 0.001211 | [0.015020](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/deepseek_old/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900-uniform50-leaf2000-depth5/model/report.md) | [0.021267](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/deepseek_old/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900-uniform50-leaf2000-depth5/model/report.md) |
| — | [AlphaAtlas+Annealing（958979b287）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-958979b287-prefix500/report.md) | deepseek-flash | 500 | 0.003331 / 0.003309 | 58 / 70，82.86% | 0.000270 | [0.012860](artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/deepseek70/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-958979b287-prefix500-leaf2000-depth5/model/report.md) | [0.011751](artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/deepseek70/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-958979b287-prefix500-leaf2000-depth5/model/report.md) |
| — | [AlphaAtlas+Annealing（958979b287）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-958979b287/report.md) | deepseek-flash | 1000 | 0.003193 / 0.003029 | 105 / 143，73.43% | 0.000698 | [0.024103](artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/deepseek143/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-958979b287-leaf2000-depth5/model/report.md) | [0.018880](artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/deepseek143/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-958979b287-leaf2000-depth5/model/report.md) |
| — | [AlphaPROBE](artifacts/comparisons/fold2_missing_prefix500_20261031/deepseek_probe/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab-prefix500/report.md) | deepseek-flash | 500 | 0.004916 / 0.004184 | 21 / 22，95.45% | 0.001449 | [0.012582](artifacts/comparisons/fold2_missing_prefix500_20261031/deepseek_probe/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab-prefix500/model/report.md) | [0.015618](artifacts/comparisons/fold2_missing_prefix500_20261031/deepseek_probe/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab-prefix500/model/report.md) |
| — | [AlphaPROBE](artifacts/comparisons/alphaprobe_deepseek_fold2_20261030/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab/report.md) | deepseek-flash | 1000 | 0.004924 / 0.004262 | 30 / 31，96.77% | 0.002026 | [0.013744](artifacts/comparisons/alphaprobe_deepseek_fold2_20261030/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab/model/report.md) | [0.014567](artifacts/comparisons/alphaprobe_deepseek_fold2_20261030/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab/model/report.md) |
| — | [MCTS-LLM](artifacts/comparisons/fold2_missing_prefix500_20261031/deepseek_mcts/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-79ad3e5e40-prefix500/report.md) | deepseek-flash | 500 | 0.002410 / 0.002238 | 19 / 26，73.08% | 0.000351 | [0.007819](artifacts/comparisons/fold2_missing_prefix500_20261031/deepseek_mcts/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-79ad3e5e40-prefix500/model/report.md) | [0.010176](artifacts/comparisons/fold2_missing_prefix500_20261031/deepseek_mcts/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-79ad3e5e40-prefix500/model/report.md) |
| — | [MCTS-LLM](artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-79ad3e5e40/report.md) | deepseek-flash | 1000 | 0.003213 / 0.003253 | 36 / 45，80.00% | 0.000289 | [0.009376](artifacts/comparisons/mcts_llm_deepseek_79ad3e5e40_full1000_20261101/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-79ad3e5e40-uniform50-leaf2000-depth5/model/report.md) | [0.015224](artifacts/comparisons/mcts_llm_deepseek_79ad3e5e40_full1000_20261101/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-79ad3e5e40-uniform50-leaf2000-depth5/model/report.md) |
| — | [AlphaAtlas+Annealing（ef96a68059）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059-prefix500/report.md) | claude-haiku-5-5 | 500 | 0.006987 / 0.006948 | 16 / 17，94.12% | 0.004490 | [0.016313](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_alphaatlas/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059-prefix500-uniform50-leaf2000-depth5/model/report.md) | [0.020762](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_alphaatlas/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059-prefix500-uniform50-leaf2000-depth5/model/report.md) |
| — | [AlphaAtlas+Annealing（ef96a68059）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059/report.md) | claude-haiku-5-5 | 1000 | 0.006608 / 0.006227 | 26 / 31，83.87% | 0.002971 | [0.015128](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_alphaatlas/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059-uniform50-leaf2000-depth5/model/report.md) | [0.015047](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_alphaatlas/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059-uniform50-leaf2000-depth5/model/report.md) |
| — | [AlphaAtlas+Annealing V1 Async（531d4676f6）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6-prefix500/report.md) | claude-haiku-5-5 | 500 | 0.009578 / 0.009528 | 25 / 25，100.00% | 0.007932 | [0.024550](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6-prefix500/model/report.md) | [0.019086](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6-prefix500/model/report.md) |
| — | [AlphaAtlas+Annealing V1 Async（531d4676f6）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6/report.md) | claude-haiku-5-5 | 1000 | 0.006839 / 0.008537 | 55 / 67，82.09% | 0.007120 | [0.023549](artifacts/comparisons/haiku55_531d4676f6_full1000_20261031/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6-uniform50-leaf2000-depth5/model/report.md) | [0.012112](artifacts/comparisons/haiku55_531d4676f6_full1000_20261031/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6-uniform50-leaf2000-depth5/model/report.md) |
| — | [AlphaPROBE](artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de-prefix500/report.md) | claude-haiku-5-5 | 500 | 0.006127 / 0.005777 | 20 / 20，100.00% | 0.003995 | [0.014790](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_alphaprobe/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de-prefix500-uniform50-leaf2000-depth5/model/report.md) | [0.021805](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_alphaprobe/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de-prefix500-uniform50-leaf2000-depth5/model/report.md) |
| — | [AlphaPROBE](artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de/report.md) | claude-haiku-5-5 | 1000 | 0.004680 / 0.004562 | 29 / 32，90.62% | 0.003117 | [0.014016](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_alphaprobe/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de-uniform50-leaf2000-depth5/model/report.md) | [0.017633](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_alphaprobe/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de-uniform50-leaf2000-depth5/model/report.md) |
| — | [MCTS-LLM](artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5-prefix500/report.md) | claude-haiku-5-5 | 500 | 0.007796 / 0.008423 | 13 / 13，100.00% | 0.009183 | [0.013805](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_mcts/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5-prefix500-uniform50-leaf2000-depth5/model/report.md) | [0.013144](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_mcts/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5-prefix500-uniform50-leaf2000-depth5/model/report.md) |
| — | [MCTS-LLM](artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5/report.md) | claude-haiku-5-5 | 1000 | 0.007479 / 0.007571 | 29 / 29，100.00% | 0.007956 | [0.012937](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_mcts/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5-uniform50-leaf2000-depth5/model/report.md) | [0.014285](artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/haiku_mcts/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5-uniform50-leaf2000-depth5/model/report.md) |
| — | [AlphaSchema](artifacts/comparisons/alphaschema_haiku55_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-41056bb4f8-prefix500/report.md) | claude-haiku-5-5 | 500 | 0.007062 / 0.007249 | 19 / 19，100.00% | 0.004895 | 0.015361 | 0.016599 |
| — | [AlphaSchema](artifacts/comparisons/alphaschema_haiku55_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-41056bb4f8/report.md) | claude-haiku-5-5 | 1000 | 0.007372 / 0.007545 | 40 / 40，100.00% | 0.005426 | 0.016929 | 0.018474 |
| — | [AlphaAtlas+Annealing（382bb38611）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-382bb38611-prefix500/report.md) | gpt-6-luna | 500 | 0.005266 / 0.005779 | 19 / 22，86.36% | 0.005031 | [0.012067](artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/luna22/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-382bb38611-prefix500-leaf2000-depth5/model/report.md) | [0.013569](artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/luna22/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-382bb38611-prefix500-leaf2000-depth5/model/report.md) |
| — | [AlphaAtlas+Annealing（382bb38611）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-382bb38611/report.md) | gpt-6-luna | 1000 | 0.006360 / 0.005779 | 32 / 36，88.89% | 0.005928 | [0.018198](artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/luna36/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-382bb38611-leaf2000-depth5/model/report.md) | [0.017957](artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/luna36/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-382bb38611-leaf2000-depth5/model/report.md) |
| — | [AlphaPROBE](artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77-prefix500/report.md) | gpt-6-luna | 500 | 0.006527 / 0.006200 | 6 / 6，100.00% | 0.002579 | [0.011088](artifacts/comparisons/fold2_missing_prefix500_20261031/luna_probe/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77-prefix500/model/report.md) | [0.013679](artifacts/comparisons/fold2_missing_prefix500_20261031/luna_probe/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77-prefix500/model/report.md) |
| — | [AlphaPROBE](artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77/report.md) | gpt-6-luna | 1000 | 0.006212 / 0.006200 | 8 / 8，100.00% | 0.003655 | [0.010070](artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/alphaprobe8/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77-leaf2000-depth5/model/report.md) | [0.011909](artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/alphaprobe8/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77-leaf2000-depth5/model/report.md) |
| — | [MCTS-LLM](artifacts/comparisons/fold2_missing_prefix500_20261031/luna_mcts/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-35c026f3fe-prefix500/report.md) | gpt-6-luna | 500 | 0.005712 / 0.003361 | 3 / 3，100.00% | 0.000574 | [0.009186](artifacts/comparisons/fold2_missing_prefix500_20261031/luna_mcts/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-35c026f3fe-prefix500/model/report.md) | [0.008372](artifacts/comparisons/fold2_missing_prefix500_20261031/luna_mcts/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-35c026f3fe-prefix500/model/report.md) |
| — | [MCTS-LLM](artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-35c026f3fe/report.md) | gpt-6-luna | 1000 | 0.007514 / 0.007027 | 7 / 7，100.00% | 0.005179 | [0.010630](artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/uniform50_leaf2000_depth5/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-35c026f3fe-uniform50-leaf2000-depth5/model/report.md) | [0.011676](artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/uniform50_leaf2000_depth5/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-35c026f3fe-uniform50-leaf2000-depth5/model/report.md) |
| — | [AlphaSchema](artifacts/comparisons/alphaschema_luna_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-7ce881f635-prefix500/report.md) | gpt-6-luna | 500 | 0.007785 / 0.006986 | 13 / 13，100.00% | 0.009032 | 0.011047 | 0.013835 |
| — | [AlphaSchema](artifacts/comparisons/alphaschema_luna_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-7ce881f635/report.md) | gpt-6-luna | 1000 | 0.007049 / 0.006910 | 20 / 20，100.00% | 0.008227 | 0.009622 | 0.013956 |


Haiku 5.5 的 MCTS-LLM 500 次组合模型 Test 覆盖率为 94.24%，AlphaPROBE 与 AlphaAtlas 的 Haiku
500 次截面均约 100%；各方法依统一规则排除全因子缺失行，见
[MCTS-LLM 模型报告](artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5-prefix500/model/report.md)。

AlphaSchema + GPT6-luna 的前 500 次截面入库 13 个因子，组合模型 Test 覆盖率为
94.22%（2,411,050 / 2,558,829 行），同样按统一规则排除全因子缺失行。
见[模型报告](artifacts/comparisons/alphaschema_luna_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-7ce881f635-prefix500/model/report.md)
及[截面评估记录](docs/records/experiments.md#2026-11-02-alphaschema前500次截面评估)。


AlphaSchema + Haiku 5.5 的 500／1000 次结果分别入库 19／40 个因子，已相邻列出。
前 500 次组合模型 Test 覆盖率为 99.9877%（2,558,513 / 2,558,829 行），模型参数与本表一致。
见[截面评估记录](docs/records/experiments.md#2026-11-03-alphaschema-haiku前500次截面评估)。

### 期货库内因子相似度：fold2 / product 窗口

**数据设置**：Kernel 八字段 / all / 裁剪 1%–99%。

**Fold / seed**：fold2 / 42。

Val：2022-01-01–2023-12-31；共同有限观测重新排名，全部配对等权。

| 方法 / 模型 | 尝试次数 | 因子数 | 因子对数 | 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [AlphaAtlas+Annealing V1 Async（4e3f0e7900）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900-prefix500/report.md) / deepseek-flash | 500 | 23 | 253 | 0.094962 | 0.024486 | 0.343017 | 0.691091 | 0 |
| [AlphaAtlas+Annealing V1 Async（4e3f0e7900）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900/report.md) / deepseek-flash | 1000 | 52 | 1326 | 0.113493 | 0.041008 | 0.356315 | 0.691091 | 0 |
| [AlphaAtlas+Annealing V1 Async（958979b287）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-958979b287-prefix500/report.md) / deepseek-flash | 500 | 70 | 2415 | 0.171063 | 0.125705 | 0.435736 | 0.696525 | 0 |
| [AlphaAtlas+Annealing V1 Async（958979b287）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-958979b287/report.md) / deepseek-flash | 1000 | 143 | 10153 | 0.114118 | 0.033377 | 0.345870 | 0.699157 | 0 |
| [AlphaPROBE](artifacts/comparisons/fold2_missing_prefix500_20261031/deepseek_probe/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab-prefix500/report.md) / deepseek-flash | 500 | 22 | 231 | 0.201151 | 0.154287 | 0.466962 | 0.691974 | 0 |
| [AlphaPROBE](artifacts/comparisons/alphaprobe_deepseek_fold2_20261030/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab/report.md) / deepseek-flash | 1000 | 31 | 465 | 0.155682 | 0.104627 | 0.407521 | 0.691974 | 0 |
| [MCTS-LLM](artifacts/comparisons/fold2_missing_prefix500_20261031/deepseek_mcts/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-79ad3e5e40-prefix500/report.md) / deepseek-flash | 500 | 26 | 325 | 0.165372 | 0.101248 | 0.448679 | 0.693037 | 0 |
| [MCTS-LLM](artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-79ad3e5e40/report.md) / deepseek-flash | 1000 | 45 | 990 | 0.178465 | 0.122278 | 0.441775 | 0.693037 | 0 |
| [AlphaAtlas+Annealing V1 Async（ef96a68059）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059-prefix500/report.md) / claude-haiku-5-5 | 500 | 17 | 136 | 0.060503 | 0.009680 | 0.181811 | 0.684691 | 0 |
| [AlphaAtlas+Annealing V1 Async（ef96a68059）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059/report.md) / claude-haiku-5-5 | 1000 | 31 | 465 | 0.055222 | 0.008324 | 0.176883 | 0.684691 | 0 |
| [AlphaAtlas+Annealing V1 Async（531d4676f6）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6-prefix500/report.md) / claude-haiku-5-5 | 500 | 25 | 300 | 0.101866 | 0.020113 | 0.407944 | 0.695464 | 0 |
| [AlphaAtlas+Annealing V1 Async（531d4676f6）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6/report.md) / claude-haiku-5-5 | 1000 | 67 | 2211 | 0.093666 | 0.011720 | 0.387959 | 0.695464 | 0 |
| [AlphaPROBE](artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de-prefix500/report.md) / claude-haiku-5-5 | 500 | 20 | 190 | 0.156782 | 0.054269 | 0.487625 | 0.695070 | 0 |
| [AlphaPROBE](artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de/report.md) / claude-haiku-5-5 | 1000 | 32 | 496 | 0.122526 | 0.032349 | 0.432565 | 0.695070 | 0 |
| [MCTS-LLM](artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5-prefix500/report.md) / claude-haiku-5-5 | 500 | 13 | 78 | 0.245222 | 0.163154 | 0.526695 | 0.669180 | 0 |
| [MCTS-LLM](artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5/report.md) / claude-haiku-5-5 | 1000 | 29 | 406 | 0.210330 | 0.120922 | 0.523007 | 0.698985 | 0 |
| [AlphaSchema](artifacts/comparisons/alphaschema_haiku55_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-41056bb4f8-prefix500/report.md) / claude-haiku-5-5 | 500 | 19 | 171 | 0.175447 | 0.151249 | 0.421956 | 0.573327 | 0 |
| [AlphaSchema](artifacts/comparisons/alphaschema_haiku55_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-41056bb4f8/report.md) / claude-haiku-5-5 | 1000 | 40 | 780 | 0.190992 | 0.152623 | 0.433389 | 0.688502 | 0 |
| [AlphaAtlas+Annealing V1 Async（382bb38611）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-382bb38611-prefix500/report.md) / gpt-6-luna | 500 | 22 | 231 | 0.107882 | 0.018413 | 0.398304 | 0.692477 | 0 |
| [AlphaAtlas+Annealing V1 Async（382bb38611）](artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-382bb38611/report.md) / gpt-6-luna | 1000 | 36 | 630 | 0.073438 | 0.009076 | 0.289844 | 0.692477 | 0 |
| [AlphaPROBE](artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77-prefix500/report.md) / gpt-6-luna | 500 | 6 | 15 | 0.235274 | 0.218686 | 0.525441 | 0.590610 | 0 |
| [AlphaPROBE](artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77/report.md) / gpt-6-luna | 1000 | 8 | 28 | 0.221230 | 0.203955 | 0.506893 | 0.695996 | 0 |
| [MCTS-LLM](artifacts/comparisons/fold2_missing_prefix500_20261031/luna_mcts/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-35c026f3fe-prefix500/report.md) / gpt-6-luna | 500 | 3 | 3 | 0.249300 | 0.203574 | 0.346025 | 0.381638 | 0 |
| [MCTS-LLM](artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-35c026f3fe/report.md) / gpt-6-luna | 1000 | 7 | 21 | 0.176024 | 0.037512 | 0.490516 | 0.644473 | 0 |
| [AlphaSchema](artifacts/comparisons/alphaschema_luna_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-7ce881f635-prefix500/report.md) / gpt-6-luna | 500 | 13 | 78 | 0.266841 | 0.246519 | 0.557762 | 0.689871 | 0 |
| [AlphaSchema](artifacts/comparisons/alphaschema_luna_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-7ce881f635/report.md) / gpt-6-luna | 1000 | 20 | 190 | 0.239177 | 0.189775 | 0.550637 | 0.689871 | 0 |


### B26 初始库增量补充（2026-11-03）

Fold2、seed 42、每方法 300 次；初始 B26，八字段＋26 个发布特征。
统一组合方案：Linear OLS；LightGBM 50 轮、leaf2000/depth5，使用相同训练样本和预处理。

ΔIC 相对同样本 B26，方括号为 95% 区间；WMSE 下降为正表示误差降低。
B26 的 Linear／LightGBM IC 为 0.01270556／0.01064594。

| 方法 / 运行 | 新增 / 总成员 / 入模列数 | Linear ΔIC [95% 区间] | LightGBM ΔIC [95% 区间] | Linear WMSE 下降 | LightGBM WMSE 下降 |
| --- | ---: | --- | --- | ---: | ---: |
| AlphaProbe（`b448e9fb48`） | 5 / 31 / 31 | +0.003207 [−0.003003, +0.011290] | +0.000773 [−0.009379, +0.014002] | −0.014780% | −0.030897% |
| AlphaSchema（`fbac3f9a28`） | 4 / 30 / 30 | +0.000565 [−0.003266, +0.005162] | +0.003425 [−0.005023, +0.014533] | −0.003987% | −0.026347% |
| MCTS-LLM（`d8d6f22a47`） | 4 / 30 / 30 | +0.002673 [−0.000722, +0.007564] | +0.007712 [+0.002702, +0.013721] | +0.001860% | −0.016405% |
| AlphaSchema（`3a4f640d02`） | 4 / 30 / 30 | −0.000208 [−0.005320, +0.007359] | +0.004161 [−0.000461, +0.011909] | −0.014557% | −0.015537% |
| AlphaAtlas `v1_async`（`5f77947f20`） | 9 / 35 / 35 | +0.005932 [+0.000010, +0.014157] | +0.009381 [+0.000176, +0.020036] | −0.031854% | −0.020182% |

AlphaSchema 两行对应两次独立搜索。
所有结果均为单方法对 B26 的扩充，200 次前缀与详细区间见
[统一 v5 结果报告](docs/records/zookeeper26_fold2/single_methods_v5/report.md)，
配置和复跑命令见[实验目录](configs/experiments/zk26_single_methods_v5_20261103/README.md)。

## 待开展实验

按 fold 分组；完成单因子、组合模型和库内相似度评估后，结果进入上方对应表格。

### Fold1

Train：2016-01-01–2018-12-31；Val：2019-01-01–2020-12-31；Test：2021-01-01–2022-12-31。
期货已完成的实验见上表。

| 市场 | 方法 | 状态 | 结果 / Run ID |
| --- | --- | --- | --- |
| A 股 | AlphaPROBE | 已有冻结运行，待补 TEST 评估 | — |
| A 股 | ReAct | 待专项验收 | — |

### Fold2

Train：2019-01-01–2021-12-31；Val：2022-01-01–2023-12-31；Test：2024-01-01–2025-12-31。
按独立运行组织；两个 fold 的日期存在交叠，不能视为互不重叠的最终盲测。

| 市场 | 方法 | 状态 | 结果 / Run ID |
| --- | --- | --- | --- |
| 期货 | ReAct | 待开展 | — |
| 期货 | Random | 待开展基线对照 | — |
| 期货 | GP | 待开展基线对照 | — |
| 期货 | UCT MCTS | 待开展基线对照 | — |
| A 股 | AlphaPROBE | 待开展对照 | — |
| A 股 | MCTS-LLM | 待开展对照 | — |
| A 股 | ReAct | 待专项验收 | — |
| A 股 | Random | 待开展基线对照 | — |
| A 股 | GP | 待开展基线对照 | — |
| A 股 | UCT MCTS | 待开展基线对照 | — |

### Fold 待定

| 市场 | 方法 | 状态 | 结果 / Run ID |
| --- | --- | --- | --- |
| 美股 | 数据接入与实验设计 | 待确认数据、股票池、频率和 fold | — |
