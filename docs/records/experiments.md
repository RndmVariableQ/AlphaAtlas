# 实验记录

只记录可从运行产物核对的条件与结果；研究协议见[实验规范](../research_protocol.md)，方法复现边界见
[方法规范](../method_reproduction.md)。未完成 OOS 的运行不作为样本外结论。

## 2026-10-09 Futures fold1：AlphaAtlas+Annealing (V0)

运行产物：`artifacts/runs/futures-all-fold1-annealing-42-af86399215/`。实现
`annealing_llm_atlas_v1`，seed 42，`futures / all / fold1`，500 次候选预算，数据快照
`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。模型为
`deepseek-flash`；每个 Research Analyst 计划最多生成 8 个候选。运行冻结，未见 OOS 产物。

### 准入结果

| 结果 | 数量 |
| --- | ---: |
| 入库 | 12（2.4%） |
| `quality_threshold` | 450 |
| `expression_duplicate` | 15 |
| `behavior_duplicate` | 11 |
| `low_coverage` | 11 |
| `compile_error` | 1 |

期货准入使用训练方向调整后的 train、val 主 IC 均严格大于 0.005，覆盖率至少 0.8，且
通过现有表达式／行为去重。450 个质量阈值拒绝中，312 个 train 与 val 都未达标，115 个
仅 train 达标，23 个仅 val 达标。它们的方向调整 train IC 中位数为 0.00297，val IC 中位数为
0.00146。11 个覆盖率拒绝均低于 0.8；覆盖率不是本次低入库率的主要原因。500 次中有 499
次成功完成表达式评估，只有 1 次编译失败。

另有 26 个候选同时通过 IC 和覆盖门槛，但分别因表达式重复（15）或行为重复（11）未入库；
连同 12 个入库候选，共 38 个候选达到数值与覆盖门槛。准入门槛未作调整。

### 搜索效率观察

- 63 个计划覆盖 500 次预算。前 31 个计划各探索一个不同 Region；其余 32 个计划共 252 次
  候选都落在同一个 Region，该区域最终入库 4 个（1.6%）。这与温度下降后偏向当前区域的
  方法设计一致，但本次后半段的搜索集中度很高。
- 按表达式空白归一后，500 次候选只有 235 种不同公式，265 次是公式重复；其中 262 次重复
  发生在同一个 Analyst 计划的 8 个候选内。63 个计划中有 59 个出现计划内重复；每计划不同
  公式数的中位数为 4。本次运行时 Miner 提示只提供最近一个候选，无法看到本计划全部已试公式；
  因而计划内重复是明确的预算效率问题。
- 运行记录了 758 次模型请求、约 10.01M prompt tokens 和 4.08M completion tokens。活动时间
  21,891.7 秒，其中候选生成 19,723.6 秒，评估与准入 2,056.1 秒。

本次低入库率首先来自严格的双切分 IC 门槛：90% 的预算以 `quality_threshold` 结束；此外，
重复公式消耗了过半候选预算，进一步降低了有效探索量。后续方法改动应优先减少计划内重复；
不得通过放宽统一准入门槛提高入库数。上述观察仅描述
本次 run，不代表其他资产、fold 或 seed 的结果。

本次运行后，Miner 已改为在同一计划内保留完整对话和平台评估反馈，并默认最多尝试 5 个
候选；下一计划的 Analyst 默认接收上一计划中最多 5 个正式入库候选，按主指标验证 IC 绝对值
排序。此实现变更不改变上面的历史运行事实，效果需通过后续运行验证。

## 2026-10-10 Futures fold1：区域退火冻结库 OOS

运行产物：`artifacts/runs/futures-all-fold1-annealing-42-1a44fb4805/`。实现
`annealing_llm_atlas_v1`，seed 42，`futures / all / fold1`，模型 `deepseek-flash`，每个计划
最多 5 个候选，数据快照为
`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。
500 次尝试完成后冻结 34 个因子，入库率 6.8%。

本次通过以下独立入口读取项目 `.env` 并评估测试集，结果于北京时间 2026-10-10 09:08 保存：

```bash
uv run --env-file .env atlas test artifacts/runs/futures-all-fold1-annealing-42-1a44fb4805
```

测试区间为 2021-01-01 至 2022-12-31，使用 RiceQuant 原生 5m，目标为未来 12 Bar 收盘
对数收益；因子窗口按 product panel，方向沿用训练集。34 个冻结因子全部计算成功，
`oos.json` 的因子集合与冻结库一致，无计算失败或遗漏。主指标为品种 √成交额加权 Pearson IC。

| 主指标统计 | Train（训练方向） | Val（训练方向） | Test（冻结方向） |
| --- | ---: | ---: | ---: |
| 34 个因子 IC 均值 | 0.007126 | 0.007307 | 0.002426 |
| 34 个因子 IC 中位数 | 0.006588 | 0.006682 | 0.002784 |

Test 主 IC 为正的因子有 28/34（82.4%），严格大于 0.005 的有 6/34（17.6%）；范围为
-0.004083 至 0.009865。覆盖率最低 86.90%、中位数 93.20%，全部超过 80%。上述计数只描述
冻结库分布，不作为测试集再筛选或准入。因子 IC 的均值不代表组合预测 IC 或交易收益。

