# 实验记录

## 实验总览

已完成期货 AlphaPROBE、MCTS-LLM、ReAct 与 AlphaAtlas+Annealing V1 Async 的
fold1 OOS 评估。
仓库目前没有美股数据适配或美股实验记录。

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

| 实验人 | 方法 | 数据 | 模型 | Fold / seed | 尝试次数 | TEST 单因子加权 Pearson（均值 / 中位数） | 正 IC | 单因子加权 Spearman 均值 | Linear 加权 Pearson | LightGBM 加权 Pearson |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 汤子逸 | AlphaPROBE | 原生 5m | gpt-5.5 | fold1 / 42 | 500 | 0.002393 / 0.001559 | 27 / 37（73.0%） | 0.003421 | 0.006413 | 0.011228 |
| 汤子逸 | AlphaPROBE | 原生 5m | deepseek-flash | fold1 / 42 | 1000 | 0.002365 / 0.002410 | 51 / 66（77.3%） | 0.000062 | 0.012144 | 0.020982 |
| 汤子逸 | AlphaPROBE | Kernel 四字段 | deepseek-flash | fold1 / 42 | 500 | 0.002922 / 0.003593 | 38 / 48（79.17%） | 0.002988 | 0.011288 | 0.016138 |
| 汤子逸 | AlphaPROBE | Kernel 四字段 | deepseek-flash | fold1 / 42 | 1000 | 0.002679 / 0.003072 | 60 / 78（76.92%） | 0.002439 | 0.014138 | 0.015396 |
| 汤子逸 | MCTS-LLM | 原生 5m | gpt-5.5 | fold1 / 42 | 500 | 0.002785 / 0.003165 | 32 / 44（72.7%） | 0.002229 | 0.008493 | 0.017879 |
| 汤子逸 | MCTS-LLM | 原生 5m | deepseek-flash | fold1 / 42 | 500 | 0.002512 / 0.001987 | 26 / 35（74.3%） | 0.000301 | 0.007910 | 0.008754 |
| 汤子逸 | MCTS-LLM | 原生 5m | deepseek-flash | fold1 / 42 | 1000 | 0.001567 / 0.001223 | 38 / 60（63.3%） | 0.000212 | 0.004354 | 0.009233 |
| 汤子逸 | AlphaAtlas+Annealing V1 Async | Kernel 四字段 | deepseek-flash | fold1 / 42 | 500 | 0.003563 / 0.003538 | 52 / 64（81.25%） | 0.002211 | 0.013123 | 0.015892 |
| 汤子逸 | AlphaAtlas+Annealing V1 Async | Kernel 四字段 | deepseek-flash | fold1 / 42 | 1000 | 0.004134 / 0.004646 | 147 / 166（88.55%） | 0.004777 | 0.016481 | 0.019924 |
| 汤子逸 | [ReAct](artifacts/runs/futures_kernel_5m-all-fold1-react-42-145195d3de-prefix500/report.md) | Kernel 四字段 | deepseek-flash | fold1 / 42 | 500 | 0.002494 / 0.001603 | 44 / 65，67.69% | 0.001632 | 0.012126 | 0.019378 |
| 汤子逸 | [ReAct](artifacts/runs/futures_kernel_5m-all-fold1-react-42-6990ca0c5f/report.md) | Kernel 四字段 | deepseek-flash | fold1 / 42 | 1000 | 0.001763 / 0.001166 | 88 / 136，64.71% | 0.000621 | 0.012771 | 0.021948 |
| 汤子逸 | [ReAct](artifacts/runs/futures_kernel_5m_full-all-fold1-react-42-af44aa3bd0-prefix500/report.md) | Kernel 八字段 | deepseek-flash | fold1 / 42 | 500 | 0.004672 / 0.005051 | 53 / 68（77.94%） | 0.001008 | 0.008176 | 0.015305 |

单因子沿用训练方向。已评估的下游模型使用冻结因子库在 train+val 拟合，LightGBM 固定 50 轮。
ReAct 四字段 500 次行对应运行 `futures_kernel_5m-all-fold1-react-42-145195d3de` 的
[500 次评估快照](artifacts/runs/futures_kernel_5m-all-fold1-react-42-145195d3de-prefix500/report.md)。
1000 次行对应 `futures_kernel_5m-all-fold1-react-42-6990ca0c5f`，
见[单因子报告](artifacts/runs/futures_kernel_5m-all-fold1-react-42-6990ca0c5f/report.md)和
[模型报告](artifacts/runs/futures_kernel_5m-all-fold1-react-42-6990ca0c5f/model/report.md)。
与首轮四字段 `ac32f1e3dc` 的结果见[复跑对照](artifacts/comparisons/react_kernel_four_repeat_145195d3de/results_comparison.html)。


