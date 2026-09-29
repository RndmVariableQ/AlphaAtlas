# 实验记录

## 实验总览

已完成期货 AlphaPROBE 与 MCTS-LLM 的 fold1 对照，以及 A 股 Atlas 区域搜索原型的 fold1 实验。
仓库目前没有美股数据适配或美股实验记录。

---

## 数据

### 期货

- **来源 / 频率**：RiceQuant 原生 5 分钟数据；使用主力真实合约，按品种汇总。
- **请求区间**：2015-01-01 至 2026-09-01（含端点）；记录的实际交易日覆盖为
  2015-01-05 至 2026-09-01。
- **实验快照**：`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。

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
- **搜索设置**：开放 `futures_cta` 参考库，不预置因子；seed 42，预算 500 次。
- **Fold1**：train 2016-01-01–2018-12-31，val 2019-01-01–2020-12-31，test
  2021-01-01–2022-12-31。
- **结果汇总日期**：2026-09-18。

各实验设置：

- **AlphaPROBE**：gpt-5.5；embedding 模型 Qwen3-Embedding-0.6B。
- **MCTS-LLM**：gpt-5.5。

| 实验人 | 方法 | 模型 | Fold / seed | TEST 单因子加权 Pearson（均值 / 中位数） | 正 IC | 单因子加权 Spearman 均值 | Linear 加权 Pearson | LightGBM 加权 Pearson | Run ID |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 汤子逸 | AlphaPROBE | gpt-5.5 | fold1 / 42 | 0.002393 / 0.001559 | 27 / 37（73.0%） | 0.003421 | 0.006413 | 0.011228 | `futures-all-fold1-alphaprobe-42-1e4331978d` |
| 汤子逸 | MCTS-LLM | gpt-5.5 | fold1 / 42 | 0.002785 / 0.003165 | 32 / 44（72.7%） | 0.002229 | 0.008493 | 0.017879 | `futures-all-fold1-mcts_llm-42-1ec3984a4b` |

单因子沿用训练方向。下游模型使用冻结因子库在 train+val 拟合，LightGBM 固定 50 轮。

### A 股因子搜索

实验设置：

- 数据范围：union1800；目标为未来 5 个交易日收益，主指标为截面 Spearman IC。
- Fold1：train 2016-01-01–2018-12-31，val 2019-01-01–2020-12-31，test 2021-01-01–2022-12-31。
- 方法：Atlas 区域 UCB 原型（`finite_grammar_baseline_v2`），seed 42，12 次尝试；非完整 AlphaAtlas 架构。

| 实验人 | 方法 | Fold / seed | TEST 因子 IC 均值 / 中位数 | 正 IC | Run ID |
| --- | --- | --- | ---: | ---: | --- |


### 美股

仓库尚无美股资产配置、数据适配或 TEST 实验。开展前需先确定数据来源与使用范围、股票池、频率和
独立的 fold 设计。

## 待开展实验

Fold2：train 2019-01-01–2021-12-31，val 2022-01-01–2023-12-31，test 2024-01-01–2025-12-31，
与 fold1 独立。AlphaAtlas 方法架构待完善；ReAct 仍待专项验收。美股暂不指定 fold。

| 市场 | 实验 / 方法 | Fold | 状态 / 备注 | 实验人 | 模型 | Seed | TEST 结果 / Run ID |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 期货 | AlphaAtlas（atlas） | fold1 | 方法架构待完善 |  |  |  |  |
| 期货 | AlphaAtlas（atlas） | fold2 | 方法架构待完善 |  |  |  |  |
| 期货 | ReAct | fold1 | 待专项验收 |  |  |  |  |
| 期货 | ReAct | fold2 | 待专项验收 |  |  |  |  |
| 期货 | AlphaPROBE、MCTS-LLM、Random、GP、UCT MCTS | fold2 | 待开展对照 |  |  |  |  |
| A 股 | AlphaPROBE | fold1 | 已有冻结运行，补记 TEST 评估 |  |  |  |  |
| A 股 | AlphaAtlas（atlas） | fold1 | 方法架构待完善 |  |  |  |  |
| A 股 | AlphaAtlas（atlas） | fold2 | 方法架构待完善 |  |  |  |  |
| A 股 | ReAct | fold1 | 待专项验收 |  |  |  |  |
| A 股 | ReAct | fold2 | 待专项验收 |  |  |  |  |
| A 股 | AlphaPROBE、MCTS-LLM、Random、GP、UCT MCTS | fold2 | 待开展对照 |  |  |  |  |
| 美股 | 数据接入与实验设计 | 待定 | 确认数据、股票池、频率和 fold 后再排实验 |  |  |  |  |