本次冻结库在 Test 上多数因子方向仍为正，但主 IC 均值与中位数均低于 Val；结论仅限此
run、fold 和 seed。评估时 MCTS-LLM 对照仍在搜索，本记录不作方法间 OOS 比较，也未据此
修改运行配置、因子定义或方向。逐因子结果见该运行的 `oos.json` 与 `report.md`。

### 下游 Linear 与 LightGBM 测试集评估

同日使用 `atlas model <run_dir>` 在 train+val 合并集拟合两个模型，再用
`atlas model <run_dir> --test` 评估 2021–2022 测试集；两次命令均通过
`uv run --env-file .env` 执行。结果于北京时间 2026-10-10 10:01 保存至该运行的
`model/test.json`，模型与预处理保存在 `model/fit.json`，逐项指标见 `model/report.md`。

全部 34 个冻结因子均被保留，列顺序与冻结库一致；训练样本 4,390,385 行。Linear 为带
截距的最小二乘回归，`rcond=1e-12`，有效秩 34。LightGBM 沿用固定配置：50 轮、学习率
0.05、15 个叶子、最小叶样本数 100、L2 为 1、4 个线程、seed 42。均值填补和标准化仅在
train+val 拟合；测试使用保存的模型，没有重训、因子择优或参数调整。

| 模型 | TEST 加权 Pearson IC | TEST 加权 Spearman IC | 预测行数 | 品种数 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Linear | 0.006138 | 0.009752 | 2,215,926 | 72 | 100.00% |
| LightGBM | 0.010876 | 0.014928 | 2,215,926 | 72 | 100.00% |

加权口径仍为品种 √成交额权重。以上是组合预测 IC，与前文单因子 IC 均值分别报告。
模型拟合与搜索启动的源码记录不同，本次属于导入冻结库的独立下游实验；保存的 fit/test
实验规格一致，冻结因子身份和数据校验通过，原搜索与单因子 OOS 产物保留。

## 2026-10-10 Futures fold1：统一 product 窗口的库内相似度

本次比较 `experiment_overview.md` 中三个已冻结运行的完整因子库：

| 方法 | Run ID | 原搜索窗口 | 本次复算窗口 |
| --- | --- | --- | --- |
| AlphaPROBE | `futures-all-fold1-alphaprobe-42-1e4331978d` | contract | product |
| MCTS-LLM | `futures-all-fold1-mcts_llm-42-1ec3984a4b` | contract | product |
| AlphaAtlas+Annealing (V0) | `futures-all-fold1-annealing-42-1a44fb4805` | product | product |

三者使用相同的期货快照
`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`，
universe `all`、fold1、seed 42。通过 MarketData 加载 2015 年起的预热和历史，使用既有
`execute(..., panel="product")`：主力换约时窗口按品种延续，已知缺口仍重置；标签和评分行
继续由平台按真实合约与连续段构造。验证期为 2019-01-01 至 2020-12-31，共 1,918,863 行
eligible／有效目标观测。本次不使用 Test，也不重新选择因子或方向。

相似度沿用准入的 pooled validation Spearman 定义：每对因子仅保留共同有限行，在交集上
重新排名；至少重叠 100 行，取绝对相关性。对完整库的所有无序不同因子对等权平均，排除
对角线、不重复、不抽样、不填补。此指标不按品种或成交额加权；取绝对值后不受因子方向影响。

| 方法 | 因子数 | 因子对数 | 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AlphaPROBE | 37 | 666 | 0.193626 | 0.149755 | 0.425736 | 0.688010 | 0 |
| MCTS-LLM | 44 | 946 | 0.145717 | 0.091692 | 0.352664 | 0.700253 | 1 |
| AlphaAtlas+Annealing (V0) | 34 | 561 | 0.106387 | 0.057006 | 0.256845 | 0.689016 | 0 |

全部 2,173 对均有效；最小共同样本数依次为 1,648,201、1,571,522、1,514,270。
平均相似度排序为区域退火 < MCTS-LLM < AlphaPROBE；区域退火分别比后二者低 27.0%、45.1%。
这描述现有冻结库的秩相关冗余，不能据此推断方法因果优劣或 TEST 预测能力；搜索窗口和模型
并非完全一致，AlphaPROBE / MCTS-LLM 使用 gpt-5.5，区域退火使用 deepseek-flash。

MCTS-LLM 唯一达到 0.7 的因子对为 `624eef48efbf89ca417d5fb888650f288e49feaa4a364b03fb8bd5e155784a96`
与 `0bc2c31d874d933ca4a86666ef03591ca1aa42eb902323a22b45ccd913749fb1`，相关性
0.7002533626440841，共同样本数 1,571,522。这是统一 product 后的结果；不改变原 contract
搜索的准入记录或删除冻结成员。

三个冻结库均仅依赖内置算子。独立诊断读取冻结表达式并通过现有配置指纹、冻结库校验和、
因子身份及算子依赖校验，不恢复旧运行的外部算子运行时。AlphaPROBE 的 37 个因子和 MCTS-LLM
的 44 个因子全部按 product 重算，未复用 contract 缓存；区域退火复用 5 个 product 缓存、
重建 29 个缺失因子，并抽查 1 个缓存与重建值完全一致。原 run、trial、冻结库、OOS 与下游
模型产物均保留；总览中的历史 TEST 指标不因本次相似度诊断而改写。