### 期货库内因子相似度：统一 product 窗口

截至 2026-10-24，下表 12 组结果的相似度均按 **product 窗口**，
在 **Val 2019–2020** 上评估。
每对因子取共同有限观测重新排名，计算绝对 Spearman；全部不同因子对等权平均，排除自身相关，
不重复计数、不抽样、不填补缺失。此处沿用库内去重的 pooled 行口径，不使用品种成交额权重。
Kernel 四字段的 AlphaPROBE、V1 Async 与 ReAct 使用四字段 Research Kernel 复权派生快照，
ReAct 八字段使用八字段 Kernel 快照；
其余五组使用原生 5m 快照；
不同快照间的数值差异不能作为同数据条件下的方法排名。

| 方法 / 模型 | 数据 | 尝试次数 | 因子数 | 因子对数 | 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AlphaPROBE / gpt-5.5 | 原生 5m | 500 | 37 | 666 | 0.193626 | 0.149755 | 0.425736 | 0.688010 | 0 |
| AlphaPROBE / deepseek-flash | 原生 5m | 1000 | 66 | 2145 | 0.109710 | 0.053192 | 0.294732 | 0.695848 | 0 |
| AlphaPROBE / deepseek-flash | Kernel 四字段 | 500 | 48 | 1128 | 0.155238 | 0.096048 | 0.418874 | 0.699248 | 0 |
| AlphaPROBE / deepseek-flash | Kernel 四字段 | 1000 | 78 | 3003 | 0.149145 | 0.091602 | 0.397867 | 0.699248 | 0 |
| MCTS-LLM / gpt-5.5 | 原生 5m | 500 | 44 | 946 | 0.145717 | 0.091692 | 0.352664 | 0.700253 | 1 |
| MCTS-LLM / deepseek-flash | 原生 5m | 500 | 35 | 595 | 0.184539 | 0.131507 | 0.431388 | 0.699873 | 0 |
| MCTS-LLM / deepseek-flash | 原生 5m | 1000 | 60 | 1770 | 0.131032 | 0.069709 | 0.355669 | 0.699873 | 0 |
| AlphaAtlas+Annealing V1 Async / deepseek-flash | Kernel 四字段 | 500 | 64 | 2016 | 0.115394 | 0.072774 | 0.288826 | 0.695866 | 0 |
| AlphaAtlas+Annealing V1 Async / deepseek-flash | Kernel 四字段 | 1000 | 166 | 13695 | 0.127824 | 0.073150 | 0.332904 | 0.695866 | 0 |
| [ReAct](artifacts/runs/futures_kernel_5m-all-fold1-react-42-145195d3de-prefix500/report.md) / deepseek-flash | Kernel 四字段 | 500 | 65 | 2080 | 0.208221 | 0.170754 | 0.463688 | 0.695703 | 0 |
| [ReAct](artifacts/runs/futures_kernel_5m-all-fold1-react-42-6990ca0c5f/report.md) / deepseek-flash | Kernel 四字段 | 1000 | 136 | 9180 | 0.179021 | 0.140757 | 0.411079 | 0.695703 | 0 |
| [ReAct](artifacts/runs/futures_kernel_5m_full-all-fold1-react-42-af44aa3bd0-prefix500/report.md) / deepseek-flash | Kernel 八字段 | 500 | 68 | 2278 | 0.119819 | 0.042138 | 0.355628 | 0.699055 | 0 |

ReAct 四字段的逐对结果与准入核对见[相似度汇总](artifacts/comparisons/react_kernel_four_reset500_6990ca0c5f/similarity_summary.json)。


### 美股

仓库尚无美股资产配置、数据适配或 TEST 实验。开展前需先确定数据来源与使用范围、股票池、频率和
独立的 fold 设计。

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
| 期货 | AlphaPROBE | 待开展对照 | — |
| 期货 | MCTS-LLM | 待开展对照 | — |
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
