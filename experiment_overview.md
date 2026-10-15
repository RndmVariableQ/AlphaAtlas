# 实验记录

## 实验总览

已完成期货 AlphaPROBE、MCTS-LLM、AlphaAtlas+Annealing (V0) 与
AlphaAtlas+Annealing (V1 Async) 的 fold1 OOS 评估，含 MCTS-LLM + DeepSeek 和
V1 Async 的 500→1000 次续跑，以及 A 股 Atlas 区域搜索原型的 fold1 实验。
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
- **搜索设置**：开放 `futures_cta` 参考库，首轮不预置因子，seed 42；各运行预算见表。
  V1 Async 与 MCTS-LLM + DeepSeek 的 1000 次条目继承原 500 次记录与状态，再追加 500 次。
- **Fold1**：train 2016-01-01–2018-12-31，val 2019-01-01–2020-12-31，test
  2021-01-01–2022-12-31。
- **结果汇总日期**：AlphaPROBE / MCTS-LLM 为 2026-09-18；区域退火与 MCTS + DeepSeek 为
  2026-10-10；V1 Async 500 次为 2026-10-11，续跑 1000 次为 2026-10-12；
  MCTS + DeepSeek 续跑 1000 次为 2026-10-15。

各实验设置：

- **AlphaPROBE**：gpt-5.5；embedding 模型 Qwen3-Embedding-0.6B。
- **MCTS-LLM**：gpt-5.5 与 deepseek-flash 各一组 500 次实验；deepseek-flash 另续跑至 1000 次。
- **AlphaAtlas+Annealing (V0)**：deepseek-flash；每个研究计划最多 5 个候选。
- **AlphaAtlas+Annealing (V1 Async)**：deepseek-flash；最多并行 3 个研究计划，每计划最多 5 个
  候选；Explorer 对话上限 40 轮。

| 实验人 | 方法 | 模型 | Fold / seed | 预算 | TEST 单因子加权 Pearson（均值 / 中位数） | 正 IC | 单因子加权 Spearman 均值 | Linear 加权 Pearson | LightGBM 加权 Pearson | Run ID |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 汤子逸 | AlphaPROBE | gpt-5.5 | fold1 / 42 | 500 | 0.002393 / 0.001559 | 27 / 37（73.0%） | 0.003421 | 0.006413 | 0.011228 | `futures-all-fold1-alphaprobe-42-1e4331978d` |
| 汤子逸 | MCTS-LLM | gpt-5.5 | fold1 / 42 | 500 | 0.002785 / 0.003165 | 32 / 44（72.7%） | 0.002229 | 0.008493 | 0.017879 | `futures-all-fold1-mcts_llm-42-1ec3984a4b` |
| 汤子逸 | MCTS-LLM | deepseek-flash | fold1 / 42 | 500 | 0.002512 / 0.001987 | 26 / 35（74.3%） | 0.000301 | 0.007910 | 0.008754 | `futures-all-fold1-mcts_llm-42-0ccecc542e` |
| 汤子逸 | MCTS-LLM（续跑） | deepseek-flash | fold1 / 42 | 1000（500→1000） | 0.001567 / 0.001223 | 38 / 60（63.3%） | 0.000212 | 0.004354 | 0.009233 | `futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000` |
| 汤子逸 | AlphaAtlas+Annealing (V0) | deepseek-flash | fold1 / 42 | 500 | 0.002426 / 0.002784 | 28 / 34（82.4%） | 0.003010 | 0.006138 | 0.010876 | `futures-all-fold1-annealing-42-1a44fb4805` |
| 汤子逸 | AlphaAtlas+Annealing (V1 Async) | deepseek-flash | fold1 / 42 | 500 | 0.002484 / 0.001655 | 23 / 31（74.2%） | 0.002662 | 0.009586 | 0.006026 | `futures-all-fold1-v1_async-42-b53b446ea7` |
| 汤子逸 | AlphaAtlas+Annealing (V1 Async，续跑) | deepseek-flash | fold1 / 42 | 1000（500→1000） | 0.002184 / 0.001694 | 41 / 58（70.7%） | 0.002126 | 0.011739 | 0.009524 | `futures-all-fold1-v1_async-42-b53b446ea7-extend1000` |

单因子沿用训练方向。已评估的下游模型使用冻结因子库在 train+val 拟合，LightGBM 固定 50 轮。