本次结果保存时间为 `2026-10-10T10:26:05.108195+08:00`；项目源码来源记录在汇总文件中。运行命令：

```bash
POLARS_MAX_THREADS=8 uv run --env-file .env python -u \
  artifacts/comparisons/library_similarity_product_20261010/compute.py
```

[汇总](../../artifacts/comparisons/library_similarity_product_20261010/summary.json)与
[复算脚本](../../artifacts/comparisons/library_similarity_product_20261010/compute.py)
位于同一目录，三个 `<run_id>.json` 保存因子身份、每对相关性、共同样本数和有效状态。
已核对每个冻结成员均被包含，因子对无遗漏或重复，汇总与逐对明细一致；合成数据检查覆盖
共同有效行重排名、负相关取绝对值、同秩、常量以及样本不足。

## 2026-10-10 Futures fold1：MCTS-LLM + DeepSeek 完整评估

Run ID：`futures-all-fold1-mcts_llm-42-0ccecc542e`；模型 `deepseek-flash`，seed 42，500/500 次尝试完成后冻结 35 个因子。
沿用运行保存的 RiceQuant 原生 5m、product 窗口、未来 12 Bar 目标和数据快照
`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。
Train 为 2016–2018，Val 为 2019–2020，Test 为 2021–2022。独立后处理仅在搜索冻结且
释放运行锁后启动，读取 `.env`；没有将测试结果反馈给搜索、重新选因子或调整参数。

全部 35 个因子 OOS 计算成功；训练方向沿用。TEST 品种 √成交额加权 Pearson IC 均值
0.002512、中位数 0.001987，正 IC 26/35，
严格大于 0.005 的有 11/35，最低覆盖率 89.28%。
TEST 加权 Spearman IC 均值为 0.000301。

两种组合模型在 4,390,385 行 train+val 样本上拟合，按现有训练期
缺失与常量规则处理完整冻结库。Linear 使用带截距最小二乘、rcond=1e-12；LightGBM 固定
50 轮、学习率 0.05、15 叶、最小叶样本 100、L2=1、4 线程、seed 42。预处理仅拟合于
train+val；测试复用保存模型，不调参。组合预测指标与单因子均值分别报告。

| 模型 | TEST 加权 Pearson IC | TEST 加权 Spearman IC | 预测行数 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.007910 | 0.007652 | 2,215,926 | 100.00% |
| lightgbm | 0.008754 | 0.004179 | 2,215,926 | 100.00% |

库内相似度采用与既有三组相同的 Val/product 口径：每对共同有限行重新排名，计算绝对
Spearman，再对所有无序不同因子对等权平均。595 对全部有效，平均值
0.184539、中位数 0.131507、
P90 0.431388、最大值 0.699873，
达到 0.7 的有 0 对；最小共同样本数
1,612,605，验证期 eligible 行数 1,918,863。
复用 8 个匹配的 product 缓存，重建
27 个缺失因子。此相关性分布描述库内冗余，不代表交易收益或方法因果优劣。

逐因子结果见该运行的 `oos.json`、`report.md`；模型见 `model/fit.json`、`model/test.json`
与 `model/report.md`。配置、冻结库、数据及因子身份沿用平台校验，原搜索产物保留。
[相似度汇总](../../artifacts/comparisons/mcts_deepseek_0ccecc542e/summary.json)与逐对结果、
[后处理脚本](../../artifacts/comparisons/mcts_deepseek_0ccecc542e/finish.py)位于同一目录。
脚本按顺序调用 `atlas test`、`atlas model`、`atlas model --test`，再计算相似度、补写两张
总览表和本记录，最后运行 `pwsh -File scripts/check.ps1`；执行日志为
`logs/mcts_deepseek_0ccecc542e_finish.log`。

## 2026-10-11 Futures fold1：V1 Async 冻结库 OOS

Run ID：`futures-all-fold1-v1_async-42-b53b446ea7`；实现 `atlas_explorer_v1_async`，
模型 `deepseek-flash`，seed 42。运行保存的配置为并行 3 个计划、每计划 5 个候选，
Explorer 对话上限 40 轮。500/500 次尝试完成后冻结 31 个因子，入库率 6.2%，记录活动时间
43,135.6 秒（约 11 小时 59 分钟）。

检查点包含 100 个完整计划、连续且不重复的 500 个 trial 索引；各计划的候选交错提交，
结束时没有 pending 候选、活动研究组或 Explorer。方法记录的 31 个入库成员与冻结库一致，
区域累计尝试数为 500、入库数为 31。准入结果为 `accepted` 31、`quality_threshold` 424、
`behavior_duplicate` 35、`low_coverage` 9、`undefined_train_metric` 1。

本次读取项目 `.env`，通过独立入口评估全部冻结因子，结果于北京时间
2026-10-11 09:02 保存：

```bash
POLARS_MAX_THREADS=4 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 NUMBA_NUM_THREADS=4 \
  uv run --env-file .env atlas test artifacts/runs/futures-all-fold1-v1_async-42-b53b446ea7
```

沿用运行保存的 RiceQuant 原生 5m、product 窗口、未来 12 Bar 收盘对数收益和数据快照
`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。
Train 为 2016–2018，Val 为 2019–2020，Test 为 2021–2022；方向沿用训练集。
配置、冻结库、数据和因子身份均通过平台校验；31 个冻结因子 OOS 全部计算成功，
结果集合与冻结库一致，无重复或遗漏。

| 主指标统计 | Train（训练方向） | Val（训练方向） | Test（冻结方向） |
| --- | ---: | ---: | ---: |
| 31 个因子加权 Pearson IC 均值 | 0.009276 | 0.007660 | 0.002484 |
| 31 个因子加权 Pearson IC 中位数 | 0.008836 | 0.006965 | 0.001655 |

主指标按品种 √成交额加权。Test 主 IC 为正的因子有 23/31（74.2%），严格大于 0.005
的有 7/31（22.6%），范围为 -0.006127 至 0.014761；加权 Spearman IC 均值为 0.002662。
覆盖率最低 85.74%、中位数 95.22%，全部超过 80%。这些计数只描述完整冻结库的分布，
不用于测试集再筛选或准入。

本次多数因子在 Test 上保持正主 IC，但均值与中位数均低于 Val。结论限于此 run、fold
和 seed；单因子 IC 均值不代表组合预测 IC 或交易收益。搜索配置、冻结因子或方向均未修改。
逐因子结果见该运行的 `oos.json` 与 `report.md`；下游模型与库内相似度见下节。

### 下游 Linear、LightGBM 与库内相似度

同日使用全部 31 个冻结因子，在 4,390,251 行 train+val 样本上拟合两种模型，
再用保存的模型评估 2021–2022 测试集；结果于北京时间 2026-10-11 09:14 保存。
训练期预处理未移除因子，列顺序与冻结库一致，Linear 有效秩为 31。
Linear 使用带截距最小二乘、rcond=1e-12；LightGBM 固定 50 轮、学习率 0.05、15 叶、
最小叶样本 100、L2=1、4 线程、seed 42，与总览已有实验的模型配置一致。
均值填补和标准化仅在 train+val 拟合；测试不重新拟合、挑选因子或调参。

| 模型 | TEST 加权 Pearson IC | TEST 加权 Spearman IC | 预测行数 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.009586 | 0.015293 | 2,215,902 | 99.9989% |
| lightgbm | 0.006026 | 0.011197 | 2,215,902 | 99.9989% |

两模型均覆盖 72 个品种；2,215,926 行 eligible 测试观测中有 24 行所有因子缺失，因此不预测。
模型指标按品种 √成交额加权；以上组合预测 IC 与前文单因子 IC 均值分别报告。
拟合与测试保存的实验规格一致，因子集合、列顺序、数据与配置校验通过；本次模型拟合的
源码来源记录与搜索记录一致。原搜索与单因子 OOS 产物保留。

库内相似度沿用前四组的 Val 2019–2020 / product 口径，在共同有限行上逐对重新排名，
计算绝对 Spearman，再对全部无序不同因子对等权平均；不抽样、不填补、不按品种加权。
31 个因子的 465 对全部有效，无遗漏或重复，验证期 eligible 行数 1,918,863，
最小共同样本数 1,369,870。平均绝对 Spearman 为 0.077518，
中位数 0.027665，P90 为 0.224777，最大值 0.656651，
达到 0.7 的有 0 对。本次复用 6 个匹配的 product 缓存，重建 25 个缺失因子，
抽查缓存与重建值的最大绝对误差为 0。原四组已保存结果保留，没有重新计算。

运行命令均读取项目 `.env`，执行时设置 `POLARS_MAX_THREADS=4`、
`OPENBLAS_NUM_THREADS=1`、`OMP_NUM_THREADS=1`、`NUMBA_NUM_THREADS=4`：

```bash
uv run --env-file .env atlas model artifacts/runs/futures-all-fold1-v1_async-42-b53b446ea7
uv run --env-file .env atlas model artifacts/runs/futures-all-fold1-v1_async-42-b53b446ea7 --test
uv run --env-file .env python -u artifacts/comparisons/v1_async_b53b446ea7/compute.py
```

模型见该运行的 `model/fit.json`、`model/test.json` 与 `model/report.md`。
[相似度汇总](../../artifacts/comparisons/v1_async_b53b446ea7/summary.json)、
[逐对结果](../../artifacts/comparisons/v1_async_b53b446ea7/futures-all-fold1-v1_async-42-b53b446ea7.json)及
[复算脚本](../../artifacts/comparisons/v1_async_b53b446ea7/compute.py)保存了结果与复算入口。
本次相似度最低只说明当前冻结库的平均秩相关冗余较低，不说明预测能力或交易收益更强。

## 2026-10-12 Futures fold1：V1 Async 500→1000 续跑完整评估

Run ID：`futures-all-fold1-v1_async-42-b53b446ea7-extend1000`；父运行
`futures-all-fold1-v1_async-42-b53b446ea7`。沿用父运行的模型 `deepseek-flash`、seed 42、
fold1、3 个并行研究计划与每计划 5 个候选，从第 501 次追加至第 1000 次。
前 500 条方法记录和原 31 个冻结因子与父运行完全一致；累计 200 个计划全部结束，
无 pending 候选、活动研究组或 Explorer。最终冻结 58 个因子，新增 27 个；累计入库率
5.8%，追加阶段入库率 5.4%。累计活动时间 108,541.7 秒，较父运行增加
65,406.1 秒。