区域退火完成 500 次尝试，冻结 34 个因子（入库率 6.8%），34/34 个因子均完成 TEST 计算。
TEST 加权 Pearson IC 严格大于 0.005 的有 6/34 个，覆盖率最低 86.90%。冻结库因子主 IC
均值由 Val 的 0.007307 降至 Test 的 0.002426；这是单因子统计，不是组合预测 IC。
详见[实验记录](docs/records/experiments.md#2026-10-10-futures-fold1区域退火冻结库-oos)及
[逐因子报告](artifacts/runs/futures-all-fold1-annealing-42-1a44fb4805/report.md)。
区域退火的两个下游模型使用全部 34 个冻结因子，在 4,390,385 行 train+val 样本上拟合；
TEST 预测覆盖率均为 100%，覆盖 2,215,926 行、72 个品种。模型与逐项测试指标见
[组合模型报告](artifacts/runs/futures-all-fold1-annealing-42-1a44fb4805/model/report.md)。

MCTS-LLM + DeepSeek 从 500 次续跑至 1000 次，冻结 60 个因子；原 35 个全部保留，
追加阶段新增 25 个，累计入库率 6.0%，新增阶段入库率 5.0%。60/60 个因子 TEST 计算成功，
主 IC 严格大于 0.005 的有 14/60 个；新增 25 个因子的 TEST 主 IC 均值为 0.000244，
正 IC 为 12/25。原 35 个因子的样本数和覆盖率不变，全部 IC 最大绝对差仅为 1.39×10⁻¹⁷。
两个模型使用全部 60 个因子，在 4,390,385 行 train+val 样本上拟合；TEST 均覆盖
2,215,926 行、72 个品种，覆盖率 100%。TEST 加权 Spearman 分别为 0.007351、0.004979。
相对原 500 次，Linear 的加权 Pearson 从 0.007910 降至 0.004354，LightGBM 从
0.008754 升至 0.009233；单因子主 IC 均值由 0.002512 降至 0.001567。
完整结果见[续跑实验记录](docs/records/experiments.md#2026-10-15-futures-fold1mcts-llm-deepseek-5001000-续跑完整评估)、
[逐因子报告](artifacts/runs/futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000/report.md)与
[组合模型报告](artifacts/runs/futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000/model/report.md)。

V1 Async 的 500 次运行完成 100 个研究计划，冻结 31 个因子（入库率 6.2%），活动时间约
11 小时 59 分钟；异步任务全部收尾。31/31 个因子均完成 TEST 计算，主指标沿用
品种 √成交额加权 Pearson IC。TEST 主 IC 严格大于 0.005 的有 7/31 个，覆盖率最低 85.74%。
冻结库主 IC 均值由 Train 的 0.009276、Val 的 0.007660 降至 Test 的 0.002484；
主 IC 中位数分别为 0.008836、0.006965、0.001655。单因子 IC 统计不代表组合预测 IC 或交易收益。
本次使用 product 窗口与冻结方向。下游 Linear 与 LightGBM 使用全部 31 个因子，在
4,390,251 行 train+val 样本上拟合；TEST 均预测 2,215,902 / 2,215,926 行，
覆盖率 99.9989%，覆盖 72 个品种；24 行因所有因子缺失未预测。
TEST 加权 Spearman IC 分别为 0.015293、0.011197，
详见[组合模型报告](artifacts/runs/futures-all-fold1-v1_async-42-b53b446ea7/model/report.md)。库内相似度见下表。
详见[实验记录](docs/records/experiments.md#2026-10-11-futures-fold1v1-async-冻结库-oos)及
[逐因子报告](artifacts/runs/futures-all-fold1-v1_async-42-b53b446ea7/report.md)。

V1 Async 从上述 500 次检查点续跑至 1000 次，累计 200 个研究计划，冻结 58 个因子；
原 31 个因子全部保留，追加的 500 次新增 27 个，累计入库率 5.8%，新增阶段入库率 5.4%。
58/58 个因子 TEST 计算成功，主 IC 严格大于 0.005 的有 14/58 个；
新增 27 个因子的 TEST 主 IC 均值为 0.001839，正 IC 为 18/27。
两个组合模型使用全部 58 个因子，在 4,390,319 行 train+val 样本上拟合；
TEST 均预测 2,215,914 / 2,215,926 行，覆盖率 99.9995%、72 个品种。
TEST 加权 Spearman IC 分别为 0.014334、0.010908。
本次两模型的加权 Pearson IC 高于 500 次结果，加权 Spearman IC 则略低；单因子主 IC
均值由 0.002484 降至 0.002184。详细结果见
[续跑实验记录](docs/records/experiments.md#2026-10-12-futures-fold1v1-async-5001000-续跑完整评估)、
[单因子报告](artifacts/runs/futures-all-fold1-v1_async-42-b53b446ea7-extend1000/report.md)与
[组合模型报告](artifacts/runs/futures-all-fold1-v1_async-42-b53b446ea7-extend1000/model/report.md)。

上述两组续跑前均已查看各自 500 次运行的同一 Test 结果；这两次属于 2021–2022 测试集
复评，不能视为新的独立盲测。MCTS 继承原树与方法状态；V1 Async 前 500 次按原预算退火，
追加阶段按 1000 总预算计算进度。它们不等同于从零预设 1000 次预算的运行。
搜索沿用 train/val 反馈，未向方法提供 Test 指标。

### 期货库内因子相似度：统一 product 窗口

截至 2026-10-15，下表七份冻结因子库均采用 **product 窗口**，在 **Val 2019–2020** 上评估。
每对因子取共同有限观测重新排名，计算绝对 Spearman；全部不同因子对等权平均，排除自身相关，
不重复计数、不抽样、不填补缺失。此处沿用库内去重的 pooled 行口径，不使用品种成交额权重。

| 方法 / 模型 | 预算 | 因子数 | 因子对数 | 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AlphaPROBE（gpt-5.5） | 500 | 37 | 666 | 0.193626 | 0.149755 | 0.425736 | 0.688010 | 0 |
| MCTS-LLM（gpt-5.5） | 500 | 44 | 946 | 0.145717 | 0.091692 | 0.352664 | 0.700253 | 1 |
| MCTS-LLM（deepseek-flash） | 500 | 35 | 595 | 0.184539 | 0.131507 | 0.431388 | 0.699873 | 0 |
| MCTS-LLM（deepseek-flash，续跑） | 1000（500→1000） | 60 | 1770 | 0.131032 | 0.069709 | 0.355669 | 0.699873 | 0 |
| AlphaAtlas+Annealing (V0)（deepseek-flash） | 500 | 34 | 561 | 0.106387 | 0.057006 | 0.256845 | 0.689016 | 0 |
| AlphaAtlas+Annealing (V1 Async)（deepseek-flash） | 500 | 31 | 465 | 0.077518 | 0.027665 | 0.224777 | 0.656651 | 0 |
| AlphaAtlas+Annealing (V1 Async，续跑)（deepseek-flash） | 1000（500→1000） | 58 | 1653 | 0.076121 | 0.031223 | 0.215492 | 0.693620 | 0 |

按各库分别计数，合计 6,656 对全部有效；500 与 1000 次的 V1 Async 库共有 465 个配对，
MCTS-LLM（deepseek-flash）两份库共有 595 个配对；各自共有配对的相关性与有效重叠行数
完全一致。平均相似度从低到高为 V1 Async 1000 次续跑、V1 Async 500 次、V0、
MCTS-LLM（deepseek-flash）1000 次续跑、MCTS-LLM（gpt-5.5）、
MCTS-LLM（deepseek-flash）500 次、AlphaPROBE。
相似度较低表示该冻结库的平均秩相关冗余较低，不直接代表预测能力更强。

V1 Async 500 次为 465 对、1000 次续跑为 1,653 对，均全部有效且没有配对达到 0.7；
两者最小共同样本数均为 1,369,870，验证期 eligible 行数均为 1,918,863。
MCTS-LLM（deepseek-flash）1000 次续跑为 1,770 对，最小共同样本数为 1,545,841；
验证期 eligible 行数同为 1,918,863，平均相似度较原 500 次下降。

AlphaPROBE 和 gpt-5.5 版 MCTS-LLM 原运行使用 contract，本次相似度按 product 重算；
其余 deepseek-flash 运行原本使用 product。gpt-5.5 版 MCTS-LLM 有 1 对达到 0.700253；
全部冻结成员均保留。上方 TEST 与下游模型指标仍来自各运行保存的窗口设置。
原三组的[汇总与逐对明细](artifacts/comparisons/library_similarity_product_20261010/summary.json)、
MCTS + DeepSeek 的[汇总与逐对明细](artifacts/comparisons/mcts_deepseek_0ccecc542e/summary.json)、
V1 Async 500 次的[汇总与逐对明细](artifacts/comparisons/v1_async_b53b446ea7/summary.json)、
V1 Async 1000 次续跑的[汇总与逐对明细](artifacts/comparisons/v1_async_b53b446ea7_extend1000/summary.json)、
MCTS + DeepSeek 1000 次续跑的[汇总与逐对明细](artifacts/comparisons/mcts_deepseek_0ccecc542e_extend1000/summary.json)，
以及[实验记录](docs/records/experiments.md)保存了口径和复算依据。

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