总计准入结果：`accepted` 58、`quality_threshold` 838、`behavior_duplicate` 79、
`low_coverage` 23、`undefined_train_metric` 2。追加 500 次分别为 27、414、44、14、1。

本次继承的是原运行 train/val 搜索状态，旧冻结库与 OOS、模型产物保留。父运行在 500 次
冻结后已评估并查看 2021–2022 Test，本次属于同一测试区间的复评，不作为新的独立盲测。
原前 500 次的退火按 500 总预算执行，续跑将总预算改为 1000，第 501 次前温度约
0.070621，再随累计进度下降；这与从零预设 1000 次预算的搜索不同。
未向搜索方法传入 Test 指标；本次完整冻结库评估不据 Test 重新筛选因子、方向或模型参数。

### 单因子 OOS

沿用 RiceQuant 原生 5m、product 因子窗口、未来 12 Bar 收盘对数收益，数据快照为
`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。Train 为 2016–2018，
Val 为 2019–2020，Test 为 2021–2022，方向沿用训练集。58/58 个因子计算成功，
身份、数量及顺序与冻结库一致；原 31 个因子的 OOS 结果与父运行逐项完全一致。

| 主指标统计 | Train（训练方向） | Val（训练方向） | Test（冻结方向） |
| --- | ---: | ---: | ---: |
| 58 个因子加权 Pearson IC 均值 | 0.008805 | 0.007924 | 0.002184 |
| 58 个因子加权 Pearson IC 中位数 | 0.008589 | 0.007573 | 0.001694 |

主指标按品种 √成交额加权。Test 正 IC 为 41/58（70.7%），严格大于 0.005
的有 14/58（24.1%），范围 -0.006127 至 0.014761。
Test 加权 Spearman IC 均值 0.002126，覆盖率最低
85.74%、中位数 95.40%。
新增 27 个因子的 Test 主 IC 均值 0.001839、中位数
0.001733，正 IC 18/27、严格大于 0.005 的有 7/27。
以上均描述完整冻结库及其新增成员，不用于测试集再准入。

### Linear 与 LightGBM

使用全部 58 个因子在 4,390,319 行 train+val 样本上拟合，预处理未移除任何
因子，Linear 有效秩为 58。训练均值填补与标准化、带截距最小二乘的 rcond=1e-12，以及
LightGBM 的 50 轮、学习率 0.05、15 叶、最小叶样本 100、L2=1、4 线程、seed 42 均
沿用原 500 次实验配置。模型保存后才进行 Test 预测，fit/test 保存的规格和因子列顺序一致。
模型拟合记录的源码来源与搜索记录一致；配置、数据、冻结因子身份通过平台校验。

| 模型 | TEST 加权 Pearson IC | TEST 加权 Spearman IC | 预测行数 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.011739 | 0.014334 | 2,215,914 | 99.9995% |
| lightgbm | 0.009524 | 0.010908 | 2,215,914 | 99.9995% |

两模型均覆盖 72 个品种，2,215,926 行 eligible 测试观测中有 12 行因所有因子缺失未预测。
相对父运行，Linear 加权 Pearson 从 0.009586 升至
0.011739，LightGBM 从 0.006026 升至
0.009524；两者加权 Spearman 则分别由
0.015293、0.011197 降至
0.014334、0.010908。这描述本次续跑在同一
测试区间的结果，不据此选择模型或推广为搜索方法的因果优劣；IC 不代表交易收益。

### Val/product 库内相似度

验证期 2019–2020，eligible 行数 1,918,863；每对共同有限行重新排名后计算绝对 Spearman，
对所有无序不同因子对等权平均，不抽样、不填补、不使用品种权重。全部 58 个冻结成员的
1,653 对均有效，无遗漏、重复或自身配对，最小共同样本数 1,369,870。

| 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| ---: | ---: | ---: | ---: | ---: |
| 0.076121 | 0.031223 | 0.215492 | 0.693620 | 0 |

本次没有可复用的因子值缓存，按现有执行接口重建 58 个缺失因子。原 31 个因子之间的
465 对与旧审计的重叠行数及相关性完全一致，相关性最大绝对差为 0。
平均相似度较 500 次的 0.077518 略降；原五份库的审计产物保留。

本次命令读取项目 `.env`，执行时设置 `POLARS_MAX_THREADS=4`、
`OPENBLAS_NUM_THREADS=1`、`OMP_NUM_THREADS=1`、`NUMBA_NUM_THREADS=4`：

```bash
uv run --env-file .env atlas test artifacts/runs/futures-all-fold1-v1_async-42-b53b446ea7-extend1000
uv run --env-file .env atlas model artifacts/runs/futures-all-fold1-v1_async-42-b53b446ea7-extend1000
uv run --env-file .env atlas model artifacts/runs/futures-all-fold1-v1_async-42-b53b446ea7-extend1000 --test
uv run --env-file .env python -u artifacts/comparisons/v1_async_b53b446ea7_extend1000/compute.py
```

逐因子结果见该运行的 `oos.json` 与 `report.md`；模型见 `model/fit.json`、`model/test.json`
与 `model/report.md`。[相似度汇总](../../artifacts/comparisons/v1_async_b53b446ea7_extend1000/summary.json)、
[逐对结果](../../artifacts/comparisons/v1_async_b53b446ea7_extend1000/futures-all-fold1-v1_async-42-b53b446ea7-extend1000.json)及
[复算脚本](../../artifacts/comparisons/v1_async_b53b446ea7_extend1000/compute.py)保存了口径和复算入口。

## 2026-10-12 Futures fold1：MCTS-LLM DeepSeek 500→1000 续跑准备

父运行 `futures-all-fold1-mcts_llm-42-0ccecc542e` 已完成 500 次、冻结 35 个因子。
按用户要求准备续跑目录 `futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000`，总预算
1000 次，从第 501 次继续。准备完成时尚未启动新增搜索，状态为 `interrupted`。

继承全部 500 条原子 trial、35 个库成员、494 个搜索节点、93 棵已启动树的历史、
当前树剩余 1 次局部扩展机会、RNG、FSA 统计、模型阶段与用量；方法状态逐项保持一致。
沿用 `deepseek-flash`、seed 42、fold1、product 窗口及父运行的配置和数据快照。
仅续跑目录的运行 ID、总预算、状态和来源记录改变，使用现有配置指纹与检查点校验。
配置、数据快照、运行时、检查点、库重建和方法状态往返校验通过。

原 500 次搜索及其冻结库、OOS 和组合模型产物保留。续跑目录不导入 OOS 或下游模型，
搜索仍仅使用 train/val。父运行的 Test 已查看，后续同一区间评估应标为复评。

在项目目录执行（读取 `.env`）：

```bash
uv run --env-file /home/ziyi/AlphaAtlas/.env atlas resume \
  /home/ziyi/AlphaAtlas/artifacts/runs/futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000
```


## 2026-10-15 MCTS 续跑：相关性路径性能诊断

针对 `futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000` 的只读诊断，固定第 966 次
候选及其当时 56 个已入库因子。独立进程经 MarketData 加载原 train/val 数据；不调用
模型、不增加 trial、不改原运行状态。基准使用 4 个 Polars 线程，不能直接当作原进程
未限制线程时的各阶段耗时或整轮加速比。

同样 56 对训练 Pearson 相关性，默认 512 MiB 缓存从冷态开始耗时 164.838 秒，
其中取值与重建（包含公式执行、过滤、数据对齐）151.304 秒，相关性指标 5.447 秒，
配对对齐及其余处理 8.086 秒；57 次缓存未命中，结束时仅保留 6 个训练值面板。
独立基准保留全部 57 个因子面板后，同一计算耗时 14.545 秒，指标计算 5.908 秒，
对齐及其余处理 8.636 秒；112 次取值全部命中，结果与冷态逐项完全一致。
全部训练值面板估计占 4.436 GB。这是性能探针的内存对照，未改变正式运行缓存设置。

另从现存成员缓存读取 8 个 Val 面板，选其中一个与其余 7 个比较：加载耗时
0.522 秒，配对对齐 1.077 秒，Spearman 1.930 秒。每个面板包含 1,918,863 行，
各对仍按共同有限观测计算；该结果是 7 对基准，不是全库 56 对实测。

原运行第 901–966 次记录中，质量拒绝的 53 次平均评估 7.94 秒；入库的 8 次平均
387.62 秒，行为重复的 3 次平均 405.85 秒。这两组候选自身公式和指标计算分别仅
4.78、6.35 秒。日志差额落在准入路径，但未单独埋点，不能把差额全部归为公式重算。
代码中的成员缓存缺失会通过 `_rebuild_member` 重跑原 train/val 评估。

本次测量支持优先减少因子值重复重建；“全时段且有几十个成员”不能单独解释分钟级开销。
未调整相关性口径、抽样、准入、搜索策略或正在运行的进程。

[训练相关性探针](../../artifacts/diagnostics/mcts_correlation_20261015/probe.py)、
[56 对冷态／内存对照](../../artifacts/diagnostics/mcts_correlation_20261015/threads_4_pairs_56.json)、
[7 对 Val 缓存基准](../../artifacts/diagnostics/mcts_correlation_20261015/val_cached_pairs.json)
保存了测量及其范围。

### ChaoticTrader_Future 相似度实现对照

只读检查相邻工作区的 `market/dataset.py:ensure_sample_set`、
`factor/similarity.py`、`factor/similarity_store.py:nearest` 和
`factor/admission.py:check`。该实现按年与品种分层，固定抽取最多 32,768 个行 ID；
再按品种在固定样本序列中每 10 行取 1 行，形成约 3,300 行的共同布局。
这是相似度抽样，不是将原生 1m K 线重采样。各次评估区间之外和缺失因子值仍为 NaN。

每个因子的样本值与品种内排名使用 float32 缓存；库矩阵不变时复用。普通相似度反馈
先用缓存排名批量筛选，再对前 5 个邻居按共同有限观测重新排名复核。正式准入逐一比较
全部库成员，仍只使用上述固定样本。所谓精确复核指样本上的精确 Spearman，未回到全时段。
其指标为品种等权时间序列 Spearman，重复阈值为绝对相关性大于 0.80；与本项目
Train 品种加权 Pearson、Val 全量池化 Spearman 及 0.70 准入阈值不同。

独立合成基准直接调用上游纯 NumPy 函数：seed 42、72 个品种、32,768 行、
300 个库因子加 1 个候选、2% 独立缺失；布局含 3,312 行。三次测量中位数如下：

| 范围 | 耗时 |
| --- | ---: |
| 301 个因子的样本值与排名预处理 | 0.507 秒 |
| 热缓存筛选 300 个因子，再复核前 5 个 | 0.043 秒 |
| 热缓存逐一精确比较全部 300 对 | 1.782 秒 |

此基准不含数据库、行情读取、公式执行或模型调用，不是上游真实研究运行的端到端耗时。
不能与本项目全量 56 对 Train 的 14.545 秒直接解释为实现加速比。

另对本次续跑现存 8 个 Val 成员缓存做只读对照，每个面板 1,918,863 行。
固定一个成员与其他 7 个比较，保持全量共同有限观测和原 Spearman 算法，三次中位数
从逐对 join 的 2.501 秒降到预对齐列的 1.427 秒，减少约 43%；7 对的重叠行数与
相关性全部一致，最大绝对差为 0。一次排序与行 ID 检查耗时 0.208 秒，单独列出。
这组现存缓存与前一节 7 对基准的成员不同，不能跨组比较绝对耗时。

结论：上游速度主要受益于固定小样本，同时复用布局、排名和矩阵。保持本项目口径时，
可优先减少逐对行对齐及重复分组；全局缓存排名不能直接替代不同共同有限样本上的重新排名。
本次仅验证预对齐方案，未改变正式计算、抽样、阈值或运行状态。

[对照探针](../../artifacts/diagnostics/upstream_similarity_20261015/probe.py)及
[原始结果](../../artifacts/diagnostics/upstream_similarity_20261015/results.json)
记录了因子 ID、逐次耗时及数值一致性。

### 预对齐接入正式计算后的验证

训练相关性和正式库准入已复用排序后的内存值缓存。同一行布局直接配列；行集合不同
或存在空行 ID 时继续 join。完整时段、精度、共同有限样本、品种权重、排名和准入阈值
保持原口径，不新增持久状态。缓存生命周期见[研究工具](../research_tools.md#训练相关性)。

对上述续跑做独立只读基准，使用 4 个 Polars 线程；同一进程中分别强制走正式代码的
join 分支和预对齐分支，各运行三次，取中位数。排除数据加载和首次因子值重建：

| 正式入口与范围 | join 对照 | 预对齐 | 减少 |
| --- | ---: | ---: | ---: |
| Train：第 966 次候选对当时 56 个库成员，2,471,522 行 | 12.804 秒 | 5.818 秒 | 54.6% |
| Val：现存缓存中一个候选对 7 个成员，1,918,863 行 | 2.506 秒 | 1.500 秒 | 40.1% |

Train 全部 56 对的有效样本数与相关性一致，最大绝对差为 0。Val 使用
`FactorLibrary.consider` 及原期货质量门槛，完整反馈完全一致，包括是否准入、最近成员、
最大绝对相关性和比较完整性。Val 本次实际缓存成员清单见原始结果，不能与之前不同
成员组合的耗时混用。上述比例仅描述相关性热缓存路径，不是整轮搜索加速比。

独立进程首次准备 57 个 Train 值面板耗时 148.070 秒，单独列出；进程重启仍需按需
填充缓存。本次没有模型请求，不改原运行、trial 或原缓存文件。正式检查通过：
Ruff、格式、算子目录及 740 项测试。新增覆盖乱序、共同缺失和重复值、部分／无重叠、
空行 ID、重复缓存行、恢复、训练分组与品种权重；原 200 成员缓存与恢复测试继续通过。

[正式路径探针](../../artifacts/diagnostics/prealigned_correlation_20261015/probe.py)及
[完整结果](../../artifacts/diagnostics/prealigned_correlation_20261015/results.json)
保存了三次耗时和逐对结果。

## 2026-10-15 Futures fold1：MCTS-LLM DeepSeek 500→1000 续跑完整评估

Run ID：`futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000`；父运行
`futures-all-fold1-mcts_llm-42-0ccecc542e`。沿用 `deepseek-flash`、seed 42、fold1 和父运行
搜索状态，从第 501 次追加至第 1000 次。前 500 条原子 trial 与父运行逐字节一致，原 35 个
冻结因子的定义、方向和报告完全保留。最终冻结 60 个因子，新增 25 个；累计入库率 6.0%，
追加阶段入库率 5.0%。记录的累计活动时间为 361,730.7 秒，追加阶段为 220,222.1 秒。

总计结果：`accepted` 60、`quality_threshold` 836、`behavior_duplicate` 83、
`low_coverage` 20、`constant_factor` 1；追加 500 次分别为 25、433、33、8、1。

父运行已查看 2021–2022 Test，本次属于同一测试区间的复评，不是新的独立盲测。
续跑继承前 500 次树、随机状态与研究记录，不等同于从零设置 1000 次预算。
本次在完整因子库冻结后评估，不按 Test 重新筛选因子、方向或模型参数，也不将 Test
指标反馈给搜索。原 500 次运行及其 OOS、模型、相似度产物保留。

### 单因子 OOS

沿用 RiceQuant 原生 5m、product 因子窗口、未来 12 Bar 收盘对数收益，数据快照为
`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。
Train 为 2016–2018，Val 为 2019–2020，Test 为 2021–2022，方向沿用训练集。
60/60 个因子计算成功，身份及顺序与冻结库一致。原 35 个因子的样本数、品种数和覆盖率
与父运行一致，全部 IC 最大绝对差为 1.39×10⁻¹⁷，处于浮点舍入量级。

| 主指标统计 | Train（训练方向） | Val（训练方向） | Test（冻结方向） |
| --- | ---: | ---: | ---: |
| 60 个因子加权 Pearson IC 均值 | 0.008284 | 0.009095 | 0.001567 |
| 60 个因子加权 Pearson IC 中位数 | 0.008155 | 0.007884 | 0.001223 |

主指标按品种 √成交额加权。Test 正 IC 为 38/60（63.3%），严格大于 0.005 的有
14/60（23.3%），范围 -0.005808 至 0.008241。Test 加权 Spearman IC 均值为
0.000212，覆盖率最低 87.01%、中位数 95.01%。
新增 25 个因子的 Test 主 IC 均值 0.000244、中位数 -0.000178，正 IC 为 12/25，
严格大于 0.005 的有 3/25。完整库的主 IC 均值低于父运行的 0.002512；这些结果只描述
本次冻结库，不代表更多预算必然降低表现，也不等同于组合预测 IC 或交易收益。

### Linear 与 LightGBM

两个模型使用全部 60 个因子，在 4,390,385 行 train+val 样本上拟合；没有因子被预处理
移除，Linear 有效秩为 60。沿用父运行的训练均值填补、标准化及模型参数：Linear 带截距、
rcond=1e-12；LightGBM 固定 50 轮、学习率 0.05、15 叶、最小叶样本 100、L2=1、
4 线程、seed 42。Test 复用保存模型，不重新拟合或调参。

| 模型 | TEST 加权 Pearson IC | TEST 加权 Spearman IC | 预测行数 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.004354 | 0.007351 | 2,215,926 | 100% |
| lightgbm | 0.009233 | 0.004979 | 2,215,926 | 100% |

两模型均覆盖全部 2,215,926 行 eligible 测试观测和 72 个品种。相对父运行，Linear 的
加权 Pearson 从 0.007910 降至 0.004354，LightGBM 从 0.008754 升至 0.009233；
加权 Spearman 则分别从 0.007652 降至 0.007351、从 0.004179 升至 0.004979。
拟合与测试规格及因子列顺序一致，配置、冻结库、数据和因子身份通过平台校验。
拟合源码来源与搜索启动记录不同；这里按既有模型协议导入冻结表达式执行后处理，保留
双方来源记录，不改原搜索配置与状态。

### Val/product 库内相似度

验证期 2019–2020，eligible 行数 1,918,863；沿用全量共同有限行上的绝对 Spearman。
每对在有效交集上重新排名，所有无序不同因子对等权平均，不抽样、不填补、不使用品种权重。
60 个冻结成员的 1,770 对全部有效，无遗漏、重复或自身配对；最小共同样本数 1,545,841。

| 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| ---: | ---: | ---: | ---: | ---: |
| 0.131032 | 0.069709 | 0.355669 | 0.699873 | 0 |

复用 1 个匹配缓存，按公共执行接口重建 59 个缺失因子；缓存与重建抽查最大误差为 0。
原 35 个因子之间的 595 对与父运行的相似度和重叠行数完全一致。
平均相似度从 0.184539 降至 0.131032，表示本次库内平均秩相关冗余减少；不据此推断
预测效果或交易收益更好。此前六份相似度产物保留。

本次命令读取项目 `.env`，并设置 `POLARS_MAX_THREADS=4`、`OPENBLAS_NUM_THREADS=1`、
`OMP_NUM_THREADS=1`、`NUMBA_NUM_THREADS=4`：

```bash
uv run --env-file .env atlas test artifacts/runs/futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000
uv run --env-file .env atlas model artifacts/runs/futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000
uv run --env-file .env atlas model artifacts/runs/futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000 --test
uv run --env-file .env python -u artifacts/comparisons/mcts_deepseek_0ccecc542e_extend1000/compute.py
uv run --env-file .env python artifacts/comparisons/mcts_deepseek_0ccecc542e_extend1000/summarize.py
```

逐因子结果见该运行的 `oos.json` 与 `report.md`；模型见 `model/fit.json`、`model/test.json`
和 `model/report.md`。[相似度汇总](../../artifacts/comparisons/mcts_deepseek_0ccecc542e_extend1000/summary.json)、
[逐对结果](../../artifacts/comparisons/mcts_deepseek_0ccecc542e_extend1000/futures-all-fold1-mcts_llm-42-0ccecc542e-extend1000.json)及
[评估与一致性汇总](../../artifacts/comparisons/mcts_deepseek_0ccecc542e_extend1000/evaluation_summary.json)
保存了原始精度数值与父运行对照；同目录的 `compute.py`、`summarize.py` 为复算入口。
