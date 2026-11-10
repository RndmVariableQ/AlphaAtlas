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

## 2026-10-16 Futures fold1：AlphaPROBE DeepSeek 1000 次完整评估

Run ID：`futures-all-fold1-alphaprobe-42-7d74488b25`。使用 `deepseek-flash`、
`Qwen3-Embedding-0.6B`、seed 42，从零设置 1000 次预算；没有继承旧的 500 次运行。
最终完成 1000 条原子 trial，冻结 66 个因子，入库率 6.6%。累计活动时间 51,549.2 秒
（约 14 小时 19 分钟），生成耗时 43,595.5 秒，评估及准入耗时 6,654.0 秒。
演化图 308 个节点、18 轮父因子检索、238 个生成批次（含失败批次）。

搜索配置：pool capacity 50、top-k 15、每批 offspring 5、depth penalty 0.05、
retrieval penalty 0.10、最低训练质量 0.006；temperature 1.0、输出上限 65,536 tokens。
开放 `futures_cta` 参考库，不预置初始因子。记录 1,085 次 chat 请求、17 次 embedding 请求；
prompt / completion / embedding tokens 分别为 21,125,315 / 6,715,476 / 25,532，
未知用量请求为 0。这是服务返回的累计用量，不是精确费用。

总计结果：`accepted` 66、`quality_threshold` 719、`low_coverage` 70、
`behavior_duplicate` 63、`compile_error` 81、`expression_duplicate` 1。
81 次编译失败占预算 8.1%：47 次为空表达式、29 次引用不存在的字段 `close_p1`、
5 次超过表达式节点／深度预算。这些搜索失败已计入 1000 次预算，与冻结库 OOS 计算失败不同。
此前输出上限为 4,096 的独立失败启动 `futures-all-fold1-alphaprobe-42-0f4f17143b`
及其 3 次尝试保留，不合并入本次运行。

本次与总览中的 GPT-5.5 AlphaPROBE 使用同一数据快照、fold、字段、规则和 seed，
但预算为 1000 而非 500，因子窗口为 product 而非 contract，输出上限也从 4,096
变为 65,536；不能将结果差异单独归因于语言模型。2021–2022 Test 已用于既有实验，
本次是新冻结库在同一测试区间的评估，不是新取得的独立盲测区间。
搜索仅使用 train/val，本次后处理不按 Test 重新筛选因子、方向或模型参数。

### 单因子 OOS

沿用 RiceQuant 原生 5m、product 因子窗口、未来 12 Bar 收盘对数收益；目标不跨真实
合约／连续段。数据快照为
`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。
Train 为 2016–2018，Val 为 2019–2020，Test 为 2021–2022；使用冻结训练方向。
66/66 个因子计算成功，身份和顺序与冻结库一致。

| 主指标统计 | Train（训练方向） | Val（训练方向） | Test（冻结方向） |
| --- | ---: | ---: | ---: |
| 66 个因子加权 Pearson IC 均值 | 0.009067 | 0.007709 | 0.002365 |
| 66 个因子加权 Pearson IC 中位数 | 0.009026 | 0.007189 | 0.002410 |

主指标按品种 √成交额加权。Test 正 IC 为 51/66（77.3%），严格大于 0.005 的有
14/66（21.2%），范围 -0.006227 至 0.010777；加权 Spearman IC 均值为 0.000062。
Test 覆盖率最低 89.22%、中位数 95.89%。这些是单因子统计，不是组合预测 IC 或交易收益。

### Linear 与 LightGBM

两个模型使用全部 66 个因子，在 4,390,121 行 train+val 样本上拟合；没有因子被预处理
移除，Linear 有效秩为 66。因全部因子缺失而排除的训练行共 264 行。
沿用统一配置的训练均值填补与标准化：Linear 带截距、rcond=1e-12；LightGBM 固定
50 轮、学习率 0.05、15 叶、最小叶样本 100、L2=1、4 线程、seed 42。
拟合保存的源码来源与搜索启动记录一致。Test 复用保存模型，不重新拟合或调参。

| 模型 | TEST 加权 Pearson IC | TEST 加权 Spearman IC | 预测行数 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.012144 | 0.010838 | 2,215,878 | 99.9978% |
| lightgbm | 0.020982 | 0.009791 | 2,215,878 | 99.9978% |

两模型均覆盖 72 个品种，eligible 测试观测 2,215,926 行；48 行因全部因子缺失未预测。
拟合与测试规格及因子列顺序一致；模型参数与已有 MCTS + DeepSeek 1000 次评估完全一致，
快照、fold、字段、universe、warmup、seed、目标及窗口也一致。
同为 1000 次预算，MCTS 与 V1 Async 的结果来自 500→1000 续跑，本次为从零运行，
仍需区分搜索路径和冻结库，不能据此声称方法在其他 seed 或测试区间也更优。

### Val/product 库内相似度

验证期 2019–2020，eligible 行数 1,918,863；66 个冻结成员的 2,145 对全部有效，
无遗漏、重复或自身配对。最小共同样本数 1,572,236。每对在全量共同有限观测上重新排名，
计算绝对 Spearman，所有无序不同因子对等权平均；不抽样、不填补、不使用品种权重。

| 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| ---: | ---: | ---: | ---: | ---: |
| 0.109710 | 0.053192 | 0.294732 | 0.695848 | 0 |

本次未找到可复用的成员缓存，按公共 MarketData／DSL 接口重建全部 66 个因子的 Val 值；
没有缓存抽查项目，也未覆盖原搜索缓存。冻结清单、配置、数据快照和因子身份校验通过。
与既有相似度审计的快照、split、product 窗口及 eligible 行数一致。
平均相似度低于 GPT-5.5 AlphaPROBE 500 次库的 0.193626，但两份库的搜索条件不同；
该差异仅描述库内平均秩相关冗余，不代表预测能力或交易收益更好。

本次后处理设置 `POLARS_MAX_THREADS=4`、`OPENBLAS_NUM_THREADS=1`、
`OMP_NUM_THREADS=1`、`NUMBA_NUM_THREADS=4`；三个 `atlas` 命令读取项目 `.env`：

```bash
uv run --env-file .env atlas test artifacts/runs/futures-all-fold1-alphaprobe-42-7d74488b25
uv run --env-file .env atlas model artifacts/runs/futures-all-fold1-alphaprobe-42-7d74488b25
uv run --env-file .env atlas model artifacts/runs/futures-all-fold1-alphaprobe-42-7d74488b25 --test
uv run python artifacts/comparisons/alphaprobe_deepseek_7d74488b25/compute.py
uv run python artifacts/comparisons/alphaprobe_deepseek_7d74488b25/summarize.py
```

逐因子结果见[运行报告](../../artifacts/runs/futures-all-fold1-alphaprobe-42-7d74488b25/report.md)
及同目录的 `oos.json`；两个模型见[模型报告](../../artifacts/runs/futures-all-fold1-alphaprobe-42-7d74488b25/model/report.md)
与 `model/fit.json`、`model/test.json`。
[相似度汇总](../../artifacts/comparisons/alphaprobe_deepseek_7d74488b25/summary.json)、
[逐对结果](../../artifacts/comparisons/alphaprobe_deepseek_7d74488b25/futures-all-fold1-alphaprobe-42-7d74488b25.json)及
[评估一致性汇总](../../artifacts/comparisons/alphaprobe_deepseek_7d74488b25/evaluation_summary.json)
保留原始精度数值；同目录的 `compute.py`、`summarize.py` 为复算入口。

## 2026-10-19 Futures kernel 5m fold1：V2 观测频率覆盖

运行产物：`artifacts/runs/futures_kernel_5m-all-fold1-v2-42-3cca6b6671/`。使用
`gpt-6-luna`、seed 42、product 面板和未来 12 根 5m `adj_close` 对数收益目标。
本轮 Explorer 已明确基础数据频率不限制研究观测频率，按实际 context 列出可选频率。
上层退火算法沿用原实现。本次记录仅涉及 Train/Val 搜索，未执行 OOS。

运行已冻结：100 次预算实际提交 74 次，剩余 26 次；涉及 32 个不同 factor ID，入库 3 个。
记录 333 次模型请求，其中 2 次用量未知；未生成 `failures.json`。

| 观测频率 | 涉及的不同因子数 | Train 提交 | Valid 提交 | 首次 trial |
| --- | ---: | ---: | ---: | ---: |
| 5m | 29 | 52 | 14 | 1 |
| 15m | 2 | 2 | 2 | 63 |
| 30m | 1 | 2 | 2 | 33 |
| 60m | 1 | 1 | 1 | 14 |
| 1d | 0 | 0 | 0 | 未执行 |

频率按候选表达式的字段 token 统计，忽略注释与字符串；无后缀字段按基础 5m 计。
不同因子按平台 factor ID 去重，Train/Valid 与重复提交不增加不同因子数。
一个 5m/60m 混合频率因子同时计入两项，因此频率列不可直接相加为总因子数。
入库成员中，2 个仅使用 5m，1 个仅使用 15m。

提前结束来自 Explorer 调用 `report_exploration`，报告当前没有不与待研究问题实质重叠、
且具备明确判别力的新方向；V2 在活动组结束后据此停止搜索。终态仍有 33 条 open、
17 条 in_progress、10 条 resolved 提案。其中 `proposal:24:3` 和 `proposal:25:1` 是明确的
`@1d` 方向，状态均为 open，研究记录为空；两者所在轮次选中了其他方向。
因此日线缺失不能解释为平台不支持或日线实验无效；它们尚未进入执行验证。

本轮说明放开观测频率选择已使 15m、30m、60m 进入实际评估，但未形成均衡覆盖。
后续应区分频率选择偏好、按空位与评分选择提案、以及“无新增方向”触发停止三个环节；
停止前可先检查既有未执行提案的可行性。上述是本次运行的观察，不推断其他 seed 的结果。

原始证据见[运行报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v2-42-3cca6b6671/report.md)
及同目录 `checkpoint.json`、原子 `trials/`。本轮监测未修改实验中的提示词、配置、预算或退火算法。

## 2026-10-19 Futures kernel 5m fold1：V2 221 次阶段审查

运行产物：`artifacts/runs/futures_kernel_5m-all-fold1-v2-42-2e3e0d89bf/`。
1000 次预算实际提交 221 次，入库 3 个，剩余 779 次；运行在北京时间 23:09 的下一次
Explorer 请求中因 HTTP 400 失败，并未正常完成或冻结。只审查既有 Train/Val 记录，未运行 OOS。
本轮保存的 Explorer 提示词和输入已包含频率覆盖反馈与未核验 open 提案检查。

原子 trial 与检查点统计一致：167 次 Train、54 次 Valid，涉及 78 个不同 factor ID。
Train 涉及 78 个不同因子，同式重复提交 89 次；Valid 涉及 30 个不同因子，同式重复提交
24 次，其中至少 11 次连 controls 原始文本也相同。不同控制组的同式 Valid 可以对应不同
研究问题，不能将全部同式提交直接计作无用实验。每次实际提交仍按公共预算扣费。

| 阶段 | 结果 | 次数 |
| --- | --- | ---: |
| Train | screened | 136 |
| Train | behavior_duplicate | 30 |
| Train | constant_factor | 1 |
| Valid | research_criteria_failed | 48 |
| Valid | quality_threshold | 3 |
| Valid | accepted | 3 |

48 次研究判据拒绝的首个失败原因分别为：Valid 绝对主 IC 不足 0.005 共 40 次、控制增益
为负共 7 次、控制增益不可计算共 1 次。54 次 Valid 中，45 次的 Train 主 IC 绝对值已不超过
平台入库门槛 0.005；其中包括研究基准和反证，不等于这些验证均无研究用途。48 次研究拒绝中，
仅同一因子的两次验证在平台 IC 与覆盖率门槛上达标，均因控制增益 -0.00282 未通过研究判据；
尚未执行其最终查重，不能断言取消研究门槛即可新增成员。

本轮不同因子的频率覆盖为 5m 21、15m 21、30m 16、60m 15、1d 14；混合频率分别计入，
不可直接相加为因子总数。三个成员来自 trial 43 的 5m/15m 高波动条件收益、trial 176 的
日线收盘/VWAP 相对位置及 trial 183 的 60m 成交量异常滞后量。其定向 Val 主 IC 分别为
0.009417、0.014000、0.007733。此前未覆盖日线的问题已改变，均衡不等于准入产出提高。

复选身份存在实际缺口：23 条标题明确声称复选已有 Proposal 的记录均使用了新 ID，其中
22 条原提案在审查时仍为 open。例如 `proposal:50:1` 声称复选 `proposal:49:2`，但两者
分别保存为 in_progress 和 open。当前服务端按归一化正文匹配，模型改写机制、条件、预测或
竞争解释后不能沿用原 ID。终态为 55 条 open、76 条 in_progress、12 条 resolved。
同一 `RETURN($adj_close,12)` 因子累计提交 29 次，跨研究组的结果复用不足；不能仅以新题名
或新的 Exploration 解释为新因子发现。

最后失败的 Explorer 对话只有 7 个模型回复，但已累计 1,630,090 个 content 字符、
3,066,810 字节 messages JSON。三次 list_proposals 各返回约 46.4–46.7 万字符，包含
完整提案与研究历史，超过工具所描述的紧凑列表职责。随后请求返回 HTTP 400；现有失败记录
未保留供应商错误细节，不能据此确认具体是上下文上限、请求大小还是其他请求校验失败。
累计记录 1149 次模型请求、88,507,098 个输入 token、538,396 个输出 token，2 次用量未知。

本次审查只更新说明与历史证据，没有修改搜索机制或恢复模型调用。优先修复事项为复选身份
绑定和提案列表的返回范围；后续研究应通过已有查询复用证据，区分冲库候选与必要基准，
不以免费重复评估、放宽准入或改变退火公式掩盖预算效率问题。

原始证据见[运行报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v2-42-2e3e0d89bf/report.md)、
同目录 `checkpoint.json`、`failures.json` 与原子 `trials/`。

## 2026-10-20：V2 合并评估后首次 1000 次预算运行中断

Run ID：`futures_kernel_5m-all-fold1-v2-42-8d10a0f694`。模型 `gpt-6-luna`，
沿用 Research Kernel 复权 5m 快照、fold1、all、seed 42，默认四个数值字段。
本轮使用每次候选统一 Train/Val 的 V2，预算 1000；截至 01:37 中断时完成 84 次、
79 个不同因子、4 个入库成员，剩余 916 次，尚未冻结，未执行 OOS。

84 条原子记录均为 `stage=valid`；方法反馈、候选次数与实际扣费对应。
按编译后的因子 ID 及逐组对照表达式 ID 核对，没有相同因子与相同对照组的重复提交。
5 次 `expression_duplicate` 来自已有因子在不同对照实验中的再次提交，不是新增因子。
拒绝原因计数：研究判据 53、平台质量 12、行为重复 9、表达式重复 5、常数 1，接纳 4。
不同因子的频率覆盖为 5m 33、15m 14、30m 16、60m 12、1d 11；混合频率分别计入。

中断发生在 Explorer 的新 Region 交接：父区域 `continent_ids` 在内存中是 tuple，
V2 直接继承后传给要求 list 的公共 `_validate_exploration`，抛出
`new region must reference an existing continent`。该方向继承的 `price_volume` 实际存在，
不是模型引用未知大陆，也不是模型服务失败。JSON 检查点已经将该字段保存成 list。
修复在 V2 交接处显式构造列表，保留原 Region 身份、提案和评分，不改上层退火；
合成回归覆盖新方向当场交接与保存响应后恢复两个路径。

一次性监控脚本于 01:37:33 观察到 `failed`，记录 `FOLLOWUP_BLOCKED` 后退出，
未启动 DeepSeek，也没有通知或唤醒对话来处理失败；仅留下后台日志不足以实现持续值守。
原失败与已提交 trial 保留，恢复沿用本轮检查点和剩余预算，不清空历史或重开计数。

10:22 起修复后恢复，正常保存 trial 85–89，随后以“无新增方向”提前冻结为 89/1000、
4 个成员，剩余 911 次未使用。终态共有 84 个不同因子；频率覆盖为 5m 35、15m 15、
30m 16、60m 13、1d 12。仍有 47 条 in_progress，其中 34 条为计划 continue、
Miner plan_complete、Finding continue，另 11 条 Miner blocked、2 条 Analyst stop。
无活动任务并不代表这 34 条问题已经解决；此前“已有研究记录一律不可复选”的限制使它们
不能继续，同时结束检查只看未研究的 open，从而允许过早冻结。

复选补充支持未解决、未在执行、上轮未阻塞且 Finding 要求 continue 的提案；
按原引用保留身份、正文与区域，Analyst 收到原方向上轮计划、总结、Finding 和反馈。
结束检查也处理这些可继续项，并在异步任务完成后重新判断先前的结束报告，
避免 Explorer 报告结束后、Analyst 才提交 continue 的时序造成漏调度。
13 条明确阻塞／停止的提案不自动重启，上层温度、奖励与 Metropolis 公式保持原样。
已冻结的 89 次运行保留为历史证据，未修改其状态、预算或因子库来继续实验。

证据见[运行报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v2-42-8d10a0f694/report.md)、
同目录 `checkpoint.json`、`failures.json` 与原子 `trials/`，以及
[接续日志](../../artifacts/monitors/v2-8d10a0f694-followup.log)。

## 2026-10-20：DeepSeek V2 在第 835 次后的计划编码失败

Run ID：`futures_kernel_5m-all-fold1-v2-42-1df434f87b`。模型 `deepseek-flash`，
预算 1000，沿用相同 kernel 数据快照、all、fold1、四字段和 seed 42。
用户手动续跑后，截至 23:03 完成 835 次、31 个入库成员，剩余 165 次，尚未冻结，未执行 OOS。

Analyst 组 165 累计 104 个回复；56 次 submit_plan 中，前两次是无效 JSON，
其后 54 次均在工具参数外多放了一层 `arguments`，内层还出现 JSON 字符串、嵌套包装等形式。
服务端在外层查找必需的 decision，因此有效计划也反复收到
`missing required tool arguments: decision`，最终耗尽原 100 个研究回合、提交回合与 3 次修复。
这是参数编码失败，不是第 836 个因子的数值评估失败。

修复在 submit_plan 边界还原有限层的编码包装，保留完整门禁；目录返回的分类 ID 也按
既有 taxonomy 归一。耗尽检查点重放最后一条已保存回复，不调用模型、不追加回合或清零计数。
对该真实检查点进行仅内存重放时，最后提交通过完整校验，原方向、原始调用参数、回合与用量
均保留；没有写回检查点、改动 trial 或执行新的真实评估。合成回归覆盖不同编码、恢复重放，
以及编码还原后仍拒绝越权字段的边界。

证据见[运行报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v2-42-1df434f87b/report.md)、
同目录 `checkpoint.json`、`failures.json` 与 `trials/`。

## 2026-10-21 Futures kernel 5m fold1：V2 DeepSeek 1000 次完整评估

Run ID：`futures_kernel_5m-all-fold1-v2-42-1df434f87b`。使用 `deepseek-flash`、
all、fold1、seed 42，从零设置 1000 次预算。第 835 次后的计划编码失败修复后，用户按原
检查点续跑，2026-10-21 02:32（北京时间）完成 1000 次并冻结 37 个因子，入库率 3.7%。
中断和恢复均沿用同一运行、1000 次预算与原子记录。
记录的累计活动时间为 55,685.7 秒，约 15 小时 28 分钟。

本轮使用独立资产 `futures_kernel_5m`：RiceQuant final 主力真实合约的 kernel 原生 1m
数据，按用户指定口径合并为 5m，沿用 kernel 的换月累计比例复权。特征字段为
`adj_close`、`adj_vwap`、`volume`、`amount`；目标为未来 12 根基础 5m 的 `adj_close`
对数收益，目标不跨真实合约／连续段，因子窗口按 product 计算。运行记录的 MarketData
快照为 `8f689ef3680e43b4f8f1c21f92bd21ad5f4c6a308e19ad31530f60bda4b4b49a`。
Train 为 2016–2018，Val 为 2019–2020，Test 为 2021–2022，预热从 2015-01-01 开始。
聚合和来源见[数据记录](data.md#2026-10-19-research-kernel-复权五分钟导出)。
该数据与既有 `futures` 原生 5m、不复权快照不同，不能将跨表差异单独归因于方法或模型。

1000 条原子 trial 均为 `stage=valid` 的合并 Train/Val 提交，每次只扣一次预算；
973 条成功计算 Train/Val 指标，另有 18 次评估超时、5 次训练指标未定义、4 次常数因子。
共涉及 950 个不同编译因子 ID；同一因子可以用于不同对照研究，不将这些提交视为新因子。
最终结果计数为：研究判据拒绝 888、接纳 37、行为重复 31、计算错误 18、平台质量拒绝 13、
训练指标未定义 5、表达式重复 4、常数 4。搜索失败与冻结库 OOS 计算失败分别记录。
记录 3,572 次 chat 请求，prompt / completion tokens 为 320,797,690 / 14,468,337，
其中 5 次请求用量未知，其余为服务返回的累计用量。

| 观测频率 | 涉及的不同编译因子数 | 冻结成员数 |
| --- | ---: | ---: |
| 5m | 380 | 13 |
| 15m | 182 | 9 |
| 30m | 197 | 5 |
| 60m | 165 | 6 |
| 1d | 153 | 6 |

频率来自编译表达式的字段节点，无后缀按基础 5m 计；混合频率同时计入，列合计不等于
不同因子或成员总数。五种频率均有实际评估和入库产出，使用次数仍不完全均衡。
终态无活动研究组、无待提交候选；停止边界为 1000 次预算耗尽。

### 单因子 OOS

37/37 个冻结因子计算成功，因子 ID、顺序及冻结库校验值一致，沿用冻结的训练方向。
下表对冻结成员等权汇总；每个成员的主 IC 按品种 √成交额加权。

| 主指标统计 | Train（训练方向） | Val（训练方向） | Test（冻结方向） |
| --- | ---: | ---: | ---: |
| 37 个因子加权 Pearson IC 均值 | 0.007989 | 0.009794 | 0.002139 |
| 37 个因子加权 Pearson IC 中位数 | 0.007106 | 0.008765 | 0.002427 |

Test 正 IC 为 30/37（81.1%），严格大于 0.005 的有 7/37（18.9%），范围为
-0.004632 至 0.009616；覆盖率最低 88.06%、中位数 96.32%。每个因子的有效测试样本为
1,951,401–2,215,922 行，覆盖 63–72 个品种。Test 加权 Spearman IC 均值为 0.001279，
品种等权 Pearson / Spearman 均值分别为 -0.001079 / -0.000901。
这些是单因子 IC 统计，不是组合预测 IC 或交易收益。

2021–2022 Test 已用于既有方法实验，本次是在同一测试区间评估新的冻结库，
不能视为新的独立盲测。预处理、全部因子及两个模型已在查看本次 Test 前固定；
本次未按 Test 重新选因子、翻转方向、调参或向搜索反馈结果。

### Linear 与 LightGBM

两个模型使用全部 37 个因子，在 4,390,424 行 train+val 样本上拟合；预处理未移除任何
因子，Linear 有效秩为 37。预处理只使用 train+val 的均值和标准差；部分缺失按训练均值
填补。沿用统一 `configs/model.toml`：Linear 带截距、rcond=1e-12；LightGBM 固定
50 轮、学习率 0.05、15 叶、最小叶样本 100、L2=1、4 线程、seed 42。
先保存两个模型，再执行单因子与模型 Test；模型 Test 复用保存参数。

| 模型 | TEST 加权 Pearson IC | TEST 加权 Spearman IC | 预测行数 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.006112 | 0.011662 | 2,215,926 | 100% |
| lightgbm | 0.012239 | 0.015932 | 2,215,926 | 100% |

两个模型均覆盖全部 2,215,926 行 eligible 测试观测和 72 个品种。
Linear 的等权 Pearson / Spearman 为 0.022972 / 0.022602，LightGBM 为
0.026369 / 0.025055。拟合和测试规格、因子列顺序一致，配置、冻结库、数据快照、运行环境
与因子身份通过平台校验。拟合源码来源与搜索启动记录不同；按公共模型协议导入冻结
表达式评估，保留双方来源，不重写原搜索配置、trial 或冻结清单。

### Val/product 库内相似度

2026-10-21 补充冻结库内的全量相似度审计。37 个因子共 666 个无序不同因子对，全部有效，
无遗漏、重复或自身配对；Val 2019–2020 的 eligible 行数为 1,918,863，最小共同样本数
1,579,731。每对在共同有限观测上重新排名并计算绝对 Spearman，全部配对等权汇总，
不抽样、不填补缺失、不使用品种成交额权重，沿用公共库准入的 pooled 行口径。

| 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| ---: | ---: | ---: | ---: | ---: |
| 0.091101 | 0.019933 | 0.308960 | 0.691957 | 0 |

复用已有比较脚本的 `run_audit` 与 `pair_batch`。本轮 37 个冻结成员均只依赖基础算子，
按原 product 窗口全部重建，缓存复用为 0；搜索期间登记的 35 个额外算子没有被这些成员
引用，原定义保留。本次逐个重编译并核对已有因子 ID，配置、冻结库校验值与数据快照一致。
重建与配对耗时 190.8 秒，不含此前的行情加载。

逐对结果与原准入反馈交叉核对：除首个成员没有既有邻居，其余 36 个成员入库时的最近因子
ID 全部一致；最大绝对相关的最大差为 5.55×10⁻¹⁷，处于浮点舍入量级。
统计值从 666 条逐对结果重新汇总，因子身份与顺序均与冻结库一致。

本轮使用 Research Kernel 复权 1m 合并成的 5m 数据，与总览中八份原生 5m 冻结库的数据快照不同。
统计口径相同不等于数据条件相同，本项用于描述本轮冻结库的冗余程度。

本次通过标准入口和独立比较脚本评估，设置数值线程上限，不发起模型服务或供应商请求：

```bash
export POLARS_MAX_THREADS=4 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 NUMBA_NUM_THREADS=4
uv run atlas model artifacts/runs/futures_kernel_5m-all-fold1-v2-42-1df434f87b
uv run atlas test artifacts/runs/futures_kernel_5m-all-fold1-v2-42-1df434f87b
uv run atlas model artifacts/runs/futures_kernel_5m-all-fold1-v2-42-1df434f87b --test
uv run python -u artifacts/comparisons/v2_deepseek_1df434f87b/compute.py
```

逐因子结果见[运行报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v2-42-1df434f87b/report.md)
及同目录 `oos.json`；组合结果见
[模型报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v2-42-1df434f87b/model/report.md)、
`model/fit.json` 与 `model/test.json`。运行过程与预算保存在同目录 `run.json`、
`checkpoint.json` 和原子 `trials/`，历史失败另见 `failures.json`。
[相似度汇总](../../artifacts/comparisons/v2_deepseek_1df434f87b/summary.json)与
[逐对结果](../../artifacts/comparisons/v2_deepseek_1df434f87b/futures_kernel_5m-all-fold1-v2-42-1df434f87b.json)
保留完整精度；同目录 `compute.py` 为复算入口。

## 2026-10-21 Futures kernel 5m fold1：V1 Async DeepSeek 前 500 次截面

源 Run ID：`futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb`。
模型 `deepseek-flash`，all、fold1、seed 42，从零预设 1000 次预算。按用户要求，在原运行
继续搜索期间，读取不可变的前 500 条原子 trial，还原该时点 64 个正式成员，入库率 12.8%。
派生评估目录为 `futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb-prefix500`；
其中仅复制这 500 条 trial，冻结原表达式、训练方向、依赖和成员顺序，不重放模型对话，
不修改源运行、检查点或因子库。派生目录仅用于评估，不能作为搜索检查点恢复。

这是 **1000 次预算运行的前 500 次截面**，退火进度沿用原定 1000 次预算；不等同于
独立预设 500 次预算的实验，也不同于旧原生 5m 实验的 500→1000 次续跑。
最多并行 3 个计划、每计划最多 5 个候选、Explorer 对话上限 40 轮；三个 Agent 均可调用
`get_context`、`library_search`、`library_get`、`library_list`、`library_stats`，
上层退火机制保持原样。第 500 条 trial 记录累计活动时间为 19,986.142 秒，约 5 小时 33 分钟。
截面未保存恰好第 500 次的对话用量检查点，不将后来累计的 chat/token 用量算作该截面成本。

本轮与上方 Kernel V2 使用同一 MarketData 快照
`8f689ef3680e43b4f8f1c21f92bd21ad5f4c6a308e19ad31530f60bda4b4b49a`：
RiceQuant final 主力真实合约的 kernel 1m 数据合并为 5m，沿用 kernel 的换月累计比例复权。
四字段为 `adj_close`、`adj_vwap`、`volume`、`amount`；目标为未来 12 根基础 5m 的
`adj_close` 对数收益，目标不跨真实合约／连续段，因子窗口按 product 计算。
Train 为 2016–2018、Val 为 2019–2020、Test 为 2021–2022，预热从 2015-01-01 开始。
开放 `futures_cta` 参考库，使用统一主指标品种 √成交额加权 Pearson IC。
按资产 profile，Train 绝对 IC 与按训练方向的 Val IC 均须严格大于 0.005；
Val 覆盖率至少 0.8，与既有成员的 Val 绝对 Spearman 须小于 0.7，至少 100 行共同观测。
原生 5m 旧实验使用另一份数据快照，不能把跨数据差异单独归因于搜索方法。

前 500 条 trial 的最终裁决：质量阈值拒绝 392、正式入库 64、行为重复 38、覆盖率不足 3、
编译错误 1、训练指标未定义 2，合计 500。64 个成员的完整编译记录逐项重建并与原 trial
核对一致，没有自定义算子依赖。按表达式字段节点统计，59 个成员只用 5m，
3 个混合使用 5m / 15m，2 个混合使用 5m / 1d；没有使用 30m 或 60m 的成员。
基础字段清单不保留 `@频率` 后缀，频率统计以表达式树为准。

### 单因子 OOS

64/64 个冻结因子完成 Test，因子身份、顺序和训练方向与前 500 次准入结果一致。
下表对成员等权汇总；每个成员内部的主 IC 按品种 √成交额加权。

| 主指标统计 | Train（训练方向） | Val（训练方向） | Test（冻结方向） |
| --- | ---: | ---: | ---: |
| 64 个因子加权 Pearson IC 均值 | 0.009068 | 0.008308 | 0.003563 |
| 64 个因子加权 Pearson IC 中位数 | 0.008803 | 0.007755 | 0.003538 |

Test 正 IC 为 52/64（81.25%），严格大于 0.005 的有 23/64（35.94%），范围为
-0.004870 至 0.012341。覆盖率最低 83.32%、中位数 90.93%；每个因子的有效测试样本为
1,846,415–2,214,351 行，覆盖 63–72 个品种。Test 加权 Spearman IC 均值为 0.002211，
品种等权 Pearson / Spearman 均值分别为 -0.001433 / -0.002075。
这些是单因子统计，不是组合预测 IC 或交易收益。

### Linear 与 LightGBM

两个模型使用全部 64 个因子，在 4,388,595 行 train+val 样本上拟合；没有移除因子，
Linear 有效秩为 64。预处理仅使用 train+val 的均值和标准差，部分缺失按训练均值填补。
沿用统一 `configs/model.toml`：Linear 带截距、rcond=1e-12；LightGBM 固定 50 轮、
学习率 0.05、15 叶、最小叶样本 100、L2=1、4 线程、seed 42。
先保存两个模型，再计算单因子与模型 Test，测试复用已保存参数。

| 模型 | TEST 加权 Pearson IC | TEST 加权 Spearman IC | 预测行数 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.013123 | 0.014171 | 2,215,546 | 99.9829% |
| lightgbm | 0.015892 | 0.016737 | 2,215,546 | 99.9829% |

两个模型的 eligible 测试观测均为 2,215,926 行、72 个品种；380 行因全部因子缺失未预测。
Linear 的等权 Pearson / Spearman 为 0.034022 / 0.025703，LightGBM 为
0.033476 / 0.029260。模型规格和因子列顺序在拟合、测试间一致，来源校验
`source_matches_search=true`，冻结库、数据快照、运行环境与因子身份均通过平台校验。

为避免重复执行较慢的长窗口算子，单因子和模型 Test 复用公共 `factor_panel` 生成的同一
冻结因子矩阵，指标使用原 `metrics` 和 `evaluate_models`，不另写数值实现。
首个因子额外按标准单因子 Test 的执行、连接与方向流程核对，所有指标最大绝对差为 0，
样本、品种数和覆盖率一致。拟合参数不因 Test 重新训练或修改。

### Val/product 库内相似度

64 个冻结成员共 2,016 个无序不同因子对，全部有效，无遗漏、重复或自身配对。
Val 2019–2020 的 eligible 行数为 1,918,863，最小共同样本数为 1,500,583。
每对在共同有限观测上重新排名，计算绝对 Spearman，全部配对等权汇总；不抽样、
不填补缺失、不使用品种成交额权重，沿用公共库准入的 pooled 行口径。

| 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| ---: | ---: | ---: | ---: | ---: |
| 0.115394 | 0.072774 | 0.288826 | 0.695866 | 0 |

复用既有比较脚本的 `run_audit` 和 `pair_batch`，全部 64 个成员按原冻结表达式、
product 窗口与既有预热重建，没有复用缓存，也没有新设窗口上限。
因子重建与全量配对耗时 2,785.8 秒，不含此前行情加载；长窗口滚动秩相关占用了较多时间。

除首个成员没有既有邻居，其余 63 个成员入库时的最近因子 ID 全部一致，原准入最大绝对
相关与本次对应配对的最大差为 1.11×10⁻¹⁶，处于浮点舍入量级。汇总统计从全部 2,016 对
重新核对，成员 ID、顺序与冻结库、单因子 Test 及模型列一致。

### 结果边界与产物

用户要求在源运行继续时评估这一截面。2021–2022 Test 已用于既有实验，本次又查看了
前 500 次截面；后续 1000 次结果属于同一 Test 区间复评，不能视为新的独立盲测。
截面成员、方向、预处理与模型参数在查看 Test 前固定；本次未按 Test 筛因子、翻转方向、
调参或向搜索方法反馈结果。源运行继续使用 train/val，保留原 1000 次预算与退火轨迹。

逐因子指标见[运行报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb-prefix500/report.md)
及同目录 `oos.json`；组合结果见
[模型报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb-prefix500/model/report.md)、
`model/fit.json` 与 `model/test.json`。截面元数据与原始前缀证据见同目录 `run.json`、
`frozen/library.json` 和 `trials/`。
[截面重建脚本](../../artifacts/comparisons/v1_async_kernel_180a7f87cb_prefix500/prepare.py)与
[共用 Test 矩阵脚本](../../artifacts/comparisons/v1_async_kernel_180a7f87cb_prefix500/test_shared_panel.py)
保留复算入口，不调用模型服务或供应商 API。
[完整评估汇总](../../artifacts/comparisons/v1_async_kernel_180a7f87cb_prefix500/evaluation_summary.json)、
[相似度汇总](../../artifacts/comparisons/v1_async_kernel_180a7f87cb_prefix500/summary.json)与
[逐对结果](../../artifacts/comparisons/v1_async_kernel_180a7f87cb_prefix500/futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb-prefix500.json)
保留完整精度；同目录 `compute.py` 为相似度入口，`summarize.py` 汇总全部结果并核对原准入证据。

## 2026-10-22 Futures kernel 5m fold1：V1 Async 第 927 次后的 Explorer 不足报告

Run ID：`futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb`。预设 1000 次预算，
已提交 927 次、入库 150 个因子。Explorer 第 188 轮已有两个活动组（186、187），
各完成 1/5 个候选；只空出一个位置，新组预留配额为 5。Explorer 查询一次库后，在第二条
回复返回 `summary/unresolved`，表示无法补足第三个方向。旧协议要求每批恰好三个，
代码立即抛错；再次续跑仍读取已处理的同一不足报告，因此不发新请求就重复失败。
该报告是模型对研究空间的判断，不等于平台已经证明四字段搜索空间穷尽。

后台跟踪在 00:57 记录 `failed`，00:58 记录写锁释放并等待用户续跑；没有自动切换模型、
扩字段、重置预算或发起新的搜索。此次修复前仍停在同一 927 次检查点，完整测试和 1000 次
条目尚未产生；前 500 次截面的既有结果保留。

修复将 V1 共用 Explorer 协议改为一至三个完整方向，单条即可提交；评分权重、同分顺序、
异步配额和既有重复／证据校验保留，排序按实际批次长度处理。首次 `unresolved` 在原有
对话额度内澄清一次，强调无需凑满三个；再次不足或已经耗尽对话额度时仍显式停止。
旧的已处理回复也能进入这一步，保留原文本、摘要、请求计数和已完成 trial，不重置轮数。

该检查点保存了 525 条历史方向展示，但只有 185 个完全不同的 `(region_id, direction)`。
异步计划交错提交使同一方向反复出现。新请求只去掉完全相同的展示项，保留首次出现顺序；
不合并语义近似方向，不改原始快照、地图统计或试验记录。

对真实检查点进行只读复放，在发模型请求前截获输入：新请求看到一个空位、两个活动组、
185 个历史方向和原四字段；按已有两条回复继续，温度为原修复温度 0.8。
未访问模型服务、供应商或市场标签，原 `run.json`、`checkpoint.json` 逐字节不变；
活动组、完成次数、RNG 与请求用量不变。合成回归覆盖一／两个方向、批次原子性、
一个空位下旧不足报告的恢复、再次不足的停止、空批次／过量批次拒绝和历史展示去重。

上层退火的温度、奖励与 Metropolis 公式及四字段数据边界不改。该运行的剩余阶段将使用
修复后的数量协议，完整实验报告须披露这项中途修复，不能将其描述为全程固定旧协议。
跟踪进程继续等待原运行完成冻结，再执行既定评估和写表流程。

证据见原运行的 `checkpoint.json`、`failures.json` 和不可变 `trials/`，以及
[跟踪日志](../../artifacts/monitors/v1_async-180a7f87cb-followup.log)。

## 2026-10-22 Futures kernel 5m fold1：V1 Async DeepSeek 前 927 次截面

源 Run ID：`futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb`；评估截面：`futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb-prefix927`。
用户要求先查看当前水平。按已提交的前 927 个原子 trial 固定 150 个准入成员，
原先 500 次截面的 64 个成员全部保留，后 427 次新增 86 个。入库率为 16.18%，
截面累计活动时间 44089.133 秒。截面建立时第 928 次待提交，
未纳入本次评估；原搜索继续。没有调用模型或供应商服务，没有改原运行。

源运行从零预设 1000 次预算，保留其退火轨迹；927 是历史前缀，不是独立配置的预算。
该评估目录不可恢复为搜索检查点。150 个冻结表达式、编译定义、方向、顺序与准入记录
逐一一致，全部重编译校验通过；只使用已有内置算子，没有新增算子。所有成员均在
第 927 次后的 Explorer 数量协议修复产生新候选之前入库。

### 数据与冻结口径

沿用 Kernel 复权 5m 快照 `8f689ef3680e43b4f8f1c21f92bd21ad5f4c6a308e19ad31530f60bda4b4b49a`。
来源为 Research Kernel 的 RiceQuant final 主力真实合约 1m 聚合到 5m，价格按 kernel
既有累计比例复权；与旧原生 5m 实验分开。字段为 adj_close、adj_vwap、volume、amount。
目标为未来 12 根基础 bar 的 adj_close 对数收益，按真实合约和连续段隔离；因子窗口
使用 product panel，已知缺口重置。Train 2016–2018、Val 2019–2020、Test 2021–2022，
预热从 2015 年开始。主指标为品种 √成交额加权 Pearson IC，方向只由 Train 固定。
Train 绝对主 IC 和定向 Val 主 IC 均须严格大于 0.005，Val 覆盖率至少 0.8，
与已入库成员的 pooled Val 绝对 Spearman 小于 0.7，至少 100 个共同观测。

### 单因子 TEST

150/150 个因子计算成功；每个因子的全部指标均为有限值。

| split | 主 IC 均值 | 中位数 | 正 IC | 严格大于 0.005 |
| --- | ---: | ---: | ---: | ---: |
| Train（定向） | 0.008939 | 0.008415 | 150/150 | 150/150 |
| Val | 0.009525 | 0.009094 | 150/150 | 150/150 |
| Test | 0.004039 | 0.004628 | 132/150 | 62/150 |

TEST 主 IC 范围 -0.004870–0.012341；单因子覆盖率最低 83.3248%、中位数 89.0929%。
TEST 加权 Spearman 均值 0.004592；品种等权 Pearson / Spearman 均值分别为 0.002077 / 0.003278。
新增 86 个因子 TEST 主 IC 均值 / 中位数为 0.004393 / 0.004782，正 IC 80/86，严格大于 0.005 的有 39/86。

### 固定 Linear 与 LightGBM

使用全部 150 个因子，在 4,388,602 行 train+val 样本上拟合；移除因子 0 个，Linear 有效秩 150。
预处理只用 train+val 均值和标准差，部分缺失按训练均值填补。沿用固定模型配置：
Linear 带截距、rcond=1e-12；LightGBM 50 轮、学习率 0.05、15 叶、最小叶样本 100、
L2=1、4 线程、seed 42。先保存拟合模型并通过标准 run_model 入口校验，再查看 Test。

| 模型 | TEST 加权 Pearson | 加权 Spearman | 预测行数 / 可评估行数 | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.015535 | 0.014660 | 2,215,546 / 2,215,926 | 99.9829% |
| lightgbm | 0.016632 | 0.015668 | 2,215,546 / 2,215,926 | 99.9829% |

同一运行的前 500 次，Linear / LightGBM 的 TEST 加权 Pearson 为 0.013123 / 0.015892。
主 IC 和两个组合的加权 Pearson 均高于前 500 次；LightGBM 加权 Spearman 从 0.016737 降至 0.015668，不同指标的增益不完全一致。
两次均使用全部冻结成员和同一固定模型参数，未按 Test 选择因子、方向或参数。

因子矩阵复用公共 factor_panel，从既定预热日期因果计算至 Test 结束；指标和模型使用
公共 metrics、fit_models 与 evaluate_models，不另写数值实现。首个因子 Train/Val
对原准入报告的最大绝对差为 3.47e-18；
原 64 个成员的全部 Test 指标对已发布 500 次截面的最大绝对差为 0，样本、品种数、覆盖率与指标元数据一致。
本次模型 `source_matches_search=false`；
拟合时源码已包含上述 Explorer 修复，源码记录只说明来源。原搜索指纹未改写；
冻结因子身份、数值引擎和数据口径保持一致。

### Val/product 库内相似度

150 个冻结成员共 11,175 个无序不同因子对，全部有效，无自身配对、遗漏或重复。
复用既有 run_audit、pair_batch；按原冻结表达式、product 窗口和既定预热重建
全部成员。每对只在共同有限观测上重新排名，计算绝对 Spearman；全部配对等权，
不抽样、不填补缺失、不使用成交额权重，与既有相似度表和库内去重口径一致。

| 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| ---: | ---: | ---: | ---: | ---: |
| 0.127126 | 0.074642 | 0.326596 | 0.695866 | 0 |

Val 2019–2020 的 eligible 行数为 1,918,863，最小共同样本数为 1,467,232。
缓存复用 0 个，重建 150 个；重建与全量配对耗时 1831.5 秒，不含此前行情加载。
149 次有既有邻居的准入记录，其最近因子 ID 全部一致，
对应最大绝对相关的最大差为 1.11e-16。
与前 500 次共有的 2,016 个配对，重叠行数完全一致；
相关性最大绝对差为 0。成员顺序与单因子、模型一致。

[相似度汇总](../../artifacts/comparisons/v1_async_kernel_180a7f87cb_prefix927/summary.json)和
[逐对明细](../../artifacts/comparisons/v1_async_kernel_180a7f87cb_prefix927/futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb-prefix927.json)
保存完整精度。结果已补入总览的期货库内因子相似度表。

### 因子构成与结果边界

128 个因子只用 5m；4 个混合 5m/15m、2 个混合 5m/30m、1 个混合 5m/60m、
15 个混合 5m/1d。按频率涉及成员数统计为 5m 150、15m 4、30m 2、60m 1、1d 15，
混合成员会重复计数。

用户在搜索继续时明确要求评估此截面。2021–2022 Test 已用于既有实验及本运行前 500 次，
本次及后续 1000 次结果属于同一区间复评，不能当作新的独立盲测。评估输出没有反馈给
搜索方法；原搜索仍只使用 train/val。本截面的库内相似度另在 Val 全量配对计算，
不新增 Test 评估，也不把准入阈值或每次最大相关当作全库均值。

逐因子指标见[运行报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb-prefix927/report.md)及同目录 `oos.json`；
组合结果见[模型报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb-prefix927/model/report.md)、
`model/fit.json` 和 `model/test.json`。完整精度见
[评估汇总](../../artifacts/comparisons/v1_async_kernel_180a7f87cb_prefix927/evaluation_summary.json)。
复算入口为同目录 `prepare.py`、`evaluate.py`、`compute.py`、`summarize.py`；
无模型或供应商 API 调用。

## 2026-10-22 Futures kernel 5m fold1：V1 Async DeepSeek 1000 次完整评估

Run ID：`futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb`。`deepseek-flash`，all、fold1、seed 42；从零预设 1000 次预算，
沿原运行完成并冻结 166 个正式成员，入库率 16.60%。
记录的累计活动时间为 49,399.9 秒。
这不是旧原生 5m 的 500→1000 续跑；本轮保留原定 1000 次退火轨迹。
三个 Agent 共享五项只读研究工具；最多并行 3 个计划、每计划最多 5 个候选，Explorer 上限 40 轮。
第 927 次后修复 Explorer 的数量要求：由固定三个改为一至三个完整方向；
首次不足报告在原对话额度内澄清一次，再次不足仍停止，并去掉历史方向的完全重复展示。
此次为运行中途的协议修复，不改已有 trial、方向、四字段、准入、奖励或退火公式。
比较固定旧协议的运行时须披露该差异，详见 2026-10-22 的 Explorer 中断记录。

使用已冻结的 Research Kernel 复权 5m 数据，MarketData 快照为
`8f689ef3680e43b4f8f1c21f92bd21ad5f4c6a308e19ad31530f60bda4b4b49a`。四字段为 `adj_close`、`adj_vwap`、`volume`、`amount`，
来自此前指定的 kernel 1m 合并 5m 和换月累计比例复权；与原生 5m 旧实验不是同一快照。
目标为未来 12 根基础 5m 的 adj_close 对数收益，目标不跨真实合约／连续段，因子按 product 窗口计算。
Train 2016–2018、Val 2019–2020、Test 2021–2022，预热从 2015-01-01 开始。
统一期货主指标、0.005 Train/Val 门槛、0.8 Val 覆盖率、0.7 库内相关门槛与参考库设置均沿用保存配置。

1000 条原子 trial 的裁决计数：`quality_threshold` 659、`accepted` 166、`behavior_duplicate` 155、`low_coverage` 14、`compile_error` 1、`undefined_train_metric` 3、`expression_duplicate` 2。
频率按表达式树字段节点统计，混合频率同时计入；不是按不保留后缀的基础字段列表统计。

| 观测频率 | 冻结成员数 |
| --- | ---: |
| 5m | 166 |
| 15m | 4 |
| 30m | 2 |
| 60m | 1 |
| 1d | 20 |

### 单因子 OOS

全部 166 个成员完成 Test，沿用冻结训练方向，不按 Test 选择因子。
下表对成员等权汇总，每个成员内部仍按品种 √成交额加权。

| 主指标统计 | Train（训练方向） | Val（训练方向） | Test（冻结方向） |
| --- | ---: | ---: | ---: |
| 加权 Pearson 均值 | 0.009053 | 0.009654 | 0.004134 |
| 加权 Pearson 中位数 | 0.008605 | 0.009209 | 0.004646 |

Test 正 IC 为 147/166，严格大于 0.005 的有 70/166。
Test 主 IC 范围为 -0.004870 至 0.012341；
覆盖率最低 83.32%、中位数 88.93%。
Test 加权 Spearman 均值为 0.004777。

前 500 次的 64 个因子全部保留，后 500 次新增 102 个。
原成员 Test 样本、品种数和覆盖率不变，全部 IC 的最大绝对差为 0。
前 927 次的 150 个成员定义、方向与顺序全部保留；
原成员 Test 指标最大绝对差为 0，样本、品种数和覆盖率一致。
最后 73 次新增 16 个因子，其 Test 主 IC 均值 / 中位数为
0.005025 / 0.005311，
正 IC 15/16，
严格大于 0.005 的有 8/16。

### Linear 与 LightGBM

两模型在 4,388,602 行 train+val 样本上拟合，输入 166 列，
保留 166 列，Linear 秩为 166。
预处理仅使用 train+val 的均值、标准差；部分缺失按训练均值填补，全列缺失的行不预测。
沿用公共模型配置：Linear 带截距、rcond=1e-12；LightGBM 固定 50 轮、学习率 0.05、
15 叶、最小叶样本 100、L2=1、4 线程、seed 42，没有早停、top-k 或自动调参。
先保存拟合结果，再执行 Test，测试不重训。

| 模型 | TEST 加权 Pearson | TEST 加权 Spearman | 预测行数 / eligible | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.016481 | 0.015439 | 2,215,546 / 2,215,926 | 99.9829% |
| lightgbm | 0.019924 | 0.017285 | 2,215,546 / 2,215,926 | 99.9829% |

两个截面均使用全部冻结成员和相同固定模型参数，直接对照如下：

| 模型 | 927 次加权 Pearson | 1000 次加权 Pearson | 927 次加权 Spearman | 1000 次加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.015535 | 0.016481 | 0.014660 | 0.015439 |
| lightgbm | 0.016632 | 0.019924 | 0.015668 | 0.017285 |

单因子和组合 Test 复用公共 factor_panel 的冻结矩阵与原 metrics/evaluate_models。
首个表达式额外按标准单因子流程核对；模型和 Test 因子顺序与冻结清单一致。
模型拟合源码来源与搜索记录一致：`False`；来源记录不作为新冻结门禁。

### Val/product 库内相似度

166 个成员共 13,695 个无序不同因子对，全部有效；不遗漏、不重复、不含自身。
复用公共比较脚本，在 Val 2019–2020 的 1,918,863 行上计算。
每对只取共同有限观测、重新排名并计算绝对 Spearman，配对等权，不抽样、不填补缺失，
沿用 pooled 行口径，不使用品种成交额权重；最小共同样本数为 1,467,232。

| 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| ---: | ---: | ---: | ---: | ---: |
| 0.127824 | 0.073150 | 0.332904 | 0.695866 | 0 |

缓存复用 5 个、按原表达式重建 161 个；
因子处理与全量配对耗时 1,917.2 秒，不含此前行情加载。
165 次有既有邻居的准入记录中，最近因子 ID 全部一致，
最大绝对相关与本次结果的最大差为 1.11e-16。
与前 927 次共有的 11,175 对，其重叠行数完全一致；
相关性最大绝对差为 0。

### 结果边界与产物

前 500 次和 927 次截面均已查看同一 2021–2022 Test，本轮属于复评，不是新的独立盲测。
所有成员、方向、模型参数在查看本轮 Test 前冻结；本次没有按 Test 筛选、翻向、调参或反馈搜索。
这些是因子和组合预测 IC，不是交易收益。

逐因子结果见[运行报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb/report.md)与同目录 oos.json；
组合结果见[模型报告](../../artifacts/runs/futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb/model/report.md)、model/fit.json、model/test.json。
完整精度与交叉校验见[评估汇总](../../artifacts/comparisons/v1_async_kernel_180a7f87cb/evaluation_summary.json)、
[相似度汇总](../../artifacts/comparisons/v1_async_kernel_180a7f87cb/summary.json)与
[逐对结果](../../artifacts/comparisons/v1_async_kernel_180a7f87cb/futures_kernel_5m-all-fold1-v1_async-42-180a7f87cb.json)。
同目录 test_shared_panel.py、compute.py、summarize.py 保留复算入口。
跟踪与后处理日志见[日志](../../artifacts/monitors/v1_async-180a7f87cb-followup.log)。

## 2026-10-22 Futures kernel 5m fold1：AlphaPROBE DeepSeek 500 次评估

源 Run ID：`futures_kernel_5m-all-fold1-alphaprobe-42-4bacb24032`；
评估目录：`futures_kernel_5m-all-fold1-alphaprobe-42-4bacb24032-prefix500`。
按用户要求读取已提交的前 500 条原子 trial，固定该时点 48 个正式成员，入库率 9.6%。
源运行从零预设 1000 次预算；500 是本次评估的历史前缀，不是独立设置的搜索预算。
派生目录只用于评估，不作为搜索检查点恢复；源运行继续，未修改其 trial、方法状态或因子库。
48 个表达式的完整编译定义、因子 ID、训练方向和成员顺序均与原准入记录核对一致。

### 数据与搜索口径

MarketData 快照为
`8f689ef3680e43b4f8f1c21f92bd21ad5f4c6a308e19ad31530f60bda4b4b49a`。
使用 Research Kernel 复权 5m，字段为 `adj_close`、`adj_vwap`、`volume`、`amount`；
目标为未来 12 根基础 5m 的 `adj_close` 对数收益，按真实合约与连续段隔离，因子按 product 窗口计算。
Train 2016–2018、Val 2019–2020、Test 2021–2022，预热从 2015-01-01 开始。
快照、字段、universe、fold、预热、资产配置、准入规则、seed 与 Kernel V1 Async 的 500 次评估逐项一致。

模型为 `deepseek-flash`，embedding 为 `Qwen3-Embedding-0.6B`，seed 42，
无预置初始因子，开放 `futures_cta` 参考库；方法参数沿用本次运行保存值。
前 500 次的裁决为：`accepted` 48、`quality_threshold` 362、`behavior_duplicate` 69、
`compile_error` 17、`low_coverage` 4，合计 500。
第 500 条 trial 记录累计活动时间 19,502.755 秒；不把当前检查点的累计模型用量归给这一历史前缀。
按表达式树统计，使用 5m、15m、30m、60m、1d 的成员分别为 33、3、3、10、7 个；混合频率分别计入。

### 单因子 TEST

全部 48 个因子成功完成 Test，沿用冻结的训练方向。下表对因子等权汇总，
每个因子内部采用品种 √成交额加权 Pearson IC。

| 主指标统计 | Train | Val | Test |
| --- | ---: | ---: | ---: |
| 加权 Pearson 均值 | 0.008480 | 0.009573 | 0.002922 |
| 加权 Pearson 中位数 | 0.007222 | 0.008194 | 0.003593 |

Test 正 IC 为 38/48，比例 79.17%；严格大于 0.005 的有 17/48。
加权 Pearson 范围为 -0.010496 至 0.011722；加权 Spearman 均值为 0.002988。
单因子覆盖率最低 87.11%、中位数 91.69%，有效观测数为 1,930,207–2,215,795，覆盖 63–72 个品种。

### Linear 与 LightGBM

两模型使用全部 48 个因子，在 4,390,424 行 train+val 样本上拟合，保留 48 列，Linear 秩为 48。
使用统一 `configs/model.toml`，LightGBM 固定 50 轮；先保存模型，再评估 Test，不按 Test 调参或选列。

| 模型 | TEST 加权 Pearson | TEST 加权 Spearman | 预测行数 / eligible | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.011288 | 0.010297 | 2,215,926 / 2,215,926 | 100% |
| lightgbm | 0.016138 | 0.014449 | 2,215,926 / 2,215,926 | 100% |

两模型覆盖 72 个品种。单因子和模型 Test 复用公共 `factor_panel` 生成的冻结矩阵，
指标与模型评估复用 `metrics`、`evaluate_models`；首个因子额外按标准单因子流程核对，
指标最大绝对差为 0，覆盖率和指标元数据一致。
模型拟合与搜索的源码来源记录不同，`source_matches_search=false`；来源不作为新的冻结门禁，
原配置、快照、算子运行环境、成员身份和冻结校验均通过。

### Val/product 库内相似度

48 个成员的 1,128 个无序不同因子对全部有效，Val 2019–2020 共 1,918,863 行。
复用同一因果计算的冻结因子矩阵及既有 `pair_batch`，每对只取共同有限观测重新排名，
计算绝对 Spearman，配对等权，不含自身，不重复计数、不抽样、不填补缺失，也不使用品种成交额权重。
最小共同观测数为 1,607,227。

| 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| ---: | ---: | ---: | ---: | ---: |
| 0.155238 | 0.096048 | 0.418874 | 0.699248 | 0 |

47 个有既有邻居的成员，其最近因子 ID 全部与原准入记录一致；
最大绝对相关与原反馈的最大差为 6.72e-15。
全量配对耗时 124.34 秒，复用此前已算好的因子矩阵，未重复执行全部表达式。

### 结果边界与产物

用户明确要求在搜索继续时评估前 500 次。2021–2022 Test 已用于既有实验，
后续完整运行的同区间评估属于复评；此次未按 Test 筛选成员、翻转方向、调参或向搜索方法反馈结果。
本次结果是单因子与组合预测 IC，不是交易收益；与原生 5m 的旧 AlphaPROBE 结果分开记录。

逐因子结果见[运行报告](../../artifacts/runs/futures_kernel_5m-all-fold1-alphaprobe-42-4bacb24032-prefix500/report.md)
及同目录 `oos.json`；组合结果见
[模型报告](../../artifacts/runs/futures_kernel_5m-all-fold1-alphaprobe-42-4bacb24032-prefix500/model/report.md)、
`model/fit.json` 与 `model/test.json`。
[完整评估汇总](../../artifacts/comparisons/alphaprobe_kernel_4bacb24032_prefix500/evaluation_summary.json)、
[相似度汇总](../../artifacts/comparisons/alphaprobe_kernel_4bacb24032_prefix500/summary.json)与
[逐对结果](../../artifacts/comparisons/alphaprobe_kernel_4bacb24032_prefix500/futures_kernel_5m-all-fold1-alphaprobe-42-4bacb24032-prefix500.json)
保留完整精度。
同目录 `prepare.py`、`test_shared_panel.py`、`summarize.py` 及评估日志保留复算入口；
模型拟合使用标准 `atlas model`，后处理不调用模型服务或供应商 API。

## 2026-10-23 Futures kernel 完整字段 fold1：ReAct DeepSeek 1000 次启动

Run ID：`futures_kernel_5m_full-all-fold1-react-42-af44aa3bd0`。
按用户要求在 screen `atlas-react-deepseek-1000` 中启动，预设 1000 次尝试，all、fold1、seed 42。
本条记录保留运行启动与真实工具链验收事实；500 次暂停截面的冻结评估见后续 ReAct 记录。

使用 `futures_kernel_5m_full` 的八字段 Research Kernel 复权 5m 快照
`73db06c2d7223ce5829b8d3d9846a27b35e59e0d621a11a4a94d99dcc6ec0e87`：
`adj_open`、`adj_high`、`adj_low`、`adj_close`、`adj_vwap`、`volume`、`amount`、`open_interest`。
目标为未来 12 根基础 5m 的 `adj_close` 对数收益，因子采用 product 窗口。
Train 2016–2018、Val 2019–2020，预热从 2015-01-01 开始；实际加载 5,241,386 行研究行情。
本轮与四字段 Kernel 历史实验的数据输入不同，比较时须固定相同快照和字段。

模型配置为 AgentScope 2.0.8 的单 Agent ReAct、`deepseek-flash`、温度 1.0，
输出上限 65,536 token，context 压缩容量 131,072 token；无预置因子、无 embedding。
复用公共五项只读查询与 runner 的原子候选评估，参考库由共同 benchmark 配置开放。
启动前修正了原生 SDK 的 DeepSeek `max_tokens` 与思考内容格式、评估排队权限、
多轮客户端事件循环复用及关闭；模拟 SSE 服务已验证连续两轮工具调用和平台反馈。
真实运行已确认有多个 trial 原子落盘，预算、数值评估和准入仍由平台裁决。

启动命令：

```bash
uv run atlas run --asset futures_kernel_5m_full --universe all --fold fold1 \
  --method react --seed 42 --attempts 1000
```

凭据从项目 `.env` 加载到启动进程环境，不写入命令、配置或日志。
运行日志见[screen 日志](../../artifacts/monitors/react_kernel_full_deepseek_1000_20261023_082602.log)，
复算入口见同目录 `react_kernel_full_deepseek_1000_20261023_082602.sh`；
运行配置、trial 与方法状态位于 `artifacts/runs/<run_id>/`。

运行先后在完成 443、458 次时因 DeepSeek 响应流中途断开失败，异常为
`APIConnectionError`，底层为 `RemoteProtocolError: incomplete chunked read`。
AgentScope 原重试范围仅覆盖建立响应，未覆盖消费响应流；已改为完整消费单次响应后才交给
Agent，断流仅重发当前请求，最多额外重试 3 次，等待 1/2/4 秒，关闭 SDK 内层重试。
模拟 SSE 验证了已完成工具不重复、断流候选不提交、重试上限、参数错误与中断；
`pwsh -File scripts/check.ps1` 通过，846 个测试通过。

在原 screen 的 `resume-458` 窗口从同一运行继续，保留 458 条原子 trial、
待回传的 6 条反馈和原定 1000 次预算，配置与数据快照未变。
恢复后已确认第 459–461 条 trial 成功提交，运行状态为 `running`。
本次日志见[恢复日志](../../artifacts/monitors/react_kernel_full_deepseek_1000_resume_20261023_105516.log)，
同目录同名 `.sh` 保存恢复命令。修复后模型请求数按实际请求尝试累加；修复前的 115 次计数
为 Agent 回复轮次，因此本运行累计请求数不能当作完整 HTTP 请求数，两次失败流的用量未知。

## 2026-10-23 Futures kernel 5m fold1：AlphaPROBE DeepSeek 1000 次完整评估

Run ID：`futures_kernel_5m-all-fold1-alphaprobe-42-4bacb24032`。
沿原定 1000 次预算完成搜索并冻结 78 个正式成员，入库率 7.80%，累计活动时间 43,951.8 秒。
前 500 次的 48 个成员定义、训练方向与顺序全部保留，后 500 次新增 30 个。

### 数据与搜索口径

使用四字段 Research Kernel 复权 5m，MarketData 快照为
`8f689ef3680e43b4f8f1c21f92bd21ad5f4c6a308e19ad31530f60bda4b4b49a`，
字段为 `adj_close`、`adj_vwap`、`volume`、`amount`。
新导出的八字段资产 `futures_kernel_5m_full` 供新实验使用，本运行沿用冻结的四字段资产。
目标为未来 12 根基础 5m 的 `adj_close` 对数收益，按真实合约与连续段隔离，因子按 product 窗口计算。
Train 2016–2018、Val 2019–2020、Test 2021–2022，预热从 2015-01-01 开始。
快照、字段、universe、fold、预热、资产配置、准入规则、seed 和尝试预算与
Kernel V1 Async 1000 次运行逐项一致；方法机制分别按各自保存配置执行。

模型为 `deepseek-flash`，embedding 为 `Qwen3-Embedding-0.6B`，seed 42，
无预置初始因子，开放 `futures_cta` 参考库。
1000 条原子 trial 的裁决计数：`accepted` 78、`quality_threshold` 711、
`behavior_duplicate` 167、`compile_error` 35、`low_coverage` 7、`expression_duplicate` 2。
按表达式树字段节点统计，使用 5m、15m、30m、60m、1d 的成员分别为 51、4、11、13、16 个；
混合频率分别计入。

### 单因子 TEST

全部 78 个成员成功完成 Test，沿用冻结训练方向。下表对成员等权汇总，
每个成员内部仍按品种 √成交额加权。

| 主指标统计 | Train（训练方向） | Val（训练方向） | Test（冻结方向） |
| --- | ---: | ---: | ---: |
| 加权 Pearson 均值 | 0.008421 | 0.009689 | 0.002679 |
| 加权 Pearson 中位数 | 0.007486 | 0.008352 | 0.003072 |

Test 正 IC 为 60/78，比例 76.92%；严格大于 0.005 的有 27/78。
加权 Pearson 范围为 -0.010496 至 0.011722；加权 Spearman 均值为 0.002439。
单因子覆盖率最低 87.11%、中位数 91.90%，有效观测数为 1,930,207–2,215,906，覆盖 63–72 个品种。
前 500 次成员的 Test 指标、样本、品种数与覆盖率均与原评估一致，指标最大绝对差为 0。
新增 30 个成员的 Test 主 IC 均值 / 中位数为 0.002289 / 0.001928，正 IC 为 22/30。

### Linear 与 LightGBM

两模型使用全部 78 个冻结因子，在 4,390,424 行 train+val 样本上拟合，保留 78 列，Linear 秩为 78。
使用统一 `configs/model.toml`，配置与依赖版本均与 Kernel V1 Async 的模型拟合记录一致。
Linear 带截距、rcond=1e-12；LightGBM 固定 50 轮、学习率 0.05、15 叶、最小叶样本 100、
L2=1、4 线程、seed 42。先保存模型，再评估 Test；无 Test 选列或调参。

| 模型 | TEST 加权 Pearson | TEST 加权 Spearman | 预测行数 / eligible | 覆盖率 |
| --- | ---: | ---: | ---: | ---: |
| linear | 0.014138 | 0.013136 | 2,215,926 / 2,215,926 | 100% |
| lightgbm | 0.015396 | 0.016470 | 2,215,926 / 2,215,926 | 100% |

两模型覆盖 72 个品种。单因子和组合 Test 复用公共 `factor_panel` 的冻结矩阵及原
`metrics`、`evaluate_models`；首个因子按标准单因子流程额外核对，指标最大绝对差为 0。
模型拟合源码来源与搜索记录一致：`False`；来源记录不作为新的冻结门禁，
原配置、数据快照、算子运行环境、成员身份及冻结校验均通过。

### Val/product 库内相似度

78 个成员共 3,003 个无序不同因子对，全部有效。Val 2019–2020 共 1,918,863 行，
每对取共同有限观测重新排名，计算绝对 Spearman；配对等权，不含自身、不重复计数、
不抽样、不填补缺失，沿用 pooled 行口径，不使用品种成交额权重。
最小共同观测数为 1,592,743。

| 平均绝对 Spearman | 中位数 | P90 | 最大值 | ≥ 0.7 的对数 |
| ---: | ---: | ---: | ---: | ---: |
| 0.149145 | 0.091602 | 0.397867 | 0.699248 | 0 |

核对前 500 次的冻结定义、快照、字段、Val 窗口与已有配对来源后，复用其中 1,128 对，
通过既有 `pair_batch` 计算新增 1,875 对；配对阶段耗时 209.85 秒。
77 次有既有邻居的准入记录中，最近因子 ID 全部一致，最大绝对相关与原反馈的最大差为 6.72e-15。

### 结果边界与产物

2021–2022 Test 已用于前 500 次评估，本次完整运行属于同区间复评。
全部成员、方向与模型参数在本轮 Test 前冻结；未按 Test 筛选、翻向、调参或反馈搜索。
结果为因子及组合预测 IC，与原生 5m 的旧 AlphaPROBE 实验分开记录。

逐因子结果见[运行报告](../../artifacts/runs/futures_kernel_5m-all-fold1-alphaprobe-42-4bacb24032/report.md)
及同目录 `oos.json`；组合结果见
[模型报告](../../artifacts/runs/futures_kernel_5m-all-fold1-alphaprobe-42-4bacb24032/model/report.md)、
`model/fit.json` 与 `model/test.json`。
[完整评估汇总](../../artifacts/comparisons/alphaprobe_kernel_4bacb24032/evaluation_summary.json)、
[相似度汇总](../../artifacts/comparisons/alphaprobe_kernel_4bacb24032/summary.json)与
[逐对结果](../../artifacts/comparisons/alphaprobe_kernel_4bacb24032/futures_kernel_5m-all-fold1-alphaprobe-42-4bacb24032.json)
保留完整精度。同目录 `evaluate.py`、`summarize.py` 及日志保留复算入口；
模型拟合使用标准 `atlas model`，后处理不调用模型服务或供应商 API。


## 2026-10-23 Futures kernel 完整字段 fold1：ReAct DeepSeek 500 次评估

原运行：`futures_kernel_5m_full-all-fold1-react-42-af44aa3bd0`。按用户要求在第 500 条原子 trial 保存后暂停，
父运行状态为 `interrupted`，检查点完成数 500、无 pending trial，原定 1000 次预算及方法状态保留。
固定全部 68 个准入成员的定义、训练方向与顺序，保存只读评估截面 `futures_kernel_5m_full-all-fold1-react-42-af44aa3bd0-prefix500`。
本截面来自原定 1000 次预算的前 500 次搜索；评估后父运行保持暂停。

使用完整八字段资产 `futures_kernel_5m_full`，快照 `73db06c2d7223ce5829b8d3d9846a27b35e59e0d621a11a4a94d99dcc6ec0e87`。
字段为 `adj_open`、`adj_high`、`adj_low`、`adj_close`、`adj_vwap`、`volume`、`amount`、`open_interest`。
目标为未来 12 根基础 5m 的 adj_close 对数收益，因子按 product 窗口；all、fold1、seed 42。
Train 2016–2018、Val 2019–2020、Test 2021–2022，预热从 2015-01-01 开始。
模型为 AgentScope 单 Agent ReAct 和 deepseek-flash，配置、准入与原运行一致。
入库率 13.60%，trial 记录累计活动时间 7927.38 秒。
接入本次停止点时按原检查点重启过一次；未提交的评估依恢复规则重做，未落盘活动时间未知。
裁决计数：`{"quality_threshold": 349, "accepted": 68, "behavior_duplicate": 71, "low_coverage": 11, "constant_factor": 1}`。

全部 68 个成员完成 Test，沿用冻结训练方向。
单因子 Test 加权 Pearson 均值 / 中位数为 0.004672 / 0.005051，
正 IC 为 53/68，严格大于 0.005 的有 34 个；
加权 Spearman 均值为 0.001008。
两模型使用统一 model.toml，在 4,389,584 行 train+val 上拟合，
保留 68 个因子，Linear 秩为 68；LightGBM 固定 50 轮。
先保存模型再读取 Test，单因子与组合共用冻结矩阵，并核对首个因子的标准单因子执行结果。
Linear / LightGBM 的 Test 加权 Pearson 分别为 0.008176 / 0.015305。

Val/product 全量 2278 个无序不同因子对全部有效，取共同有限观测重新排名，
按配对等权计算绝对 Spearman，不抽样、不填补缺失，沿用 pooled 行口径。
均值 / 中位数 / P90 / 最大值为 0.119819 /
0.042138 / 0.355628 /
0.699055，≥ 0.7 的有 0 对。
与原始准入反馈核对的最大相关差为 7.77e-15。

此结果使用八字段快照；AlphaPROBE、V1 Async 的既有 Kernel 结果使用四字段，须按数据范围比较。
2021–2022 Test 已用于既有实验，本次属于同区间复评；Test 输出未反馈给搜索，也未用于筛选或调参。
模型拟合源码记录与搜索启动记录一致：`False`；配置、数据和冻结校验通过。
搜索早期请求计数按 Agent 回复轮次累加，不能据累计计数还原全部 HTTP 请求，断流用量未知。

单因子与模型报告位于 [500 次运行报告](../../artifacts/runs/futures_kernel_5m_full-all-fold1-react-42-af44aa3bd0-prefix500/report.md)
和 [模型报告](../../artifacts/runs/futures_kernel_5m_full-all-fold1-react-42-af44aa3bd0-prefix500/model/report.md)。
[完整评估汇总](../../artifacts/comparisons/react_kernel_full_af44aa3bd0_prefix500/evaluation_summary.json) 与同目录逐对结果保存完整精度；
`run_to_500.py`、`prepare.py`、`evaluate.py`、`summarize.py`、`publish.py` 保存暂停、冻结和复算入口。

## 2026-10-23 Futures kernel 四字段 fold1：ReAct DeepSeek 运行启动

Run ID：`futures_kernel_5m-all-fold1-react-42-ac32f1e3dc`。
按用户要求新开四字段 ReAct 实验，使用 `futures_kernel_5m` 的原四字段快照
`8f689ef3680e43b4f8f1c21f92bd21ad5f4c6a308e19ad31530f60bda4b4b49a`，
显式指定 `adj_close`、`adj_vwap`、`volume`、`amount`。
该数据输入与已有 Kernel 四字段 AlphaPROBE、V1 Async 实验一致。

沿用 AgentScope ReAct、`deepseek-flash`、温度 1.0、输出上限 65,536 token，
context 容量 131,072 token，all、fold1、seed 42；公共 benchmark 与参考库设置不变。
Train 2016–2018、Val 2019–2020，预热从 2015-01-01 开始，加载 5,241,386 行研究行情。
目标为未来 12 根基础 5m 的 adj_close 对数收益，因子按 product 窗口计算。
独立运行与因子库从零开始，原八字段运行继续停在第 500 次，其产物保留。

为沿用八字段运行的预算口径，本轮预设 1000 次，先在第 500 条 trial 完整保存后暂停；
停止点只用于本次启动，调用既有 runner 和检查点，不修改配置或原运行状态。
暂停后保留方法状态、反馈与剩余预算。当前仅启动 train/val 搜索，未产生本轮 OOS 结论。

screen：`atlas-react-kernel4-deepseek-500-20261023_114134`。
启动入口为 `artifacts/monitors/react_kernel_four_deepseek_1000_to500_20261023.py`，
使用正常 `runner.run`，四字段通过 `field_names` 显式传入。
暂停边界通过合成检查验证：第 500 次状态落盘、原预算保留，未进入第 501 次。
启动脚本已通过语法与 Ruff 检查；真实运行已确认至少 52 条 trial 原子提交，运行状态为 `running`。
正常运行环境下 `pwsh -File scripts/check.ps1` 通过，846 个测试通过。
[运行日志](../../artifacts/monitors/react_kernel_four_deepseek_500_20261023_114134.log)
与同名 `.sh` 保存实际启动过程；凭据仅从项目 `.env` 加载到模型进程，不写入命令或日志。

## 2026-10-23 Claude Haiku 5.5 ReAct 订阅联调

按用户明确要求，使用本机 Claude Code 2.1.309 的 `claude.ai` / Max 订阅登录，
通过独立 `integrations/claude_cli/` 模块验证真实模型与工具往返。请求及实际返回模型均为
`claude-haiku-5-5`。认证由 CLI 处理，没有提取或记录凭据；子进程清除 API key 和云服务路由。

联调使用现有 `ReactSearch` 的原生 AgentScope 路径、完整 23,428 字符系统提示、
`SearchSession`、DSL 编译／数值评估、准入及原子 `RunStore`。输入为 12 个合成资产、
30 个日 Bar、`close` 和 `x` 两字段，预算为两次评估；未读取真实市场数据或供应商。
人工联调指令仅约束工具顺序、逐个提交和预算结束后的总结；这是接入验收，
不作为 ReAct 的市场实验或方法表现比较，不写入实验成绩表。

普通文本请求正确返回指定标记，耗时 2.488 秒。ReAct 通过本机 Chat Completions SSE
完成 7 次真实模型请求，工具共调用 7 次，覆盖全部六种公共工具：

| 工具 | 次数 | 实际路径 |
| --- | ---: | --- |
| `get_context` | 1 | SearchSession 公共研究上下文 |
| `library_stats` | 1 | 当前正式库统计 |
| `library_list` | 1 | 已准入成员列表 |
| `library_search` | 1 | 当前运行库检索 |
| `library_get` | 1 | 实际准入因子的定义和评估报告 |
| `evaluate` | 2 | 原生 Toolkit 排队，再由平台编译、评估、准入和原子提交 |

两个候选均由真实 Haiku 生成，实际裁决如下；这些结果只验证流程，不说明策略有效性。

| 表达式 | 平台评估状态 | 准入 |
| --- | --- | --- |
| `NEG(RETURN($close, 5))` | `success` | 接受 |
| `TS_ZSCORE($x, 10)` | `undefined_train_metric` | 拒绝 |

合成 `x` 在每个资产内保持不变，第二个候选没有有效标准化值，平台返回 null 指标及零覆盖率。
验收脚本最初误把有效的拒绝裁决要求为 `success`；修正验收条件后，从原方法状态恢复并回传
已保存的第二条反馈，没有重新调用已完成的模型阶段或重新评估／提交 trial。
真实模型正确解释该拒绝，读到 `remaining_attempts=0` 后停止提案。
最终 trial 数为 2、正式成员数为 1、待处理候选为 0，未评估 OOS。

ReAct 累计已报告输入 161,725 token、输出 4,521 token，未知用量请求为 0；输入包含缓存
读写，不代表订阅账单或底层 API 的精确请求次数。两个阶段累计活动时间为 39.849 秒，
其中包含一次方法状态恢复；单独的文本连通请求不计入这组 ReAct 用量。
临时桥接在验收后关闭；默认模型配置和当前 DeepSeek 市场实验未切换。
CLI 的采样参数差异仍按模块说明处理，短流程成功不代表已完成千次长任务稳定性验证。

[汇总](../../artifacts/analyses/claude_cli_haiku55_live_20261023/summary.json)、
[模型和工具请求](../../artifacts/analyses/claude_cli_haiku55_live_20261023/model_calls.json)、
[方法状态](../../artifacts/analyses/claude_cli_haiku55_live_20261023/method_state.json)
与同目录的 `react_live.py`、`complete_saved.py`、`synthetic_run/` 保存实际执行证据。

## 2026-10-23 Futures kernel 四字段 fold1：ReAct DeepSeek 独立复跑 500 次评估与继续搜索

原运行：`futures_kernel_5m-all-fold1-react-42-145195d3de`。
本轮从空因子库独立重复四字段搜索，沿用原定 1000 次预算；北京时间 18:22 在第 500 条
原子 trial 保存后暂停，入库 65 个，入库率 13.00%，累计活动时间 7,974.32 秒。
评估快照为 `futures_kernel_5m-all-fold1-react-42-145195d3de-prefix500`，固定全部成员的
定义、训练方向、依赖和顺序；快照不可续跑，原运行及检查点经逐字节核对未变化。
暂停时无 pending trial，保留 4 条待回传反馈，没有未评估候选。

### 数据与搜索设置

使用 `futures_kernel_5m` 四字段快照
`8f689ef3680e43b4f8f1c21f92bd21ad5f4c6a308e19ad31530f60bda4b4b49a`，
输入为 `adj_close`、`adj_vwap`、`volume`、`amount`；all、fold1、seed 42。
目标为未来 12 根基础 5m 的 adj_close 对数收益，目标按真实合约及连续段隔离，因子按 product 窗口。
Train 2016–2018、Val 2019–2020、Test 2021–2022，预热从 2015-01-01 开始。
沿用 AgentScope ReAct、deepseek-flash、温度 1.0、输出上限 65,536 token、context 131,072 token，
参考库与准入规则不变。与四字段首轮 `ac32f1e3dc` 的数据、字段、模型配置、固定系统提示和源码来源一致；
ReAct 未把 seed 用于模型采样，候选序列可以不同。

500 次裁决计数：`accepted` 65、`quality_threshold` 312、`behavior_duplicate` 115、
`low_coverage` 6、`constant_factor` 1、`compile_error` 1。
暂停检查点记录模型请求 291 次，已知输入 19,498,497 token、输出 411,305 token，未知用量请求 1 次。

### 单因子与组合 TEST

先在 Train+Val 的 4,390,424 行上拟合并保存两个模型，再读取 Test；保留全部 65 列，
无移除因子，Linear 秩为 65。沿用统一模型配置：Linear 带截距、rcond=1e-12；
LightGBM 固定 50 轮、学习率 0.05、15 叶、最小叶样本 100、L2=1、4 线程、seed 42。
北京时间 18:34，全部 65 个单因子及两个组合模型完成 Test。

| 指标 | 结果 |
| --- | ---: |
| 单因子加权 Pearson 均值 / 中位数 | 0.002494 / 0.001603 |
| 单因子正 IC | 44 / 65，67.69% |
| 单因子主 IC 严格大于 0.005 | 23 / 65 |
| 单因子加权 Spearman 均值 | 0.001632 |
| Linear 加权 Pearson | 0.012126 |
| LightGBM 加权 Pearson | 0.019378 |
| 两模型预测行数 / eligible | 2,215,926 / 2,215,926 |
| 两模型预测覆盖率 | 100% |

指标内部按品种 √成交额加权，单因子统计再对成员等权汇总；单因子与模型均沿用训练方向。
两模型覆盖 72 个品种。单因子与组合共用冻结因子矩阵，首个表达式与标准单因子路径的
最大指标差为 0。配置、数据、冻结库与算子运行环境校验通过，拟合源码与搜索来源一致。

首轮四字段前 500 次入库 122 个，本轮为 65 个，八字段为 68 个；首轮的数量优势未在本轮复现。
三组使用相同 Test 区间，但模型预测覆盖率和实际评分行不同，不能把成绩差单独归因于字段数。
2021–2022 Test 已在既有实验中查看，本轮属于同区间复评；Test 输出未反馈给搜索，也未用于选列或调参。

### 从原检查点继续至 1000 次

按用户要求，沿本轮原运行 `145195d3de` 的检查点继续剩余 500 次尝试，总预算仍为 1000；
复用已提交 trial、正式因子库、方法状态和待回传反馈。500 次评估快照及其结果保留。
恢复使用标准 `atlas resume`，搜索仍只加载 Train/Val，Test 结果不进入方法上下文。
当前 500 次 Test 已查看，后续 1000 次的 Test 如再评估仍属于同区间复评。

screen：`atlas-react-kernel4-repeat-1000`。
[恢复入口](../../artifacts/monitors/react_kernel_four_repeat_145195d3de_resume1000_20261023.sh)
与[恢复日志](../../artifacts/monitors/react_kernel_four_repeat_145195d3de_resume1000_20261023.log)
保存实际启动命令和进度；凭据仅由外部 `.env` 加载，不写入命令或日志。

[单因子报告](../../artifacts/runs/futures_kernel_5m-all-fold1-react-42-145195d3de-prefix500/report.md)、
[模型报告](../../artifacts/runs/futures_kernel_5m-all-fold1-react-42-145195d3de-prefix500/model/report.md)、
[完整汇总](../../artifacts/comparisons/react_kernel_four_repeat_145195d3de/evaluation_summary.json)
和[结果对照](../../artifacts/comparisons/react_kernel_four_repeat_145195d3de/results_comparison.html)保存评估证据；
同目录 `monitor_evaluate_500.py` 保存监测与评估入口，`parent_capture.json` 保存原运行保留核对结果。

## 2026-10-24 Futures kernel 四字段 fold1：ReAct 保留 500 次因子池并重置上下文

新分支：`futures_kernel_5m-all-fold1-react-42-6990ca0c5f`。
按用户要求，使用 `145195d3de-prefix500` 的完整 65 个正式成员作为第 500 次时的因子池，
清空模型对话与摘要，从第 501 次继续到总预算 1000，剩余 500 次尝试。
已核对冻结库校验值，成员身份、定义、方向及顺序与前 500 条原子 trial 的准入记录一致。
新分支保留前 500 条 trial，正式库由现有 RunStore 重建；数据、目标、字段、fold、seed、
DeepSeek 配置、参考库及准入规则沿用源运行。

ReAct 采用新建的方法状态，初始对话、摘要、待评估候选和待回传反馈均为空。
新上下文通过现有 SearchSession 获取公共研究设置，并可用公共库工具查询保留的 65 个因子。
分支记录 `source_run_id`、`source_trial_count=500` 和 `context_reset_at_trial=500`；
模型用量计数从重置后开始，前 500 次的既有成本仍保留在源运行及对应实验记录中。
后续结果按第 500 次重置上下文的设置报告，已有 500 次 TEST 结果保留且未进入新模型上下文。

旧分支 `futures_kernel_5m-all-fold1-react-42-145195d3de` 已正常停止在 901 条已提交 trial、
201 个入库因子；第 902 个候选尚未提交，原检查点及全部已提交产物保留。
旧分支前 500、600、700、800、900 次累计入库分别为 65、80、87、141、200 个。

新分支使用标准 `atlas resume`，平台继续执行既有配置、预算、数据与方法状态校验。
screen：`atlas-react-kernel4-reset500-1000`。
[分支准备入口](../../artifacts/monitors/react_kernel_four_reset500_20261024.py)、
[启动入口](../../artifacts/monitors/react_kernel_four_reset500_6990ca0c5f_20261024.sh)及
[运行日志](../../artifacts/monitors/react_kernel_four_reset500_6990ca0c5f_20261024.log)保存实际恢复过程。

### 912 次中断后按原口径完成搜索与测试

`6990ca0c5f` 在北京时间 2026-10-24 03:10:31 停于 912 条已提交 trial、119 个正式因子。
错误为 `AgentScope ReAct made no evaluation request`，发生在生成下一候选时；
检查点没有待提交候选。119 个成员包含第 500 次继承的 65 个成员及后续新增的 54 个。
`run.json` 中尚未更新的 `library_size=65` 不代表当前库数量，数量以原子 trial 和检查点为准。

按用户要求，恢复原有未裁剪 `benchmark.toml`，保留第 912 次的方法状态和全部正式成员，
使用标准 `atlas resume` 继续到总预算 1000；不再次清空模型上下文。
旧运行没有 `clip_quantiles`，其搜索、缓存重建与单因子 OOS 均沿用原始因子值和标签。
当前新增的 1%–99% 裁剪配置已按原文件字节备份，只有搜索完成并且全部单因子与
Linear、LightGBM 的 Test 成功后才恢复。模型配置与既有 500 次评估核对一致。

[执行入口](../../artifacts/monitors/react_kernel_four_6990ca0c5f_complete_test_restore_20261024.sh)
串联恢复、冻结、评估和配置还原；
[流程脚本](../../artifacts/comparisons/react_kernel_four_reset500_6990ca0c5f/continue_evaluate_restore.py)
在冻结后核对成员数量，并复核继承的 65 个因子的 Test 指标与原 500 次快照一致。
同目录保存待还原配置和原搜索配置；异常退出保留检查点与日志，不提前宣告测试完成。
Test 已在前述实验中查看，后续结果仍属于同区间复评，不进入搜索上下文。

北京时间 08:58，流程在 screen `atlas-react-kernel4-complete-test` 启动，
[日志](../../artifacts/monitors/react_kernel_four_6990ca0c5f_complete_test_restore_20261024.log)
记录恢复与后续评估、配置还原结果。启动前完整检查通过，872 项测试全部通过。

### 1000 次最终结果

北京时间 10:14:40 搜索完成并冻结，1000 条已提交 trial 最终入库 136 个因子。
其中 65 个来自前 500 次因子池，后 500 次新增 71 个；从第 912 次恢复后新增 17 个。
最终准入原因计数为：入库 136、质量门槛 626、行为重复 220、常量 1、编译错误 7、
覆盖率不足 10。检查点没有待提交候选，`run.json`、检查点和冻结库的成员数均为 136。

10:41:12，136 个单因子及 Linear、LightGBM 的 Test 全部完成，随后将 `benchmark.toml`
恢复为启动前保存的完整文件，按字节核对一致，`clip_quantiles = [0.01, 0.99]` 已恢复。
本轮搜索与测试全程没有裁剪因子值或标签；恢复配置不改变已保存的运行和测试结果。

| 指标 | 结果 |
| --- | ---: |
| 单因子加权 Pearson 均值 / 中位数 | 0.001763 / 0.001166 |
| 单因子正 IC | 88 / 136，64.71% |
| 单因子主 IC 严格大于 0.005 | 33 / 136 |
| 单因子加权 Spearman 均值 | 0.000621 |
| Linear 加权 Pearson | 0.012771 |
| LightGBM 加权 Pearson | 0.021948 |
| 两模型预测行数 / eligible | 2,215,926 / 2,215,926 |
| 两模型预测覆盖率 | 100% |

两个模型覆盖 72 个品种，配置与原 500 次评估一致，Test 不重新拟合。
单因子与组合共用冻结因子矩阵，首个表达式与标准单因子路径的最大指标差为 0；
继承的 65 个成员全部 Test 指标与原 500 次快照的最大差也为 0。
模型产物分别记录搜索与拟合来源，本次拟合源码与搜索启动源码不同；既有因子身份、
运行配置、冻结库、数据快照和算子运行环境校验保留。

[单因子报告](../../artifacts/runs/futures_kernel_5m-all-fold1-react-42-6990ca0c5f/report.md)、
[模型报告](../../artifacts/runs/futures_kernel_5m-all-fold1-react-42-6990ca0c5f/model/report.md)和
[完整汇总](../../artifacts/comparisons/react_kernel_four_reset500_6990ca0c5f/evaluation_summary.json)
保存最终结果，主表同步更新至 1000 次。Val 库内因子相似度的补评见下文。


### 四字段 500、1000 次 Val/product 库内相似度补评

使用各自冻结定义、同一四字段快照和 Val 2019–2020，按 product 窗口重建；
每对取共同有限观测重新排名，计算绝对 Spearman，所有无序不同因子对等权，
不抽样、不填补缺失、不使用成交额权重。沿用原运行的未裁剪口径。

500 次的 65 个成员共 2,080 对，全部有效。绝对 Spearman 均值 / 中位数 / P90 / 最大值为 0.208221 / 0.170754 / 0.463688 / 0.695703，≥ 0.7 的有 0 对；
最小共同观测数为 1,441,531。原始准入反馈的 64 次比较全部核对通过，最近成员一致，最大相关差 2.22e-16。

1000 次的 136 个成员共 9,180 对，全部有效。绝对 Spearman 均值 / 中位数 / P90 / 最大值为 0.179021 / 0.140757 / 0.411079 / 0.695703，≥ 0.7 的有 0 对；
最小共同观测数为 1,433,205。原始准入反馈的 135 次比较全部核对通过，最近成员一致，最大相关差 2.22e-16。

已核对前 500 次的 65 个成员身份、定义、方向及研究设置，与完整 136 个成员库的对应前缀一致；
500 次结果直接取完整配对结果中的对应子集，没有重复计算或改变评分样本。
当前 benchmark 配置保持 1%–99% 裁剪，原运行、冻结库、Test 和模型产物保留。

[完整汇总](../../artifacts/comparisons/react_kernel_four_reset500_6990ca0c5f/similarity_summary.json)、
[1000 次逐对结果](../../artifacts/comparisons/react_kernel_four_reset500_6990ca0c5f/futures_kernel_5m-all-fold1-react-42-6990ca0c5f.json)及
[500 次逐对结果](../../artifacts/comparisons/react_kernel_four_reset500_6990ca0c5f/futures_kernel_5m-all-fold1-react-42-145195d3de-prefix500.json)
保存完整精度；总览的相似度表同步补齐两行。

## 2026-10-25 Futures Kernel 5m fold2：V1 Async 八字段 500、1000 次

原运行为 `futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900`，配置模型
`deepseek-flash`、seed 42，最多并行 3 个计划、每计划最多 5 个候选，预设 1000 次预算。
实际开放复权 OHLC、复权 VWAP、volume、amount 和 open_interest；数据快照为
`73db06c2d7223ce5829b8d3d9846a27b35e59e0d621a11a4a94d99dcc6ec0e87`。
Train 为 2019–2021，Val 为 2022–2023，Test 为 2024–2025，预热为 2018 年。
按 product 窗口执行，方向仅由 Train 确定；单因子评分和 Val 相似度均复用冻结的
Train 1%–99% 裁剪边界。组合模型沿用[模型预处理口径](../model.md)，配置没有改变。

运行在第 720 次因模型服务 HTTP 402 停止，余额可用后从原检查点继续，保留 37 个因子和
原方法状态，最终完成 1000 次、冻结 52 个因子。实际始终使用 DeepSeek，没有切换 Haiku。
1000 次的单因子、两种组合模型和 Val 全库相似度均由标准完成流程计算并自动填表。

500 次评估目录为原 Run ID 加 `-prefix500`。从前 500 条原子 trial 重建当时的 23 个成员，
定义、方向、评估报告和裁剪边界与完整冻结库的前 23 个成员逐项一致。它是同一 1000 次
预算搜索的前 500 次提交截面，沿用原退火调度，不代表独立预设 500 次预算的搜索。
截面只用于评估，没有复制最终方法状态，也不能作为搜索检查点恢复；原运行产物保留。
使用标准 `atlas finish` 重新拟合该截面的模型并完成全部三项评估，没有新增候选。

| 指标 | 500 次 | 1000 次 |
| --- | ---: | ---: |
| 入库因子数 | 23 | 52 |
| Test 单因子加权 Pearson 均值 | 0.003478 | 0.002062 |
| Test 单因子加权 Pearson 中位数 | 0.002727 | 0.001921 |
| Test 单因子正 IC | 18 / 23，78.26% | 38 / 52，73.08% |
| Test 单因子主 IC 严格大于 0.005 | 6 / 23 | 8 / 52 |
| Test 单因子加权 Spearman 均值 | 0.002806 | 0.001211 |
| Linear Test 加权 Pearson | 0.010187 | 0.014061 |
| LightGBM Test 加权 Pearson | 0.003268 | 0.017574 |
| Linear Test 加权 Spearman | 0.012472 | 0.014273 |
| LightGBM Test 加权 Spearman | 0.011205 | 0.011897 |
| 组合模型预测行数 / eligible | 2,558,265 / 2,558,829 | 2,558,541 / 2,558,829 |
| 组合模型预测覆盖率 | 99.978% | 99.989% |
| Val 因子对数 | 253 | 1326 |
| Val 平均绝对 Spearman | 0.094962 | 0.113493 |
| Val 绝对 Spearman 中位数 | 0.024486 | 0.041008 |
| Val 绝对 Spearman P90 | 0.343017 | 0.356315 |
| Val 最大绝对 Spearman | 0.691091 | 0.691091 |
| Val 绝对 Spearman ≥ 0.7 的对数 | 0 | 0 |

两组模型使用各自冻结库，在 Train+Val 上分别拟合，Test 不重新训练或选择因子；
预测主指标覆盖 85 个品种。Val 共 2,281,830 行，所有配对均有效，最小共同观测数分别为
2,039,283 和 1,915,122。前 23 个因子的全部单因子 Test 指标及覆盖率与完整库对应结果
最大差为 0；其 253 对相似度的相关值最大差也为 0，共同观测数全部相同。

后 500 次新增 29 个因子。完整库的单因子平均 IC 下降，但两个组合模型的加权 Pearson
均提高，尤其是 LightGBM；这是同一 Test 区间的观察，不据此调整搜索或模型参数。

[500 次报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900-prefix500/report.md)、
[1000 次报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4e3f0e7900/report.md)及
[完整对照与核对](../../artifacts/comparisons/v1_async_kernel8_fold2_4e3f0e7900_prefix500/evaluation_summary.json)
保存具体结果；同目录的 `prepare.py`、`verify.py` 保存截面重建和核对入口。
总览的 Fold2 主表与库内相似度表均按 500、1000 次排列。

## 2026-10-26 Futures Kernel 5m fold2：V1 Async Haiku 5.5 启动

Run ID：`futures_kernel_5m_full-all-fold2-v1_async-42-4b05105ca2`。使用独立
`integrations/claude_cli/` 桥接和本机 Claude Code 2.1.310 的 `claude.ai` / Max 订阅登录，
请求与真实连通检查返回的模型均为 `claude-haiku-5-5`。桥接仅监听本机 8765，
未使用 Anthropic API key 路线；凭据由 CLI 管理，不写入配置或日志。

从零创建新运行，预设 1000 次预算；沿用 DeepSeek 对照的 Kernel 八字段、同一数据快照、
all、fold2、seed 42、product 窗口、Train 1%–99% 冻结裁剪、参考库和准入规则。
仍为 3 个并行研究计划、每计划最多 5 个候选，其余研究参数及下游模型配置逐项核对一致。
配置只更换模型、Chat 地址、凭据环境变量名，并把传输超时从 120 秒改为 1800 秒。

CLI 后端不映射 temperature、repair_temperature，也不映射调用方的输出额度。
方法配置保留的温度值不代表已用于模型采样，实际生成由 CLI 自身设置控制。
此实验记录为 Claude CLI 后端适配，不能宣称与 DeepSeek
API 使用了相同的采样参数。差异与工具边界见[模块说明](../../integrations/claude_cli/README.md)。

真实连通检查在 5.374 秒内返回固定标记，已报告输入 560、输出 21 token。
[连通记录](../../artifacts/analyses/haiku55_v1_async_kernel8_fold2_live/preflight.json)只保存模型、
用量和核对结果，不保存登录凭据。原 DeepSeek、500 次截面及其所有评估产物保留；
原模型配置字节备份于
`artifacts/comparisons/v1_async_haiku55_kernel8_fold2_1000/v1_async.deepseek.toml`。

screen 为 `atlas-fold2-v1async-kernel8-haiku55-1000`；独立桥接 screen 为
`atlas-claude-cli-bridge`。[运行配置](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-4b05105ca2/run.json)
与[启动日志](../../artifacts/monitors/v1_async_kernel8_fold2_haiku55_1000_20261026_000010.log)
保存本次实际条件。搜索冻结后默认执行单因子 Test、组合模型和 Val 相似度并填表。
本节只记录启动，不将未完成的运行计入实验成绩。

## 2026-10-26 Futures Kernel 5m fold2：Haiku 5.5 使用 JSON 兼容逻辑从零重跑

Run ID：`futures_kernel_5m_full-all-fold2-v1_async-42-ba99320ee0`。按用户要求，从零创建
独立研究状态和因子库，预算 1000 次；启动检查点为 0 次完成、0 个成员、0 条 trial。
旧运行 `4b05105ca2` 已中断，检查点保留 268 次完成、8 个成员，全部原产物保留。

新运行使用公共 JSON 回复解析：接受完整 JSON 代码块，Analyst 的非空对象型
`miner_instruction` 无损转为 JSON 文本交给 Miner；修复耗尽保留最后一条具体校验错误。
字段权限、DSL、准入和预算仍执行原规则；实现与模拟验证见
[公共协议](../method_reproduction.md#公共信息与初始化)和
[三 Agent 说明](../methods/annealing.md#一轮研究)。启动前已完成全量检查，932 项通过。
与历史运行比较时须披露本次解析和提示词改动。

逐项核对与旧 Haiku 运行的模型配置、资产 profile、benchmark、八字段、fold2、seed 42、
all 和 1000 次预算一致；数据快照仍为
`73db06c2d7223ce5829b8d3d9846a27b35e59e0d621a11a4a94d99dcc6ec0e87`。
模型为 `claude-haiku-5-5`，仍使用同一 Claude CLI 订阅桥接；3 个并行计划、每计划最多
5 个候选，Train 1%–99% 冻结裁剪。CLI 采样参数的限制沿用上一节，不据此声称 API 等价。

screen 为 `atlas-fold2-v1async-kernel8-haiku55-1000`，窗口为 `fresh-1000`。
[运行配置](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ba99320ee0/run.json)和
[日志](../../artifacts/monitors/v1_async_kernel8_fold2_haiku55_compat_1000_20261026_091057.log)
记录本次启动；完成后自动执行单因子 Test、组合模型、Val 库内相似度并填表。
启动联调已确认 Explorer 和 Miner 的带代码块回复正常解析，并完成真实候选评估与入库；
该确认只证明调用与解析流程可用，不作为完整 1000 次实验结论。

## 2026-10-26 Futures Kernel 5m fold2：Haiku 5.5 前台运行在 413 次停止

Run ID：`futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059`。用户从零在前台启动，
使用英文提示词；预算 1000 次，保存的检查点为 413 次完成、16 个正式成员。
模型仍为 `claude-haiku-5-5`，Kernel 八字段、fold2、seed 42、all、1%–99% 裁剪。

北京时间 12:27:10，Explorer 第 84 轮连续两次返回合法 JSON 的 `unresolved`，触发既有
终止规则。回复认为已测及活动机制大类覆盖了当前可提出的方向，其余设想需要缺少的数据或
算子；这只是模型判断，不是平台验证过的全空间耗尽结论。两组已有计划各完成 4/5，
尚未冻结或执行 Test。[失败记录](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059/failures.json)
及[检查点](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059/checkpoint.json)
保留当时的回复、进度和因子库版本。

本次调整仅增强首次 `unresolved` 的服务端澄清：已有大类不是其中所有问题耗尽的证据；
同一区域可以提出有实质差异的可证伪子问题、条件或交互及判别预测，同时解释与最近方向的
区别。保留用户修改的主提示词，以及重复、字段、算子、频率、准入、预算和再次不足即停止
规则。模拟验证覆盖同步／异步澄清后提交、重复不足停止，以及终止检查点不重发模型请求。
相关 109 项测试与全量检查均通过，全量共 934 项。
该调整尚未经过真实模型验证，不能保证模型找到后续方向或跑满预算；此次没有重置检查点、
修改因子池或重启真实运行。

北京时间 13:09:57，用户授权保留本次进度并重开失败的 Explorer。操作持有原运行写锁，
先备份[原检查点](../../artifacts/repairs/v1_async_ef96a68059_reopen_explorer_20261026_130957/checkpoint.json)、
运行配置及失败记录，再仅将当前检查点的 `method_state.explorer` 置空。原始 413 条 trial、
16 个不同因子、82／83 两组各 4/5 的计划进度、RNG、模型用量、运行配置和失败记录保留。
总预算仍为 1000，剩余 587 次；配置及原子 trial 校验通过。只读模拟恢复确认下一次补位会
创建第 85 轮空 Explorer 对话，两组计划及请求计数不变。没有调用真实模型或启动实验；
已交付用户在前台执行的原 run `atlas resume` 命令。

北京时间 15:02:06，同一运行已实际续跑到 444 次、16 个成员，在新的第 91 轮 Explorer
再次报告不足。此前从 413 次继续后的 31 条 trial 中，24 条质量拒绝、6 条编译失败、
1 条行为重复，没有新增成员。检查点确认新的澄清已被模型收到，此次是新的不足报告。

用户随后明确选择允许回访已有方向做证据驱动的深入研究，继续原 1000 次预算。Explorer
主提示、收敛提醒、澄清及异步活动计划说明统一采用该策略：历史方向只是尝试记录，
少量候选不代表整个机制已被穷尽；允许回访已完成方向，须说明尚未回答的问题、已有证据
或缺失测量，以及下一步可证伪检验。breadth/depth 继续用于分配研究重心，不要求持续发明
全新机制。批内和活动计划重复检查、四项评分、字段、算子、频率、准入、Metropolis、
查询／修复上限及候选预算保持原样；新问题和合理回访都无法提出时仍保留显式停止规则。
这是本地搜索策略调整；本 run 的前 444 次保留原轨迹，后续采用回访策略，不能作为全程
采用同一搜索协议的纯模型对照。相关 101 项及全量检查通过，全量共 936 项。

北京时间 17:15:59，持原写锁备份
[444 次检查点](../../artifacts/repairs/v1_async_ef96a68059_revisit_from444_20261026_171559/checkpoint.json)、
运行配置和失败记录，仅将当前检查点的 `method_state.explorer` 置空。444 条原子 trial、
16 个因子、90 组 4/5 的计划进度、RNG 和模型用量保留，总预算 1000、剩余 556 次。
配置及 trial 校验通过；离线模拟确认下一轮为第 92 轮，实际发给模型的消息会包含回访
许可、现有活动计划排除及原八字段权限。此次没有发送真实模型请求或启动实验，前台
续跑仍由用户执行。

## 2026-10-26 Futures Kernel 5m fold2：Haiku 5.5 前 500 次评估

原运行 `futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059` 已超过 500 次。
按用户要求，从其前 500 条原子 trial 重建并冻结 17 个入库成员，保存为
`futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059-prefix500`。这是原 1000 次预算及
退火日程的提交截面，标记为不可续跑；原搜索继续独立运行。复制前后 500 条 trial 字节
一致，17 个成员的定义、编译身份、训练方向和 Train 裁剪边界保持原样，没有重选因子。

使用 Kernel 八字段、all、fold2、seed 42、product 窗口与 Train 1%–99% 冻结裁剪。
Train 为 2019–2021，Val 为 2022–2023，Test 为 2024–2025；快照与 DeepSeek 对照相同。
500 次中入库 17 次、质量拒绝 367 次、编译失败 74 次、行为重复 25 次、覆盖不足 12 次、
常量因子 2 次、表达式重复 3 次。原子记录的活动时间合计为 6074.34 秒，不含停机等待。

| 指标 | Haiku 5.5 / 500 次 |
| --- | --- |
| 入库因子 | 17 |
| Test 单因子加权 Pearson 均值 | 0.006987 |
| Test 单因子加权 Pearson 中位数 | 0.006948 |
| Test 单因子正 IC | 16 / 17，94.12% |
| Test 单因子加权 Pearson > 0.005 | 12 / 17 |
| Test 单因子加权 Spearman 均值 | 0.004490 |
| Test Linear 加权 Pearson / Spearman | 0.023946 / 0.013454 |
| Test LightGBM 加权 Pearson / Spearman | 0.024710 / 0.014953 |
| Val 因子对数 | 136 |
| Val 平均绝对 Spearman | 0.060503 |
| Val 绝对 Spearman 中位数 / P90 | 0.009680 / 0.181811 |
| Val 最大绝对 Spearman | 0.684691 |
| Val 绝对 Spearman ≥ 0.7 的对数 | 0 |

单因子 Test 全部成功，有限值覆盖率最小 88.6552%、中位数 93.9361%。Linear 与
LightGBM 使用相同的既定模型配置，在 Train+Val 的 5,308,089 行上分别拟合并保存后，
才加载 Test；不对模型输入和标签套用单因子 IC 裁剪。Test 两模型均预测 2,558,824 /
2,558,829 个有效目标行，覆盖率 99.9998%。Val 共 2,281,830 行，136 对全部有效，
最小共同有限观测数 1,997,982；沿用冻结因子裁剪，逐对重新排名、全部配对等权。

本运行使用英文提示词、公共 JSON 回复兼容逻辑，且第 444 次后才启用证据驱动回访；
CLI 订阅后端也不映射调用方的采样温度。这些差异使其不能作为与历史 DeepSeek 运行的
纯模型对照。评估结果仅写入独立截面产物及表格，不回传搜索，也不据 Test 改变后续策略。

[截面报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059-prefix500/report.md)、
[组合模型报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059-prefix500/model/report.md)、
[评估日志](../../artifacts/monitors/haiku55_ef96a68059_prefix500_finish_20261026_1753.log)及
[核对与 DeepSeek 500 次对照](../../artifacts/comparisons/v1_async_kernel8_fold2_haiku55_ef96a68059_prefix500/evaluation_summary.json)
保存结果；同目录的 `prepare.py` 和 `verify.py` 保存重建及核对入口。总览的 Fold2
主表和库内相似度表已同时填入本次 500 次结果。

## 2026-10-26 Futures Kernel 5m fold2：Haiku 5.5 在 622 次的 Explorer 证据误读

原运行 `futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059` 于北京时间 18:13:56
在第 128 轮 Explorer 连续两次报告不足后停止，已提交 622 次、入库 22 个不同因子。
两个活动计划为第 126 组 4/5、第 127 组 3/5，总预算仍为 1000，剩余 378 次。

本轮两条回复都没有发起查询，却声称 `library_get` 已对 `trial:508` 和 `trial:133`
返回 null，并把 `previous_directions` 称为待执行队列。实际输入已包含 `trial:133` 的
完整公式和 Train/Val 指标；它未入库，正式库查询为空也不代表试验缺少定义。
`trial:508` 已入库，原子记录保留其完整定义，可通过正式成员的真实 `factor_id` 或
`library_search` 查询。历史方向表来自已有 trial，只有 `active_proposals` 表示当前
活动计划，过去未选提案并没有排队。这次不足理由包含与已保存输入及对话不符的模型判断。

修正 Explorer 主提示与首次不足澄清，明确上述证据、引用和状态语义：地图附带的
`candidate.expression`、`evaluation` 可直接引用；`trial:N` 不是成员 ID；查询失败
只能以已保存查询结果为依据。公共工具、数据权限、字段、算子、预算、评分、准入和
两次不足即停止规则保持原样。完整 `scripts/check.ps1` 通过，共 936 项；
[检查日志](../../artifacts/monitors/v1_async_ef96a68059_after622_checks.log)保存结果。

北京时间 18:22:15，持原运行写锁备份
[622 次检查点](../../artifacts/repairs/v1_async_ef96a68059_evidence_from622_20261026_182215/checkpoint.json)、
运行配置和失败记录，仅将当前检查点的 `method_state.explorer` 置空。
其他检查点内容逐项一致，包括 622 次进度、22 个因子身份、两个活动计划、RNG 和模型用量；
运行配置、失败记录和原子 trial 保留。配置校验通过，离线恢复确认下一轮为第 129 轮，
新槽配额 5，原两组进度及请求计数不变。此次没有发送真实模型请求或启动搜索，
前台续跑仍使用原运行的 `atlas resume` 命令；原 500 次评估产物保留。

## 2026-10-26 Futures Kernel 5m fold2：887 次补位不足的异步调度修复

原运行 `futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059` 于北京时间 19:24:25
再次停止，已提交 887 次、入库 29 个不同因子。第 182 轮 Explorer 两次报告需要先获得
活动计划的结果；第 179 组仍为 4/5、第 181 组为 3/5，合计还有 3 次计划内候选。
此前实现将补位的研究不足直接作为全局异常抛出，阻止了这两组继续产生反馈。
前几次提示词澄清没有处理这一调度边界。

还存在证据交接缺口：本轮地图完整示例只包含 10 条各区域极值试验，
原两组已经产生的 874、876、879、884、885、886、887 共 7 条结果全部不在其中。
这些试验均未入库，不能通过正式库查询补全；因此模型对 B/C 测量缺失的判断不能仅靠
提示词纠正。新 Explorer 现在接收最近完成计划的全部候选记录，串行一组、异步按
`parallel_plans` 取最近完成组，含未入库与失败项。地图与近期 trial 被引用时，完整候选
和 Train/Val 反馈通过已有 `sources` 交给 Analyst，不再只传试验编号；不扩展公共工具权限。

此次区分有界对话中的研究不足与格式、传输等硬错误。补位快照中存在活动计划时，
不足回复先保留，原计划继续执行；等待期间不重新请求 Explorer。全部活动计划完成后，
不足轮次和原回复移入最后一条研究 step 的 `deferred_explorer`，再用最新地图补位。
活动计划完成后才到达的旧不足回复也遵循该规则。新的 Explorer 没有活动计划可等待时，
仍不足则保留明确停止；格式修复耗尽和模型硬错误继续报错。

沿用原检查点、回复、研究 step 和预算控制，不新增等待队列或状态版本；未清除已保存的
模型请求，不增加候选或放宽字段、算子和准入。总预算仍为 1000，887 次后的剩余预算
为 113，其中 3 次属于原活动计划。该调度调整影响之后的异步轨迹，比较时须披露。

北京时间 23:42:11，持原运行写锁备份
[887 次检查点](../../artifacts/repairs/v1_async_ef96a68059_defer_from887_20261026_234211/checkpoint.json)、
运行配置和失败记录。原三份文件字节保持一致，没有手动重置 Explorer。
离线加载真实检查点后，连续三次执行补位调度均不再抛出不足异常，不发送模型请求，
全部方法状态及两个活动计划保持一致；可直接以前台 `atlas resume` 恢复。
此次没有启动真实搜索，也没有重新执行或向方法回传 Test。

相关回归覆盖实时、已保存和恢复后的不足，以及 1/40 轮边界、等待期间不重复请求、
计划完成后使用新证据、迟到回复、无活动计划时停止、格式错误继续失败和预算尾部。
另验证近期完成计划的选择边界、失败／拒绝保留、预算信息隔离，以及地图和近期未入库证据
到 Analyst 的完整交接。真实检查点的近期证据包含第 177、178、180 组的 15 条完整结果，
全部未入库；[离线核对](../../artifacts/repairs/v1_async_ef96a68059_defer_from887_20261026_234211/offline_verification.json)
确认原状态不变、零模型请求及证据只包含 Train/Val 指标。相关 V1 / V1 Async 共 116 项通过，
最终完整检查共 951 项通过，结果保存在
[检查日志](../../artifacts/monitors/v1_async_ef96a68059_after887_scheduler_feedback_checks.log)。

## 2026-10-27 Futures Kernel 5m fold2：AlphaPROBE Haiku 5.5 完成 1000 次评估

Run ID：`futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de`。搜索已冻结，
1000 次提交、32 个正式成员。使用 `claude-haiku-5-5` 的 Claude CLI 订阅桥接，
embedding 为独立 `Qwen3-Embedding-0.6B`；无预置初始因子，活跃池上限 50、top-k 15、
每批 5 个子代。模型采样及输出额度的 CLI 限制沿用[模块说明](../../integrations/claude_cli/README.md)。

数据为 Kernel 八字段、all、fold2、seed 42、product 窗口，Train 2019–2021、
Val 2022–2023、Test 2024–2025，Train 1%–99% 冻结裁剪。与本轮 AlphaAtlas Haiku
运行 `ef96a68059` 的数据快照、字段、fold、规则、数值执行设置及下游模型参数逐项核对一致。
两方法各自维护独立因子库。搜索记录为质量拒绝 767 次、行为重复 175 次、编译失败 5 次、
表达式重复 9 次、覆盖不足 11 次、常量因子 1 次和入库 32 次；已记录活动时间 5.86 小时，
不含停机等待，不代表精确模型账单或墙钟完成时间。

用户请求查看结果时，搜索已完成而三项评估产物尚不存在。本次使用标准 `atlas finish`
完成 Train+Val 拟合、单因子 Test、保存模型的 Test 和 Val 全库相似度，三项成功后同步填表。
全部 32 个冻结定义、训练方向和裁剪边界保留，没有按 Test 选择成员或重启搜索。

| 指标 | AlphaPROBE / Haiku 5.5 | AlphaAtlas V1 Async / Haiku 5.5 |
| --- | --- | --- |
| 尝试次数 / 入库因子 | 1000 / 32 | 1000 / 31 |
| Test 单因子加权 Pearson 均值 | 0.004680 | 0.006608 |
| Test 单因子加权 Pearson 中位数 | 0.004562 | 0.006227 |
| Test 单因子正 IC | 29 / 32 | 26 / 31 |
| Test 单因子加权 Pearson > 0.005 | 15 / 32 | 19 / 31 |
| Test 单因子加权 Spearman 均值 | 0.003117 | 0.002971 |
| Test Linear 加权 Pearson / Spearman | 0.010547 / 0.012009 | 0.028795 / 0.010938 |
| Test LightGBM 加权 Pearson / Spearman | 0.009124 / 0.013949 | 0.032993 / 0.007007 |
| Val 平均绝对 Spearman | 0.122526 | 0.055222 |
| Val 因子对数 | 496 | 465 |
| Val 绝对 Spearman ≥ 0.7 的对数 | 0 | 0 |

AlphaPROBE 的单因子 Test 全部成功，覆盖率中位数 93.2015%、最小 78.3083%。
其中 `alphaprobe:182:2` 的成交额冲击快慢均值比因子，Val 覆盖率为 81.1553%，
Test 降至 78.3083%，Test 主 IC 为 -0.006941；它是唯一 Test 覆盖率低于 80% 的成员。
80% 是搜索期准入门槛，不据 Test 重新准入或剔除，汇总仍保留全部 32 个冻结成员。

两种组合模型均在 Train+Val 的 5,308,104 行上拟合后保存，输入和标签沿用既定未裁剪
预处理；Test 各预测 2,558,827 / 2,558,829 个有效目标行，覆盖率 99.9999%。
Val 共 2,281,830 行，496 对全部有效，最小共同观测数 1,825,621；绝对 Spearman
中位数 0.032349、P90 0.432565、最大 0.695070，沿用冻结裁剪并在共同有限行重新排名。

本轮 AlphaPROBE 的正 IC 比例和两个组合的 Spearman 较高，AlphaAtlas 的单因子平均
主 IC 与两个组合的 Pearson 较高、库内平均相关性较低。AlphaAtlas 搜索中途有提示词、
回访策略和异步调度调整，因此这里只作同数据评估对照，不将差异归为严格固定协议的算法优势。

[运行报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de/report.md)、
[模型报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de/model/report.md)、
[评估日志](../../artifacts/monitors/alphaprobe_bc919289de_fold2_finish_20261027.log)及
[核对与对照汇总](../../artifacts/comparisons/alphaprobe_kernel8_fold2_haiku55_bc919289de/evaluation_summary.json)
保存结果；同目录的 `verify.py` 为核对入口。总览 Fold2 主表和库内相似度表已补齐，
并移除期货 AlphaPROBE 的待开展条目。

## 2026-10-27 Futures Kernel 5m fold2：AlphaPROBE 前 500 次截面对照

原运行 `futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de` 已完成 1000 次。
根据用户请求，从前 500 条原子 trial 重建评估截面，入库 20 个因子；
`-prefix500` 仅用于评估，不是独立的 500 次预算搜索，也不是可续跑检查点。
Kernel 八字段、all、fold2、seed 42、Haiku 5.5、Train 1%–99% 裁剪以及其余配置
均沿用原运行，20 个成员的定义、训练方向和裁剪边界与最终冻结库的对应成员逐项一致。

使用标准 `atlas finish`，分别以这 20 个因子在 Train+Val 的 5,308,104 行上拟合
Linear、LightGBM，再进行模型 Test、单因子 Test 与 Val 全配对相似度评估。
没有复制 32 因子组合的拟合结果，没有按 Test 筛选成员或修改原 1000 次运行。

| 指标 | 前 500 次 | 1000 次 |
| --- | --- | --- |
| 入库因子数 | 20 | 32 |
| Test 单因子加权 Pearson 均值 | 0.006127 | 0.004680 |
| Test 单因子加权 Pearson 中位数 | 0.005777 | 0.004562 |
| Test 单因子正 IC | 20 / 20 | 29 / 32 |
| Test 单因子加权 Pearson > 0.005 | 12 / 20 | 15 / 32 |
| Test 单因子加权 Spearman 均值 | 0.003995 | 0.003117 |
| Test Linear 加权 Pearson / Spearman | 0.011898 / 0.013114 | 0.010547 / 0.012009 |
| Test LightGBM 加权 Pearson / Spearman | 0.018286 / 0.016223 | 0.009124 / 0.013949 |
| Val 平均绝对 Spearman | 0.156782 | 0.122526 |
| Val 因子对数 | 190 | 496 |
| Val 绝对 Spearman ≥ 0.7 的对数 | 0 | 0 |

前 500 次单因子 Test 全部成功，覆盖率最小 90.9651%、中位数 93.5841%。
两个组合模型各预测 2,558,827 / 2,558,829 个有效目标行，覆盖率 99.9999%。
Val 190 对全部有效，最小共同观测数 2,040,833；绝对 Spearman 中位数 0.054269、
P90 0.487625、最大值 0.695070。

核对确认，共同 20 个因子的全部单因子指标及覆盖率、共同 190 对的相关性和共同观测数
与原 1000 次评估一致，数值最大绝对差为 0。这个截面的单因子平均主 IC 和两个组合
的 Test 主 IC 较高，1000 次截面的因子数更多、Val 平均库内相关性较低；仅记录本次
同一路径的截面对照，不据 Test 回调搜索或裁剪因子库。

[500 次运行报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de-prefix500/report.md)、
[500 次模型报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-bc919289de-prefix500/model/report.md)、
[评估日志](../../artifacts/monitors/alphaprobe_bc919289de_prefix500_finish_20261027.log)及
[核对与对照汇总](../../artifacts/comparisons/alphaprobe_kernel8_fold2_haiku55_bc919289de_prefix500/evaluation_summary.json)
保存结果；同目录的 `prepare.py`、`verify.py` 为重建和核对入口。
总览 Fold2 主表与库内相似度表均已加入 500 次行，放在同一方法的 1000 次行之前。

## 2026-10-28 V1 历史计划证据：Haiku 190、194、196 只读复放

证据来源为已冻结运行 `futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059`。
第 39 组包含 trial 190、192、194、196、197；190 测主交互，194 测持仓增加项，
两者未过质量门槛，196 是波动率对照，因行为重复未入库。第 169 组提案只引用 196，
曾把可见对照与主假说的差异当成继续检验持仓项的依据。

新实现按现有 `round_index` 组织历史索引和完整 plan/trial 证据，具体读取及确认规则见
[V1 历史读取](../methods/v1.md#按计划读取历史)。本次直接读取保存的方法状态；在最终地图
的初始展开范围内已没有第 39 组，但历史索引仍能将 196 关联到这五条实际测量。
用保存的第 169 组提案作两次模拟回复：首次草稿触发整组证据补全，未形成可执行提案；
第二次确认后，原引用仍为 `trial:196`，交给 Analyst 的 `sources` 包含原计划和全部五条记录。

190、194、196 的公式、数值指标和裁决与原子 trial 逐项一致；原检查点、运行配置、失败
记录和这三条 trial 的字节内容保持不变，原方法状态、RNG 与用量不变。没有调用模型或供应商，
没有重新评估市场数据、冻结库或 Test；模拟确认只检验证据可见性及交接，不证明真实模型
一定正确解释这些记录或改善市场搜索。历史运行不回写新策略，后续采用新策略的运行须披露。

同步、异步、并发计划隔离、未展示历史的确认边界、对话耗尽、非法引用、补全前后及确认后
恢复、旧输入格式恢复，共 135 项 V1/V1 Async 回归通过；均使用合成数据与模拟服务。
完整执行 `pwsh -File scripts/check.ps1`：Ruff、格式及算子目录检查通过，987 项测试全部通过。
本次检查限制进程内计算线程，检查范围不变；[完整日志](../../artifacts/monitors/v1_plan_memory_20261028_checks.log)
保留输出。
真实记录复放结果见[核对结果](../../artifacts/comparisons/v1_plan_memory_haiku_ef96a68059_20261028/verification.json)，
同目录 `verify.py` 为复算入口。

## 2026-10-27 Futures Kernel 5m fold2：MCTS-LLM Haiku 5.5 前 500 次截面对照

原运行 `futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5` 的总预算为 1000 次。
根据用户请求，从前 500 条已提交原子 trial 建立 `-prefix500` 评估截面，入库 13 个因子。
建立截面时原搜索已提交 528 次，数值核对时为 532 次；本次没有暂停搜索、改写原运行的
配置、方法状态或检查点，也没有向方法回传 Test。截面仅用于评估，不是独立 500 次预算
搜索，也不支持续跑。前 500 条 trial 字节与原件逐一一致，冻结定义、训练方向和 Train
裁剪边界逐项保留。

使用 Haiku 5.5 的本机 Claude CLI 订阅桥接，MCTS-LLM 的 UCT c 和维度 T 均为 1，
树初始预算 3、突破增量 1、FSA top-k 3，保留树选择及五维奖励。CLI 采样限制沿用
[桥接说明](../../integrations/claude_cli/README.md)。数据为 Kernel 八字段、all、fold2、
seed 42、product 窗口，Train 2019–2021、Val 2022–2023、Test 2024–2025，
Train 1%–99% 冻结裁剪。与 AlphaPROBE、AlphaAtlas 的 Haiku 500 次截面逐项核对，
数据快照、字段、fold、预热、平台规则、算子执行设置和下游模型配置一致。

前 500 次包含质量拒绝 448 次、行为重复 35 次、覆盖不足 4 次和入库 13 次。
已记录搜索活动时间为 6.08 小时，不含停机等待，不代表模型账单或精确推理次数。
使用标准 `atlas finish`，完成 Train+Val 独立拟合、模型 Test、单因子 Test 及 Val
全配对相似度评估，全部成功后同步填入总览。

| 指标 | MCTS-LLM / 500 | AlphaPROBE / 500 | AlphaAtlas V1 Async / 500 |
| --- | --- | --- | --- |
| 入库因子数 | 13 | 20 | 17 |
| Test 单因子加权 Pearson 均值 | 0.007796 | 0.006127 | 0.006987 |
| Test 单因子加权 Pearson 中位数 | 0.008423 | 0.005777 | 0.006948 |
| Test 单因子正 IC | 13 / 13 | 20 / 20 | 16 / 17 |
| Test 单因子加权 Pearson > 0.005 | 12 / 13 | 12 / 20 | 12 / 17 |
| Test 单因子加权 Spearman 均值 | 0.009183 | 0.003995 | 0.004490 |
| Test Linear 加权 Pearson / Spearman | 0.015626 / 0.013526 | 0.011898 / 0.013114 | 0.023946 / 0.013454 |
| Test LightGBM 加权 Pearson / Spearman | 0.016760 / 0.009559 | 0.018286 / 0.016223 | 0.024710 / 0.014953 |
| Val 平均绝对 Spearman | 0.245222 | 0.156782 | 0.060503 |
| Val 有效因子对数 | 78 | 190 | 136 |
| Val 绝对 Spearman ≥ 0.7 的对数 | 0 | 0 | 0 |
| Train+Val 有效拟合行数 | 5,067,849 | 5,308,104 | 5,308,089 |
| 两种组合模型 Test 覆盖率 | 94.2372% | 99.9999% | 99.9998% |

MCTS-LLM 的 13 个单因子 Test 全部成功，覆盖率最小 86.6410%、中位数 89.8228%。
Val 共 2,281,830 行，78 对全部有效，最小共同观测数 1,924,852；绝对 Spearman
中位数 0.163154、P90 0.526695、最大 0.669180。

两种组合均保留全部 13 个输入列，无常量或不可用列移除。模型沿用原始未裁剪输入和
目标、Train+Val 统计标准化及均值填补。统一规则不拟合或预测全部因子都缺失的行，
因此有效拟合行数低于另外两种方法；Test 各预测 2,411,368 / 2,558,829 行，覆盖率
94.2372%。这属于各自因子库联合覆盖的差别，比较组合 IC 时须同时查看覆盖率。

本次 MCTS-LLM 的单因子平均主 IC 较高，库内平均相关性也较高；组合主 IC 未超过
AlphaAtlas 的对应截面。这里只作相同数据与平台配置下的截面对照，AlphaAtlas 在
第 444 次后调整过回访策略，不将差异解释为严格固定协议的算法消融结果。

[500 次运行报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5-prefix500/report.md)、
[500 次模型报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-aa870d70c5-prefix500/model/report.md)、
[评估日志](../../artifacts/monitors/mcts_llm_aa870d70c5_prefix500_finish_20261027.log)及
[核对与对照汇总](../../artifacts/comparisons/mcts_llm_kernel8_fold2_haiku55_aa870d70c5_prefix500/evaluation_summary.json)
保存结果；同目录的 `prepare.py`、`verify.py` 为重建和核对入口。
总览 Fold2 主表和库内相似度表已加入本次 500 次结果，并移除期货 MCTS-LLM 的
待开展条目；原 1000 次预算搜索保留，后续评估使用其独立冻结库。

## 2026-10-28 因子尝试记录精简：原子 trial 证据读取核对

使用历史冻结运行 `futures_kernel_5m_full-all-fold2-v1_async-42-ef96a68059` 作只读验证。
当前实现将旧 `steps` 加载为 `factor_trial_records`，保留计划及评估编号，丢弃其中的候选、
评估和准入副本；1000 条记录均符合该结构。权威读取与恢复规则见
[三 Agent 研究记录](../methods/annealing.md)和[V1 历史读取](../methods/v1.md#按计划读取历史)。

展开第 39 组只读取 trial 190、192、194、196、197。190、194、196 的实际公式、完整
Train/Val 指标、裁决和因子身份与原子 trial 一致；一次全图组装中每条 trial 只读取一次。
最终地图的初始正文仍不含第 39 组；使用保存的第 169 组提案作两次模拟回复，引用 196 后
先补齐整组证据，再确认交接。Analyst 的交接对象保留原引用及五条测量。

原检查点、运行配置、失败记录及 190、194、196 的原子文件字节保持不变；原方法状态输入
未被修改，没有调用真实模型、供应商或市场评估。这只验证数据来源与交接行为，不证明
真实模型的选题判断或市场表现改善。

同步、异步、旧格式与提交后中断恢复、错误旧副本、按需读取、缺失／损坏记录、预算与
权限边界等专项检查通过。完整执行 `pwsh -File scripts/check.ps1`，Ruff、格式及算子目录
检查通过，1011 项测试通过。检查限制进程内计算线程，检查范围不变。

[只读核对结果](../../artifacts/comparisons/factor_trial_records_20261028/verification.json)、
同目录的 `verify.py` 及[完整检查日志](../../artifacts/monitors/factor_trial_records_20261028_checks.log)
保留复核入口。旧运行产物不回写，新运行继续使用原准入、预算、冻结和 OOS 规则。

## 2026-10-28 Futures Kernel 5m fold2：完整计划证据版本 Haiku 5.5 新运行

按用户要求启动新运行 `futures_kernel_5m_full-all-fold2-v1_async-42-2069a54d6f`，
总预算 1000 次，计划在第 500 次原子因子评估完成、方法反馈保存后停止。
使用 `claude-haiku-5-5` 和原 Claude CLI 订阅桥接；Kernel 八字段、all、fold2、seed 42、
三组并发、每计划五个候选、参考库、Train 1%–99% 裁剪及准入配置与旧 Haiku 运行
`ef96a68059` 一致。启动前核对模型配置、下游模型配置和原数据快照一致；
首次检查点再次逐项核对完整配置、warmup 和 runtime，随后才发起 Explorer 请求。

新运行从头使用完整计划证据、按需读取原子评估事实及精简 `factor_trial_records`，
新记录目录为 `factor_trials/`。历史运行及其 `trials/` 保留；旧目录读取、报告与续跑
兼容。相关完整检查通过，1019 项测试通过；检查日志见
[目录命名检查](../../artifacts/monitors/factor_trial_directories_20261028_checks.log)。

停止后脚本会保留原 1000 次预算的可恢复检查点，另建 `-prefix500` 冻结评估截面，
执行单因子 Test、既定组合模型及 Val 库内相似度，与旧 Haiku 前 500 次截面对比。
OOS 不回传搜索，不据此选因子或修改后续策略。旧运行第 444 次后才启用回访且曾中断；
新运行采用更新后的完整计划证据，异步时序和模型随机输出也会影响轨迹，不能将差异
全部归因于单项机制。启动记录不代表已完成 500 次或已获得新市场结果。

[启动与停止脚本](../../artifacts/comparisons/v1_async_haiku55_fold2_memory_20261028/run_to_500.py)、
[运行日志](../../artifacts/monitors/v1_async_kernel8_fold2_haiku55_memory_1000_pause500_20261028.log)及
[阶段状态](../../artifacts/comparisons/v1_async_haiku55_fold2_memory_20261028/status.json)
保留过程；完成后同目录生成 `comparison.json` 和 `comparison.md`。

## 2026-10-28 Futures Kernel 5m fold2：研究记忆版本前 500 次完成与对照

新运行 `futures_kernel_5m_full-all-fold2-v1_async-42-2069a54d6f` 按要求停在 500 次：
总预算仍为 1000，检查点 completed/attempts 均为 500，pending 为空，没有第 501 条记录。
独立 `-prefix500` 截面完成单因子 Test、固定组合模型与 Val 库内相似度；
500 条复制记录与原件逐条字节一致。旧 Haiku `ef96a68059-prefix500` 与新截面的资产、
快照、字段、fold、seed、规则、参考库、方法配置、runtime、模型配置及依赖版本均一致。
原搜索保留为可恢复的 interrupted 状态，Test 没有回传方法。

| 指标 | 旧 Haiku 前 500 次 | 新 Haiku 500 次 |
| --- | --- | --- |
| 入库因子 | 17 | 26 |
| 成功测量 | 424 | 490 |
| 编译失败 | 74 | 9 |
| 低覆盖拒绝 | 12 | 87 |
| 行为重复拒绝 | 25 | 82 |
| 入库因子 Val 主 IC 均值 | 0.007831 | 0.008280 |
| 单因子 Test 加权 Pearson 均值 / 中位数 | 0.006987 / 0.006948 | 0.004584 / 0.004203 |
| 单因子 Test 正 IC | 16/17 | 22/26 |
| 单因子 Test IC > 0.005 | 12/17 | 11/26 |
| Linear Test 加权 Pearson | 0.023946 | 0.012257 |
| LightGBM Test 加权 Pearson | 0.024710 | -0.000512 |
| LightGBM Test 加权 Spearman | 0.014953 | 0.012296 |
| Val 平均绝对 Spearman | 0.060503 | 0.133843 |

本次入库更多、编译成功率更高，但冻结库的样本外 IC 更弱，因子间平均相关性更高。
LightGBM 的加权 Pearson 接近零，Spearman 仍为正；现有聚合报告不足以确定是幅度、
品种权重、异常预测或其他因素导致，不将其直接解释成已证明的过拟合机制。

只读核对 100 份已提交计划归档，其中 97 份带 sources；185 条按计划组织的证据正文中，
183 条属于已结束计划，其候选、方向、覆盖、指标和准入裁决均与对应原子记录一致，且
覆盖该计划的全部已提交试验；另外两条是活动计划的当时快照。统计按归档计划计数，
不是独立证据条数。新计划 4 引用新运行的 1、3、6、8、10，并收到原计划 1 的整组正文。
这验证证据交接路径接通，不证明模型所有结论正确或未知识别已解决。

仍有具体不足：新计划 19 的 90/92、计划 29 的 141/153 分别重复相同因子定义，后一次
命中计算缓存但仍占提交预算。全局同定义额外提交为旧 10 次、新 22 次；跨计划重复
可能是有意对照，不能据此直接判定语义选题重复。计划 28 明确设计状态外置 null 的
分组诊断，预期覆盖低于 0.8；134/137 的覆盖为 51.10%/43.55%，均低覆盖拒绝。
这些测量可以有研究价值，但与因子发现共用预算；计划中要求的分品种、独立样本日数等
诊断没有对应的结构化执行结果，不能仅凭 plan 文字或完成标记认定已测。

旧运行在第 444 次后才启用回访且曾中断，新运行从头采用完整计划证据；另有异步时序
和模型随机差异。这是单次系统对照，不是只改变研究记忆的受控消融，不能把差异全部
归因于本次改动。此轮仅审计已有结果，没有重算或修改历史试验，也没有调整搜索策略。

[对比数据](../../artifacts/comparisons/v1_async_haiku55_fold2_memory_20261028/comparison.json)、
[只读核对](../../artifacts/comparisons/v1_async_haiku55_fold2_memory_20261028/audit.json)及
[核对脚本](../../artifacts/comparisons/v1_async_haiku55_fold2_memory_20261028/audit.py)
保存具体证据；实验总览已由原完成流程填入新 500 次截面的结果。

## 2026-10-28 Futures Kernel 5m fold2：精简提示词与 2–4 方向 Haiku 5.5 运行

新运行 `futures_kernel_5m_full-all-fold2-v1_async-42-818ffbaf7a`，使用
`claude-haiku-5-5`、fold2、all、Kernel 八字段及 seed 42。研究配置、数据快照和组合模型
与原 Haiku 对照一致；总预算 1000，在第 500 次原子因子评估及反馈保存后自动停止。
通过 screen 会话 `atlas-haiku55-prompt2to4` 启动，历史运行保留。

此前启动 `dcbe4d7c5e` 暴露评分说明歧义：模型把四项理由放入嵌套的 `reasons` 对象，现有协议要求
理由直接位于每项 proposal 的 `review` 下。运行在 completed 为 0 时人工停止，
没有提交因子评估，日志和检查点保留。提示词改用现有格式的紧凑 JSON 示例；
回归测试直接解析提示词示例并验证可经正常 Explorer 提交流程接受。

第二次启动 `d33089476a` 通过评分格式，但连续将 context 的字段路径写入 references，
同样在 completed 为 0 时停止并保留产物。提示词补清 references 复制已有 trial 或
查询结果的 `ref`，没有证据引用时填写 `[]`。这两次均未形成频率使用的测量样本。

本次从头使用精简提示词、基础及高周期字段语义，以及 Explorer 每批 2–4 个方向。
计划并发仍为 3，每计划 5 个候选。修正格式后完整检查通过，1024 项测试通过；
见[检查日志](../../artifacts/monitors/prompts_evidence_refs_20261028_checks.log)。

本轮关注提交公式是否仍全部使用基础 5m 字段。完成后的 `comparison.json` 记录仅基础
周期公式数、含高周期公式数、各 `@15m/@30m/@60m/@1d` 使用次数及最早示例。
长窗口的基础 5m 公式仍计入基础周期；同一公式含多个高周期时各周期分别计数。
统计包括失败提交，具体示例另保留评估状态，不把写入高周期语法视为成功测量。
首批 5 次提交中，仅基础 5m 的有 2 次，含 `@1d` 的有 3 次，均成功计算，
其中日线原始收益控制项（第 4 次）入库。第 1 次公式为
`NEG(CS_DEMEAN(RETURN($adj_close@1d,1)))`；第 2 次结合 5m 偏离与日线波动率状态。
因此本轮已不是 100% 基础频率；这不代表完整 500 次的频率比例。
[首批频率核对](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt2to4_refs_20261028/frequency_initial.json)
保存公式和裁决。停止后沿用独立冻结截面的既定评估流程。

[运行及统计脚本](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt2to4_refs_20261028/run_to_500.py)、
[实时日志](../../artifacts/monitors/haiku55_fold2_prompt2to4_refs_20261028.log)和
[阶段状态](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt2to4_refs_20261028/status.json)
分别保存执行入口、进度及新运行位置。

## 2026-10-28 Haiku 精简提示词版本完成：与最初 500 次对照诊断

`818ffbaf7a` 于 20:22 完成 500 次截面及后处理。原预算仍为 1000，搜索检查点
completed/attempts 均为 500、pending 为空，无第 501 条记录；原搜索保留为 interrupted。
独立 `-prefix500` 截面 frozen，50 个因子完成 Test；500 条复制记录与原件逐条字节一致。
本次比较对象为最初 `ef96a68059` 的前 500 次，而非中间的 `2069a54d6f`。

| 指标 | 最初 500 次 | 本次 500 次 |
| --- | --- | --- |
| 成功测量 / 入库 | 424 / 17 | 500 / 50 |
| 含高周期提交 | 41 | 393 |
| 入库因子 Val 加权 Pearson 均值 | 0.007831 | 0.009244 |
| 单因子 Test 加权 Pearson 均值 | 0.006987 | 0.003569 |
| 单因子 Test IC > 0.005 | 12/17 | 15/50 |
| Linear Test 加权 Pearson | 0.023946 | 0.011984 |
| LightGBM Test 加权 Pearson | 0.024710 | -0.003906 |
| Val 库内平均绝对 Spearman | 0.060503 | 0.156937 |

本次仅基础 5m 提交 107 次；高周期分项为日线 367、60m 23、15m 6、30m 5，
分项存在重叠。入库按实际 AST 字段组合分为：仅 5m 2、仅日线 14、5m+日线 28、
5m+60m 4、仅 60m 1、15m+日线 1。

研究集中于早期结果：100 个计划中 46 个直接引用 `trial:22`；185 次提交包含该试验的
同一 24 根 5m 均线偏离子树，其中 25 次入库。按顶层条件在 `x` 和 `NEG(x)` 间切换
这一明确 AST 结构归组，50 个入库项中有 29 个，平均 Val IC 0.008890，Test IC
0.001946，仅 4 个 Test IC > 0.005；该组内 Val 平均绝对相关为 0.342980。
这验证局部结构扩展和泛化衰减，不把所有引用或对照检验视为重复研究。

计划与执行仍有缺口。计划 24（包含试验 116）要求 G0、primary、H、L、HR、LR 及
分品种、固定品种半组等验证；实际只有 116–120 五条提交，其中三条分支项低覆盖拒绝，
另两条主因子入库。保存反馈含聚合 IC 和 Train ICIR/换手，没有计划要求的品种半组结果。
计划中的验证条款没有全部变为测量事实或准入条件。

使用原冻结表达式及保存模型重建 Test 预测，没有重新拟合，也没有改历史产物。
两版四个组合主 IC 均在 1e-12 内复现。旧版 Linear 在 2025 年的主 IC 为 0.019672，
新版为 -0.000364；分年诊断固定使用完整 Test 的品种权重。
新版 LightGBM 为 39/85 个品种 Pearson 正值，旧版为 76/85。
按各品种绝对标准化预测的 99% 分位及更极端部分归因，含并列值，新版这部分对总加权
Pearson 的协方差贡献为 -0.010509，其余部分为 +0.006603，合计 -0.003906。
这是固定完整样本均值、标准差和权重的贡献分解，不是删尾后重新计算的 IC。
对应旧版两部分贡献为 +0.014635 和 +0.010075。

条件翻转组只占新版 LightGBM 训练分裂增益的 10.96%，不能据其数量断言它单独导致
组合负 IC。现有证据定位到研究集中、泛化衰减及极端预测负贡献；数值配置与模型参数
一致，但方法历史、提示词和方向数量同时变化，尚无单项消融来归因具体改动。
本轮只诊断既定结果，没有按 Test 选择子库或调整参数。

[对照诊断数据](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt2to4_refs_20261028/diagnosis_vs_original.json)、
[只读诊断脚本](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt2to4_refs_20261028/diagnose_vs_original.py)、
[组合预测分解](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt2to4_refs_20261028/prediction_audit.json)和
[预测复现脚本](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt2to4_refs_20261028/prediction_audit.py)
保存逐项依据。原始总体对比仍见同目录 `comparison.json`。

### 提示词与退火边界复核

两版前 500 次的 100 个计划均处于 breadth，温度尚未低于几何中点，未进入 depth。
最初版完成计划时记录 Metropolis 接受 47、拒绝 13、初始化或同区域继续 40；
新版对应为 48、17、35。退火正常执行，在计划结束后更新当前 Region；
同一区域继续不作概率拒绝，不回滚此前已准入的因子，也不直接判断机制或表达式差异。
Region 奖励仍是累计入库次数除以尝试次数。不能把研究集中解释成退火主动进入 depth。

简化前备份明确要求不能只改名字、窗口、符号或频率，并要求证据缺口与判别检验；
新版压缩为问题与预测须不同，删去了这一具体判别标准。新版另增
“Consider whether the signal persists across feature timeframes or changes with a slower trend
or volatility state.”，从频率事实说明进一步变成了慢趋势／波动率状态的选题引导。
新版无历史证据的首批方向已包含日线波动率调制 5m 反转，与该引导一致。
这提供了提示词改变研究偏好的线索，不是对 Test 退步的单项因果证明。

运行保存的 context 确认两版都允许 15m、30m、60m、1d；新版只在原周期语义前补充
基础 5m 及可用周期列表，未新增频率权限。按保存提案去除仅 selected 不同的副本，
旧版前 500 次为 95 批（90 批单方向、5 批双方向），新版为 73 批（70 批双方向、
3 批三方向）。2–4 规则改变了提案批次分布，现有结果不足以判断它对质量的净影响。

对照提示原文见[提示词核对](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt2to4_refs_20261028/prompt_audit.json)。
该文件区分“简化前备份”和“最初 500 次”：旧运行在 444 次后才允许回访，备份不是
前 444 次提示词的逐字归档，不将两者冒充为固定提示词的纯 A/B 实验。

## 2026-10-28 Futures Kernel fold2：移除选题偏向后 Haiku 重跑

北京时间 22:43 启动 `futures_kernel_5m_full-all-fold2-v1_async-42-db466d4b19`。
使用 `claude-haiku-5-5`、原 CLI 桥、Kernel 八字段、all、fold2、seed 42、三组并发、
每计划五个候选；数据快照、研究数值配置和组合模型参数与 `ef96a68059`、`818ffbaf7a`
核对一致。总预算仍为 1000，在第 500 次评估及反馈保存后自动停止。

本次移除 Explorer 的慢趋势／波动率状态选题引导，明确回访的证据缺口与判别检验，
补回仅改变名字、窗口、符号或周期不足以构成新方向的标准；研究记忆、基础及可用频率
说明、每批 2–4 个方向保留。异步提示删除“一条即可”的旧句，修正四空位边界，
默认并发仍为三组。源码修改后的完整检查为 1024 项通过，见
[完整检查](../../artifacts/monitors/prompts_neutral_research_20261028_final_checks.log)。

screen 会话为 `atlas-haiku55-neutral`。实验进程保存实际组装的 Explorer 系统提示及
收敛提醒，避免把日后源码版本误作本轮输入。第 500 次停止后沿用独立冻结截面的
既定评估流程；`comparison.json` 的 previous 为上一轮 `818ffbaf7a`，original 为
最初 `ef96a68059` 的 500 次截面。启动记录尚不代表最终结果。

[阶段状态](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt_neutral_20261028/status.json)、
[提示词原文](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt_neutral_20261028/prompt_snapshot.json)、
[运行脚本](../../artifacts/comparisons/v1_async_haiku55_fold2_prompt_neutral_20261028/run_to_500.py)和
[实时日志](../../artifacts/monitors/haiku55_fold2_prompt_neutral_20261028.log)保留本轮来源及进度。

## 2026-10-29 Futures Kernel fold2：Boltzmann 选题 500 次轨迹审计

运行 `futures_kernel_5m_full-all-fold2-v1_async-42-a6d7473de9` 使用 Haiku 5.5、
`selection_policy = "boltzmann"`、原 1000 次总预算，第 500 次反馈保存后停止。
准入 27 个因子；原搜索保持 interrupted，保留检查点，独立 `-prefix500` 截面完成评估。
00:35 在 175 次评估后因 Analyst 连续四次 HTTP 502 停止；10:01 从原检查点恢复，
11:45 到达 500 次。汇总表曾因真实目录与独立配置根目录的相对路径失败，已修正实验脚本
传给 finish 的路径，并复用全部已保存结果完成报告。模型、OOS、相似度文件和原搜索
检查点均未在补齐报告时重算或修改。

按 seed 42、日志中的启动进度和已保存提案分数重放全部 75 批选择，选中索引逐批一致，
最终 RNG 与检查点一致。25 批全部提案都被选中；其余 50 批中有 20 批选中集合不同于
稳定最高分排序，16 批没有选中最高分项。75 批中 74 批只有两个提案，首批有三个。
首批温度 0.25，最后已保存批次约 0.07170；只走 1000 次日程的前半程。
停止时第 101 个计划已选中、尚未提交候选，因此选择审计包含该在途计划。
Region 当前状态为 null，没有旧 Metropolis 记录，符合新策略的执行路径。

| 因子评估编号 | 含高周期的提交数 | 新入库 |
| --- | --- | --- |
| 1–100 | 20 | 3 |
| 101–200 | 38 | 6 |
| 201–300 | 35 | 2 |
| 301–400 | 60 | 6 |
| 401–500 | 96 | 10 |

周期从编译表达式的字段节点统计，避免把注释中的周期当作实际依赖。最后 100 次的
96 条高周期公式均使用日线；全程 249 条含高周期，241 条只有基础周期，另有 10 次
未生成可编译公式。轨迹由量价、波动、持仓等问题逐步集中到 VWAP 偏离、日线收益符号
及日线价位／形态的条件拆分。选择器只能在当批提案中选择，不能产生提案之外的机制。

执行交接存在可复核的偏离：计划 43、46、49、55 要求保持 VWAP 因子并分解预测期限，
但 20 次提交均沿用平台固定 12-bar 目标，Miner 改为因子变体，没有得到所要求的期限
分解。计划 52、57 已明确远月字段不可用、应 BLOCKED，仍进入 Miner，各产生五次
空公式生成失败。证据缺口被记为未回答，却没有把能力阻塞落实到调度；反复回访因此
继续产生评估尝试。不能把这些 trial 的完成视为原研究问题已经得到验证。

冻结结果：单因子 Test weighted Pearson 均值 0.00660406，22/27 为正；线性组合
Test Pearson 0.01338964，LightGBM 0.00039410，Val 平均绝对 Spearman 0.13311493。
这是一次异步系统运行，与旧 Region 流程相比同时改变了中性输入和计划结束后的转移路径，
选择器效果仍需显式 greedy/random 使用相同输入与执行路径的对照。

[完整轨迹审计](../../artifacts/comparisons/v1_async_haiku55_fold2_boltzmann_20261028/trajectory_audit_first500_20261029.json)、
[500 次对照](../../artifacts/comparisons/v1_async_haiku55_fold2_boltzmann_20261028/comparison.json)及
[完成状态](../../artifacts/comparisons/v1_async_haiku55_fold2_boltzmann_20261028/status.json)保留依据。

## 2026-10-29 Futures Kernel fold2：五组 500 次冻结库统一模型预处理

对本线程五组 Haiku 5.5 的 500 次冻结库分别重训 Linear 和 LightGBM。原搜索、检查点、
冻结定义、旧模型和 OOS 保留；新增模型评估截面放在比较目录的 `runs/` 内，不作为新的
搜索运行列入搜索汇总。原因子 ID、列顺序、方向、训练行数和 Test 预测覆盖逐组一致，
NumPy、Polars、LightGBM 版本及模型超参也与各自旧模型一致。

参考本地 ChaoticTrader_Future 的模式 2，采用全局预处理与品种全拟合期去均值：特征与
训练 Label 各自按 Train+Val（2019–2023）全局 P1–P99 裁剪；特征再拟合全局均值和
标准差。Linear 对特征与标签按品种全期去均值；LightGBM 使用全局预处理特征和品种
全期去均值标签。Test（2024–2025）复用冻结特征边界，Label 保持原值。
标签分位边界独立于因子缺失，五组均为 `[-0.019324272826402925, 0.019212713223309814]`。
两模型保持样本等权，未引入对方可选的 Ridge、品种年波动率权重或观测标记列。
具体协议见[模型评估](../model.md)，本次保存配置以比较目录的 manifest 为准。

全部模型在首次新 Test 前拟合完成，不按新 Test 结果选因子、翻转方向或调参。
主指标均为品种 √成交额加权 Pearson IC：

| 500 次冻结库（原 run 后缀） | 因子数 | Linear 原口径 | Linear 新口径 | LightGBM 原口径 | LightGBM 新口径 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 最初 `ef96a68059` | 17 | 0.023946489 | 0.016312738 | 0.024710396 | 0.021086895 |
| 研究记忆版 `2069a54d6f` | 26 | 0.012257035 | 0.011220989 | -0.000511892 | 0.020724769 |
| 频率提示版 `818ffbaf7a` | 50 | 0.011983523 | 0.011482840 | -0.003906409 | 0.000810876 |
| 中性提示版 `db466d4b19` | 29 | -0.000612031 | 0.003155068 | -0.020096515 | -0.017407512 |
| 本次 Boltzmann `a6d7473de9` | 27 | 0.013389643 | 0.010065375 | 0.000394102 | 0.003169810 |

原始 500 次在新口径下也下降，不能只更新新版而沿用原版旧指标比较。统一后，Boltzmann
的 Linear 仍比最初低 38.30%，LightGBM 低 84.97%；预处理调整没有消除本次运行的差距。
研究记忆版的 LightGBM 则从略负变为 0.02072477，与最初新口径接近，说明不同因子库
对预处理的敏感度不同。此次同时调整特征裁剪、训练标签裁剪与品种去均值，不能拆分
各项贡献，也不能将这些异步历史运行之间的差异单独归因为提示词或选择器。

独立 Test 并行调度曾与原汇总进程触发目录写锁冲突；三个独立 Test 进程均成功，随后
汇总复用全部已保存 fit/Test，未再次拟合。保留中断状态和执行日志。模型产物移至比较目录
后，再次验证两阶段缓存命中及模型文件字节不变。

[完整对照与模型报告](../../artifacts/comparisons/model_global_product_clip1to99_20261029/comparison.md)、
[配置与输入映射](../../artifacts/comparisons/model_global_product_clip1to99_20261029/manifest.json)、
[一致性检查](../../artifacts/comparisons/model_global_product_clip1to99_20261029/checks.json)及
[完成状态](../../artifacts/comparisons/model_global_product_clip1to99_20261029/status.json)保存本次依据。

同次开发补齐 Analyst／Miner 的结构化阻塞交接：保存阻塞计划与原因，释放未用额度，
让 Explorer 换题；部分真实 trial 保留，零提交阻塞不生成空公式 trial。语义与恢复约定见
[计划阻塞与交接](../methods/annealing.md#计划阻塞与交接)。该修复不改历史搜索轨迹，本次
下游模型对照也没有重新调用 Haiku 搜索。最终
`pwsh -File scripts/check.ps1` 通过 1067 项测试、Ruff、格式和算子目录检查，日志为
`artifacts/monitors/model_and_blocked_20261029_final_checks.log`。

## 2026-10-29 最初与 Boltzmann 的 500 次研究轨迹对照

按原子 trial 关联 `ef96a68059`、`a6d7473de9` 的各 100 个实际提交计划，核对原提案、
Analyst 计划、实际表达式与冻结库。原版虽已有 1000 条记录，本审计只读取前 500 条的归属。
最初快照记载 444 次后启用回访；本次从计划 6（约第 20 次）已开始追补未报告的诊断。

计划文本出现 `no admission` 的数量由 1 增至 82；这 82 个计划仍实际提交 410 次，
准入 24 个成员。这不是 Agent 有权决定是否入库，而是 Analyst 的诊断作业与 Miner 的
候选生成接口错配。已有工具不支持它要求的其他前瞻目标、目标横截面去均值和任意行组统计，
未完成测量又被 Explorer 作为新缺口持续回访。不能把上述 410 次全部计作无效尝试。

直接断点包括计划 72 固定 F 的正负状态诊断被 trial:357 替换为 ABS(F)，以及计划 83
固定 F、比较 T0/T1 被 trial:413 替换为日线收盘位置十日极值时点的共同成分；实际没有
生成 T1。这两个变体占新口径冻结 LightGBM 的训练分裂增益分别为 13.28%、20.11%。
另有计划 67 将“计划 62 写了 T1”作为该能力已经可用的理由，混淆计划意图与实际能力。

原版与本次编译失败为 74 / 10，重复拒绝为 28 / 107；入库纯日线成员为 2/17 / 20/27，
Val 平均绝对相关为 0.06050 / 0.13311。本次五条共同状态及其变体占冻结 LightGBM
71.80% 的训练分裂增益。该值不是预测权重或 IC 的因果贡献，集中本身也不足以证明
剔除它们会改善泛化。

为检验这一点，另用固定模型参数在 Train 拟合、Val 诊断，不加载 Test、不改历史模型：

| 因子库 | Linear Val IC | LightGBM Val IC |
| --- | ---: | ---: |
| 原版完整 17 个 | 0.022662 | 0.024383 |
| 本次完整 27 个 | 0.019076 | 0.019976 |
| 本次移除五个共同状态，22 个 | 0.013874 | 0.011400 |

移除共同状态反而降低 Val，故不据 Test 差异给出删因子建议。这里的 Val 已参与上游
因子筛选，是开发诊断；也不是上一节 Train+Val 拟合模型的独立 Val 指标。
能够直接确认的是任务契约与能力闭环缺失，以及同族研究集中；跨期稳定性和组合增量
仍需进一步验证，不能把两次系统运行的全部性能差距单独归给提示词或退火。
既有 75 批退火选择重放正确的结论不变。

既有新口径冻结模型中，Linear 的 Test weighted Spearman 为原版 0.013335、本次
0.013860，排序信息并未随 Pearson 一起下降；LightGBM 则从 0.016988 降为 0.008082。
这说明两模型的退化不同，不能只按重复率解释全部差距，预测幅度与跨期稳定性还需核对。
本次重训未重跑搜索，输入仍为结构化 blocked 修复前的历史因子库。

[完整分析与计划案例](../../artifacts/comparisons/trajectory_original_vs_boltzmann_20261029/report.md)、
[审计摘要](../../artifacts/comparisons/trajectory_original_vs_boltzmann_20261029/summary.json)、
[Train/Val 诊断原始结果](../../artifacts/comparisons/trajectory_original_vs_boltzmann_20261029/train_val_diagnostic.json)
保留本次依据。仅新增只读分析及诊断产物，未修改搜索实现、准入门槛或历史运行。

## 2026-10-29 Haiku fold2：能力边界提示与五计划并发，1000 预算截取 500

新运行 `futures_kernel_5m_full-all-fold2-v1_async-42-ee12e6fcdd`，使用 Haiku 5.5、
Boltzmann、seed 42、原八字段和相同 fold2 快照、评估及准入配置。按用户要求将并发计划
从 3 提高到 5，每计划仍最多五个候选，每批提案仍为二至四个；不增加初筛或评分门槛。
Explorer 提案须考虑现有字段、算子和反馈能力，并说明现有测量如何检验预测；本次也首次
在真实搜索中使用已有的结构化阻塞交接修复。两项方法修改及并发变化同时生效，不能将
后续差异单独归因为提示词。总预算为 1000，降温日程按该预算计算，在第 500 次反馈保存后
停止；保留父运行检查点，再冻结独立 `-prefix500` 完成统一口径 Linear、LightGBM 和 OOS。
模型配置采用上一节约定的全局 P1–P99 预处理与品种全拟合期去均值。当前只记录启动事实，
尚无结果结论；不覆盖旧运行或旧比较。配置、提示词及启动信息保存在
[实验目录](../../artifacts/comparisons/v1_async_haiku55_fold2_executable_20261029/manifest.json)。

15:11:34 的前 79 次反馈快照中，已保存 12 批、26 条提案，21 个进入研究的计划，入库 2 个；
6 个计划记录 blocked，其中也包括候选耗尽。Explorer 仍提出当前能力外的要求：计划 4
需要 session-open 识别，Analyst 在零评估时阻塞；carry 备选明确引用未开放的
`adj_close_p1` 并让 Analyst 确认能否开放，本快照未选中；计划 8 需要象限未来收益均值和
远月 OI 检验，实际四条指示因子 IC 后阻塞。计划 14 的 readiness 为 5，仍要求标准反馈
之外的状态占比等统计。Analyst 计划 5 又额外要求每时点品种数分布与品种贡献集中度；
计划 21 固定 close-minus-VWAP/range，trial:78 在行为重复后改为 VWAP-minus-midpoint/range。
因此提示词约束与显式阻塞已有部分作用，尚未解决全部能力判断和计划交接偏离。

分组研究不能一概视为越界：条件外置 null 的 DSL 已实际返回有限子样本 IC，如
trial:38、40，覆盖不足影响准入而不抹除测量；条件外置 0 的 IC 则属于零填充因子，
不能当作条件内 IC。此时 19 条 low_coverage 拒绝不全是无效研究。未停止或修改运行，
不据局部轨迹判断最终性能；证据见
[前 79 次能力审阅](../../artifacts/comparisons/v1_async_haiku55_fold2_executable_20261029/trajectory_capability_review_first79_151134.md)。

同日只读核对 ReAct、AlphaPROBE、MCTS-LLM、annealing、V1、V1 Async 的期货运行配置：
主指标均为加权 Pearson，Train/Val 同方向各严格大于 0.005，Val 覆盖率至少 0.8，
Val 全量 pooled Spearman 去重阈值 0.7、至少 100 个共同样本。早期未配置评分裁剪，
近期使用 P1–P99，因此基础门槛一致不表示所有历史数值口径完全一致；旧 V2 还曾叠加
研究判据，见其历史记录。

另仅用已提交 Train/Val 指标作质量条件诊断：Pearson 与 Spearman 分别只从各自 Train
确定方向，各自验证 Val_raw 定向后 >0.005，并要求原覆盖率 >=0.8。最初 500 次、
上次 Boltzmann 500 次、本次前 110 次中，满足 Spearman 条件却不满足 Pearson 条件的
分别为 142、116、38 次，对应 135、106、35 个不同公式 ID。该诊断没有重放新入库顺序、
行为去重或后续搜索，不代表可新增这么多独立成员，也不据此调整门槛；两个指标使用同一
数值阈值仅用于描述差异，不表示显著性或错误发现率相同。本次 trial:92 的 Spearman
Train/Val_raw 为 -0.012042/-0.010417，但覆盖率仅 0.360629，即使改用该指标仍不足准入。
完整依据为
[双指标质量条件核对](../../artifacts/comparisons/v1_async_haiku55_fold2_executable_20261029/pearson_spearman_quality_audit_first110.json)。

## 2026-10-29 Haiku 五计划并发：前六个成员的独立阶段评估

按用户最新要求固定 `ee12e6fcdd` 第 132 次反馈后的六个入库成员，来源因子评估编号为
9、39、93、122、123、129。五因子评估未启动；六因子快照在首次 Test 前固定，
位于比较目录下独立 `artifacts/runs/`，不覆盖活跃搜索。复用标准 `finish_experiment`：
Train+Val（2019–2023）拟合固定 Linear、LightGBM，之后完整单因子 Test、组合 Test
（2024–2025）、Val 库内相似度及独立总表。预处理沿用已统一的全局 P1–P99 与品种
全拟合期去均值，两模型训练 5,308,000 行，输入六列全部保留。

| 模型 | Test 加权 Pearson | Test 加权 Spearman |
| --- | ---: | ---: |
| Linear | 0.011131752 | 0.018917254 |
| LightGBM | 0.011707064 | 0.017764354 |

两模型预测 2,558,794 / 2,558,829 个可评估行，覆盖率 0.99998632。六个单因子 Test
Pearson 均为正，按上述编号分别为 0.011574961、0.011617611、0.004343681、
0.005146808、0.004855149、0.006191540，均值 0.007288291。Val 15 对全部有效，
平均绝对 Spearman 0.21028361，最大 0.68462508，无超过 0.7 的配对。
单因子评分与组合模型的 Test 标签裁剪口径不同，不将它们的 IC 差值直接解释为组合增量。
六个因子 ID 与列顺序、冻结库、两模型及 OOS 清单一致，复制的 132 条源记录字节不变。
原搜索继续沿 1000 总预算、500 次停止运行，本次结果不回传 Agent 或用于改题、调参。

[阶段评估摘要](../../artifacts/comparisons/v1_async_haiku55_fold2_executable_20261029/evaluation_first6_prefix132_20261029/summary.md)、
[一致性核对](../../artifacts/comparisons/v1_async_haiku55_fold2_executable_20261029/evaluation_first6_prefix132_20261029/checks.json)
及同目录标准运行报告保留依据。

## 2026-10-29 最初 Haiku 前六个成员：与当前六个同口径评估

按用户要求，固定最初 `ef96a68059` 实际入库顺序的前六个成员，来源评估编号为
7、31、79、80、81、149；新建独立 `-first6-prefix149` 快照。沿用上节六因子评估的
相同数据快照、字段、fold、评分规则、算子运行环境、模型预处理与固定超参，复用
`finish_experiment` 完成两模型拟合、单因子和模型 Test、15 对 Val 相似度及独立填表。
两组均在 Train+Val 2019–2023 拟合、Test 2024–2025 汇报。

| 模型 | 最初六个 Test 加权 Pearson | 当前六个 Test 加权 Pearson | 最初六个 Test 加权 Spearman | 当前六个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.011402162 | 0.011131752 | 0.013095041 | 0.018917254 |
| LightGBM | 0.015481434 | 0.011707064 | 0.015845010 | 0.017764354 |

当前相对最初六个的 Pearson 为 Linear 低 2.37%、LightGBM 低 24.38%，但两模型
Spearman 均更高，不能概括为当前信号全面退化。最初六个单因子 Test Pearson 均值
0.007160948，5/6 为正；当前为 0.007288291、6/6 为正。最初六个 Val 平均／最大
绝对 Spearman 为 0.08655246／0.48675505，当前为 0.21028361／0.68462508；两组均
无 >=0.7 的配对。最初训练 5,308,085 行，Test 预测 2,558,822 / 2,558,829 行；当前
相应为 5,308,000 和 2,558,794 / 2,558,829，特征缺失使覆盖略有不同，未作共同预测行重评。

这是同因子数、不同搜索截点（149／132 次）的系统比较，不能唯一归因于提示词、并发或
选择策略。最初五个成员只用基础 5m、一个混合日线；当前五个成员含日线输入。原始
八个运行／模型产物 SHA256 与评估前一致，149 条原子记录字节一致；六列身份与顺序、
冻结库、OOS、模型及相似度清单交叉核对通过，未更改历史运行或将结果回传活跃搜索。

[六因子对照与逐项结果](../../artifacts/comparisons/original_haiku55_fold2_first6_20261029/comparison.md)、
[一致性核对](../../artifacts/comparisons/original_haiku55_fold2_first6_20261029/checks.json)
保留完整依据。

## 2026-10-29 最初与当前 Haiku：各自前九个成员

按用户要求，当前 `ee12e6fcdd` 固定前 230 次后的九成员库，入库编号
9、39、93、122、123、129、195、220、225；最初 `ef96a68059` 固定前 173 次后的九成员库，
入库编号 7、31、79、80、81、149、158、171、173。两份名单在本次 Test 前同时冻结，
两组沿用六因子比较的相同数据、字段、fold、平台规则、算子环境、模型预处理和固定参数。
独立快照复用标准 `finish_experiment`，Train+Val 2019–2023 拟合后评估 Test 2024–2025。

| 模型 | 最初九个 Test 加权 Pearson | 当前九个 Test 加权 Pearson | 最初九个 Test 加权 Spearman | 当前九个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.012161941 | 0.011470127 | 0.014512578 | 0.018891894 |
| LightGBM | 0.017015177 | 0.010093465 | 0.018009641 | 0.017623197 |

当前相对最初九个，Linear Pearson 低 5.69%、LightGBM 低 40.68%；当前 Linear 的
Spearman 仍较高，LightGBM 两个加权指标均低于最初。六个增至九个后，最初的 LightGBM
Pearson 从 0.015481434 增至 0.017015177（+9.91%），当前从 0.011707064 降至
0.010093465（-13.78%）；各组原六列、模型参数、训练行数与预测行数保持一致。
当前三个新增成员单因子 Test Pearson 均为正（0.010872915、0.005221805、0.009407400），
不能把组合下降简单解释为新增成员的单因子失效，也不能据该结果唯一分摊成员贡献。

九个单因子 Test Pearson 均值：最初 0.007232881（8/9 为正），当前 0.007692430
（9/9 为正）。Val 36 对全部有效，平均／最大绝对 Spearman 最初为
0.08615876／0.68469129，当前为 0.22437471／0.69656866；两组均无 >=0.7 的配对。
当前九个有八个含日线输入，新增三个仍为日线锚点或日收益的条件／对比变体；最初九个
只有一个混合日线。来源集中与组合表现差异同时存在，不能据此单独识别提示词、并发或
选择器的因果影响。两组特征缺失略有不同，未另做共同预测行重评。

每组九个因子身份、顺序、OOS、两模型和相似度清单一致，原六成员完整保留；旧运行八个
历史产物及两组复制的已提交记录保持不变。当前搜索继续，本次结果不回传 Agent，
不据 Test 删因子或调参。配置、阶段状态、完整逐因子结果和核对见
[九因子对照](../../artifacts/comparisons/haiku55_fold2_first9_20261029/comparison.md)及
[原始结果与核对](../../artifacts/comparisons/haiku55_fold2_first9_20261029/comparison.json)。

对当前前 230 次进一步按实际编译字段统计：含日线输入的候选 85 次，入库 8 次；
不含日线输入 145 次，入库 1 次，对应本轮已采样候选准入率 9.41%／0.69%。这不代表
日线频率具有普遍优势，也不能把候选提交比例与入库占比混为一谈。最初 50 次只提交
3 条含日线候选，第 151–200 次则有 33 条，显示偏好是在反馈过程中逐步形成的。
非日线候选中另有 51 次同时满足覆盖率与按自身 Train 方向的 Spearman 双切分 >0.005，
却因 Pearson 质量门槛拒绝；含日线候选对应为 3 次。该计数包含重复尝试，未重放新准入。

可追溯的分支是 trial:9 日线锚点 → trial:122 大小状态、trial:195 波动状态；
trial:93 日收益 → trial:123／129 下跌分支、trial:220 日收盘位置对比、trial:225 波动状态。
提案理由明确以已入库因子为研究价值来源，并将新的状态条件视为新的可观测预测。
提示词允许证据驱动回访并要求检查不同状态，选择器没有日线权重；准入也只检查单因子
质量与两两相关，没有组合增量检查。因此已有事实支持“早期反馈、条件回访和当前准入
共同放大同族研究”的流程解释，不能单独归因于退火或某一句提示词。源自字段可用性的
可执行性要求可能进一步鼓励已知路线，但本轮没有隔离该作用的消融。
逐次统计及原提案理由见
[日线偏好轨迹核对](../../artifacts/comparisons/v1_async_haiku55_fold2_executable_20261029/daily_preference_audit_first230_20261029.json)。

## 2026-10-29 当前 Haiku 在十五成员时按用户要求停止

用户将当前 `ee12e6fcdd` 的原定 500 次停止改为立即停止。专属 screen 接收 Ctrl+C 后
退出，平台保存为 `interrupted`：337 条已完成反馈、15 个正式成员；待提交的第 338 次
候选仍在原检查点中，没有对应原子 trial，未清空或改写恢复信息。1000 总预算配置保留，
本次停止不恢复剩余额度，也没有启动后续搜索。实验监督状态改记 `stopped_by_user`，
区别于正常 500 次截点和真正的请求失败。

按实际入库顺序固定当前前 15 个（截点 337）和最初前 15 个（截点 411），在本次 Test
前同时冻结，复用九因子比较的配置与标准收尾流程。拟议的“初期覆盖优先”提示词调整
仅提供审阅表，尚未实施；本次十五因子结果来自原搜索提示词。
待审阅措辞见
[最小改动对照](../../artifacts/comparisons/haiku55_fold2_first15_20261029/prompt_review.md)。

两组标准收尾均成功，每组 15 个单因子、两模型及 105 对 Val 相似度完整。沿用全局
P1–P99 与品种全拟合期去均值，Train+Val 2019–2023 拟合、Test 2024–2025 汇报：

| 模型 | 最初十五个 Test 加权 Pearson | 当前十五个 Test 加权 Pearson | 最初十五个 Test 加权 Spearman | 当前十五个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.013901127 | 0.010125900 | 0.013410815 | 0.018159883 |
| LightGBM | 0.019533067 | 0.010905040 | 0.016767129 | 0.014520797 |

当前相对最初的 Pearson 为 Linear 低 27.16%、LightGBM 低 44.17%。九个增至十五个时，
最初 Linear／LightGBM Pearson 分别提升 14.30%／14.80%；当前分别下降 11.72%／
提升 8.04%。当前 LightGBM 从九个回升，仍低于六个时 0.011707；两模型排序 IC 均
低于当前九个时的值。当前 Val 平均绝对相关从九个时 0.22437471 降至 0.15038406，
最初十五个为 0.06805632，故不能仅用两两相关解释组合变化。

单因子 Test Pearson 均值最初 0.006968087、当前 0.007276587，均为 14/15 正值。
日线输入成员最初 3/15、当前 12/15。最初训练 5,308,089 行、Test 预测
2,558,824 / 2,558,829 行；当前分别为 5,308,000 和 2,558,794 / 2,558,829，未做共同
预测行重评。最初从九个新增 4 条训练行、2 条预测行，当前行数不变。两组首九个
成员的 ID 和顺序完整保留，旧源文件、已提交记录与停止检查点均未修改；第 338 次
仍未提交。配置、逐项结果和核对见
[十五因子对照](../../artifacts/comparisons/haiku55_fold2_first15_20261029/comparison.md)及
[原始结果与核对](../../artifacts/comparisons/haiku55_fold2_first15_20261029/comparison.json)。

## 2026-10-29 Haiku fold2：覆盖优先提示词，五计划并发新运行

按用户审阅的最小方案实施两处调整：Explorer 在研究覆盖稀疏时优先未测机制与信息来源，
覆盖扩展后回访有前景的方向；`difference` 评分改为参数或条件变体 1、已知机制内新预测 3、
未测机制或信息来源 5。异步包装去除重复的旧回访鼓励语，统一采用主提示词；既有回访
测试同步更新措辞。未增加阶段字段、初筛、预算输入或频率配额，四项权重和 Boltzmann
选择保持不变。完整检查通过 Ruff、格式、算子目录与 1067 项测试。

新运行 `futures_kernel_5m_full-all-fold2-v1_async-42-5c5dd7640f` 使用 Haiku 5.5、fold2、
5 个并发计划、每计划最多 5 个候选、每批 2–4 个提案、seed 42。相对停止的
`ee12e6fcdd`，数据快照、八字段、平台评估与准入、全部方法参数及下游模型配置一致；
1000 总预算，降温按该预算计算，在第 500 次反馈保存后停止，独立冻结 prefix500 完成
标准评估，父检查点不接收 OOS 反馈。旧运行仍停在 337 次，未恢复或覆盖。

启动时核对实际加载的主提示、异步系统提示和首轮 `review_rubric` 均已更新；当前只记录
启动事实，不预判覆盖或模型表现。配置、提示词、运行信息和检查日志见
[新实验目录](../../artifacts/comparisons/v1_async_haiku55_fold2_coverage_first_20261029/manifest.json)。

## 2026-10-29 覆盖优先版与初版：各前七个因子

按用户要求，固定最新 `5c5dd7640f` 前 120 次中的七个入库成员，与初版 `ef96a68059`
前 158 次的七个比较；两份名单在本次 Test 前同时冻结。新版入库编号为
5、15、68、70、102、105、107，初版为 7、31、79、80、81、149、158。
这次比较没有停止当前搜索，原 1000 总预算、500 次停止继续有效。独立截面复用标准
`finish_experiment`，模型预处理、固定参数与此前六／九／十五因子比较一致：全局
P1–P99、品种全拟合期去均值，Train+Val 2019–2023 拟合，Test 2024–2025 评估。

| 模型 | 初版七个 Test 加权 Pearson | 新版七个 Test 加权 Pearson | 初版七个 Test 加权 Spearman | 新版七个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.011614262 | 0.011734659 | 0.013356542 | 0.021673469 |
| LightGBM | 0.014652241 | 0.011729639 | 0.015684120 | 0.018918741 |

新版 Pearson 相对初版：Linear +1.04%、LightGBM -19.95%；两模型 Spearman 均更高。
新版第 107 次达到七个成员，初版为第 158 次；这是同成员数量阶段比较，不是同预算或
提示词单变量消融。初版与新版还存在选择器、并发及历史搜索流程差异。

新版单因子 Test Pearson 均值 0.003707753，7/7 为正；初版 0.007416042，6/7 为正。
新版含日线输入 5/7、纯日线 4/7，初版为 1/7、0/7。新版 #68 是日线收盘位置，
#102／#105 是其大振幅／小振幅分支，在共同有效数据上 #68 = #102 + #105；两两相关
检查不能排除这种多列线性依赖。新版 #15、#70、#102 的 Test Pearson 分别仅为
0.000147997、0.000960089、0.000771174。成员增加、正值占比与独立预测信息不能等同，
这些事实也不能单独识别各成员对模型差异的贡献。

Val 21 对相似度全部有效；初版平均／最大绝对 Spearman 为 0.11369270／0.55806378，
新版为 0.25011913／0.68213140，均无 >=0.7 的配对。初版训练 5,308,085 行、Test
预测 2,558,822 / 2,558,829 行；新版为 5,308,061 和 2,558,814 / 2,558,829，未另做
共同预测行重评。每组七个单因子、两个模型及相似度因子身份和顺序一致，复制的已提交
原子记录及已记录的初版历史产物保持不变；结果不回传搜索。完整结果见
[七因子对照](../../artifacts/comparisons/haiku55_fold2_coverage_first7_20261029/comparison.md)及
[数值与核对](../../artifacts/comparisons/haiku55_fold2_coverage_first7_20261029/comparison.json)。

## 2026-10-29 覆盖优先版与初版：各前十个因子

按入库顺序固定新版 `5c5dd7640f` 前 176 次中的十个成员，与初版 `ef96a68059` 前
177 次的十个比较。新版新增编号 125、130、169，初版新增 171、173、177；原七个成员
身份和顺序完整保留。两组名单在本次 Test 前同时冻结，复用七因子比较的数据、规则、
模型预处理和参数：Train+Val 2019–2023 拟合，Test 2024–2025 汇报。

| 模型 | 初版十个 Test 加权 Pearson | 新版十个 Test 加权 Pearson | 初版十个 Test 加权 Spearman | 新版十个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.011854859 | 0.010442021 | 0.014172319 | 0.019671285 |
| LightGBM | 0.017505869 | 0.014356807 | 0.017861804 | 0.020979130 |

新版相对初版 Pearson：Linear -11.92%、LightGBM -17.99%，两个 Spearman 均更高。
七个增至十个时，新版 Linear Pearson 下降 11.02%，LightGBM 提升 22.40%；初版分别
提升 2.07%／19.48%。两组模型训练与预测行数分别保持七个时的数值，首七列完整保留。
这说明新版新增三列后树模型组合改善，尚未达到初版十列的 Pearson，不能将变化唯一
归因于某个新增成员。

新版新增 #125 五分钟影线不平衡、#130 日收益横截面共同成分、#169 高成交量收盘位置的
单因子 Test Pearson 分别为 0.009493167、-0.001777465、0.007769729，均值
0.005161810；初版新增三个均值为 0.006554561。新版十个单因子均值 0.004143970，
初版 0.007157598，均为 9/10 正值。新版日线输入占比从七个时的 5/7 降到 6/10，
其中五个纯日线；初版为 1/10、零个纯日线。新版新增 #169 延续已有收盘位置条件分支，
#68／#102／#105 的多列线性依赖仍在，不能把十个成员等同于十个独立机制。

Val 45 对全部有效且均无 >=0.7 的配对。新版平均／最大绝对 Spearman 为
0.19212956／0.68213140，初版为 0.09954729／0.68469129；新版平均相关较七个时
下降，仍高于初版。新版训练 5,308,061 行，Test 预测 2,558,814 / 2,558,829；初版为
5,308,085 和 2,558,822 / 2,558,829，未额外做共同预测行重评。新版第 169 次达到十个，
初版为第 177 次；搜索次数与历史机制不同，本次不是同预算或提示词单变量消融。

每组十个单因子、两模型与 45 对相似度均成功，身份、顺序、配置和冻结内容核对一致。
源已提交记录及已记录的初版历史产物保持不变；当前搜索继续，结果不回传搜索方法。
完整检查通过 Ruff、格式、算子目录与 1067 项测试。完整结果见
[十因子对照](../../artifacts/comparisons/haiku55_fold2_coverage_first10_20261029/comparison.md)
及[数值与核对](../../artifacts/comparisons/haiku55_fold2_coverage_first10_20261029/comparison.json)。

## 2026-10-29 覆盖优先版与初版：各前十三个因子

按用户要求同时冻结新版 `5c5dd7640f` 前 249 次中的十三个入库成员，以及初版
`ef96a68059` 前 331 次中的十三个。首十个成员身份、顺序完整保留；新版新增编号
212、236、241，初版新增 191、288、331。沿用十因子比较的数据快照、fold2、八字段、
平台规则、模型预处理与固定参数，标准收尾在 Train+Val 2019–2023 拟合，独立评估
Test 2024–2025。当前搜索没有停止，评估结果不回传方法。

| 模型 | 初版十三个 Test 加权 Pearson | 新版十三个 Test 加权 Pearson | 初版十三个 Test 加权 Spearman | 新版十三个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.015117889 | 0.009355694 | 0.015088105 | 0.016327626 |
| LightGBM | 0.020867557 | 0.015205292 | 0.019141316 | 0.021718594 |

新版相对初版 Pearson：Linear -38.12%、LightGBM -27.13%，两个 Spearman 仍较高。
从十个增至十三个，新版 Linear Pearson -10.40%、LightGBM +5.91%，初版分别
+27.52%／+19.20%。本阶段两种模型的 Pearson 差距扩大；这不是提示词单变量消融，
不将变化唯一归因于任何一个新增成员。

新版新增 #212 横截面对数价格、#236 稳健收益峰度、#241 日线 VWAP 区间位置的单因子
Test Pearson 分别为 -0.000042514、0.003099796、-0.000025930，均值 0.001010451；
初版新增三个均值 0.006482283，其中 #288 前一日参考位穿越信号为 0.012416207。
新版全十三个单因子均值 0.003420850，10/13 为正；初版为 0.007001756，12/13 为正。
新版新增三列中两个样本外 IC 接近零，即使探索到不同形式，也没有相应的单因子强度。
单因子和组合模型的裁剪拟合区间不同，不据其 IC 相减分摊组合增量。

新版含日线输入 7/13、纯日线 6/13，初版为 2/13、0/13。Val 78 对全部有效且均无
>=0.7 的配对：新版平均／最大绝对 Spearman 为 0.13214906／0.68213140，初版为
0.08003779／0.68469129。新版平均相关从十个时 0.19212956 继续下降，Linear 仍下滑，
不能只用两两相关解释组合变化；已有日线收盘位置及大小振幅分支的多列依赖仍保留。

新版训练 5,308,104 行、Test 预测 2,558,829 / 2,558,829 行，较十个时增加 43／15 行；
初版为 5,308,089 和 2,558,824 / 2,558,829，分别增加 4／2 行。未额外进行共同预测行
重评。新版第 241 次达到十三个，初版为第 331 次；本次是同成员数阶段比较。
每组十三个单因子、两模型和 78 对相似度全部成功，因子身份、顺序、冻结配置、原子
记录与已记录历史文件核对通过。完整检查通过 Ruff、格式、算子目录及 1067 项测试。
完整结果见[十三因子对照](../../artifacts/comparisons/haiku55_fold2_coverage_first13_20261029/comparison.md)
及[数值与核对](../../artifacts/comparisons/haiku55_fold2_coverage_first13_20261029/comparison.json)。

## 2026-10-29 覆盖优先版与初版：各前十六个因子

按用户要求同时冻结新版 `5c5dd7640f` 前 323 次中的十六个入库成员，以及初版
`ef96a68059` 前 413 次中的十六个。原十三成员身份和顺序保留；新版新增编号
263、264、314，初版新增 374、411、413。沿用此前数据快照、fold2、八字段、平台规则、
全局 P1–P99 与品种全拟合期去均值、模型参数及标准收尾：Train+Val 2019–2023 拟合，
Test 2024–2025 汇报。当前搜索继续，评估结果不回传方法。

| 模型 | 初版十六个 Test 加权 Pearson | 新版十六个 Test 加权 Pearson | 初版十六个 Test 加权 Spearman | 新版十六个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.013678505 | 0.012641529 | 0.014189143 | 0.017060801 |
| LightGBM | 0.017804197 | 0.015254803 | 0.016906723 | 0.019544989 |

新版相对初版 Pearson：Linear -7.58%、LightGBM -14.32%，两个 Spearman 仍较高。
十三个增至十六个时，新版 Linear Pearson +35.12%、LightGBM +0.33%；初版分别
-9.52%／-14.68%。差距缩小同时来自新版 Linear 回升和初版两模型下降，不能把全部
收窄解释为新版组合改进。两组训练行数和预测行数均与十三个时相同；首十三列模型
预处理均值、尺度和裁剪边界在 1e-12 容差内一致，标签裁剪边界相同。

新版新增 #263 市场高波动状态下的日线收盘位置、#264 相对前一日区间突破、#314
十二根五分钟跳空价差之和，单因子 Test Pearson 分别为 0.006696226、0.009044906、
0.000318042，均值 0.005353058；初版新增三个均值为 0.005482621。新版十六个单因子
均值 0.003783139，13/16 为正，初版为 0.006716918，15/16 为正。单因子沿用 Train
冻结裁剪边界，模型独立使用 Train+Val 预处理，不按二者 IC 相减分摊组合贡献。

新版含日线输入 9/16、纯日线 7/16，初版为 4/16、2/16。Val 120 对全部有效，均无
>=0.7 的配对；新版平均／最大绝对 Spearman 为 0.13518045／0.68213140，初版为
0.06367867／0.68469129。新版平均相关较十三个时 0.13214906 略升，Linear 仍回升，
说明不能仅凭两两相关推断组合变化；既有日线收盘位置和大小振幅分支的多列依赖保留。

新版训练 5,308,104 行、Test 预测 2,558,829 / 2,558,829，初版为 5,308,089 和
2,558,824 / 2,558,829，未额外进行共同预测行重评。新版第 314 次达到十六个，初版为
第 413 次；本次是同成员数量阶段比较，不是同预算或提示词单变量消融。
每组十六个单因子、两个模型和 120 对相似度均成功，身份、顺序、冻结配置、源原子
记录和已记录历史文件核对通过。完整检查通过 Ruff、格式、算子目录及 1067 项测试。
完整结果见[十六因子对照](../../artifacts/comparisons/haiku55_fold2_coverage_first16_20261029/comparison.md)
及[数值与核对](../../artifacts/comparisons/haiku55_fold2_coverage_first16_20261029/comparison.json)。

## 2026-10-29 覆盖优先版 Haiku：429 中断及原检查点续跑尝试

`5c5dd7640f` 在 17:50 因模型 HTTP 429、四次请求仍失败退出。已提交 357 次因子
评估、17 个入库成员；第 358 次未形成原子记录，两个 Miner 计划及 Explorer 状态保存在
检查点。日志显示 358 是正在准备的下一次，不能记为已完成 358 次。

18:02 核对桥接 `/health` 正常、实验配置预检通过后，使用原 `--resume` 在 screen
中续跑，保持 Haiku 5.5、五并发、1000 总预算及 500 次停止。18:03 再次收到 HTTP 429，
四次请求后退出，没有新增因子评估；仍为 357 次、17 个成员。健康检查仅确认桥接存活，
不能确认上游额度可用；桥接将 CLI 的 `rate_limit`／`rate_limit_error` 映射为 429，
没有提供本次限流的重置时间。本次未修改模型、并发、提示词或重试策略，也未启动无限
重试。待上游限制解除后仍可沿原检查点续跑。

原失败日志保留，续跑日志独立保存于
[429 续跑日志](../../artifacts/monitors/haiku55_fold2_coverage_first_20261029_resume_429.log)。

18:13 再次尝试原检查点续跑，仍在 357 次处因 429 退出。用户通知可以继续后，18:26
沿原检查点重启；已确认模型请求成功返回并提交第 358 次评估，入库仍为 17，运行状态
恢复为 `running`。模型、五并发、1000 总预算及 500 次停止保持不变；新日志见
[恢复推进日志](../../artifacts/monitors/haiku55_fold2_coverage_first_20261029_resume_429_3.log)。

## 2026-10-29 覆盖优先版 Haiku：Explorer 上下文超限修复

`5c5dd7640f` 推进到 416 次、20 个入库成员后，Explorer 因 `ContextLimitError` 退出。
失败请求尚无本轮回复，其已保存输入为 2,352,845 字符：地图支持／反向样例所属的
91 个计划被整组展开，计划正文及候选约 164 万字符，84 条阻塞计划正文另约 50 万。
不是模型限流或候选评估失败，重复发送同一输入不能解决。

V1 及异步 V1 改为保留完整历史索引和地图统计，自动展开最近完成计划及地图样例所属的
进行中计划；串行为最近一组，异步为最近 `parallel_plans` 组。较早试验仍能通过已有
trial 引用补全整组 `sources` 并确认后交接 Analyst。阻塞索引只展示区域、方向及原因。
恢复过大的旧输入也执行同样投影，但保留已校验提案引用的计划和已保存对话证据，
不重发已确认回复，不裁剪原始 trial／plan，不新增存储、Agent 或检查点格式。

本次失败输入投影后为 348,645 字符，自动展开正文从 91 组降到 5 组，历史方向索引、
所有 Region 统计和 84 条阻塞原因保留。同步、异步及恢复定向测试 184 项通过；新增覆盖
旧大型输入恢复、历史证据按需补全，以及已确认旧提案不重发且原证据完整交接。
Haiku 190／194／196 的主项、持仓项、波动率对照关系相关回归仍通过。全量 Ruff、格式、
算子目录及 1071 项测试通过。检查及体积核对见
[完整检查](../../artifacts/comparisons/v1_async_haiku55_fold2_coverage_first_20261029/context_fix_checks.log)
和[输入核对](../../artifacts/comparisons/v1_async_haiku55_fold2_coverage_first_20261029/context_size_audit_20261029.json)。

从原 416 次检查点在 screen 续跑后，已确认 Explorer 请求成功返回并进入 Analyst 阶段，
不再在这条输入处报上下文超限。Haiku 5.5、五并发、1000 总预算与 500 次停止不变。
本次恢复起新请求的证据展示结构已变化，后续实验比较须披露该时点；不将恢复后的研究
轨迹当作完全未修改的对照。原运行产物及失败日志保留，续跑见
[上下文修复续跑日志](../../artifacts/monitors/haiku55_fold2_coverage_first_20261029_resume_context_fix.log)。

## 2026-10-29 覆盖优先版 Haiku：500 次评估完成及汇总路径修复

`5c5dd7640f` 已在第 500 次反馈保存后停止，父检查点保留 1000 总预算、500 次已提交
反馈及空 pending。独立 `5c5dd7640f-prefix500` 冻结 27 个成员，单因子 Test、Linear／
LightGBM 模型拟合与 Test、351 对 Val 相似度均已完成。首次收尾仅在写实验总览时失败：
工作区的 `artifacts` 是指向项目全局产物目录的符号链接，而 `directory.relative_to(root)`
要求运行真实目录在工作区词法路径之下，因而抛出 `ValueError`。

汇总链接改为解析工作区和运行目录真实路径后计算允许向上回溯的相对路径。真实目录和
符号链接入口生成相同链接，不重复插入两组表格行。新增合成回归覆盖外部运行目录、
链接实际可达，以及换用符号链接入口重试时复用全部模型和评估、总览内容保持不变。
16 项实验收尾定向测试通过，全量 Ruff、格式、算子目录及 1072 项测试通过。
实际补收尾禁用特征重载及模型重训，仍成功完成；父运行
配置和检查点、冻结清单、模型 fit／Test、单因子与相似度文件的 SHA256 均保持原值。
实验监督状态已从 `postprocess_failed` 更新为 `complete`，没有继续搜索到 1000 次。

与初版前 500 次冻结的 17 个因子比较，模型使用相同的全局 P1–P99 与品种全拟合期
去均值，Train+Val 2019–2023 拟合、Test 2024–2025；复用初版已保存的同口径重训结果，
不混用原始预处理的 Linear 0.023946。

| 模型 | 初版 500 Test 加权 Pearson | 新版 500 Test 加权 Pearson | 初版 500 Test 加权 Spearman | 新版 500 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.016312738 | 0.007576028 | 0.013334925 | 0.013259840 |
| LightGBM | 0.021086895 | -0.009275330 | 0.016988047 | 0.007167565 |

新版 LGBM 的负 Pearson 在路径修复前已产生，本次只补填表，没有改变该结果。新版
单因子 Test Pearson 均值 0.004126672，22/27 为正；初版 0.006987219，16/17 为正。
含日线输入成员为新版 19/27、初版 4/17。Val 平均绝对 Spearman 为新版 0.09119050、
初版 0.06050283。新版训练 5,308,104 行，初版 5,308,089；双方数据快照、字段、fold、
平台规则及模型参数核对相同，但搜索流程、并发和提示词有历史差异，新版在第 416 次
后精简过 Explorer 输入，不能单独识别各改动的因果贡献。
完整结果及恢复文件核对见
[500 次对照](../../artifacts/comparisons/v1_async_haiku55_fold2_coverage_first_20261029/comparison_500.md)、
[恢复核对](../../artifacts/comparisons/v1_async_haiku55_fold2_coverage_first_20261029/overview_path_fix_recovery.json)。

## 2026-10-29 GPT6-luna Fold2：两方法启动配置

按用户要求准备 AlphaPROBE 与 AlphaAtlas V1 Async 的独立 GPT6-luna 配置及启动命令。
复用本机已有 `gpt-6-luna` Chat 路由；两个本机模型列表接口分别确认目标 Chat 模型和
`Qwen3-Embedding-0.6B` 可用，仅作只读检查，没有发起模型推理或启动搜索。
两组使用原生期货 5m 全八字段、fold2、seed 42、1000 总预算、500 次停止，之后自动
冻结 prefix500 并完成标准评估。AlphaAtlas 保持五计划并发与 Boltzmann；AlphaPROBE
保留原图搜索参数、冷启动和 Qwen embedding，Chat 模型及路由改为 GPT6-luna。

配置放在各自 workspace，不覆盖 Haiku 或其他历史配置；两方法独立保存 run 和因子库。
启动脚本分别创建 screen 会话及独立日志，加载项目 `.env`，线程数及本机代理例外
沿用此前实验。两方法配置预检、Python Ruff／格式及 shell 语法检查通过；尚无本组研究
结果，不将服务列表可读视为真实推理成功。命令、参数与配置位置见
[准备清单](../../artifacts/comparisons/gpt6_luna_fold2_20261029/manifest.json)。

## 2026-10-30 GPT6-luna Fold2：500 次评估后继续到 1000

用户授权两方法在各自完成 500 次评估后，沿原检查点继续到 1000 次。监控启动时，
AlphaPROBE 运行 `3656be6b77` 已提交约 467 次、入库 6 个；AlphaAtlas V1 Async
运行 `382bb38611` 约 354 次、入库 14 个，原搜索进程均正常。

每方法各启动一个独立 screen 衔接进程，等待原 500 阶段状态为 `complete`，再用标准
收尾入口核对已保存的全部评估结果。要求父运行仍为 1000 总预算，500 次检查点无
pending，prefix500 属于该父运行且已完成评估，然后复用 `runner.resume` 继续原运行。
不重启种子、不创建第二轮独立搜索，不更改模型、并发、提示词或准入。500 次结果不传回
研究 Agent；到 1000 次再对父运行执行标准冻结评估，核对 prefix500 结果文件保持原值。
若 500 阶段失败，衔接停止，不跳过测试继续搜索；后续异常保留原检查点并记录日志。

四项合成测试覆盖等待 500 测试完成、沿原预算恢复、500 结果不变及失败时不越过阶段；
Ruff、格式检查通过。运行状态沿用每方法原 `status.json`；衔接日志为各方法目录中的
`continue_1000.log`，screen 分别为 `atlas-luna-alphaprobe-to1000-20261030` 和
`atlas-luna-v1_async-to1000-20261030`。授权、脚本和阶段说明已写入
[两方法实验清单](../../artifacts/comparisons/gpt6_luna_fold2_20261029/manifest.json)。

## 2026-10-30 GPT6-luna 本地桥接 Fast 参数探测

按用户要求，对正在使用的 ClovAPI `/codex/v1/chat/completions` 路由发起四个极小
请求，指定 `gpt-6-luna`，输入相同，分别不传 `service_tier`、传 `fast`、`priority`
及无效值 `invalid_tier_for_test`。四个请求均 HTTP 200 并返回 OK；耗时依次为
2.024、1.732、1.625、1.414 秒。所有响应均未返回实际 `service_tier`。

无效值也被接受，说明请求成功不足以证明桥接启用了 Fast；当前无法验证是否向上游
传递或实际采用该参数。桥接日志没有档位记录，探测的 OpenAPI 说明地址均为 404。
单次极短请求不能作为速度基准。未改实验配置、桥接配置或重启进程，不影响因子预算。
不记录凭据，探测结果见
[Fast 参数探测](../../artifacts/comparisons/gpt6_luna_fast_probe_20261030/results.json)。

后续追踪已确认当前路径没有转发该参数：运行二进制为 ClovAPI 0.2.20，commit
`a0f41900de8164c58ddff68fac6b298c9bc81741`。对应 Chat 解码器只提取已知字段，没有保留
`service_tier`。运行中服务的纯转换诊断与真实发送共用 `PrepareUpstreamRequest`，
chat 入站的未指定、fast、priority、无效值四种请求得到完全相同的上游预览，均无该
字段。预览仅将 input/messages 数组替换为长度，其他顶层字段保留。
44 条真实测试日志显示客户端确有参数，上游请求长度均为 177 字节，目标为 ChatGPT
Codex 订阅接口；日志本身不含上游请求正文，长度仅为旁证。日志只读，不导出凭据。

同版本 Responses 入站的转换对照能保留 `service_tier=fast`；这不代表已验证 Codex
上游接受或实际采用该档位。此前各 10 次测试没有通过此参数切换上游档位，不能作为
Fast 与 Standard 的比较；上游默认档位仍未确定。此次没有更改实验／桥接配置、重启
服务或额外调用模型。代码位置、运行时证据与脱敏日志见
[转发确认](../../artifacts/comparisons/gpt6_luna_fast_probe_20261030/service_tier_trace.md)。

随后按用户要求扩展为每种 10 次，共 40 次：10 个随机顺序分组，每组含未指定、fast、
priority 和无效值各一次，串行发送，不自动重试，输入和输出额度相同。40 次均返回
HTTP 200 和 OK；所有响应仍未返回实际档位，无效值对照也全部成功。

| 参数 | 均值（秒） | 中位数（秒） | P90（秒） | 最慢（秒） |
| --- | ---: | ---: | ---: | ---: |
| 未指定 | 1.881 | 1.678 | 2.510 | 3.271 |
| fast | 3.384 | 1.849 | 4.120 | 16.780 |
| priority | 1.966 | 1.642 | 2.815 | 4.554 |
| 无效值对照 | 1.500 | 1.467 | 1.769 | 1.797 |

同组配对，fast 仅 2/10 次快于未指定，priority 为 5/10；本批短请求未显示稳定提速。
不能确认 Fast 已启用，也不能据此确定参数是否向上游传递。未指定参数不等同于明确
选择上游标准档位；本次未测实际长上下文研究延迟。原实验及桥接配置未改，原四次
探测产物保留。逐次结果见[40 次探测](../../artifacts/comparisons/gpt6_luna_fast_probe_20261030/ten_each.md)。

### Responses 入口的上游验证

同日改用 `/codex/v1/responses`，对显式 `default`、`fast`、`priority` 和无效值各测
10 次，共 40 次。相同短输入，串行随机交错，不重试；40 条测试均匹配到只读日志中
`https://chatgpt.com/backend-api/codex/responses` 的上游原始响应。

| 参数 | 成功生成 | 上游状态 | 成功请求均值／中位数（秒） |
| --- | ---: | --- | ---: |
| default | 10/10 | 200 | 1.779 / 1.649 |
| priority | 10/10 | 200 | 1.624 / 1.648 |
| fast | 0/10 | 400 | — |
| 无效值对照 | 0/10 | 400 | — |

此路径保留并转发档位字段。`fast` 全部被上游明确拒绝，原始错误为
`Unsupported service_tier: fast`；无效值也全部被拒绝。`priority` 被上游接受，但
20 次成功请求的原始 `response.created` 和 `response.completed` 均不包含实际档位。
priority 均值低 8.71%，中位数仅低 0.09%，配对比较仅 5/10 次更快，尚不能确认实际
启用了优先调度或产生稳定加速。此前 Chat 入口的 40 次测试未切换档位，与此次区分。
未改桥接／实验配置，未重启服务，不记录认证头。见
[Responses 对比](../../artifacts/comparisons/gpt6_luna_fast_probe_20261030/responses_ten_each.md)
及其逐次脱敏上游证据。

### ClovAPI Codex 默认 priority 与 Chat 入口透传

按用户授权修改并部署本机 ClovAPI，基于原提交
`a0f41900de8164c58ddff68fac6b298c9bc81741` 构建 `0.2.20+priority.1`。
Chat 解码保留客户端 `service_tier`；Codex 订阅路由默认补入 `priority`，显式客户端值
优先。复用已有 request-field extensions，不改 AlphaAtlas 搜索方法或客户端；其他
路由不补该默认值。原二进制保留，profiles.json 未改，仍监听 0.0.0.0:27483。

ClovAPI 全部 Go 测试 233 个测试／子测试通过、1 个跳过，12 个包通过；隔离服务另
验证 28 个转换组合。部署后从 Chat 入口发送 10 次相同短请求，均未传档位，10/10
返回 HTTP 200 和 OK，平均 1.568672 秒、中位数 1.456054 秒。运行时转换确认默认
priority 与客户端覆盖，10 条上游日志均匹配；上游请求长度均符合补入 priority 的
正文。上游原始响应仍不报告实际档位，尚不能确认优先调度或稳定提速。

原服务曾被 ClovAPI 自带固定端口 CLI 测试的清理逻辑停止，现已恢复为修复版并完成
上述验证。受影响的原 GPT6-luna AlphaAtlas 运行在 914 次处因连接失败退出；保留
检查点和失败记录后，已用原研究状态恢复 914→1000，不重置 Explorer，并保留原先
692 次恢复时的提示词附录。500 次快照与原收尾流程保留。
AlphaAtlas 完整检查也已通过，含 Ruff、格式、算子目录与 1075 个测试。
部署情况、脱敏证据、源码补丁及检查结果见
[ClovAPI priority 修改记录](../../artifacts/comparisons/clovapi_codex_priority_20261030/report.md)。

## 2026-10-30 GPT6-luna AlphaPROBE：1000 次、8 个因子最终评估与总表

`futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77` 已完成 1000 次评估，冻结
8 个成员。用户要求先评估并写表；重新执行标准收尾以验证已保存结果，禁止重新拟合与
加载行情，确认全部命中缓存后，通过既有填表函数发布到仓库根目录
`experiment_overview.md` 的 fold2 结果表及相似度表。工作区总表原有记录保留。
运行、检查点、冻结库、模型及 OOS／相似度文件逐字节核对未变。

研究模型 GPT6-luna，fold2、seed42、Kernel 八字段；模型全局 P1–P99、品种全拟合期
去均值，Train+Val 2019–2023 拟合，Test 2024–2025。两种模型训练行均为 5,049,365，
Test 预测 2,409,077 / 2,558,829 行，覆盖率 94.1476%。

| 模型 | Test 加权 Pearson | Test 加权 Spearman |
| --- | ---: | ---: |
| Linear | 0.010070367 | 0.016241590 |
| LightGBM | 0.019461898 | 0.014319253 |

单因子 Test 加权 Pearson 8/8 为正，均值 0.006212430，中位数 0.006200178；
Spearman 均值 0.003655。入库序号为 5、77、150、192、200、202、578、932。
最后两个分别为收盘位置的六根 5m Bar 变化及影线不平衡的六根均值，单因子 Test
Pearson 为 0.008533751、0.002002538。所有 8 个都只引用基础 5m 字段。
Val 28 对相似度全部有效，平均／最大绝对 Spearman 0.221230253／0.695995528，
无 >=0.7 配对。模型有效列、OOS 因子身份和相似度顺序核对一致。

结果见[完整报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77/report.md)、
[模型报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77/model/report.md)
及[实验总表](../../experiment_overview.md)。

## 2026-10-30 GPT6-luna AlphaPROBE：前 500 次评估结果

`futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77-prefix500` 已冻结并完成标准
收尾。前 500 次共入库 6 个成员，入库率 1.2%；六个都使用原生 5m 字段，无 `@1d`
输入。机制集中于收盘位置与活跃度、滚动区间位置、距近期高点的偏离、标准化影线
不平衡、近期涨跌方向下的拒绝位置、VWAP 价差变化。

模型沿用全局 P1–P99 与品种全拟合期去均值，Train+Val 2019–2023 拟合，Test
2024–2025；固定参数、全六列，不根据 Test 选因子。训练 5,049,365 行，两个模型
Test 均预测 2,409,077 / 2,558,829 行，覆盖率 94.1476%。

| 模型 | Test 加权 Pearson | Test 加权 Spearman |
| --- | ---: | ---: |
| Linear | 0.011088147 | 0.017522906 |
| LightGBM | 0.019798325 | 0.015134083 |

单因子 Test 加权 Pearson 六个全部为正，均值 0.006527192，范围 0.002030429 至
0.011608604；其中两个的加权 Spearman 为负，不能把正 Pearson 等同于排序能力一致。
Val 15 对相似度全部有效，平均／最大绝对 Spearman 为 0.23527404／0.59061028，无
>=0.7 的配对。六个原始单因子 Test 覆盖率约 86.82%–94.13%。

500 阶段完成后，后台衔接已从原父检查点启动剩余研究，核对时已推进到至少 508 次。
500 快照单独保留，结果不回传研究 Agent，后续按原 1000 总预算继续。结果见
[500 次模型报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77-prefix500/model/report.md)
及[500 次完整报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-3656be6b77-prefix500/report.md)。

## 2026-10-30 GPT6-luna AlphaAtlas：500 次、22 个因子结果入总表

`382bb38611-prefix500` 的单因子、Linear、LightGBM 和全库 Val 相似度评估已完成，
按用户要求补入仓库根目录 `experiment_overview.md` 的 fold2 结果表及相似度表。
研究模型 GPT6-luna，seed42、Kernel 八字段；总预算 1000，在前 500 次时冻结完整
22 个入库成员。此快照形成于 692 次人工换题干预之前，不包含后续搜索或 Test 反馈。

| 模型 | Test 加权 Pearson | Test 加权 Spearman |
| --- | ---: | ---: |
| Linear | 0.012066520 | 0.015226873 |
| LightGBM | 0.013775656 | 0.014027585 |

模型沿用全局 P1–P99 与品种全拟合期去均值，Train+Val 2019–2023 拟合，Test
2024–2025，预测覆盖率约 100%。单因子 Test 加权 Pearson 均值 0.005265904，
中位数 0.005778772，19/22 为正；231 对 Val 相似度全部有效，平均／最大绝对
Spearman 0.107881900／0.692477033。模型、OOS 和相似度身份顺序核对一致。
重新执行标准收尾验证缓存，禁止重新拟合和加载行情，评估产物逐字节未变；总表沿用
共同数据与 Fold／seed 设置，未加回已移除的重复列或阶段记录。

结果见[完整运行报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-382bb38611-prefix500/report.md)
及[模型报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-382bb38611-prefix500/model/report.md)。

## 2026-10-30 GPT6-luna AlphaAtlas：692 次不足终止后的人工授权恢复

`382bb38611` 自动完成 500 次快照评估并继续至 692 次，入库 25 个。Explorer 302
在无活动计划时连续两次报告不足：对 #570 小时成交额／成交量与 #677 小时减日线收益
的解释，需要当前平台没有的条件／增量诊断。按现有规则抛出 `ExplorerUnresolved`；
原停止符合方法定义，直接恢复会重放已保存的终止判断，不能视为传输失败重试。

用户明确授权“保留不足报告，换题后续跑”。一次性脚本
`gpt6_luna_fold2_20261029/resume_after_unresolved_692.py` 使用原方法类型、配置及 runner
恢复接口，仅在内存加载时将该次 Explorer 状态置空，随后按原流程创建 Explorer 303。
692 条既有提交、25 个成员、RNG、退火进度、计划历史、阻塞记录及 1000 总预算保留；
没有修改评估、准入或增加诊断工具。旧研究历史中已有的 `deferred_explorer` 保持原值。

失败时完整 `checkpoint.json`、`run.json` 和 `failures.json` 在续跑前备份到
`gpt6_luna_fold2_20261029/v1_async/recovery_692/`，包含两次不足的完整对话和 sources；
同目录 `recovery.json` 记录授权、时间及本次运行追加的提示：

> Leave the unresolved attribution questions about trial:570 and trial:677 open, and propose a different question executable with the existing fields, DSL and standalone Train/Val IC feedback.

这是有记录的人工换题干预；不能将 692 次之后描述为未干预的原始策略运行，也不宣称
原归因问题已解决。源代码及冻结配置未修改，追加提示只在该续跑进程生效，不含 Test。
恢复仍保留无活动计划时再次不足的停止规则，不自动循环重启。达到 1000 后执行标准
模型、OOS 与相似度收尾，并核对 500 快照结果未变。screen 为
`atlas-luna-v1_async-recover692-20261030`，日志继续追加原 `v1_async/continue_1000.log`。

一次性恢复与原自动交接的 10 项模拟测试通过，覆盖不足状态识别、活动／待反馈任务
拒绝、完整失败备份、历史记录保留及异常时恢复进程内钩子；不调用真实模型服务。
恢复后已核对推进到 698/1000，入库仍为 25 个。`scripts/check.ps1` 在宿主环境完整
通过：1072 tests passed，102.94 秒；先前沙箱执行在既有 AgentScope 模拟测试停滞，
已终止该测试进程，单独验证该例通过后重跑全检。该环境排查未改源代码或影响研究进程。

## 2026-10-30 GPT6-luna Fold2：AlphaAtlas 21 个与 AlphaPROBE 7 个

按用户选择，比较当前各自完整入库集合：AlphaAtlas `382bb38611` 前 433 次的 21 个，
AlphaPROBE `3656be6b77` 前 585 次的 7 个。两边均用 GPT6-luna、1000 总预算，
数据快照、fold2、八字段、平台规则、算子环境与模型配置核对一致。名单先固定，再通过
标准实验收尾独立训练、测试和计算 Val 相似度；原搜索及历史产物保留，Test 不回传方法。
模型 Train+Val 2019–2023 拟合，全局 P1–P99 与品种全拟合期去均值，Test 2024–2025。

| 模型 | AlphaAtlas Test 加权 Pearson | AlphaPROBE Test 加权 Pearson | AlphaAtlas 加权 Spearman | AlphaPROBE 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.012647461 | 0.010958833 | 0.016009835 | 0.017360543 |
| LightGBM | 0.013290529 | 0.018815273 | 0.014367431 | 0.015208169 |

AlphaAtlas 的 Linear Pearson 高 15.41%，LightGBM Pearson 低 29.36%，两种模型的
Spearman 均低于 AlphaPROBE。单因子 Test 加权 Pearson 均值分别为 0.005548697、
0.006813843，正值数分别为 19/21、7/7；Val 平均绝对 Spearman 为 0.103924473、
0.251274089，最大为 0.692477033、0.695995528，各 210／21 对全部有效。

AlphaAtlas 相对 18 个时新增 #407 前日下跌条件的小时影线、#421 日线收盘位置变化
方向、#425 小时开盘位置，单因子 Test Pearson 为 0.006104088、0.000803807、
0.004708575；组合 Linear Pearson 下降 4.16%，LightGBM 上升 4.07%。AlphaPROBE
新增 #578 五分钟收盘位置的六根 Bar 变化，单因子 Test Pearson 0.008533751，组合
Linear 下降 1.17%、LightGBM 下降 4.97%。完整新增因子身份与旧集合前缀核对一致。

两组模型训练行分别为 5,308,104 和 5,049,365；Test 预测行分别为 2,558,827 和
2,409,077，共同分母为 2,558,829，覆盖率分别约 100%、94.1476%。这次按用户要求
比较当前集合，搜索次数不同，且未按共同预测行重评；不能作为等预算的最终方法结论。
AlphaAtlas 积累因子更多、覆盖更广，当前 AlphaPROBE 的树模型表现更强。

28 个单因子、4 个模型与两组相似度均完成；核对因子顺序、冻结哈希、源原子记录和
模型配置。结果见[21 对 7 对照](../../artifacts/comparisons/gpt6_luna_fold2_first21_20261030/comparison.md)
及[公式和核对数据](../../artifacts/comparisons/gpt6_luna_fold2_first21_20261030/comparison.json)。
核对续跑时 AlphaPROBE 已完成 611 次并生成第 612 个候选，仍入库 7 个；500 后的日志
在 `gpt6_luna_fold2_20261029/alphaprobe/continue_1000.log`，原 `run.log` 对应 500 阶段。

## 2026-10-30 GPT6-luna AlphaAtlas：前 18 个入库因子阶段评估

按用户要求，将 `382bb38611` 前 396 次提交中的 18 个因子冻结为独立快照，与初版
`ef96a68059` 前 508 次的前 18 个成员同时固定名单后完成标准评估。源搜索继续，原子
记录逐字节核对未变；不向方法回传 Test。双方数据快照、fold2、八字段、平台规则、
算子环境和模型配置一致。模型沿用全局 P1–P99 与品种全拟合期去均值，Train+Val
2019–2023 拟合，Test 2024–2025，固定 Linear 和 LightGBM 参数。

| 模型 | 初版 Test 加权 Pearson | Luna Test 加权 Pearson | 变化 | 初版加权 Spearman | Luna 加权 Spearman |
| --- | ---: | ---: | ---: | ---: | ---: |
| Linear | 0.014942432 | 0.013195798 | -11.69% | 0.012877222 | 0.016190899 |
| LightGBM | 0.020429813 | 0.012771235 | -37.49% | 0.016938013 | 0.013552848 |

Luna 单因子 Test 加权 Pearson 均值 0.005828120，16/18 为正；初版均值
0.006693780，17/18 为正。Luna 最高为 #62 持仓收缩条件下的 VWAP 回归
0.010604909；#95 影线事件累计 -0.001485580、#235 日线收盘位置极值 -0.000670329。
两组各 153 对 Val 相似度全部有效；平均绝对 Spearman 分别为 Luna 0.102597621、
初版 0.059621145，最大分别为 0.692477033、0.684691290，均无 >=0.7 的配对。

Luna 8 个仅基础 5m、1 个引用 30m、4 个引用 60m、5 个引用日线；初版 14 个仅
基础 5m、4 个引用日线。模型训练行分别为 5,308,104 和 5,308,089；Test 预测行分别
为 2,558,827 和 2,558,824，共同分母 2,558,829，覆盖率均接近 100%。未额外在共同
预测行重评。这是相同成员数、不同搜索次数和方法／模型版本的阶段比较，不能作为
提示词单变量消融或仅由频率解释差异。Luna 更早积累 18 个成员，但组合 Pearson
仍低于初版，尤其 LightGBM；Linear 的排序 IC 则高于初版。

36 个单因子、4 个模型和两组相似度均成功，因子身份、顺序、冻结哈希和模型配置
核对一致。完整名单、公式和覆盖率见
[18 因子对照](../../artifacts/comparisons/gpt6_luna_fold2_first18_20261030/comparison.md)
及[核对结果](../../artifacts/comparisons/gpt6_luna_fold2_first18_20261030/comparison.json)。

## 2026-10-29 覆盖优先版前 273 次：证据误读和计划偏离

只读核对 `5c5dd7640f` 的前 273 次已提交记录与相应研究计划：共 15 个入库成员，
第十三个后新增 #263、#264，含日线成员为 9/15、纯日线为 7/15。没有停止搜索、
修改源记录或新增 Test 评估。

#263 来自研究计划 70：Explorer 声称 #68 的 Train/Val 反号，要求用市场活跃度状态
解释。原子证据却为 Train 原始 -0.016083837、Val 原始 -0.009792924、direction=-1，
定向后的 Val 为 +0.009792924；两原始值同号。Explorer 混用了两个口径，Analyst
进一步在计划中将 +0.0098 写成 val_raw。该错误叙事最早可见于保存到第 101 次记录的
提案，后续延续；当前提示词已明确 Valid 为定向值，原子证据保留 val 与 val_raw。
本例不是平台计算反号，而是 Agent 未正确使用现有证据。

该计划预测高／低活跃度分支原始 IC 相反，但 #263 高分支 Train -0.008058976、
Val 原始 -0.011277212，#265 低分支 Train -0.014816624、Val 原始 -0.004134727，
均为负。高分支单因子获准入，不能据此认定反号解释成立；计划本身可执行，但其声称
要解释的原信号 Train/Val 反号并不存在。

#264 来自研究计划 71，原定测试上一日极值突破的 +1／-1／0 事件与固定二值对照。
按原式提交的 #262 Train -0.001670431、Val 原始 +0.002971917，质量拒绝。
Miner 随后在同一计划提交突破距离除以前日区间的连续幅度 #264，Train +0.005437174、
Val 原始 +0.013261051，获准入；#266 将滚动对照也改成幅度，#268／#270 再拆上下行。
保存的 Analyst 计划未授权这项测量变更。连续幅度可以另行研究，但不能将其准入当作
原二值事件假说已获支持。日线锚定突破与已有区间位置问题仍有区别，不因含日线就
将该方向整体判为重复。

两例表明问题已经涉及证据语义与固定计划交接，不能只按入库数量评价新方向发现。
提案、计划、原指标及源记录路径见
[轨迹核对](../../artifacts/comparisons/v1_async_haiku55_fold2_coverage_first_20261029/trajectory_audit_after13_prefix273_20261029.md)
及[原文证据](../../artifacts/comparisons/v1_async_haiku55_fold2_coverage_first_20261029/trajectory_audit_after13_prefix273_20261029.json)。

对已保存十三因子模型进一步只读核对：两组模型配置与 Train+Val 标签裁剪边界完全一致。
入库因子定向 Pearson 均值，初版 Train／Val／Test 为
0.010253893／0.006832205／0.007001756，新版为
0.010232607／0.007328062／0.003420850。训练均值接近，新版验证均值略高，样本外均值
却明显更低，支持因子层面的跨期泛化差异。新版含日线组的 Test 均值为 0.001204549，
非日线组为 0.006006535；这是本轮已选成员的分组事实，不代表日线频率普遍较差。

保存的新版 LightGBM 中，#130 日收益市场共同成分占训练分裂增益 56.54%，其单因子
Test Pearson 为 -0.001777465；初版最大单列增益占比约 19.27%。该指标反映训练侧
分裂增益分配，不是预测占比或样本外贡献，不能据此断言删除 #130 必然改善模型。
新版 Linear 最大三项绝对系数均来自 #68／#102／#105，分别为 -0.000265773、
+0.000217327、+0.000214286；说明该信号族参与较强的正负抵消，但未进行逐列消融。
两组实际模型有效秩均为 13；表达式层面的多列关系不能据此推断预处理后的设计矩阵奇异。
第十五个成员 #264 不在十三因子模型里，不能用它解释已经存在的十三因子性能差距。
本次未重训、未新增 Test 或因子选择，派生指标及解释边界见
[十三因子性能核对](../../artifacts/comparisons/haiku55_fold2_coverage_first13_20261029/performance_diagnosis.json)。

按保存计划的核心问题人工分组，同样只取前 273 次：新版开始执行 73 个计划，其中
12 个计划、39 次尝试围绕一根收益反转及其状态分支，反馈为 38 次质量拒绝、1 次覆盖
拒绝，零新增入库。其基准 #13 Train Pearson +0.008903078、Val +0.001180586，
训练有效但验证未达门槛。另有 7 个计划、27 次尝试围绕各周期自身收盘位置及条件分支，
形成 7 个入库结果，包含作为对照的日收益符号 #70，不应把所有结果都算作位置新机制。
这两条已识别主线合计 19/73 个计划、66/273 次尝试；不把它们等同于全部相近机制。

初版前 273 次开始执行 55 个计划，其中人工识别的十二根收益条件化主线为 21 个计划、
105 次尝试，包含 8 次编译失败，形成 #171、#173、#177、#191 四个入库结果。因此
初版也存在大量围绕已有收益主线的条件扩展，不能据新版局部回访认定整体机制集中度
一定更高。两组统计是范围不同、非穷尽的人工分组，未建立统一全量机制分类，Region
数量也不作为独立机制数。尝试数包含计划内控制、失败与拒绝。新版更直接的问题是
在验证偏弱的一根反转主线上持续增加状态解释，以及位置族中已有错误反号叙事推动
回访；这些事实支持局部利用过多的诊断，尚不能分摊组合性能差距的因果贡献。
逐计划原文、人工分组与反馈计数见
[机制投入核对](../../artifacts/comparisons/v1_async_haiku55_fold2_coverage_first_20261029/mechanism_allocation_prefix273_20261029.json)。

## 2026-10-29 DeepSeek fold2：当前覆盖优先方案，三计划并发启动

按用户要求与 Haiku 同时启动独立运行
`futures_kernel_5m_full-all-fold2-v1_async-42-958979b287`。模型沿用历史 DeepSeek 八字段
运行 `4e3f0e7900` 的 `deepseek-flash`、API 地址和 120 秒请求超时，凭据仅从现有环境
变量加载，不写入产物。用户将此次并发数明确改为 3，对齐历史 DeepSeek；现有 Haiku
`5c5dd7640f` 保持 5 并发继续运行，未切换模型、停止或修改检查点。

采用当前覆盖优先 Explorer 提示词、新颖度评分及 Boltzmann 提案选择；seed 42，
每计划最多 5 个候选、每批 2–4 个提案，1000 次总预算、500 次提交反馈后停止。
温度按 1000 总预算从 0.25 向 0.02 调度。数据快照、fold2、八字段和平台准入与历史
DeepSeek 一致，因子库从空库开始；达到截点后保留父检查点，独立冻结 prefix500，
执行单因子、Linear、LightGBM 与 Val 库内相似度的标准收尾，不将结果回传搜索。

与历史 DeepSeek 相比，本次提示词、研究记忆／执行、选择方式及下游模型预处理均有
变化；新模型评估使用全局 P1–P99 与品种全拟合期去均值，旧保存模型没有这项配置。
与当前 Haiku 比较还存在 3／5 并发、API／CLI 后端及实际采样行为差异，不能直接视为
单一模型变量消融。差异及旧模型参数已保存在 manifest，历史结果保留。

启动前配置预检通过，完整检查通过 Ruff、格式、算子目录和 1067 项测试。真实 API
已返回 Explorer 查询响应，进入正常研究对话；本节记录启动事实，不预判最终结果。
screen 为 `atlas-deepseek-fold2-coverage-first-20261029`，入口与核对见
[实验清单](../../artifacts/comparisons/v1_async_deepseek_fold2_coverage_first_20261029/manifest.json)，
实时输出见[运行日志](../../artifacts/monitors/deepseek_fold2_coverage_first_20261029.log)。

## 2026-10-29 DeepSeek：当前方案与历史方案，各前六个因子

按用户要求，在本次 Test 前同时冻结当前 `958979b287` 前 74 次中的六个成员，与历史
DeepSeek `4e3f0e7900` 前 110 次中的六个。新版入库编号为 20、45、50、58、63、68，
旧版为 34、35、36、40、55、110。两组均为 deepseek-flash、3 个并发计划、1000 次
总预算，具有相同数据、fold2、八字段、平台规则与运行环境。旧版六个也按现在的全局
P1–P99、品种全拟合期去均值重新训练，不与旧保存模型的未裁剪结果混比。
Train+Val 2019–2023 拟合，Test 2024–2025 评估；旧产物保留，当前搜索继续。

| 模型 | 旧版六个 Test 加权 Pearson | 新版六个 Test 加权 Pearson | 旧版六个 Test 加权 Spearman | 新版六个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.010856966 | -0.000422114 | 0.017007855 | 0.003293264 |
| LightGBM | 0.011707570 | 0.013403080 | 0.014473921 | -0.001112703 |

新版 LightGBM Pearson 高 14.48%，Linear 则降至接近零的负值，两模型 Spearman 均
明显下降。新版六个单因子 Test Pearson 均为正、Spearman 均为负；Pearson 均值
0.003712407，旧版为 0.003977303、5/6 为正。新版第 68 次达到六个，旧版第 110 次，
所需尝试减少 38.18%，不能将这个效率变化等同于所有预测指标均改善。

新版六个均包含 48 根五分钟高低通道位置主信号，后五个引入极值新鲜度、持仓增长、
位置位移一致性、成交参与度、日线方向条件；只有一个混合日线输入。旧版前四个为
三十分钟自适应收益符号的估计窗口／相关算子变体，另外两个为五分钟收盘位置与
波动交互、市场收益离散状态与收益位移交互。双方存在同族积累，当前这六个不能解释
为六个独立机制。Val 15 对相似度全部有效、无 >=0.7 配对；新版平均／最大绝对
Spearman 为 0.51064850／0.68251797，旧版为 0.26186506／0.69109051。

新版训练 5,049,220 行、Test 预测 2,408,618 / 2,558,829（94.1297%）；旧版为
5,307,082 和 2,558,265 / 2,558,829（99.9780%）。由于覆盖差异较大，另用保存模型
在双方共同有效的 2,408,618 行复核，保持原完整 Test 的品种成交额权重、不重训或
改变预处理。四套原标准指标逐项复现通过；共同样本上旧版 Linear Pearson／Spearman
为 0.010857364／0.017008503，旧版 LightGBM 为 0.011708766／0.014475856；新版
两模型指标与原标准结果相同。结论不因对齐 Test 行而改变，但未消除训练可用行差异。

每组六个单因子、两模型、15 对相似度及身份／顺序／冻结配置核对均完成；源原子记录
和已记录的旧版历史文件保持不变，结果不回传研究方法。完整检查通过 Ruff、格式、
算子目录及 1067 项测试。历史与新版还存在提示词、研究记忆／执行及选择策略差异，
本次为同成员数系统比较，不是单变量消融。完整结果见
[六因子对照](../../artifacts/comparisons/deepseek_fold2_coverage_first6_20261029/comparison.md)、
[原始结果与核对](../../artifacts/comparisons/deepseek_fold2_coverage_first6_20261029/comparison.json)及
[共同预测行复核](../../artifacts/comparisons/deepseek_fold2_coverage_first6_20261029/common_rows.json)。

## 2026-10-29 DeepSeek：当前方案与历史方案，各前十个因子

同时冻结新版 `958979b287` 前 110 次中的十个成员与旧版 `4e3f0e7900` 前 174 次中的
十个。首六成员身份和顺序完整保留；新版新增 77、90、97、107，旧版新增
113、116、143、174。沿用六因子比较的标准收尾与统一模型口径，全局 P1–P99、品种
全拟合期去均值，Train+Val 2019–2023 拟合、Test 2024–2025 评估。历史模型、搜索
产物均保留，当前搜索未停止，结果不回传方法。

| 模型 | 旧版十个 Test 加权 Pearson | 新版十个 Test 加权 Pearson | 旧版十个 Test 加权 Spearman | 新版十个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.006878003 | 0.010549129 | 0.012489120 | 0.004874850 |
| LightGBM | 0.007193271 | 0.019332492 | 0.011395272 | 0.007930583 |

新版两模型 Pearson 分别高 53.37%／168.76%，Spearman 仍低于旧版。从六个增至十个，
新版 Linear Pearson 从 -0.000422114 升到 0.010549129，LightGBM 从 0.013403080 升到
0.019332492（+44.24%）；旧版分别从 0.010856966 降到 0.006878003、从 0.011707570
降到 0.007193271。变化是四个新增成员一起进入组合后的结果，未做逐列消融归因。

新版新增 #77 高持仓水平条件通道趋势、#90 高成交额品种条件通道趋势、#97 成交量
比值历史排名对照、#107 通道趋势横截面共同成分的单因子 Test Pearson 分别为
0.007599760、0.005175928、0.003028692、0.003272284；均值 0.004769166，全为正。
对应 Spearman 为 -0.004661413、-0.009521154、0.003348875、-0.005023220。
旧版新增四个 Pearson 均值仅 0.000293053，其中 #113／#116 分别为
-0.001690903／-0.003959867。新版十个单因子 Pearson 均值 0.004135111、10/10 正值，
旧版为 0.002503603、7/10 正值；Spearman 均值分别 -0.006321219／-0.000037521，
正值数量为 1/10／3/10。新版 Pearson 与排序能力仍存在分化。

新版九个成员包含 48 根五分钟高低通道位置，只有 #97 是独立成交量对照；#97 的
DELAY(...,288) 只按原生 bar 延迟执行，不据名称将其解释为同一日内时点。
Val 45 对全部有效且无 >=0.7 配对，新版平均／最大绝对 Spearman 为
0.37674167／0.68256411，旧版为 0.13180224／0.69109051。新版平均相关较六个时
0.51064850 下降，但仍更集中。新版第 107 次达到十个，旧版为 174 次，所需尝试少
38.51%。不能把阶段表现直接归因于提示词或某一选择策略。

新版训练 5,092,707 行，较六个时增加 43,487；Test 预测 2,411,526 / 2,558,829
（94.2433%），增加 2,908 行。旧版训练 5,307,082、Test 预测 2,558,265，两者与六个时
相同。另用保存模型在共同有效的 2,411,526 行核对，保留原完整 Test 品种成交额权重，
不重训或改预处理，四套标准指标逐项复现。共同样本上旧版 Linear Pearson／Spearman
为 0.006878330／0.012490160，LightGBM 为 0.007193830／0.011396559；新版指标与标准
结果相同，比较结论不变，但仍有训练可用行差异。

每组十个单因子、两模型与 45 对相似度完成，身份、顺序、冻结与配置核对通过，源
原子记录及已记录的旧版历史文件保持不变。完整检查通过 Ruff、格式、算子目录和
1067 项测试。完整结果见
[十因子对照](../../artifacts/comparisons/deepseek_fold2_coverage_first10_20261029/comparison.md)、
[数值与核对](../../artifacts/comparisons/deepseek_fold2_coverage_first10_20261029/comparison.json)及
[共同预测行复核](../../artifacts/comparisons/deepseek_fold2_coverage_first10_20261029/common_rows.json)。

## 2026-10-29 DeepSeek：当前方案与历史方案，各前二十七个因子

按用户要求同时冻结新版 `958979b287` 前 220 次中的 27 个入库成员与旧版
`4e3f0e7900` 前 553 次中的 27 个。新版第 218 次达到 27 个，旧版第 553 次，所需
尝试少 60.58%。旧版前 500 次仅 23 个，本次是同成员数阶段比较，不是同预算比较。
首十成员身份和顺序完整保留，沿用十因子比较的快照、fold2、八字段、平台规则、模型
预处理与参数；Train+Val 2019–2023 拟合、Test 2024–2025 评估。父搜索继续，历史
结果保留，Test 不回传方法。

| 模型 | 旧版二十七个 Test 加权 Pearson | 新版二十七个 Test 加权 Pearson | 旧版二十七个 Test 加权 Spearman | 新版二十七个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.012317625 | 0.012178065 | 0.015014787 | 0.007690237 |
| LightGBM | 0.013110966 | 0.010750464 | 0.015129954 | 0.007718142 |

新版相对旧版 Pearson：Linear -1.13%、LightGBM -18.00%，两个 Spearman 均较低。
从十个增至二十七个，新版 Linear Pearson 从 0.010549129 升到 0.012178065（+15.44%），
LightGBM 从 0.019332492 降到 0.010750464（-44.39%）；旧版分别从 0.006878003、
0.007193271 升到 0.012317625、0.013110966。前十个时的新版组合 Pearson 优势没有
在当前成员规模保持，未做逐项消融分摊新增成员的贡献。

新版全部 27 个单因子 Test Pearson 均值 0.003159787、22/27 正值，旧版为
0.003065904、21/27；Spearman 均值分别 -0.001905910／0.002383070，正值数为
11/27／15/27。新版新增 17 个 Pearson 均值 0.002586067、12/17 正值，低于原十个
均值 0.004135111；旧版新增 17 个均值 0.003396670、14/17 正值。新版新增中较强的
#218 前日跳空方向一致分支、#194 低活跃度高成交额分支、#161 低活跃度分支的
Test Pearson 为 0.009278727、0.008436297、0.006379643；#163 波动率排名倾斜、
#213 高成交额个体成分倾斜为 -0.002399908、-0.001249899。这些是描述性结果，不据
Test 删因子或调参。

新版新增 17 个均复用 48 根五分钟通道位置或其市场共同成分，并增加活跃度、偏度、
波动率、持仓、成交额、跳空状态；累计 26/27 包含该主信号，唯一例外为 #97 成交量
排名对照。已核对各原式及编译字段，不把入库数量当作独立机制数量。Val 351 对全部
有效且无 >=0.7 配对；新版平均／最大绝对 Spearman 为 0.18139095／0.69180459，
旧版为 0.09182977／0.69109051。新版平均相关从十个时 0.37674167 下降，LGBM
仍下滑，不能单凭两两相关解释组合变化。

两组模型均训练 5,307,082 行，Test 均预测 2,558,265 / 2,558,829（99.9780%）。新版
相对十个时增加 214,375 条训练行、146,739 条预测行，旧版行数不变，因此规模变化也
包含覆盖变化，不将全部变化唯一归因于新增特征的信息。每组 27 个单因子、两模型及
351 对相似度已完成，完整检查通过 Ruff、格式、算子目录和 1067 项测试。

另用保存模型重建两组预测，四套原指标逐项复现通过；共同有效 Test 行为 2,558,265，
与两组各自预测行完全一致，共同行上的 Pearson、Spearman 均保持原值，覆盖差异不能
解释本次差距。首十个成员身份、顺序、源原子记录和七个已记录历史源文件均保持不变。
名单、公式、单因子、组合、相似度及验证见
[比较报告](../../artifacts/comparisons/deepseek_fold2_coverage_first27_20261029/comparison.md)、
[机器结果](../../artifacts/comparisons/deepseek_fold2_coverage_first27_20261029/comparison.json)。

## 2026-10-29 DeepSeek：当前方案与历史方案，各前三十一个因子

按用户要求，在本次 Test 前冻结两组前 31 个成员：当前 `958979b287` 的前 235 次
（第 232 次达到 31 个），历史 `4e3f0e7900` 的前 615 次（第 615 次达到 31 个）。
两组原前 27 个成员身份、顺序保留。沿用此前的快照、fold2、八字段、3 并发、1000
总预算及平台规则，旧库按当前模型口径重训：全局 P1–P99、品种全拟合期去均值；
Train+Val 2019–2023 拟合，Test 2024–2025 评估。当前搜索继续，Test 不反馈给方法。
旧版前 500 次仅有 23 个，故本次是相同成员数比较，消耗预算不同，不能称同预算比较。

| 模型 | 旧版 31 个 Test 加权 Pearson | 新版 31 个 Test 加权 Pearson | 旧版 31 个 Test 加权 Spearman | 新版 31 个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.015750074 | 0.013228414 | 0.015552320 | 0.008535057 |
| LightGBM | 0.021089007 | 0.011119936 | 0.016149502 | 0.009850534 |

新版 Pearson 相对旧版为 Linear -16.01%、LightGBM -47.27%。27→31 新版分别提高
8.62%、3.44%，旧版分别提高 27.87%、60.85%；两组 Spearman 同样都回升，新版仍较低。

| 版本与模型 | 10 个 Pearson | 27 个 Pearson | 31 个 Pearson |
| --- | ---: | ---: | ---: |
| 新版 Linear | 0.010549129 | 0.012178065 | 0.013228414 |
| 新版 LightGBM | 0.019332492 | 0.010750464 | 0.011119936 |
| 旧版 Linear | 0.006878003 | 0.012317625 | 0.015750074 |
| 旧版 LightGBM | 0.007193271 | 0.013110966 | 0.021089007 |

新版新增 #226 对向影线倾斜、#228 通道极端程度倾斜、#229 高对向影线分支、#232
市场共同 12-bar 收益与低活跃度交互，Test Pearson 分别为 0.005261833、0.002033004、
0.005910759、0.001004783，Spearman 分别为 0.007191021、-0.002029122、0.002566690、
0.002687916。新增四个均值 Pearson 0.003552595、Spearman 0.002604126，Pearson 全正。
旧版新增 #580 成本锚偏离、#606 离散度占比状态、#608 滞后离散度水平、#615 成交额
集中度加权离散状态，Pearson 分别为 0.009038163、0.000793298、0.001295183、-0.001196581；
均值为 0.002482516，3/4 为正，Spearman 均值 -0.003057715。新版新增四个单因子
Pearson 均值更高，组合提升却较小，单因子 IC 均值不足以解释组合差距；未做消融，
不把某成员单独归因为组合改善或恶化的原因。

全部单因子新版 Pearson 均值 0.003210472、26/31 为正，旧版为 0.002990628、24/31；
Spearman 均值新版 -0.001323970、14/31 为正，旧版 0.001681033、16/31 为正。
新版 #226、#228、#229 继续复用通道共同成分，#232 不含通道，累计 29/31 含 48-bar
高低价通道，另一个例外为 #97 成交量排名。该计数基于表达式，不能视为独立机制数。
Val 465 对相似度均有效且无 >=0.7 配对；新版平均／最大绝对 Spearman 为
0.17012830／0.69180459，旧版为 0.10323345／0.69109051。两两相关不能直接代替组合
增量评估，也不能单独解释组合性能变化。

两组训练均为 5,307,082 行，Test 预测均为 2,558,265 / 2,558,829（99.9780%），与
各自 27 个时的行数相同；相同计数不单独证明训练行身份相同。每组 31 个单因子、
两模型、465 对相似度全部成功；源原子记录保留。完整检查通过 Ruff、格式、算子目录
及 1071 项测试（120.80 秒）。提示词、记忆／执行、选择器等多项有差异，不作单变量
因果归因；持续查看的 Test 作为描述性比较，不据此挑因子、改方向或调参。

复用保存模型重建预测，四套标准指标逐项复现；两组共同预测行 2,558,265，与各自
有效预测行完全一致，共同行 Pearson、Spearman 保持原值。因此本次差距不是 Test
预测行覆盖不同造成。已记录的七个旧版历史源文件及源原子记录保持原样。
完整公式、全部单因子、10→27→31 组合轨迹和验证见
[比较报告](../../artifacts/comparisons/deepseek_fold2_coverage_first31_20261029/comparison.md)、
[机器结果](../../artifacts/comparisons/deepseek_fold2_coverage_first31_20261029/comparison.json)。

## 2026-10-29 DeepSeek：新版 1000 预算，500 次停止的完整结果

监控日志 `artifacts/monitors/deepseek_fold2_coverage_first_20261029.log` 对应当前
`958979b287` 运行，父运行预算保留 1000，检查点已提交 500、无 pending；独立冻结
`958979b287-prefix500` 保存该 500 次截面，入库 70 个，非 700 个。实验状态为 complete，
收尾于 2026-10-29 22:59（Asia/Shanghai），不是此前前 31 个入库成员的阶段评估。

全部 70 个单因子、两种组合模型和 2415 对 Val 相似度均完成。单因子 Test 加权
Pearson 均值／中位数为 0.003331／0.003309，58/70 为正；加权 Spearman 均值
0.000270。Linear Test 加权 Pearson／Spearman 为 0.012860344／0.008924589，
LightGBM 为 0.012737709／0.010124958。模型沿用 Train+Val 拟合的全局 P1–P99
裁剪及品种全拟合期去均值。Val 平均绝对 Spearman 0.171063、最大值 0.696525，
无 >=0.7 配对。

收尾原先仅写入实验专用 `workspace/experiment_overview.md`。现复用现有汇总函数，
将已完成的结果行和相似度行同步至仓库根目录总览；核对冻结哈希、成员身份／顺序、
模型规格、完整评估后发布，不重训、不新增尝试、不修改源结果。
见[根目录总览](../../experiment_overview.md)、
[完整运行报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-958979b287-prefix500/report.md)、
[组合模型报告](../../artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-958979b287-prefix500/model/report.md)。

## 2026-10-29 DeepSeek：从 500 次检查点继续至 1000 次

用户明确要求继续原 `958979b287` 至 1000 次。启动前确认父运行 interrupted、预算仍为
1000，检查点 completed/attempts 为 500、library_version 为 70、pending 为 null；
实验专用配置通过现有恢复校验，Explorer 主提示、异步系统提示、提醒和评分说明与
启动时保存版本一致。沿用 deepseek-flash、fold2、3 并发、原选择器及全部数据／评估
配置，使用公共 resume 恢复原方法状态和 RNG，不再安装 500 次停止钩子。

screen 会话为 `atlas-deepseek-fold2-coverage-first-continue1000-20261029`，日志为
`artifacts/monitors/deepseek_fold2_coverage_first_continue1000_20261029.log`。2026-10-29
23:11（Asia/Shanghai）已通过恢复检查、加载 6,189,366 行并进入 501/1000 生成阶段。
启动脚本为原实验目录下 `continue_to_1000.py`，只串联现有 resume、finish 和汇总函数；
无产品代码／配置修改。1000 次结束后自动冻结、完成单因子／两模型／Val 全库相似度
评估并同步仓库根目录总览。500 次独立冻结截面与既有阶段比较保留，OOS 不进入恢复
上下文或方法反馈。

## 2026-10-30 DeepSeek：前 78 个因子与 500 次／70 个截面对照

按用户要求，在本次 Test 前固定原 `958979b287` 前 596 次提交中的前 78 个入库成员；
第 589 次达到 78 个。独立截面为 `958979b287-first78-prefix596`，位于
`artifacts/comparisons/deepseek_fold2_coverage_first78_20261030/artifacts/runs/`。
与原 `958979b287-prefix500` 的 70 个比较，全部旧成员的身份、顺序、冻结记录保持
一致。沿用相同数据、fold2、八字段、模型参数和 Train+Val 2019–2023 全局 P1–P99、
品种全拟合期去均值；Test 2024–2025。父运行继续至原 1000 预算，测试不反馈给方法。

| 模型 | 70 个 Test 加权 Pearson | 78 个 Test 加权 Pearson | 70 个 Test 加权 Spearman | 78 个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.012860344 | 0.014228115 | 0.008924589 | 0.010693313 |
| LightGBM | 0.012737709 | 0.013590895 | 0.010124958 | 0.012536430 |

Pearson 分别提高 10.64%、6.70%。训练有效行从 5,307,082 增至 5,307,840（+758），
Test 预测行从 2,558,265 增至 2,558,685（+420），eligible 行均为 2,558,829；
覆盖率从 99.9780% 升至 99.9944%。因此组合变化也包含覆盖扩大；未做逐项消融，
不把某个因子的单独 IC 解释为组合增量。

| 新增评估序号 | 方向 | Test 加权 Pearson | Test 加权 Spearman |
| ---: | --- | ---: | ---: |
| 512 | 通道共同成分 × 共同 VWAP 溢价、区间和影线残差倾斜 | 0.010271302 | 0.005701803 |
| 515 | 通道共同成分 × 品种 VWAP 溢价、活跃度和区间残差倾斜 | 0.010026047 | 0.001733780 |
| 540 | 通道共同成分的日线低活跃度分支 | 0.005658036 | 0.000800435 |
| 542 | 通道共同成分的日内、日线双低活跃度分支 | 0.005080190 | 0.001580448 |
| 569 | 持仓冲击的价格上涨分支 | -0.009025193 | -0.008251550 |
| 583 | 通道共同成分的幅度排名符号切换 | 0.012631464 | 0.005170256 |
| 588 | 持仓冲击的高成交量冲击分支 | -0.006465369 | -0.004828645 |
| 589 | 持仓冲击的价格下跌分支 | 0.000370499 | 0.001838731 |

新增八个的 Pearson／Spearman 均值为 0.003568372／0.000468157，两项均 6/8 为正。
五个通道派生项两项 IC 全正，三个持仓冲击项两项均为两负一正，持仓新方向在此 Test
上的表现较弱。全库 Pearson 均值由 0.003331287 升至 0.003355603，正值由 58/70
变为 64/78；Spearman 均值由 0.000269725 升至 0.000290077，正值由 40/70 变为
46/78。48-bar 通道表达式计数由 64/70 变为 69/78，含日线输入由 2/70 变为 4/78；
这些是表达式计数，不能当作独立机制数量。持仓公式中的 DELAY(...,288) 表示基础
bar 数，不自动代表一天或同一时刻。

3003 对 Val 相似度均有效，无 >=0.7 配对。平均绝对 Spearman 由 0.171063114 降至
0.163927002，最大值由 0.696524645 变为 0.699157231。原 70 个单因子指标与模型
预处理的边界、均值、标准差均复现通过，源原子记录保持原样。78 个单因子、两个模型
和相似度均成功后，已复用现有函数同步根目录两张总览表。完整检查通过 Ruff、格式、
算子目录及 1072 项测试（119.25 秒），无产品源码或配置修改。

见[对比报告](../../artifacts/comparisons/deepseek_fold2_coverage_first78_20261030/comparison.md)、
[完整结果与公式](../../artifacts/comparisons/deepseek_fold2_coverage_first78_20261030/comparison.json)、
[实验总览](../../experiment_overview.md)。

## 2026-10-30 DeepSeek：连接失败后从第 865 次恢复

原 `958979b287` 继续至 1000 的进程，在生成第 866 个候选时连续四次连接／超时失败，
以 failed 退出。检查点 completed/attempts 为 865、library_version 为 122、pending
为 null；末次原子记录与检查点一致，原预算仍为 1000。配置通过既有恢复校验。

沿用原 `continue_to_1000.py` 和启动环境，在 screen 会话
`atlas-deepseek-fold2-resume865-20261030` 恢复，不改模型、3 并发、超时或重试策略。
失败日志保留；新日志为 `artifacts/monitors/deepseek_fold2_resume865_20261030.log`。
恢复后已获得有效模型回复并提交第 866 次评估（quality_threshold 拒绝），继续生成
第 867 个候选，入库仍为 122。既有评估不重算，500 次及 78 个独立截面保留；完成
1000 后仍按原启动脚本自动收尾评估并发布根目录总览。此次无源码或配置修改。

## 2026-10-30 DeepSeek：前 122 个因子与 78 个截面对照

按用户要求，在本次 Test 前固定 `958979b287` 前 867 次提交中的 122 个入库成员；
第 859 次达到 122 个。独立冻结截面为 `958979b287-first122-prefix867`，位于
`artifacts/comparisons/deepseek_fold2_coverage_first122_20261030/artifacts/runs/`。
对照原前 596 次／78 个截面，数据、fold2、八字段、平台规则、模型参数均相同；
全局 P1–P99 与品种全拟合期去均值在 Train+Val 2019–2023 拟合，Test 2024–2025。
原搜索保留 1000 总预算与 3 并发，评估不反馈给搜索，历史结果保留。

| 模型 | 78 个 Test 加权 Pearson | 122 个 Test 加权 Pearson | 78 个 Test 加权 Spearman | 122 个 Test 加权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.014228115 | 0.020735043 | 0.010693313 | 0.014455435 |
| LightGBM | 0.013590895 | 0.016112293 | 0.012536430 | 0.011152439 |

组合 Pearson 分别提高 45.73%、18.55%；Linear Spearman 提高，LightGBM Spearman
回落约 11.04%。训练有效行由 5,307,840 增至 5,307,848（+8），Test 预测行由
2,558,685 增至 2,558,697（+12），eligible 行均为 2,558,829，当前覆盖率 99.9948%。
比较同时包含少量覆盖变化，未按 Test 调参或逐项消融，不将单个因子 IC 当作组合增量。

新增 44 个单因子 Pearson 均值为 0.002347642、23/44 为正；Spearman 均值为
0.000438122、21/44 为正。按实际公式是否含 48-bar 高低价通道和持仓字段分为两组：

| 新增公式组 | 数量 | Test 加权 Pearson 均值 | Pearson 正值数 | Test 加权 Spearman 均值 | Spearman 正值数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 含 48-bar 通道 | 19 | 0.004607850 | 13/19 | 0.001301408 | 13/19 |
| 含持仓字段且不含该通道 | 25 | 0.000629885 | 10/25 | -0.000217975 | 8/25 |

持仓组整体较弱，但已出现较强个体。新增最高的 #814 使用五分钟持仓均值与已知日线
持仓之比的排名，减横截面均值后取负侧，Test Pearson／Spearman 为
0.014270018／0.006629149；#841 对同一日线锚排名偏离取负绝对值，指标为
0.011955442／0.006718873。#826 相对持仓排名偏离的负侧大幅度分支为
0.011595569／0.007500662。通道组较强的 #732、#846 Pearson 为 0.014016272、
0.012294077。较弱的非通道持仓项 #651、#764、#653 Pearson 为 -0.007310895、
-0.006217352、-0.005962978。这里的负侧排名偏离不直接等同于真实净减仓或因果机制。

全库单因子 Pearson 均值由 0.003355603 降至 0.002992076，正值数由 64/78 变为
87/122；Spearman 均值由 0.000290077 升至 0.000343470，正值数由 46/78 变为
67/122。含 48-bar 通道的公式由 69/78 变为 88/122，含日线输入由 4/78 变为 6/122。
这些是公式计数，不作为独立机制数。

Val 7381 对相似度全部有效，无 >=0.7 配对，平均绝对 Spearman 从 0.163927002
降至 0.129149547，最大值仍为 0.699157231。原 78 个成员的完整冻结记录、单因子
指标、模型预处理边界／均值／标准差均复现通过，源原子记录保持原样。全部单因子、
两模型和相似度成功后，已复用现有函数同步根目录两张总览表。完整检查通过 Ruff、
格式、算子目录和 1072 项测试（106.81 秒），无产品源码或配置修改。

见[完整对比及新增 44 个单因子](../../artifacts/comparisons/deepseek_fold2_coverage_first122_20261030/comparison.md)、
[全部公式与机器结果](../../artifacts/comparisons/deepseek_fold2_coverage_first122_20261030/comparison.json)、
[实验总览](../../experiment_overview.md)。

## 2026-10-30 Fold2：Luna 36 完整结果与三组 LGBM 参数对照

Luna AlphaAtlas `382bb38611` 已完成原 1000 次预算，冻结 36 个因子。单因子、Linear、原参数 LightGBM 和全部 630 对 Val 相似度复用已保存结果；通过标准收尾校验并写入仓库总表，发布过程禁止重新加载行情。原 run、冻结库、fit/test、OOS 和相似度文件逐字节保持不变。

Kernel 八字段 / all / fold2 / seed42；模型在 Train+Val 2019–2023 拟合，Test 2024–2025。全局 P1–P99 与品种全拟合期去均值口径保持原样。Luna 36 单因子 Test 加权 Pearson 均值 0.006359795、中位数 0.005778772，32/36 为正；Spearman 均值 0.005928139，28/36 为正。
Val 平均／最大绝对 Spearman 为 0.073437617／0.692477033，无 >=0.7 配对。

三组固定参数对照同时将 min_data_in_leaf=100→2000、max_depth=-1→5；其余参数与各自训练／测试行、预处理保持一致，Linear 直接复用。原参数与新参数是同一冻结因子库的下游模型，不是新增因子搜索运行。这是查看过既有 Test 后进行的参数敏感性诊断。

- AlphaAtlas / deepseek-flash，143 因子：LGBM 加权 Pearson 0.015380367→0.018879642，Spearman 0.012438896→0.014208468；Linear Pearson/Spearman 为 0.024103261／0.018839685。
- AlphaAtlas / gpt-6-luna，36 因子：LGBM 加权 Pearson 0.015766586→0.017956687，Spearman 0.017576763→0.017887672；Linear Pearson/Spearman 为 0.018197620／0.014730398。
- AlphaPROBE / gpt-6-luna，8 因子：LGBM 加权 Pearson 0.019461898→0.011908831，Spearman 0.014319253→0.013729659；Linear Pearson/Spearman 为 0.010070367／0.016241590。

实验主表中 DeepSeek 143、Luna 36、Luna AlphaPROBE 8 的 LightGBM 均采用新参数结果，并链接到对应模型报告；原参数结果保留在下方对照表，其他历史行保持原样。

完整检查通过 Ruff、格式、算子目录及 1084 项测试（138.12 秒）。

完整结果及预测覆盖率见[实验总表](../../experiment_overview.md)，机器可读对照见[comparison.json](../../artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/comparison.json)。因子矩阵作为本次实验的临时产物，评估及发布校验成功后清理；历史指标、模型和运行产物保留。

## 2026-10-30 Fold2：DeepSeek 70、Luna 22 的 500 次截面 LGBM 参数对照

对 `958979b287-prefix500` 的 70 个因子和 `382bb38611-prefix500` 的 22 个因子，
沿用各自冻结库、Train+Val 预处理及 Linear 模型，仅将 LightGBM 的
`min_data_in_leaf=100→2000`、`max_depth=-1→5`。其余参数保持原样。

| 500 次截面 | Test 加权 Pearson：原→新 | Test 加权 Spearman：原→新 | Test 覆盖率 |
| --- | --- | --- | --- |
| DeepSeek / 70 因子 | 0.012737709→0.011750789 | 0.010124958→0.008679014 | 99.9780% |
| Luna / 22 因子 | 0.013775656→0.013568681 | 0.014027585→0.014228038 | 99.9999% |

新旧模型的预处理、训练行及 Test 预测行分别一致，Linear 复现原结果；
实际模型参数仅有上述两处差异，树深不超过 5。DeepSeek 和 Luna 的训练行分别为
5,307,082、5,308,104，Test 预测行分别为 2,558,265、2,558,827。
两组 Pearson 均下降，Luna Spearman 略升；该次参数变更没有普遍改善不同因子集合。

两行新 LGBM 结果已写入[Fold2 主表](../../experiment_overview.md)，原／新参数及 Linear
完整结果保留在下方对照表；表格仍按模型分组，AlphaAtlas 保留 Annealing 标注。
所有原始 run、冻结库、fit/test、OOS、相似度文件在发布期间逐字节保持不变，
临时因子矩阵在评估及发布校验后删除。完整检查通过 Ruff、格式、算子目录和
1084 项测试（106.22 秒）。机器结果见
[comparison.json](../../artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/comparison.json)。

## 2026-10-30 Fold2：500 次截面 LGBM 从 50 棵树增加到 100 棵树

对 DeepSeek 70、Luna 22 两个冻结截面继续使用 `min_data_in_leaf=2000, max_depth=5`，
仅将 `num_boost_round` 从 50 改为 100。学习率 0.05、15 个叶子、L2=1、seed42、
4 线程和确定性训练均保持不变，预处理、训练行、Test 预测行及 Linear 模型一致。

| 500 次截面 | Test 加权 Pearson：50→100 棵 | Test 加权 Spearman：50→100 棵 |
| --- | --- | --- |
| DeepSeek / 70 因子 | 0.011750789→0.010818961 | 0.008679014→0.009046006 |
| Luna / 22 因子 | 0.013568681→0.013954716 | 0.014228038→0.013931448 |

两组实际均生成 100 棵树，前 50 棵树逐棵复现原模型，实际模型参数仅
`num_iterations=50→100` 发生变化。最大树深均为 5，最小叶样本数分别为 2000、2001。
增加树数没有一致改善：DeepSeek Pearson 下降而 Spearman 上升，Luna 则相反。

两行主表采用 100 棵树结果，50 棵树结果保留在参数对照表中，并明确各行的树数。
历史运行与已保存模型保留；临时因子矩阵在评估和发布校验后清理。
完整检查通过 Ruff、格式、算子目录及 1084 项测试（122.10 秒）。机器结果见
[trees100_comparison.json](../../artifacts/comparisons/lgbm_leaf2000_depth5_fold2_20261030/trees100_comparison.json)。

## 2026-10-30 Fold2：主表 13 组统一 50 棵树与模型预处理

按用户确定的参数，Fold2 主表全部 13 行使用 `num_boost_round=50`、
`min_data_in_leaf=2000`、`max_depth=5`。学习率 0.05、15 个叶子、L2=1、
4 线程、seed42 和确定性训练保持一致。100 棵树结果保留为历史对照，主表切回 50 棵。

旧版 DeepSeek AlphaAtlas、Haiku AlphaPROBE／MCTS-LLM／AlphaAtlas 的 500、1000 次
共 8 组原模型没有启用模型全局裁剪及品种去均值；本次统一为已确认的模型口径：
Train+Val 2019–2023 全局特征／标签 P1–P99 裁剪，Linear 对特征／标签按品种全拟合期
去均值，LightGBM 使用全局预处理特征及按品种去均值标签；Test 2024–2025 复用
特征边界、标签保持原值。8 组同时重训 Linear 和 LightGBM，主表两列同步更新。
这些旧模型前后同时包含预处理与 LGBM 参数变化，不能单独归因于叶样本数或树深。

新版 DeepSeek 70／143、Luna 22／36、Luna AlphaPROBE 8 的 5 组已具有完整的
同参数结果，直接复用；没有重新搜索、调用模型服务或重新评估单因子／相似度。
8 组重训通过公共 `run_model` 完成；四个父库各计算一次因子值，500 次截面复用
身份、顺序、表达式和方向一致的前缀列。每组保存的模型及 Test 均通过标准缓存复用验证。

新增 8 组的 Test √成交额加权 Pearson：

| 模型／方法 | 500 次 Linear | 500 次 LightGBM | 1000 次 Linear | 1000 次 LightGBM |
| --- | --- | --- | --- | --- |
| DeepSeek／旧版 AlphaAtlas+Annealing | 0.013321295 | 0.011558325 | 0.015020302 | 0.021267113 |
| Haiku／AlphaPROBE | 0.014789603 | 0.021805239 | 0.014015863 | 0.017632646 |
| Haiku／MCTS-LLM | 0.013805449 | 0.013143927 | 0.012936756 | 0.014284852 |
| Haiku／AlphaAtlas+Annealing | 0.016312738 | 0.020761824 | 0.015128334 | 0.015047241 |

初版 Haiku 500 次的 Linear 复现已有统一口径重训结果 0.016312738；原表的
0.023946489 属于旧预处理，已替换。13 组实际均为 50 棵树、最大树深不超过 5，
配置、资产、数据快照、字段、fold、预热及 seed 一致；标签裁剪边界均为
`[-0.019324272826402925, 0.019212713223309814]`。

8 组原始 run、冻结库、fit/Test、OOS 与相似度文件在执行期间逐字节保持不变，
新结果单独保存。主表保留按模型分组和组内方法顺序，13 行模型数值与各自保存的
Test 结果一致，41 行参数对照及报告链接核对通过。完整检查通过 Ruff、格式、
算子目录及 1085 项测试（133.98 秒）。

见[实验总表](../../experiment_overview.md)及
[完整配置与 13 组机器结果](../../artifacts/comparisons/fold2_uniform_lgbm50_leaf2000_depth5_20261030/comparison.json)。

## 2026-10-30 Fold2：八个冻结库的预测误差增量复评

按用户要求，从实验总表选择同数据、同研究模型的历史对照：Haiku AlphaAtlas
`ef96a68059`、AlphaPROBE `bc919289de`、MCTS-LLM `aa870d70c5` 的 500／1000 次
截面；补充 Luna AlphaAtlas `382bb38611` 与 AlphaPROBE `3656be6b77` 的 1000 次。
共 8 个冻结库、16 个预测模型。原始及统一模型产物保留，没有重新搜索、重训模型、
调用模型服务、重新选因子或执行 Shapley。

八组使用相同 Kernel 八字段快照 `73db06c2…`、fold2、seed42、评分与准入规则；
复用已保存的全局 P1–P99／品种去均值 Linear 与 LightGBM（50 轮、叶最少 2000、深度 5）。
通过公共 MarketData、DSL、模型 transform/predict 重新生成预测，逐项复现各模型保存的
Test IC 与预测行数；读取的 run、冻结库、fit、test 文件保持不变。

评估前将选组、基线、共同样本与重采样规则写入
[protocol.json](../../artifacts/comparisons/fold2_predictive_value_20261030/protocol.json)。
无因子基线为全部 5,308,104 条合格 Train+Val 原始目标的全局均值
`0.000038199635773434764`，不使用 Test 估计。八组共同可预测样本为
2,408,926 / 2,558,829 行（94.141734%）；主表在此交集上统一比较两个模型和两个预算点。
主指标 `1 - MSE_model / MSE_baseline` 按行等权，Test 目标保持原值；另报零预测基线，
以及全合格样本上缺失预测回退到共同常数基线的敏感性。IC 复用按品种计算、√成交额加权。
现有去均值预测未加回品种目标均值，MSE 也反映当前输出的尺度和校准，不能称独立信息量。

1000 次共同样本结果：

| 研究模型 / 方法 | 因子 | Linear 误差下降 | LightGBM 误差下降 | LightGBM 95% 时间区块区间 | LightGBM 加权 Pearson |
| --- | ---: | ---: | ---: | --- | ---: |
| Haiku / AlphaAtlas+Annealing | 31 | 0.06629% | 0.09060% | [0.00405%, 0.17756%] | 0.015035 |
| Haiku / AlphaPROBE | 32 | 0.02917% | 0.08704% | [0.02686%, 0.14180%] | 0.017545 |
| Haiku / MCTS-LLM | 29 | 0.02549% | 0.03016% | [0.00073%, 0.05972%] | 0.014282 |
| Luna / AlphaAtlas+Annealing | 36 | 0.01271% | 0.05205% | [0.01333%, 0.08788%] | 0.017964 |
| Luna / AlphaPROBE | 8 | 0.02196% | 0.03798% | [0.00334%, 0.08287%] | 0.011890 |

区间采用 20 个连续交易日的移动区块、2000 次配对重采样、seed20261030，每次同时抽取
整个品种截面及所有方法。它只反映这些冻结预测器在当前 Test 时期的抽样不确定性，不包含
独立搜索 seed 的变化。Haiku 1000 次 LightGBM 的 Atlas−PROBE 差为 +0.00356 个百分点，
区间 [-0.09009, +0.10167]；Atlas−MCTS 差 +0.06044 个百分点，区间
[-0.02141, +0.14438]。两者均跨零，不能把点估计排序称为稳定优势；Luna 对照也跨零。

Haiku 的同一路径由 500 延长到 1000 次时，因子分别为 Atlas 17→31、PROBE 20→32、
MCTS 13→29。LightGBM 相对无因子基线的误差下降率分别从 0.08353%→0.09060%、
0.10991%→0.08704%、0.03326%→0.03016%。若直接以各自 500 次模型误差为分母，
再投入 500 次提交带来的误差下降分别为 +0.00708%、−0.02290%、−0.00310%。
其中 PROBE 的配对时间区间在零以下，但结论仅针对这一历史轨迹和固定预测器，不能推广为
新增因子必然有害。因子数量少本身不取消整体预测评估价值；数量不是独立样本量，
不以增益除以因子数，也不为主比较按 Test 挑选等 K 子集。

比较范围明确为同候选提交预算，不是同 token／费用预算。1000 次的 Chat 请求数为
Haiku Atlas 2032、PROBE 859、MCTS 3021，Luna Atlas 2839、PROBE 745；部分用量未知，
不推算实际账单。Haiku Atlas 在 444／622／887 次附近改过策略或证据处理，Luna Atlas
在 692 次后经人工授权换题续跑；下游统一参数也属于查看既有 Test 后的诊断方案。
因此本轮是历史复评，不是新盲测、固定算法消融或实质新颖度证明。

实现新增公共 `evaluation.prediction_error_summary`，严格要求已对齐且有限的输入，
零基线误差返回未定义的相对改善。28 项合成测试覆盖手算、负改善、零分母、SSE 分块
等价与错误输入；离线脚本的配对区块抽样另经合成小样本验证。
完整 `pwsh -File scripts/check.ps1` 通过 Ruff、格式、算子目录及 1113 项测试
（122.49 秒）。沙箱内异步线程唤醒阻塞，模拟单测及全量检查改在沙箱外运行通过；
未访问真实模型或供应商服务。检查日志保存在本次产物的 `check_host.log`。
完整数值、复现脚本和审计见
[报告](../../artifacts/comparisons/fold2_predictive_value_20261030/report.md)、
[机器结果](../../artifacts/comparisons/fold2_predictive_value_20261030/results.json)、
[CSV](../../artifacts/comparisons/fold2_predictive_value_20261030/metrics.csv)及
[可比性与成本](../../artifacts/comparisons/fold2_predictive_value_20261030/comparability.json)。

## 2026-10-30 Haiku Atlas 531d4676f6：前 23 个入库因子

按用户指定，从运行 `futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6`
依准入顺序冻结前 23 个因子，对应截至第 427 次评估的完整原子 trial 前缀。
该运行仍继续独立搜索；新评估截面保存到单独 workspace，不修改原运行或方法状态，
Test 结果不回传搜索。因子定义、身份、训练方向及单因子裁剪边界全部在 Test 前固定。

沿用 Kernel 原生 5m 八字段、all、fold2、seed 42 和标准准入规则。
Linear 与 LightGBM 使用 2019–2023 Train+Val 的 5,307,120 条样本拟合，Test 为
2024–2025；全局 P1–P99 预处理、品种去均值。LightGBM 为 50 rounds、15 leaves、
min_data_in_leaf=100、learning_rate=0.05，未调参。

下表仅列品种 √成交额加权 IC；单因子均值是 23 个单因子指标的算术均值，
不是等权因子组合预测。

| Test 结果 | 加权 Pearson IC | 加权 Spearman IC | 组合覆盖率 |
| --- | ---: | ---: | ---: |
| 单因子指标均值 | 0.009236 | 0.008091 | — |
| Linear | 0.024160 | 0.025635 | 99.9789% |
| LightGBM | 0.018036 | 0.026363 | 99.9789% |

单因子加权 Pearson IC 为正 23/23，Spearman 为正 19/23。23 个单因子 Test、
两模型及 253 对 Val 库内相似度均完成；核对冻结库、模型和 OOS 的因子身份与顺序一致，
前缀 trial 内容与复制时原产物逐字节一致。离线脚本 Ruff 与格式检查通过；
项目检查通过 1116 项测试，6 项已知 AgentScope 阻塞测试排除，未访问模型或供应商服务。
详见[加权指标报告](../../artifacts/comparisons/haiku55_fold2_531d4676f6_first23_20261030/summary.md)、
[机器结果](../../artifacts/comparisons/haiku55_fold2_531d4676f6_first23_20261030/summary.json)
及[冻结清单](../../artifacts/comparisons/haiku55_fold2_531d4676f6_first23_20261030/manifest.json)。

## 2026-10-30 MCTS-LLM + GPT6-luna Fold2：1000 次结果填表

运行 `futures_kernel_5m_full-all-fold2-mcts_llm-42-35c026f3fe` 已冻结，检查点为
1000/1000、无待提交候选、入库 7 个因子。使用 Kernel 八字段、all、fold2、seed 42，
模型为 `gpt-6-luna`；原配置、单因子 OOS、两模型 Test 及 21 对 Val 相似度均完整。
原运行保存在独立 workspace，核对后复用已有评估，不重新搜索或调用模型服务。

原 LightGBM 为 50 轮、min_data_in_leaf=100、无深度上限。按当前 Fold2 总表固定口径，
在单独导入的冻结库上重新拟合并测试 Linear 与 LightGBM，保持同一 7 个因子、训练方向、
Train+Val 全局 P1–P99 裁剪和品种去均值，仅将 LightGBM 的 min_data_in_leaf 改为 2000、
max_depth 改为 5；其余仍为 50 轮、15 叶、学习率 0.05。Train+Val 2019–2023 的有效
拟合样本为 5,308,100 条，Test 为 2024–2025。不根据 Test 选择因子或参数。

| Test 结果 | 加权 Pearson IC | 加权 Spearman IC |
| --- | ---: | ---: |
| 单因子指标均值 | 0.007514 | 0.005179 |
| Linear | 0.010630 | 0.017943 |
| LightGBM（总表参数） | 0.011676 | 0.015559 |

以上均为品种 √成交额加权 IC；单因子 Pearson 为正 7/7，中位数 0.007027。
两模型预测覆盖 2,558,827/2,558,829 行，约 99.99992%；Linear 与原保存结果一致。
原参数 LightGBM 的加权 Pearson 为 0.013135，原产物保留，主表使用统一参数结果。
21 对 Val 相似度的平均绝对 Spearman 为 0.176024，最大 0.644473。

已核对冻结库校验值、配置、各产物的因子身份和顺序、模型配置与实际树参数、预测行数，
原 run、冻结库、模型和评估文件保持不变；根目录总表已加入结果及相似度两行，
组合指标链接到统一参数报告。见[实验总表](../../experiment_overview.md)、
[机器结果与原参数对照](../../artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/completed_1000_summary.json)
及[复现脚本](../../artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/evaluate_and_publish.py)。
脚本 Ruff 与格式检查通过；项目检查通过 Ruff、格式、算子目录及 1116 项测试，
6 项已知 AgentScope 阻塞测试排除。检查记录见
[publish_checks.log](../../artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/publish_checks.log)。

## 2026-10-30 Fold2：九个冻结库的预测误差增量复评

按用户要求，将 MCTS-LLM + GPT6-luna 的 `35c026f3fe` 1000 次运行补入
“Fold2 预测价值补充：共同样本与误差下降”。复用该运行已保存的统一参数模型
（LightGBM 50 轮、min_data_in_leaf=2000、max_depth=5），不重新训练或搜索。
读取原八个冻结库的保存预测，加上本次七因子库的两组预测，统一重算九库十八模型。

加入后共同预测样本仍为 2,408,926/2,558,829，覆盖 94.1417%，原有八库的
误差下降和加权 Pearson 经逐项核对保持一致。基线仍是全部 5,308,104 条合格
Train+Val 原始目标的全局均值 0.000038199635773434764，Test 原始目标不裁剪。
误差下降为逐行等权的 `1 - MSE_model / MSE_baseline`；IC 仍按品种 √成交额加权。
区间使用相同的 20 交易日移动区块、2000 次配对重采样和 seed 20261030。

| Luna MCTS 预测器 | 误差下降率 | 95% 时间区块区间 | 加权 Pearson IC | 加权 Spearman IC |
| --- | ---: | --- | ---: | ---: |
| Linear | 0.01907% | [-0.00377%, +0.04453%] | 0.010625 | 0.017924 |
| LightGBM | 0.04301% | [+0.01472%, +0.07729%] | 0.011648 | 0.015511 |

共同样本上基线 MSE 为 0.00003509033593626402，Linear 和 LightGBM 的 MSE
分别为 0.00003508364332641947 与 0.00003507524513640336。Luna Atlas−MCTS 的
LightGBM 误差改善差为 +0.00904 个百分点，95% 配对区间为
[-0.02956, +0.03870] 个百分点；两方法差值仍跨零，不能宣称稳定优胜。
这仍是既有 Test 后的固定模型历史诊断，不是新盲测。

单独保存新增预测、重采样日误差、九组完整结果与可比性元数据，原八库报告和
模型文件保留。新增模型在完整自身可预测样本上的全部 IC 和预测行数先复现原保存值，
随后才计算共同样本指标；后者与主表全覆盖指标的细微差异来自评分样本不同。
已更新[总表补充节](../../experiment_overview.md#fold2-预测价值补充共同样本与误差下降)，
详见[九库报告](../../artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/predictive_value/report.md)、
[CSV](../../artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/predictive_value/metrics.csv)及
[复现脚本](../../artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/predictive_value/evaluate.py)。
脚本 Ruff／格式检查及原模型 IC 复现、九库样本对齐、既有结果一致性检查通过；
项目检查通过 Ruff、格式、算子目录和 1116 项测试，6 项已知 AgentScope 阻塞测试排除，
见[检查日志](../../artifacts/comparisons/mcts_llm_gpt6_luna_fold2_20261030/predictive_value/checks.log)。

## 2026-10-31 Haiku Atlas 531d4676f6：前500次评估

按用户要求，读取运行 `futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6`
已提交的第 1–500 次原子评估，冻结其中全部 25 个入库因子为独立的 `-prefix500` 截面。
原搜索总预算为 1000，截面不混入第 501 次及之后的候选，也不按 Test 筛选因子。
源运行独立保留，评估结果不写入其方法状态；原子 trial 内容逐字节核对一致。

本轮为 Haiku 5.5、Kernel 八字段、all、fold2、seed 42。组合模型采用总表统一的
Train+Val 全局 P1–P99 裁剪、品种去均值；LightGBM 为 50 轮、15 叶、
min_data_in_leaf=2000、max_depth=5、学习率 0.05。2019–2023 的模型有效拟合
样本为 5,307,120 条，Test 为 2024–2025。

| 完整 Test 结果 | 加权 Pearson IC | 加权 Spearman IC | 组合覆盖率 |
| --- | ---: | ---: | ---: |
| 单因子指标均值 | 0.009578 | 0.007932 | — |
| Linear | 0.024550 | 0.024945 | 99.9789% |
| LightGBM | 0.019086 | 0.026477 | 99.9789% |

单因子加权 Pearson 为正 25/25，中位数 0.009528；Spearman 为正 21/25。
Val 的 300 对库内相似度均完成，平均绝对 Spearman 为 0.101866、最大 0.695464。
已将本轮 500 次结果和相似度加入总表，并通过运行后缀与旧 Haiku `ef96a68059` 区分。

预测价值补充复用原九库的保存预测，加入本轮两模型后统一重算十库二十模型。
新共同样本为 2,408,575/2,558,829，覆盖 94.1280%，比原九库少 351 条；
原表各行指标、配对差值和说明均使用新共同样本重算，旧报告及模型保留。
基线仍是 5,308,104 条合格 Train+Val 原始目标的均值 0.000038199635773434764，
Test 原始目标不裁剪，误差下降为逐行等权的 `1 - MSE_model / MSE_baseline`。
区间沿用 20 交易日移动区块、2000 次配对重采样和 seed 20261030。

| 本轮共同样本结果 | 误差下降率 | 95% 时间区块区间 | 加权 Pearson IC | 加权 Spearman IC |
| --- | ---: | --- | ---: | ---: |
| Linear | -0.07812% | [-0.19150%, +0.01936%] | 0.024556 | 0.024950 |
| LightGBM | +0.05985% | [-0.01709%, +0.13369%] | 0.019087 | 0.026478 |

Linear 的负误差下降表示其 MSE 比均值基线更大；相关性较高不保证误差更低。
两模型区间均跨零，本次仍属单 fold／seed 的阶段性历史诊断。
补充表新增尝试次数列，明确区分本轮 500 次与既有各组 1000 次；另保存与原轮
Haiku Atlas、AlphaPROBE、MCTS 各自 500 次的配对比较。本轮 500 次的准确请求用量
没有从已经继续运行的后续方法状态推算，成本表仍仅列既有 1000 次运行。

冻结库、模型与 OOS 的 25 个因子身份、顺序和配置已核对；预测价值复评先复现模型
已保存的完整 Test IC 和预测行数，再对齐共同样本。脚本 Ruff／格式检查通过，
项目检查通过 1116 项测试，6 项已知 AgentScope 阻塞测试排除。
见[主表及补充表](../../experiment_overview.md)、
[冻结清单](../../artifacts/comparisons/haiku55_531d4676f6_prefix500_20261031/manifest.json)、
[共同样本报告](../../artifacts/comparisons/haiku55_531d4676f6_prefix500_20261031/predictive_value/report.md)、
[CSV](../../artifacts/comparisons/haiku55_531d4676f6_prefix500_20261031/predictive_value/metrics.csv)及
[检查日志](../../artifacts/comparisons/haiku55_531d4676f6_prefix500_20261031/checks.log)。

## 2026-10-31 Fold2：品种加权预测误差

用户要求将“Fold2 预测价值补充”的逐行等权误差改为品种加权误差。复用十个冻结库、
二十个保存模型的预测，在同一批 2,408,575 条共同 Test 样本上重新计算误差和置信区间。
IC、Train+Val 全局目标均值基线、Test 原始目标与模型预测沿用上一轮复评；
原逐行等权报告保留，不覆盖模型拟合、测试或搜索产物。

权重取 IC 使用的完整 Test 区间各品种有效成交额之和的平方根。
先计算每个品种的 MSE，再求 `WMSE = Σ(w_g × MSE_g) / Σw_g`。
模型与基线使用同一权重，误差下降率为 `1 - WMSE_model / WMSE_baseline`，
不是各品种相对误差下降的加权平均，也不额外除以品种目标方差。
实现扩展公共 `prediction_error_summary` 的可选逐行权重；每行设置为 `w_g / n_g`，
其加权 MSE 与先按品种计算再汇总逐项核对一致。不传权重时保留原计算行为。

时间区块继续采用 20 个交易日、2000 次配对重采样和 seed 20261030。
每次按抽中的品种行数计算各品种 MSE，完整 Test 品种权重保持固定；
没有观测的品种由模型和基线同时排除，剩余品种再归一化。
这一步没有把品种内常量预测当作不可计算，也没有按 Test 选择因子或校准预测。

| 本轮 Haiku Atlas 500 次 | 品种加权误差下降率 | 95% 时间区块区间 | 加权 Pearson IC |
| --- | ---: | --- | ---: |
| Linear | -0.07346% | [-0.25828%, +0.16797%] | 0.024556 |
| LightGBM | +0.05651% | [-0.03474%, +0.11528%] | 0.019087 |

加权基线 MSE 为 0.00004034576465718569，Linear 和 LightGBM 的加权 MSE
分别为 0.00004037540221917774、0.00004032296510176161。加权后 Linear 仍为负，
不能把原先负改善完全归因于汇总权重。误差分解显示，Linear 的品种内拟合净改善项
为 +0.07650%，品种均值误差增加项为 0.14996%，相减为 -0.07346%；
这是已保存预测误差的代数分解，不是使用 Test 均值校准后得到的新模型分数。
两个模型的时间区块区间仍跨零。

39 项预测误差测试覆盖旧行为、品种样本数不同的手算、加权损失比与平均改善率的差异、
权重缩放、区块可加性、零权重、非法权重、溢出和零基线误差。
另用两个品种两个日期的手算验证重采样，包括某品种完全未被抽中的情形。
二十组结果的共同样本、IC、加权损失比及七行展示和报告链接均已核对。
见[实验总表](../../experiment_overview.md)、
[加权报告](../../artifacts/comparisons/fold2_product_weighted_error_20261031/report.md)、
[CSV](../../artifacts/comparisons/fold2_product_weighted_error_20261031/metrics.csv)、
[固定品种权重](../../artifacts/comparisons/fold2_product_weighted_error_20261031/product_weights.csv)及
[复现脚本](../../artifacts/comparisons/fold2_product_weighted_error_20261031/evaluate.py)。

## 2026-10-31 DeepSeek＋AlphaPROBE：Fold2 完整 1000 次结果

运行 `futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab` 已完成 1000 次提交并冻结，
入库 31 个因子。使用 Kernel 八字段、all、fold2、seed42；Train 2019–2021、Val
2022–2023、Test 2024–2025。搜索模型为 deepseek-flash，embedding 为
Qwen3-Embedding-0.6B；开放 futures_cta 参考定义，不预置初始因子。

31 个冻结因子的 Test 评估全部成功。单因子品种 √成交额加权 Pearson 均值／中位数为
0.004924／0.004262，30/31 为正（96.77%），加权 Spearman 均值为 0.002026。
保存的 Linear／LightGBM Test 加权 Pearson 为 0.013744／0.014567，均覆盖全部
2,558,829 条合格 Test 样本。预处理使用 Train+Val 全局 P1–P99 裁剪及品种去均值；
LightGBM 为 50 轮、15 叶、min_data_in_leaf=2000、max_depth=5，与 Fold2 总表一致。
本次发布直接复用已有 OOS、模型和相似度产物，没有重启搜索或按 Test 选择因子。

Val 465 对因子的平均／中位绝对 Spearman 为 0.155682／0.104627，P90 为
0.407521，最大值为 0.691974，≥0.7 的因子对为零。

主表、库内相似度表与原隔离工作区结果已核对并补入根目录总表。
预测价值补充复用保存模型生成预测，并逐项复现原模型 Test IC；已有模型、冻结库、
OOS 和模型测试文件保持原样。新模型在原十库的 2,408,575 条共同样本上全部有预测，
既有共同样本和十库指标不变。固定 Test 品种 √成交额权重一致；旧权重表额外列出的
五个品种均为零权重且没有共同样本，按缺失权重为零核对。

共同样本上的 Linear／LightGBM 加权 Pearson 为 0.013742／0.014569，
品种加权误差下降率为 +0.00212%／+0.02035%。95% 的 20 交易日区块重采样区间分别为
[-0.04931%, +0.05116%]／[-0.00984%, +0.06537%]，均跨零。
基准仍是 Train+Val 全局原始目标均值，沿用 2000 次重采样和 seed 20261030。
此次补表没有计算逐因子加入后的组合 IC 增量，也不将均值基准误差下降解释为独立信息量。

Ruff、格式与算子目录检查通过；1127 项测试通过，沿用此前口径排除六项已知挂起的
AgentScope 测试。主表、相似度、共同样本指标及本地报告链接均核对通过。
见[实验总表](../../experiment_overview.md)、
[完整运行报告](../../artifacts/comparisons/alphaprobe_deepseek_fold2_20261030/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab/report.md)、
[模型报告](../../artifacts/comparisons/alphaprobe_deepseek_fold2_20261030/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaprobe-42-9cbc979fab/model/report.md)、
[共同样本复评](../../artifacts/comparisons/alphaprobe_deepseek_fold2_20261030/predictive_value/report.md)、
[复现脚本](../../artifacts/comparisons/alphaprobe_deepseek_fold2_20261030/publish_completed.py)及
[检查日志](../../artifacts/comparisons/alphaprobe_deepseek_fold2_20261030/checks.log)。

## 2026-10-31 Haiku Atlas：Explorer 格式修复耗尽后的异步恢复

运行 `futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6` 在 655 次实际评估、
49 个入库因子时停止。补位 Explorer 的计划编号为 182，40 条已保存回复中 39 条
因 JSON 解析失败，另一条只有一个提案。错误集中在数组中的提案对象多写一个右花括号，
不是 HTTP 限流或候选评估预算耗尽。其启动快照包含两个活动计划，但失败检查点中的
活动组已全部结束，原先仅处理 `unresolved` 的等待逻辑没有归档这段格式失败对话。

公共模型 JSON 解析只兼容解析器明确定位的连续多余右花括号，不补缺失内容，
原文和全部字段保持。异步补位对话耗尽后复用原等待与 `deferred_explorer` 归档路径：
有活动计划的新证据可等待时，先完成原计划再重新选题；无活动计划的探索仍显式停止。
不提高每次 40 轮上限、不伪造候选、不重置用量、不改配置或检查点格式。

离线加载该检查点验证：当前冻结配置匹配；40 条原文均能解析，其中 36 条包含两个
提案，但这不代表已通过引用、评分或方向校验。失败补位可在内存归档，原检查点字节、
已完成计数、用量和 RNG 不变，没有真实模型请求，未自动续跑市场实验。
合成回归覆盖格式兼容、字符串和公式保留、缺失或歧义 JSON 拒绝、补位耗尽后的
活动计划执行、等待状态恢复、新证据补位以及无活动计划时失败。
`scripts/check.ps1` 的 Ruff、格式、算子目录检查通过，1141 项测试通过；沿用此前口径
排除六项已知挂起的 AgentScope 测试。原始运行仍处于 failed，等待用户执行原续跑命令。
见[离线核对](../../artifacts/diagnostics/haiku_explorer_dialogue_20261031/audit.json)、
[核对脚本](../../artifacts/diagnostics/haiku_explorer_dialogue_20261031/audit.py)和
[检查日志](../../artifacts/diagnostics/haiku_explorer_dialogue_20261031/checks.log)。

## 2026-10-31 公共工具调用兼容：历史回放与修复

扫描 92 份历史检查点，按原运行合并扩容、恢复和修复副本后覆盖 84 个独立运行。
只统计仍保留的错误签名，完成后已丢弃的对话不作推断，不将样本数量解释为总体失败率。
参数问题包括 `library_search` 的 source 不合法、分页超限、`library_get` 缺少
factor_id，以及 MCTS 最终风险答复附带空 queries 被误判为查询。另有三个 MCTS 运行
在 JSON 字符串直接包含换行等空白时反复解析失败。

公共解析保留字符串内真实 LF、CR、Tab，其他原始控制字符、非法转义和截断继续拒绝。
200 条可回放的去重 JSON 失败样本中，修复前可解析 47 条，修复后 179 条，增加 132 条，
与改动前离线方案逐条一致。这仅是语法可读；字段、表达式、引用和评分仍由原阶段校验。
历史回放没有发送模型请求、执行候选评估或改写历史产物。

五个只读工具的名称与参数说明统一由 `session.py` 供各方法提示和错误反馈使用。
`SearchSession.query` 按实际函数签名校验参数，错误含工具名、原因和对应调用说明。
启用的 futures_cta 名称可作为 reference 的别名；关闭时不接受，library 不作含糊映射。
搜索上限仍为 20，超限提示分页。library_get 拒绝空或非文本 ID、trial:N 历史引用，
普通未知 ID 仍返回 null。错误与查询均不生成 trial 或消耗评估额度。

MCTS、AlphaPROBE、Analyst 和 Miner 的有效最终答复可附带空 queries，不创建查询轮，
也不额外请求模型。只有空列表或最终字段不合格的答复仍按原流程失败。
工具返回、配置、检查点结构、评估准入及 OOS 权限沿用原规则。
已有真实运行没有自动重启；新启动或续跑进程加载这些修改。

定向测试覆盖空白与转义原样保留、非法控制字符拒绝、参考库权限、工具签名和类型、
错误分页及 ID、各角色有效和无效的空查询答复、模型请求计数和评估预算。
`scripts/check.ps1` 的 Ruff、格式和算子目录检查通过；1206 项测试通过，沿用此前口径
排除六项已知挂起的 AgentScope 测试。新增兼容与权限回归均在上述通过项中。
见[历史审计](../../artifacts/diagnostics/tool_compatibility_20261031/summary.json)、
[应用后回放](../../artifacts/diagnostics/tool_compatibility_applied_20261031/replay.json)、
[回放脚本](../../artifacts/diagnostics/tool_compatibility_applied_20261031/replay.py)及
[检查日志](../../artifacts/diagnostics/tool_compatibility_applied_20261031/checks.log)。

## 2026-10-31 Haiku Atlas：局部研究不足转 breadth 与正常提前冻结

运行 `futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6` 在实际评估 960 次、
库版本 67 时停在计划 296。Explorer 处于 depth，无活动计划，已保存两次关于 churn
区域缺少会话起点、换月标志和远月字段等能力的 `unresolved`。旧逻辑只能等待活动计划，
没有从局部不足转向全图 breadth 的流程，因此抛出 ExplorerUnresolved 导致 failed。

V1 和 V1 Async 在 depth 报告不足且还有对话额度时，在原对话内转为 breadth，保留
原始回复并标记 redirected_to_breadth，当前输入与阶段模式同步更新。全局 Region、
温度、提案评分和请求计数不变，不重置 40 轮上限，也不添加任何字段、工具或诊断能力。
原 depth 报告不占 breadth 的一次澄清机会；继续不足才返回研究停止信号。

runner 将 ExplorerUnresolved 视为正常研究提前结束，保存检查点并冻结实际已有因子，
将 search_stop_reason 作为运行结果元数据写入 run.json 和报告；实际尝试数和剩余额度
如实显示。技术错误和非法格式耗尽仍为 failed，不隐藏异常，也不生成空公式 trial。
提前冻结不代表平台证明搜索空间已穷尽；其后沿原冻结流程独立评估，不反馈 Test 给搜索。

原失败检查点的只读核对验证：计划 296 可在内存从 depth 转为 breadth，两条原文保留，
已完成计数、RNG、用量及 40 轮额度不变，没有请求模型或修改运行文件，也未自动续跑。
合成回归覆盖串行及异步的即时和恢复切换、切换后中断、全图不足澄清、对话额度边界、
零因子及已有提交时正常冻结、元数据指纹、报告和独立 OOS，以及传输或格式错误仍失败。
`scripts/check.ps1` 的 Ruff、格式、算子目录检查通过，1220 项测试通过；沿用此前口径
排除六项已知挂起的 AgentScope 测试。原失败运行等待用户续跑，未改写原检查点或请求模型。
见[只读核对](../../artifacts/diagnostics/explorer_unresolved_20261031/audit.json)、
[核对脚本](../../artifacts/diagnostics/explorer_unresolved_20261031/audit.py)、
[定向测试](../../artifacts/diagnostics/explorer_unresolved_20261031/targeted_checks.log)及
[完整检查](../../artifacts/diagnostics/explorer_unresolved_20261031/checks.log)。
完整项目检查通过 Ruff、格式、算子目录及 1127 项测试，6 项已知 AgentScope 阻塞测试排除，
见[检查日志](../../artifacts/comparisons/fold2_product_weighted_error_20261031/checks.log)。

## 2026-10-31 Haiku Atlas 531d4676f6：1000 次完整评估

运行 `futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6` 已正常耗满 1000 次评估预算，
检查点 completed／attempts 均为 1000，冻结库 67 个因子，没有提前结束原因。
前 500 次冻结的 25 个成员及方向全部保留，后半程新增 42 个；原运行与旧截面独立保存。

单因子 Test 加权 Pearson 均值／中位数为 0.006839／0.008537，55／67 个为正 IC，
加权 Spearman 均值 0.007120。新增 42 个因子的加权 Pearson 均值为 0.005209。
Val 全部 2211 个无序因子对有效，绝对 Spearman 均值 0.093666、最大 0.695464，
没有达到 0.7 的因子对；本次复用已完成的冻结 OOS 与相似度结果。

自动收尾模型使用 LightGBM 50 轮、min_data_in_leaf=100、深度不限，原始组合 Test
加权 Pearson 为 Linear 0.023549／LightGBM 0.008019。为匹配总表既定口径，单独导入
同一冻结库，按 50 轮、min_data_in_leaf=2000、max_depth=5 复评；裁剪、品种去均值、
训练区间、seed 与表中设置一致。统一模型的 Test 加权 Pearson 为
Linear 0.023549／LightGBM 0.012112，预测覆盖率均为 99.9998%。
原配置、检查点、冻结库、保存模型、OOS、相似度及报告均逐字节保留；总表组合指标链接到
统一模型报告。重复调用命中保存模型，不加载行情或重新训练。

预测价值补充沿用此前十一库的 2,408,575 条共同 Test 样本，覆盖率 94.1280%；
本次两个模型全部覆盖这些样本，旧库结果与样本交集不变，总计十二库、二十四个模型。
常数基准仍为 5,308,104 条 Train+Val 原始目标的全局均值
`3.8199635773434764e-05`。先求品种 MSE，再用完整 Test 的固定 √成交额权重汇总。
Linear／LightGBM 的品种加权误差下降为 -0.15894%／-0.01015%；
20 交易日区块、2000 次重采样、seed 20261030 的 95% 区间分别为
[-0.37816%, +0.12490%]／[-0.16069%, +0.08041%]，均跨零。

同一共同样本上，500→1000 次的组合加权 Pearson 增量为
Linear -0.001000／LightGBM -0.006978；相对前 500 次模型的加权误差下降为
-0.08541%／-0.06670%。在这两种固定模型下，扩库未提高组合 IC。
这是整库扩充的历史 Test 诊断，没有计算逐因子加入的边际 IC，不能据此判定每个新增
因子均无独立信息，也不作为新的盲测。

Ruff、格式及算子目录检查通过，1220 项测试通过；沿用此前设置排除六项已知挂起的
AgentScope 测试。核对了原产物保留、模型缓存复用、原始指标复现、共同样本身份、
品种权重、旧截面分数，以及逐品种与逐行加权 MSE 的数值等价。
见[实验总表](../../experiment_overview.md)、
[统一模型报告](../../artifacts/comparisons/haiku55_531d4676f6_full1000_20261031/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-531d4676f6-uniform50-leaf2000-depth5/model/report.md)、
[共同样本复评](../../artifacts/comparisons/haiku55_531d4676f6_full1000_20261031/predictive_value/report.md)、
[数值结果](../../artifacts/comparisons/haiku55_531d4676f6_full1000_20261031/predictive_value/summary.json)、
[复评脚本](../../artifacts/comparisons/haiku55_531d4676f6_full1000_20261031/evaluate.py)、
[填表脚本](../../artifacts/comparisons/haiku55_531d4676f6_full1000_20261031/publish.py)及
[项目检查](../../artifacts/comparisons/haiku55_531d4676f6_full1000_20261031/checks.log)。

## 2026-10-31 Fold2：补齐前500次与在跑MCTS截面

仅补充 Fold2。已列的十个 1000 次完整运行现均有前 500 次对照，另加入仍在运行的
DeepSeek MCTS-LLM `79ad3e5e40` 的前 500 次截面。新增四行分别为 AlphaPROBE
`9cbc979fab`、AlphaPROBE `3656be6b77`、MCTS-LLM `35c026f3fe` 和 MCTS-LLM
`79ad3e5e40`；现有七个 500 次节点按需复用。主表与相似度表各有 21 行，继续按模型、
方法、独立运行和次数排列，Fold1 及待开展清单不变。

前 500 次按已提交的原子记录重建，包含全部当时已入库成员，保留定义、顺序、训练方向及
裁剪边界；新截面不可恢复搜索，不是重新进行 500 预算实验。前三个已完成父库的成员
前缀逐一核对；在跑父运行只读取前 500 条不再变动的记录，四份新截面与源记录逐字节一致。
原搜索、冻结库与历史模型保留，不发送真实语言模型请求，不向原运行回传 Test。

新组合模型均使用 Train+Val 全局 P1–P99、品种全拟合期去均值，以及既定的 LightGBM
50 轮、min_data_in_leaf=2000、max_depth=5、seed42。Luna AlphaPROBE 已有的六因子
单因子 OOS 和 15 对相似度直接复用，另存统一组合模型；Luna Atlas 500 次的 22 因子
统一模型直接复用，仅导出保存预测。其余新截面完成单因子、组合模型及 Val 全库相似度。

以下组合 IC 沿用主表的各自有效 Test 样本，均为品种 √成交额加权 Pearson：

| 研究模型 / 方法 | 前500次因子数 | 单因子 IC 均值 | 正 IC | Linear IC | LightGBM IC |
| --- | ---: | ---: | --- | ---: | ---: |
| DeepSeek / AlphaPROBE（9cbc979fab） | 22 | 0.004916 | 21/22 | 0.012582 | 0.015618 |
| GPT6-luna / AlphaPROBE（3656be6b77） | 6 | 0.006527 | 6/6 | 0.011088 | 0.013679 |
| GPT6-luna / MCTS-LLM（35c026f3fe） | 3 | 0.005712 | 3/3 | 0.009186 | 0.008372 |
| DeepSeek / MCTS-LLM（79ad3e5e40） | 26 | 0.002410 | 19/26 | 0.007819 | 0.010176 |

四库 Val 有效因子对分别为 231／15／3／325，平均绝对 Spearman 为
0.201151／0.235274／0.249300／0.165372，均无达到 0.7 的因子对。
初次核对在跑 DeepSeek MCTS 为 728 次、32 因子，验收时为 running、734 次、33 因子；
独立 500 截面始终为 26 因子，原运行未被暂停。

既有预测价值比较补齐全部 500 节点，并加入在跑 DeepSeek MCTS 500 截面，共十七库、
三十四个保存模型。Luna MCTS 500 次只有 2,407,227 行可预测，低于旧共同集的
2,408,575 行；因此重新取全部模型共同交集为 2,406,963 行，覆盖 94.0650%，
较旧共同集减少 1,612 行。整张预测价值表的 IC、加权误差与区块区间均在新交集重算，
主表各自有效样本的指标与历史报告保留。既有 Haiku 三个 500 次的保存预测也已列入表中。

均值基准仍来自全部 5,308,104 条 Train+Val 原始目标，为 `3.8199635773434764e-05`；
目标不裁剪，先计算品种 MSE，再以完整 Test 固定 √成交额权重汇总。20 交易日整截面
区块、2000 次配对重采样、seed 20261030；各次重算品种样本内 MSE，无样本品种由两侧
同时排除。500→1000 次的组合 IC 增量和相对 500 模型误差下降另列在完整报告；这仍是
历史 Test 诊断，不是逐因子边际信息或新的盲测。

Ruff、格式及算子目录检查通过，1220 项测试通过，沿用此前设置排除六项已知挂起的
AgentScope 测试。核对冻结成员与方向、原子记录、模型保存分数复现、共同样本逐行身份、
品种权重、逐品种与逐行加权 MSE 等价，以及原有主表／相似度行与 Fold1 内容保留。
见[实验总表](../../experiment_overview.md)、
[评估清单](../../artifacts/comparisons/fold2_missing_prefix500_20261031/manifest.json)、
[完整复评报告](../../artifacts/comparisons/fold2_missing_prefix500_20261031/report.md)、
[结果 JSON](../../artifacts/comparisons/fold2_missing_prefix500_20261031/results.json)、
[CSV](../../artifacts/comparisons/fold2_missing_prefix500_20261031/metrics.csv)、
[评估脚本](../../artifacts/comparisons/fold2_missing_prefix500_20261031/evaluate.py)、
[填表脚本](../../artifacts/comparisons/fold2_missing_prefix500_20261031/publish.py)和
[检查日志](../../artifacts/comparisons/fold2_missing_prefix500_20261031/checks.log)。

## 2026-11-01 DeepSeek MCTS-LLM：1000次完整评估

运行 `futures_kernel_5m_full-all-fold2-mcts_llm-42-79ad3e5e40` 已正常完成 1000 次评估，
检查点 completed／attempts 均为 1000，冻结库 45 个因子，无提前结束原因。
前 500 次的 26 个成员及其训练方向、裁剪边界全部保留，新增 19 个；前 26 个 OOS
结果在既定浮点容差内逐项复现。新增部分的 Test 加权 Pearson 均值 0.004311，17/19 为正。

完整库单因子 Test 加权 Pearson 均值／中位数为 0.003213／0.003253，36/45 为正，
加权 Spearman 均值 0.000289。Val 的 990 个无序因子对全部有效，绝对 Spearman
均值 0.178465、最大 0.693037，没有达到 0.7 的配对；复用原冻结 OOS 和相似度报告。

自动收尾保存的 LightGBM 使用 50 轮、min_data_in_leaf=100、深度不限，Test 加权
Pearson 为 0.015070。导入同一冻结库，按总表既定的 50 轮、min_data_in_leaf=2000、
max_depth=5 另存统一模型；全局 P1–P99、品种全拟合期去均值、seed42 及 Train+Val
区间与既有表格一致。新模型训练 5,307,082 行，Test 预测 2,558,265/2,558,829 行，
覆盖 99.9780%；各自有效样本的加权 Pearson 为 Linear 0.009376／LightGBM 0.015224。
Linear 与原保存分数复现一致。原运行配置、检查点、冻结库、模型、OOS、相似度及报告
逐字节保留；新模型重复调用命中缓存，不加载行情或重新训练，没有发起语言模型请求。

预测价值比较纳入完整 1000 次后共十八库、三十六个模型。新增模型覆盖既有全部
2,406,963 条共同 Test 样本，交集及 94.0650% 覆盖率不变，其余十七库的保存分数直接
复用。共同集含 85 个品种，使用完整 Test 固定 √成交额权重；完整权重中不在共同集的
品种不参与任一模型的共同样本汇总。常数基准仍为全部 5,308,104 条 Train+Val 原始
目标的全局均值 `3.8199635773434764e-05`，模型和基准使用同一品种加权 MSE。

| 预测器 | 前500次加权 Pearson | 完整1000次加权 Pearson | IC 增量 | 相对均值基准误差下降 |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.007931 | 0.009194 | +0.001263 | -0.02589% |
| LightGBM | 0.010051 | 0.015222 | +0.005171 | +0.00836% |

相对均值基准的误差下降 95% 区间分别为 [-0.08667%, +0.02723%] 与
[-0.01774%, +0.07564%]，均跨零。相对前 500 次模型的加权误差下降分别为
+0.000085% 与 +0.008352%；配对区间分别为 [-0.04937%, +0.02631%] 与
[+0.00121%, +0.03678%]。区间沿用 20 交易日整截面区块、2000 次、seed20261030，
各次重算品种内 MSE，固定品种权重。组合相关性提升是这两种固定模型下的整库增量，
没有计算逐因子边际 IC，也不将其解释为独立信息数量或新的盲测。

主表和相似度表的 500／1000 次结果现相邻，组合分数链接到统一模型报告；预测价值表
补入完整结果并链接十八库的合并报告和 CSV，更新原“仍在搜索”说明。Fold1、待开展清单
及其他运行的主表／相似度数据保留。Ruff、格式和算子目录检查通过，1220 项测试通过，
沿用此前设置排除六项已知挂起的 AgentScope 测试；另核对冻结身份、模型分数复现、
原文件保留、共同样本逐行身份、品种权重、旧截面损失与区间，以及 MSE 两种汇总的等价。
见[实验总表](../../experiment_overview.md)、
[统一模型报告](../../artifacts/comparisons/mcts_llm_deepseek_79ad3e5e40_full1000_20261101/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-mcts_llm-42-79ad3e5e40-uniform50-leaf2000-depth5/model/report.md)、
[十八库复评报告](../../artifacts/comparisons/mcts_llm_deepseek_79ad3e5e40_full1000_20261101/predictive_value/report.md)、
[本次结果](../../artifacts/comparisons/mcts_llm_deepseek_79ad3e5e40_full1000_20261101/predictive_value/summary.json)、
[合并 CSV](../../artifacts/comparisons/mcts_llm_deepseek_79ad3e5e40_full1000_20261101/predictive_value/metrics.csv)、
[评估脚本](../../artifacts/comparisons/mcts_llm_deepseek_79ad3e5e40_full1000_20261101/evaluate.py)、
[填表脚本](../../artifacts/comparisons/mcts_llm_deepseek_79ad3e5e40_full1000_20261101/publish.py)及
[检查日志](../../artifacts/comparisons/mcts_llm_deepseek_79ad3e5e40_full1000_20261101/checks.log)。

## 2026-11-02 AlphaSchema：真实试跑与 Fold2 Luna 比较配置

真实试跑为 `futures_kernel_5m_full-all-fold1-alphaschema-42-ac65e95227`，使用
`gpt-6-luna`、Kernel 八字段、all、fold1、seed 42，16 次候选预算。数据快照为
`73db06c2d7223ce5829b8d3d9846a27b35e59e0d621a11a4a94d99dcc6ec0e87`。
通过原 `atlas run` 完成搜索与默认收尾，没有替代模型响应或放宽统一准入。

16 次提交中，入库 1 个、行为重复 1 个、质量阈值拒绝 5 个、生成／编译失败 9 个。
第 3 次的 20 周期候选入库，第 4 次的 80 周期候选与其 Val 绝对 Spearman 为
0.9942764761，高于统一 0.7 门槛，因此被拒绝。9 个失败包含 4 个复杂度超限表达式，
以及 5 个经有界修复后仍未实现的语义计划；后者包括换手率、交易时段参考和最近事件状态等
能力不足的模型报告，也包括模型认为震荡器定义不明确的拒答，不把这些模型判断视为
已证明 DSL 不可能实现。失败正常计费并保留原响应，不补造奖励。

方法保存 11 个计划记录：3 个完整且有奖励、7 个失败、1 个预算截断；共 20 次真实模型请求，
197,444 prompt tokens、11,933 completion tokens，未知用量为 0。搜索耗时约 193 秒。
16 次仍处于覆盖冷启动，没有在真实行情上触发奖励代理；代理训练、选择与变异由已有
合成数据／模拟模型测试覆盖。这次试跑验证链路，不作为搜索效果或方法排名证据。

冻结成员、原子 trial、单因子 OOS、两个组合模型和相似度文件的因子身份／顺序核对一致。
单因子 Test 成功，Linear 和 LightGBM 均完成 Train+Val 拟合与独立 Test；库内只有一个
成员，Val 配对数为 0，相关性汇总未定义。该试跑下游模型使用运行时默认配置，
不作为下述 Fold2 统一参数的对照结果。重复执行冻结运行的 `atlas resume` 命中已有结果；
检查点、冻结库、模型拟合、OOS、模型 Test 和相似度文件逐字节不变，没有新增试验或模型请求。
自动填表已验证，16 次试跑结果只保留在本记录和运行报告中，不放入正式预算结果表。
见[运行报告](../../artifacts/runs/futures_kernel_5m_full-all-fold1-alphaschema-42-ac65e95227/report.md)
及[组合模型报告](../../artifacts/runs/futures_kernel_5m_full-all-fold1-alphaschema-42-ac65e95227/model/report.md)。

另用合成数据和模拟响应定向验证 AlphaSchema → SearchSession → FactorLibrary → 原子记录：
`$x` 首次合格入库，再次提交判表达式重复，`$x * 2` 和 `NEG($x)` 判行为重复；
4 次全部计费，持久化读回仍只有 1 个成员。真实试跑没有触发精确表达式重复，
该分支的证据来自上述定向验证和既有测试。

按用户要求，正式比较配置保存在
`artifacts/comparisons/alphaschema_luna_fold2_20261102/workspace/configs/`；复用已有
`data/futures_kernel_5m_full` 快照，不改主工作区模型配置。启动入口为同目录上一级的
[run.py](../../artifacts/comparisons/alphaschema_luna_fold2_20261102/run.py)，调用公共
`runner.run`、`finish_experiment`，全部完成后用既有汇总函数同步主总表；可传已有运行目录
沿检查点恢复。预算固定 1000，seed 42、all、fold2，显式字段顺序为 `adj_close`、
`adj_open`、`adj_high`、`adj_low`、`adj_vwap`、`volume`、`amount`、`open_interest`。

与总表 Luna 组的 AlphaAtlas、AlphaPROBE、MCTS-LLM 核对：数据快照、资产配置、八字段、
fold2、目标、窗口 panel、模型及采样温度、裁剪、覆盖与 IC 门槛、相关去重及表达式复杂度
相同。下游模型配置直接取自总表 Luna 36 因子统一评估：Train+Val 全局 P1–P99，品种去均值；
LightGBM 50 轮、15 叶、min_data_in_leaf=2000、max_depth=5，其他参数也逐项相同。

已知搜索信息差异是共享诊断由历史 `icir_turnover_v1` 扩展为 `icir_turnover_lag1_v2`，
新增原符号训练期 RankIC、RankICIR 和滞后一 Bar RankIC，供 AlphaSchema 的作者代码奖励使用；
当前公共工具向所有方法开放，但历史对照没有这些新增反馈。准入和 Test 评分口径不变，
因此属于统一数据／评价下的方法适配比较，不能声称搜索反馈完全一致或将结果差异仅归因于
选择算法。原股票语义在期货八字段上的不可实现或生成失败也保留为方法成本。

完整检查 `pwsh -File scripts/check.ps1` 通过，1,266 项测试全部通过；独立入口另通过
Ruff／格式及配置一致性检查。随后后台启动正式运行
`futures_kernel_5m_full-all-fold2-alphaschema-42-7ce881f635`，启动进程 PID 为 2991637。
核对时已提交 3/1000 次并入库 1 个，状态仍为 running，尚无正式 Fold2 OOS 结论。
运行规格、检查点与因子库位于独立 workspace，完成后自动按上述参数评估并同步主总表。
见[正式运行配置](../../artifacts/comparisons/alphaschema_luna_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-7ce881f635/run.json)
和[后台日志](../../artifacts/comparisons/alphaschema_luna_fold2_20261102/run.log)。

## 2026-11-02 AlphaSchema：前500次截面评估

从仍在运行的 `futures_kernel_5m_full-all-fold2-alphaschema-42-7ce881f635` 读取前 500 个
不可变原子 trial，重建独立冻结截面 `7ce881f635-prefix500`。这是同一 1000 次预算运行的
前 500 次提交结果，不是重新启动的 500 次搜索。原搜索继续运行，Test 结果不回传方法。
复现入口为 [prepare_prefix500.py](../../artifacts/comparisons/alphaschema_luna_fold2_20261102/prepare_prefix500.py)，
准备后向原 `run.py` 传入截面目录，调用公共 `finish_experiment` 和总表发布函数。

第 500 次提交后入库 13 个因子，对应 trial 为 3、33、71、74、82、83、179、234、235、
257、408、460、499。其余提交为质量阈值拒绝 228、生成／编译失败 214、覆盖不足 23、
常数因子 15、行为重复 6、训练指标未定义 1。500 次记录逐字节核对；冻结库保留原始定义、
入库顺序、训练方向和裁剪边界，全部 13 个成员进入 OOS 和组合模型，不按 Test 筛选。
截面设为不可恢复，不复制第 500 次之后的检查点或模型用量；第 500 次的精确方法状态不可得。
报告在缺少方法状态时不再显示虚构的零计划数，已有合成测试覆盖这一情况。

沿用上节已核对的 Fold2、八字段、seed 42、快照和统一下游配置；LightGBM 为
50 轮、15 叶、min_data_in_leaf=2000、max_depth=5。Train+Val 拟合行数 5,067,777。
模型保存后才加载 Test；Linear 和 LightGBM 均预测 2,411,050 / 2,558,829 行，覆盖率
94.224741%。覆盖规则与其他方法一致，但此处不是总表预测价值补充表的共同样本复评。
历史搜索诊断差异仍按上节披露，不把当前结果视为搜索反馈完全相同的受控比较。

| 指标 | 前 500 次截面结果 |
| --- | ---: |
| 入库因子数 | 13 |
| Test 单因子加权 Pearson 均值 / 中位数 | 0.007785 / 0.006986 |
| Test 正 IC 因子 | 13 / 13 |
| Test 单因子加权 Spearman 均值 | 0.009032 |
| Linear Test 加权 Pearson | 0.011047 |
| LightGBM Test 加权 Pearson | 0.013835 |
| Val 有效 / 总配对数 | 78 / 78 |
| Val 绝对 Spearman 均值 / 中位数 | 0.266841 / 0.246519 |
| Val 绝对 Spearman P90 / 最大值 | 0.557762 / 0.689871 |
| Val 绝对 Spearman ≥ 0.7 的配对 | 0 |

单因子、保存模型及相似度文件的 13 项身份与顺序一致。重复 `finish_experiment` 命中已有
结果，运行配置、冻结库、模型拟合、OOS、模型 Test 和相似度 JSON 逐字节不变。
模型来源如实记录 `source_matches_search=false`：搜索启动后工作区源码有变动；
冻结定义、依赖身份与配置校验均通过，没有改写原搜索来源指纹或绕过冻结校验。
复核时原运行已到 853/1000 次，仍为 running。结果已写入主总表 Fold2 结果及相似度两表。

本次相关代码与截面准备脚本通过 Ruff 和格式检查；AlphaSchema、实验收尾、报告测试合计
66 项通过、3 项失败。3 项失败均为公共工具测试仍断言内置算子数为 60，当前工作区新增后
实际为 66；AlphaSchema 和实验收尾测试通过。执行 `pwsh -File scripts/check.ps1` 时，
全量检查被其他在改文件的格式问题阻断（10 个文件）；本次不记为整仓检查通过。
完整日志见 [评估日志](../../artifacts/comparisons/alphaschema_luna_fold2_20261102/prefix500.log)、
[相关测试](../../artifacts/comparisons/alphaschema_luna_fold2_20261102/prefix500-targeted-tests.log)
及[全检日志](../../artifacts/comparisons/alphaschema_luna_fold2_20261102/prefix500-check.log)。

[截面运行报告](../../artifacts/comparisons/alphaschema_luna_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-7ce881f635-prefix500/report.md)、
[组合模型报告](../../artifacts/comparisons/alphaschema_luna_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-7ce881f635-prefix500/model/report.md)。

## 2026-11-02 AlphaSchema：Haiku 5.5 Fold2 后台运行

按用户要求启动独立运行 `futures_kernel_5m_full-all-fold2-alphaschema-42-41056bb4f8`，
使用 `claude-haiku-5-5`、1000 次候选预算、fold2、all、seed 42。模型通过既有
`http://127.0.0.1:8765/v1` Claude CLI 桥接调用，启动时 CLI 版本为 2.1.316，健康检查正常。
桥接的 temperature 和 max_tokens 参数不映射到 CLI 的实际采样设置；这与表中既有
Haiku 后端一致，但不声称与 Luna API 的采样行为完全相同。没有修改或续写 Luna 运行。

独立 workspace 从 Luna 比较配置准备，使用已有版本机制保存
`rq8_fold2_alphaschema_haiku55_v1`，源配置为 `rq8_fold2_v1`。
保持原八字段、60 个规范算子、同一 Kernel 快照、fold、准入、参考库、共享诊断和方法参数；
方法配置只修改 Chat 模型、地址和认证环境变量名。当前工作区新增的 6 个算子及 Zookeeper
派生字段不进入该版本。模型配置替换为表中统一的 Train+Val P1–P99、品种去均值，
LightGBM 50 轮、15 叶、min_data_in_leaf=2000、max_depth=5；其余参数与 Luna 比较配置
逐项相同。数据历史聚合和 vintage-PIT 限制沿用源版本，不重新采集或变换行情。

版本配置进入既有运行冻结校验，原 `run`／`resume`／`finish_experiment` 负责搜索、恢复和
收尾。1000 次搜索完成后自动执行冻结全库单因子 Test、Linear／LightGBM Test、Val 全库
相似度，并同步主 `experiment_overview.md`；结果不回传搜索。启动脚本可传已有运行目录恢复。

后台启动 PID 为 418613，独立 session、PPID 1；行情加载 6,189,366 行（包含 2018 年预热）。
本次没有修改方法搜索代码。启动脚本通过 Ruff 和格式检查；Claude 桥接 28 项模拟测试通过。
完整 `pwsh -File scripts/check.ps1` 通过：Ruff、格式、算子目录一致性和 1,291 项测试全部
通过（135.08 秒）。首批 2 个真实候选均成功计算并原子提交，因统一 IC 门槛拒绝，尚未入库；
核对时第 3 次候选正在生成。已核对运行配置、数据快照、60 个算子及下游参数，无特殊放宽。

[启动／恢复脚本](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/run.py)、
[固定版本](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/workspace/configs/versions/rq8_fold2_alphaschema_haiku55_v1.json)、
[运行配置](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-41056bb4f8/run.json)、
[后台日志](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/run.log)、
[完整检查](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/checks.log)、
[桥接测试](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/bridge-tests.log)。

## 2026-11-03 AlphaSchema Haiku：前500次截面评估

Haiku 运行 `futures_kernel_5m_full-all-fold2-alphaschema-42-41056bb4f8` 已完成 1000 次，
冻结 40 个成员，完整评估结果已自动进入总表。本次按用户要求，从其前 500 个原子 trial
重建独立、不可恢复的 `41056bb4f8-prefix500` 截面，使用这 500 次时已入库的全部 19 个因子。
这是原 1000 次预算搜索的中间截面，没有重新调用 Haiku 或按 Test 选择成员。

19 项入库发生在 trial 3、16、46、68、86、94、108、184、185、194、197、200、203、212、
260、358、370、493、500。其余提交为质量阈值拒绝 390、生成／编译失败 50、覆盖不足 20、
常数因子 11、行为重复 6、训练指标未定义 3、验证指标未定义 1。500 个原子记录逐字节复制
并核对；冻结成员、顺序、训练方向和裁剪边界与完整库的前 19 项完全一致。第 500 次的精确
方法状态和模型用量不可得，因此没有复制最终检查点，也没有将最终用量冒充前 500 次成本。

保留 `rq8_fold2_alphaschema_haiku55_v1` 的数据快照、八字段、60 个算子、fold2、seed 42、
规则及模型配置。通过原 `finish_experiment`，用 19 列、5,307,581 行 Train+Val 数据重新
拟合 Linear 和 LightGBM，保存模型后再计算 Test。组合预处理与总表一致，LightGBM 为
50 轮、15 叶、min_data_in_leaf=2000、max_depth=5；没有从 40 因子的保存模型截取系数。
两个模型均预测 2,558,513 / 2,558,829 条 Test 记录，覆盖率为 99.987651%。

| 指标 | 前 500 次截面结果 |
| --- | ---: |
| 入库因子数 | 19 |
| Test 单因子加权 Pearson 均值 / 中位数 | 0.007062 / 0.007249 |
| Test 正 IC 因子 | 19 / 19 |
| Test 单因子加权 Spearman 均值 | 0.004895 |
| Linear Test 加权 Pearson | 0.015361 |
| LightGBM Test 加权 Pearson | 0.016599 |
| Val 有效 / 总配对数 | 171 / 171 |
| Val 绝对 Spearman 均值 / 中位数 | 0.175447 / 0.151249 |
| Val 绝对 Spearman P90 / 最大值 | 0.421956 / 0.573327 |
| Val 绝对 Spearman ≥ 0.7 的配对 | 0 |

19 项单因子各项 Test 指标与完整运行中对应成员完全相同，最大差异为 0；171 项 Val 配对
结果也与完整库中的对应配对完全相同。原 1000 次运行的配置、检查点、冻结库、模型拟合、
OOS、模型 Test 和相似度文件在本次评估前后逐字节不变。新模型记录真实拟合来源，
`source_matches_search=false`，没有改写搜索来源或绕过因子身份、版本及配置校验。

结果和相似度已补入总表 Haiku 分组，500／1000 次相邻；原完整结果及其他方法数值保留。
这里使用各模型自身的有效样本，未将本次分数填入“共同样本与误差下降”补充表。
历史留出集、CLI 采样行为和 AlphaSchema 共享诊断差异沿用前述说明。

本次未修改搜索或评估源码。准备脚本通过 Ruff 和格式检查；完整
`pwsh -File scripts/check.ps1` 通过，1,298 项测试全部通过（127.37 秒）。

[截面准备脚本](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/prepare_prefix500.py)、
[运行报告](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-41056bb4f8-prefix500/report.md)、
[组合模型报告](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-41056bb4f8-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/prefix500.log)、
[完整检查](../../artifacts/comparisons/alphaschema_haiku55_fold2_20261102/prefix500-checks.log)。

## 2026-11-03 AlphaSchema：DeepSeek Fold2 后台运行

用户追加 AlphaSchema + `deepseek-flash`，预算 1000 次候选尝试、fold2、all、seed 42。
运行 `futures_kernel_5m_full-all-fold2-alphaschema-42-69020461a6`，启动 PID 2355893，
独立 workspace 和空初始库。固定版本 `rq8_fold2_alphaschema_deepseek_v1` 从同组 Haiku
版本复制，仅更改版本标识、说明，以及方法配置中的模型、接口地址和凭据变量名；八字段、
60 个原算子、数据快照、参考库、搜索参数、准入、fold 和下游模型配置逐项相同。

后端为 `https://api.deepseek.com/v1`，读取已有 `OPENAI_API_KEY`；没有保存凭据。
模型、接口、temperature=1、max_output_tokens=65536、timeout_seconds=1800 与总表
DeepSeek AlphaPROBE 运行 `9cbc979fab` 一致；数据快照、资产配置、fold、字段也逐项一致。
Haiku CLI 的采样行为及历史搜索诊断差异仍按前文披露，不将本组视为仅改变搜索算法的消融。
复用原 Kernel 历史快照及其数据限制，没有重新采集、重采样或引入 Zookeeper 初始成员。

启动入口复用 Haiku 的公共 `runner.run`、`finish_experiment` 和主总表发布流程；完成搜索
后自动冻结、拟合 Train+Val 模型、评估 Test 和 Val 库内相似度，再填入主总表。
LightGBM 固定 50 轮、15 叶、min_data_in_leaf=2000、max_depth=5；预处理沿用全局
P1–P99 和品种去均值。Test 不回传搜索，独立版本避免主工作区后续配置变化改变本轮协议。

本次未修改搜索或评估源码。启动脚本通过 Ruff 与格式检查；完整
`pwsh -File scripts/check.ps1` 通过，1,298 项离线测试全部通过（137.96 秒）。
真实接口另行验证：模型目录返回 200 且包含 `deepseek-flash`，正式搜索也已收到首个 Chat
回复及 token 用量。首个计划经既有有界公式修复后，两项候选均成功编译、计算并原子提交，
评估状态为 success，均因统一 IC 门槛拒绝；核对时为 2/1000 次、入库 0，第 3 次正在生成。
运行已脱离终端并继续搜索，尚无 OOS 结果。

同日 17:02（北京时间），运行在完成 378/1000 次候选尝试、入库 20 个因子后，
因下一计划的 Chat 请求连续四次返回 HTTP 503 而停止。失败发生在 `ask` 阶段；
检查点保存 189 个已完成计划及当前待实现计划，没有待提交候选，下一次编号为 379。
运行配置及检查点指纹匹配，末项原子 trial 的库版本与检查点均为 20。
公共客户端的 503／明确过载退避已调整，规则见[方法复现规范](../method_reproduction.md)；
恢复沿用原模型、搜索与评估配置，以及原因子库和方法状态。
本次完整离线检查通过，1,317 项测试通过（128.75 秒）；新增模拟 HTTP 503 耗尽后的
续跑回归，验证原计划、已提交 trial、候选预算和未知请求用量均保留。
检查日志为 [503 恢复检查](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/recovery-503-checks.log)。
同日 17:25（北京时间）经用户授权，通过原启动／恢复脚本在后台续跑，PID 4063572，
输出追加至原日志。运行通过恢复检查并加载 6,189,366 行行情，状态为 `running`，
从第 379 次候选的原待实现计划继续请求模型；服务端有效回复及新增 trial 尚待确认。

[启动／恢复脚本](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/run.py)、
[固定版本](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/workspace/configs/versions/rq8_fold2_alphaschema_deepseek_v1.json)、
[运行配置](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-69020461a6/run.json)、
[后台日志](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/run.log)、
[完整检查](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/checks.log)。


## 2026-11-02：Zookeeper 26/37 固定目录适配与 fold2 诊断

运行 `zookeeper26-fold2-435d201527`；定义版本 `zookeeper_rq_monthly_v1`。
使用 Kernel 八字段和现有主力后两档原生远月曲线，月末信号下月生效，完整历史预热。
目标仍为未来 12 根 5m adj_close 对数收益；train 2019–2021、val 2022–2023、
test 2024–2025。方向与 1%–99% 因子/标签裁剪边界只用 train 拟合，先冻结全 26 项再加载 test。

26 项都有有效 OOS；定向加权 Pearson IC 均值 0.001948、中位数 0.000874，15/26 为正。
train/val 按现有门槛审计通过 mom1、mom12、ivolaBGR 共 3 项，未按通过结果筛选 OOS 集合。
长期价值项因覆盖不足未通过。八字段 16 项 test 均值 0.003927，期限结构 9 项 -0.002103，
BGR 残差 0.006757。2024/2025 年均值分别 0.003218/0.000856，不表示稳定交易收益。

辅助曲线仍有 2 个已记录缺 Bar，manifest 为 incomplete；本次明确属于保留缺失的诊断。
主力替代近月、VWAP/收盘代理结算价、OI 范围、BGR 组合与窗口等偏离均有逐项说明。
没有执行组合预测或新挖掘因子的条件增量测试；本次已查看的 test 不再是后续开发的盲测。

[完整说明与逐因子报告](zookeeper26_fold2/report.md)、
[可读取结果](zookeeper26_fold2/results.jsonl)、[冻结配置与源快照](zookeeper26_fold2/run.json)。
这些汇总可随 Git 发布；原始及派生行情、完整 trial 仍保留于 Git 排除目录。

本次验证：完整执行 `pwsh -File scripts/check.ps1`，Ruff、格式、算子目录检查及
1266 项测试全部通过，包含新增 8 项专项测试。早期 AgentScope 模拟测试的挂起与受限沙箱
有关；解除沙箱限制后，单例及完整套件均通过，没有修改这些方法/测试或冻结数值定义。


## 2026-11-02：Zookeeper26 的 Linear / LightGBM 历史 OOS

导入 `zookeeper26-fold2-435d201527` 的全部 26 个冻结因子，用公共 `atlas model` 拟合及测试。
新增固定目录的 MarketData 选择，保持原定义、方向、数据快照和单因子产物。
Train+Val 2019–2023 合并拟合 5,296,605 行，26 列全部保留，Linear 实际秩为 26；
Test 为 2024–2025，没有早停、调参或按已知 Test 分数筛选。

| 模型 | 加权 Pearson | 加权 Spearman | 等权 Pearson | 等权 Spearman | 预测覆盖率 |
|---|---:|---:|---:|---:|---:|
| linear | +0.012707 | +0.007586 | +0.011747 | +0.009223 | 99.62% |
| lightgbm | +0.011339 | +0.004786 | +0.010518 | +0.007943 | 99.62% |

共有预测覆盖为 2,549,205 / 2,558,829 行；四种 IC 均实际聚合 80 个有效品种、2,401,248 行。
本次 Linear 四项 IC 均高于 LightGBM，未计算显著性区间，不据此宣称普遍优胜。
预处理沿用 Train+Val 全局 P1–P99 和品种全期去均值，Test 标签保持原值。
LightGBM 使用当前配置：50 轮、15 叶、learning_rate=0.05、min_data_in_leaf=100、
lambda_l2=1、num_threads=4、seed=42，无 max_depth 限制；不是总表部分旧运行的
leaf2000/depth5 配置。Linear 为 rcond=1e-12 的 OLS。

先保存 fit.json 才读取 Test；实际重复两阶段命中缓存，拟合和 Test 文件均逐字节不变。
原配置指纹、26 项身份与顺序、冻结库及原单因子 JSON 已核对。
源码指纹只记录来源，本次模型来源与原固定目录审计来源不同，未伪装同源或改写原记录。
辅助曲线仍是保留缺失的诊断输入；单因子 Test 已查看，不能称为新的盲测。
本次完成组合基准，尚未做“26 项 + 新挖因子”的条件增量比较。

完整 `pwsh -File scripts/check.ps1` 通过，1,267 项测试，无排除用例。
[可发布模型报告](zookeeper26_fold2/model/report.md)、
[冻结模型](zookeeper26_fold2/model/fit.json)、[OOS 指标](zookeeper26_fold2/model/test.json)。

## 2026-11-02：指定 Zookeeper26 baseline 并核验整合与 PIT

用户将 `zookeeper26-fold2-435d201527` 指定为后续因子增量实验的固定对照，引用写入
[baseline.json](zookeeper26_fold2/baseline.json)。保留全 26 项和 Linear／LightGBM 两模型，
比较参数来自保存的 fit.json，不随默认模型配置改变。没有重拟合、重新评分 Test 或
改写冻结库；未把该目录设成所有搜索方法的默认参考库。

整合只在日/月派生特征与 Kernel 5m 行的对齐层，尚无通用的八字段加原始曲线数据资产。
真实抽样 2023-06-01 至 07-04 共 104,262 行、74 品种，八字段、身份、行序和目标元数据不变；
截断到 06-15 的 52,437 行与完整输入的相同历史行一致。103,614 行匹配到上月源观测，
源时间晚于使用时间为 0；648 行无上月源时点，未计为已验证。2023-05 至 06 月的
2,928 个匹配品种日合约标识忽略大小写后全部相同，未将大小写差异误判成换约。

26 项冻结 AST 都是字段读取且无算子依赖；实际执行与准备字段逐行一致。公式的日历运算
由 Polars／NumPy 完成，未把月数当 5m 窗口或以 @1d 冒充结算价。新增合成测试覆盖月中
截断、月底夜盘、未来月份扰动和乱序后的全部 26 项执行，相关专项测试共 182 项通过。

PIT 结论限定为固定快照上的计算时序。Kernel SQL 选最新 final 版本，未用历史决策时点
限制 received_at，未导出版本链；其主力标记和累计复权因子的生效时点亦无法仅凭快照
独立认证。因此 baseline 标为 historical_diagnostic，不声称完整 vintage-PIT，也不把
证据不足直接解释为发现确定的前视错误。原曲线两处缺 Bar 继续披露。

完整 `pwsh -File scripts/check.ps1` 通过：1,268 项，无排除用例。详细检查与适用边界见
[核验报告](zookeeper26_fold2/report.md#baseline-与时点核验)和
[audit.json](zookeeper26_fold2/audit.json)。


## 2026-11-02：Zookeeper 月频 DSL 与数据／算子版本

新增 `rq8_fold2_v1` 与 `rq8_zk26_fold2_v2`，分别固定八字段／60 个算子、
八字段加 26 个发布特征／66 个算子。配置与入口见[研究协议](../research_protocol.md#数据与算子版本)。
历史固定全 26 项 baseline 的 run、模型与 test 产物继续保留。

真实数据对照只读取截至 2023-12-31 的特征：base 日观察 159,432 行、
curve 日观察 133,287 行，覆盖 85 个品种、7,905 个品种月。
全部 26 项的 null 位置完全一致；有限值以 rtol=1e-8、atol=1e-10 逐项通过。
这是新版 DSL 与历史本地公式的数值一致性证据，不是论文精确复现或 vintage PIT 认证。
可通过 `uv run python scripts/validate_zookeeper_dsl.py` 重复检查。

实际 CLI 验证运行 `futures_kernel_5m_full-all-fold2-random-42-15e8e5a508`：
`atlas run --version rq8_zk26_fold2_v2 --method random --seed 42 --attempts 3 --search-only`。
加载 6,189,366 行（含一年 5m 预热），三项候选都成功计算，均因原 IC 门槛拒绝，
正常冻结为空库。至少一项候选组合了原八字段和 `zk_cv`，验证了统一字段接口。
该运行只验证通路，没有模型拟合或 OOS；不将其称为因子有效性的证据。

[机器可读数值对照与运行记录](zookeeper26_fold2/dsl_v2_validation.json)保存逐因子最大误差、
有限值数、数据来源及运行路径。原始远月 5m 行尚未物理合并，整合发生在上月特征发布层。

本次实现完成后 `pwsh -File scripts/check.ps1` 通过：Ruff、格式、算子目录一致性和
1,280 项离线测试全部通过；测试使用合成数据与模拟服务，不访问真实供应商或模型。

## 2026-11-02：B26 初始因子库与三方法 Fold2 重启

用户指定将固定 B26 的全部 26 项直接作为初始库成员，版本为
`rq8_zk26_seeded_fold2_luna_v4`。数据、34 个字段、66 个算子、fold、模型及单因子门槛沿用
v3。初始成员参与公共查询、身份去重与 Val Spearman 查重，MCTS-LLM 的训练多样性也
比较实际全库。初始成员不是本轮新发现，不按新候选门槛删除；只用当前 Train/Val 测量，
测量数量和成本单列。方法查询不包含 Test 或组合增量，搜索树和奖励历史不伪造初始记录。

真实初始化核验为 `futures_kernel_5m_full-all-fold2-random-42-816d8a4d5e`，使用固定检查脚本，
没有调用随机搜索或 LLM。初始化 26 项耗时 115.05 秒，未消耗搜索尝试；随后两条诊断提交
分别验证 `$zk_mom1` 被 `expression_duplicate` 拒绝、`2 * $zk_mom1` 与 B26 中该成员的
Val 绝对 Spearman 为 1.0 并被 `behavior_duplicate` 拒绝。两次后库仍为 26 个初始成员，
新增 0 个；未加载 Test。此诊断的两条提交不属于三方法各 300 次的正式搜索预算。
[机器可读核验证据](zookeeper26_fold2/initial_library_v4_audit.json)保存身份、计数和结果。

此前 v3 是独立空库，B26 只作为字段；AlphaProbe 第 24 次提交 `$zk_mom1`，只与本轮前两项
比较，最大绝对相关 0.005477，因此曾被接受。该结果不符合用户补充的 B26 初始库条件。
旧产物保留，停止后已提交尝试如下，未进行该组配对 Test：

| 方法 | 运行末尾 ID | 已提交尝试 | 新增入库 | 状态 |
| --- | --- | ---: | ---: | --- |
| AlphaProbe | `04c0d21d41` | 300 | 5（含与 B26 同定义的 mom1） | frozen |
| AlphaSchema | `64e86f690c` | 240 | 2 | interrupted |
| MCTS-LLM | `cdc95b13e7` | 189 | 4 | interrupted |

新轮以同一 B26 初始库重新启动三方法，各 300 次新候选尝试、seed 42；实际运行目录见
[本轮范围](../../configs/experiments/zk26_seeded_fold2_300_v2/three_methods.json)。
三组全部冻结后，再按[配对协议](../../configs/experiments/zk26_seeded_fold2_300_v2/plan.json)
比较 B 与 B+全部新增成员。初始成员不重复计数；不会把旧空库轨迹并入新轮。
AlphaSchema 的 150 个有效奖励计划冷启动阈值保持不变，300 次不足以检验完整代理引导机制。

本次完整 `pwsh -File scripts/check.ps1` 通过：1,298 项，无排除用例。新增合成测试验证初始
查询、身份／数值去重、预算、初始化及 trial 中断恢复、Test 隔离、增量计数和 MCTS 多样性。

## 2026-11-02：B26 初始库上三方法各 300 次与配对 OOS

`rq8_zk26_seeded_fold2_luna_v4` 的三个 seed 42 运行均完成 300 次并冻结。三组初始成员的
26 个身份、方向和 Train/Val 指标一致；新增准入都完成全库查重，不含 B26 的相同身份。

| 方法 | 运行末尾 ID | 200 次新增 | 300 次新增 | 最终总成员 | 总耗时（含准备） |
| --- | --- | ---: | ---: | ---: | ---: |
| AlphaProbe | `b448e9fb48` | 3 | 5 | 31 | 45.9 分钟 |
| AlphaSchema | `fbac3f9a28` | 4 | 4 | 30 | 61.4 分钟 |
| MCTS-LLM | `d8d6f22a47` | 3 | 4 | 30 | 110.5 分钟 |

三方法并集为 13 个不同新增定义，主并集共 39 列。AlphaProbe 第 4 条与 MCTS 第 10 条是
互为负号的 48-bar 收盘价区间位置表达式，训练方向分别为 +1、−1，定向后等价。各方法库
独立，跨方法并集按事先协议仅去重相同 factor_id，因此保留两个来源；Linear 主并集有效秩
为 38，200 次并集为 35/36。其他单方法组合均满列秩，没有训练预处理删除列。

先保存 9 个组合的 18 个模型，再统一读取 Test；fit 文件在 Test 前后逐字节不变。
39 条导出 DSL 重新编译后身份全部一致。共同 Train+Val 为 5,296,605 行；共同 Test 为
2,549,205 / 2,558,829 行，预测覆盖率 99.6239%。加权 MSE 使用 86 个品种、2,429,676 行；
JR、LR、PM、RI、ZC 无可用正成交额权重，共 119,529 个可预测行未进入加权评分。
主 IC 使用相同 80 个品种、2,401,248 行：L_F、PD、PP_F、PT、V_F 的 B26 预测为常量，WH
的目标为常量，相关系数未定义，不能填零。全部方法、模型及节点的共同 IC 品种集合一致。

本次配对 B26 的加权 Pearson 为 Linear 0.01270556、LightGBM 0.01118884。B26 在共同训练
行重新拟合预处理及模型；训练标签裁剪边界与旧单库拟合略有差异，因此不直接减去旧报告 IC。
300 次的主增量如下，MSE 下降为 `1 − MSE_扩充 / MSE_B`，单位为百分比：

| 扩充来源 | 模型 | 加权 Pearson ΔIC | 20 交易日块 95% 区间 | MSE 下降 |
| --- | --- | ---: | --- | ---: |
| AlphaProbe | Linear | +0.003207 | [−0.003003, +0.011290] | −0.014780% |
| AlphaProbe | LightGBM | −0.002084 | [−0.014105, +0.012310] | −0.026780% |
| AlphaSchema | Linear | +0.000565 | [−0.003266, +0.005162] | −0.003987% |
| AlphaSchema | LightGBM | +0.004046 | [−0.004430, +0.015382] | −0.009354% |
| MCTS-LLM | Linear | +0.002673 | [−0.000722, +0.007564] | +0.001860% |
| MCTS-LLM | LightGBM | +0.008276 | [+0.003202, +0.012854] | −0.000149% |
| 三方法并集 | Linear | +0.003961 | [−0.002312, +0.012372] | −0.012604% |
| 三方法并集 | LightGBM | −0.003679 | [−0.016729, +0.012482] | −0.061140% |

本轮最清楚的 IC 增量证据来自 MCTS + LightGBM：扩充 IC 为 0.01946441，2024、2025 年
增量分别为 +0.005350、+0.012142；主区间为正。其余 300 次 ΔIC 区间含零，所有 MSE
下降区间也含零，不宣称误差稳定改善。Linear 各组均为 2024 年负增量、2025 年正增量。
MCTS 的 200 次 LightGBM ΔIC 为 +0.008923，新增第 4 项没有提高 300 次的点估计；主节点
仍按预先规定保留 300，不按 Test 改选节点。AlphaSchema 两节点成员及模型完全相同。

AlphaSchema 有 146 次编译拒绝：74 次表达式规模超限、66 次缺少最终输出、6 次类型或
参数数量错误。183 个完成计划中 70 个有有效奖励，选择均为 explore，未达到 150 个有效
奖励计划的代理启动条件。该结果仅代表当前 DSL 适配和预算下的覆盖探索。
三组 Chat 请求分别为 216、302、1,086，输入 token 分别为 3,116,714、3,819,975、
15,596,636；全部 usage 已返回。AlphaProbe 另有 6 次 embedding 请求、1,518 tokens。
评估预算相同，不代表模型调用成本相同。初始化分别为 99.06、96.38、102.67 秒，包含在
总耗时中，三组并行运行。完整成本及所有拒绝类型见搜索 JSONL。

区间使用按年分层的全市场同步 20 交易日块、2,000 次重抽，每组均有 2,000 次有效重复。
这是冻结模型的时间波动描述，不能替代多 seed 或多重比较控制。2024–2025 仍是已查看的
历史诊断留出集，完整 vintage PIT 未认证；本轮 Test 未回传搜索。旧空库组不参与本表。

[报告](zookeeper26_fold2/incremental_v4/report.md)、
[全指标 JSONL](zookeeper26_fold2/incremental_v4/results.jsonl)、
[因子定义 JSONL](zookeeper26_fold2/incremental_v4/factors.jsonl)、
[搜索与成本 JSONL](zookeeper26_fold2/incremental_v4/search_runs.jsonl)。
保存模型和完整 Test 同目录；逐行预测保存在 `data/comparisons/`，已确认被 Git 排除。

## 2026-11-02：追加 Atlas 区域 UCB 的 Fold2 300 次实验

用户在前三组结果出炉后追加注册方法 `atlas`，沿用 `rq8_zk26_seeded_fold2_luna_v4`、
Fold2、seed 42、B26 初始库和 300 次预算。该方法为固定文法的区域 UCB 原型，五个区域为
return、delta、mean、std、zscore；使用相同 34 个字段，窗口为 3、6、12、24、48、96，
不调用 LLM。其搜索空间与三个 LLM 方法分组解释；它与 `v1`、`v1_async` 是不同方法。

运行 `futures_kernel_5m_full-all-fold2-atlas-42-a40f311cd5` 已冻结，300 次中新增 11 项，
最终 37 个成员；200 次前缀新增 9 项。初始 B26 的定义、方向、Train/Val 指标及数据快照
与前三组一致。结果为准入 11、质量拒绝 147、常量 63、覆盖不足 51、数值重复 27、训练
指标未定义 1，没有编译拒绝。五区域尝试数分别为 66、59、60、63、52。
搜索总耗时 1,816.88 秒，其中初始化 116.91 秒，不占候选预算；Chat、embedding 请求均为
零。墙钟受共享主机负载影响，期间也运行了离线检查，不作为独占硬件速度比较。

全部搜索定义冻结后保存配对模型，再评估 Test。B26 的预处理与保存模型和前三组完全一致，
fit 文件在 Test 前后逐字节不变。共同训练行 5,296,605，共同 Test 行 2,549,205，预测覆盖
99.6239%；主 IC 的 80 个品种、2,401,248 行以及加权 MSE 的 86 个品种、2,429,676 行
均与前三组相同。单方法范围的 `union_200`、`union_300` 是相应 Atlas 组合的重复别名，
不是额外搜索或新的方法并集。

第 41 次准入的 `RETURN($zk_basis12, 24)` 在下游预处理被删除。共同 Train+Val 中有
4,960,998 个有限值，其中零 4,871,214、负值 42,240、正值 47,544；1% 和 99% 分位均为
零，裁剪后标准差为零。搜索时按 Train 拟合的边界为 `[0, 0.000014241312000118356]`；
下游模型按既定协议在共同 Train+Val 上重估，因此可触发不同的常量判定。没有改裁剪参数
或依据 Test 筛选成员。该项仍在冻结库和 JSONL 定义中；200 次组保留 34/35 列，300 次组
保留 36/37 列，Linear 均满列秩。详细数值见预处理核验文件。

| 节点 | 模型 | 扩充加权 Pearson IC | 相对 B26 的 ΔIC | 20 交易日块 95% 区间 | MSE 下降 |
| --- | --- | ---: | ---: | --- | ---: |
| 200 | Linear | −0.004221 | −0.016926 | [−0.039301, +0.002189] | −0.077039% |
| 200 | LightGBM | −0.006814 | −0.018002 | [−0.043003, +0.006829] | −0.073819% |
| 300 | Linear | −0.004374 | −0.017079 | [−0.039134, +0.002278] | −0.098770% |
| 300 | LightGBM | −0.010563 | −0.021752 | [−0.046319, +0.001500] | −0.095746% |

300 次时两模型的 IC 点估计均下降，ΔIC 区间仍包含零。Linear 的年度 ΔIC 为 2024 年
−0.038120、2025 年 −0.003956；LightGBM 为 −0.035560、−0.003150。MSE 下降区间分别为
`[−0.213084%, −0.029099%]` 和 `[−0.236254%, −0.010282%]`，均低于零，表示该冻结组合
的误差恶化。新增定义较多并未带来本轮组合改善；保持原 300 次主节点和全部准入成员，
不根据已查看 Test 删除因子或改选前缀。

本轮是用户追加的单 seed 历史诊断；2024–2025 此前已查看，不能解释为新盲测或稳定方法
排名。没有重跑前三组或将其 Test 反馈给 Atlas。预处理核验也只读取 Train/Val。

[报告](zookeeper26_fold2/atlas_v4/report.md)、
[完整结果 JSONL](zookeeper26_fold2/atlas_v4/results.jsonl)、
[37 项定义 JSONL](zookeeper26_fold2/atlas_v4/factors.jsonl)、
[搜索与成本 JSONL](zookeeper26_fold2/atlas_v4/search_runs.jsonl)、
[预处理核验](zookeeper26_fold2/atlas_v4/preprocessing_audit.json)、
[本轮范围](../../configs/experiments/zk26_seeded_fold2_300_v2/atlas.json)。
37 条导出 DSL 重新编译后的身份均一致；模型、完整 Test 和版本配置同目录保存，逐行预测
保存在被 Git 排除的 `data/comparisons/zk26_seeded_fold2_atlas_300_v2/`。

`pwsh -File scripts/check.ps1` 的 Ruff、格式、算子目录及 1,298 项离线测试全部通过。
沙箱内模拟异步用例停顿，单例在主机环境 1.88 秒通过；完整主机检查随后在 131.99 秒通过，
没有修改测试或使用真实模型服务。

## 2026-11-03：按实验总览纠正 AlphaAtlas 方法与模型配置

用户明确以 `experiment_overview.md` 为方法配置依据。此前追加的 `atlas` 是有限文法的
区域 UCB 控制，不能代表总览中的 AlphaAtlas。旧完整结果保留；其 seed 42 重复运行
`futures_kernel_5m_full-all-fold2-atlas-42-71d892ead6` 在 198 次、26 个初始加 9 个新增成员时
中断，没有开始本次重复的配对模型或 Test。刚启动的 v4 AlphaSchema 重复
`futures_kernel_5m_full-all-fold2-alphaschema-42-69e8ee0b7b` 也在首次候选提交前停止。
两者不进入纠正后的比较，不更改已保存 trial。

新版本 `rq8_zk26_seeded_fold2_overview_luna_v5` 保留本轮明确指定的 B26 初始库、
34 字段、Fold2、seed 42 和每方法 300 次预算。AlphaAtlas 使用 `v1_async`，方法参数完整
复制总览 GPT6-luna 的 `382bb38611`：最多 5 个并行研究计划、每计划 5 个候选、Explorer
最多 40 轮。AlphaSchema 参数完整复制 `7ce881f635`；两者均使用 `gpt-6-luna`。
下游配置复制总览统一模型：LightGBM 50 轮、`min_data_in_leaf=2000`、`max_depth=5`；
其余预处理和 Linear 参数不变。B26 与扩充组合在相同训练行上按新配置重新拟合。

本次是 B26 增量实验中的方法参数复现。总览原实验使用八字段、空初始库、1000 次预算；
本轮保留先前约定的增量实验条件，不把两种实验混为一轮。共享 lag1 诊断继续对两方法
开放，旧 Luna AlphaAtlas 使用的 `icir_turnover_v1` 与当前诊断不同。当前实现、在线模型
回复和异步完成顺序也不承诺复现历史轨迹。

实际运行与冻结状态见[本轮范围](../../configs/experiments/zk26_seeded_fold2_overview_300_v3/two_methods.json)：
AlphaAtlas `5f77947f20`、AlphaSchema `3a4f640d02`。两项均完成 300 次并冻结，正常退出。
两项搜索冻结、14 个配对模型全部保存后才读取 Test。模型文件在 Test 前后逐字节一致；
历史 Test 不反馈给搜索。

| 方法 | 200 次新增 | 300 次新增 | 最终成员 | 总分钟（含准备） | Chat 请求 |
| --- | ---: | ---: | ---: | ---: | ---: |
| AlphaAtlas `v1_async` | 6 | 9 | 35 | 52.3 | 657 |
| AlphaSchema | 2 | 4 | 30 | 56.7 | 299 |

两方法新增并集为 13 个不同定义，主并集共 39 列；200 次并集为 34 列。全部组合没有
全缺失或常量列被预处理删除，Linear 均满列秩。初始 26 项的定义、方向、Train/Val 数值
一致，全部新增准入完成全库相关性比较，且没有与 B26 相同身份的新增项。
39 条导出 DSL 重新编译后的身份全部一致。

共同 Train+Val 为 5,296,605 行；共同 Test 为 2,549,205 / 2,558,829 行，覆盖率 99.6239%。
全部组的 IC 使用同样 80 个品种、2,401,248 行；加权 MSE 使用 86 个品种、2,429,676 行。
本次配对 B26 的加权 Pearson 为 Linear 0.01270556、LightGBM 0.01064594。
LightGBM 已采用总览的 leaf2000/depth5 配置，不能直接减去此前 leaf100 的基线分数。

| 300 次扩充来源 | 模型 | 扩充 IC | ΔIC | 20 交易日块 95% 区间 | MSE 下降 |
| --- | --- | ---: | ---: | --- | ---: |
| AlphaAtlas | Linear | +0.018637 | +0.005932 | [+0.000010, +0.014157] | −0.031854% |
| AlphaAtlas | LightGBM | +0.020027 | +0.009381 | [+0.000176, +0.020036] | −0.020182% |
| AlphaSchema | Linear | +0.012497 | −0.000208 | [−0.005320, +0.007359] | −0.014557% |
| AlphaSchema | LightGBM | +0.014807 | +0.004161 | [−0.000461, +0.011909] | −0.015537% |
| 两方法并集 | Linear | +0.016606 | +0.003900 | [−0.002719, +0.013641] | −0.048741% |
| 两方法并集 | LightGBM | +0.016183 | +0.005538 | [−0.003512, +0.018353] | −0.027027% |

AlphaAtlas 的两项 ΔIC 区间下限略高于零，尤其 Linear 下限只有 0.00000972，应谨慎解释。
其 2024／2025 年度增量均为正：Linear +0.000911／+0.009055，LightGBM
+0.007400／+0.011545。但两项 MSE 点估计均恶化，MSE 变化区间均含零。
AlphaSchema 的两项 ΔIC 区间含零；Linear 的 MSE 下降区间为
`[−0.028499%, −0.000739%]`，表示误差恶化。其他五个 300 次组合的 MSE 区间均含零，
所有 300 次扩充组的 MSE 点估计均恶化。

AlphaAtlas 的 200 次 LightGBM ΔIC 为 +0.014541，区间为 [+0.008120, +0.022347]，
高于 300 次点估计；仍保留事先规定的 300 次主节点，不根据 Test 改选。两方法并集的
300 次增量区间含零，不能把增加定义数等同于增加独立预测信息。

AlphaSchema 的 300 次中有 143 次编译拒绝；历史记录包含 184 个计划，其中 73 个有
有效完整奖励、110 个失败、1 个因预算尾部只提交一个周期。计划选择全部为 explore，
未达到 150 个有效完整奖励计划的代理启动阈值，因此本轮不检验代理利用与变异阶段。
AlphaAtlas 的原配置完整运行，没有编译拒绝；其定义重复 26 次、数值重复 30 次，
质量拒绝 218 次、覆盖不足 16 次、常量 1 次。

AlphaAtlas／AlphaSchema 的输入 token 分别为 17,574,661／3,777,533，输出 token 为
302,920／187,933，所有 usage 均已返回。初始化分别为 94.92／93.98 秒，已包含在总耗时中。
两组并行，墙钟受共享主机负载影响；相同候选预算不代表相同模型调用成本。

各区间使用 2,000 次按年分层、全市场同步的 20 交易日区块重采样，全部重复有效。
模型与预测固定，区间不替代多 seed；2024–2025 已被查看，本轮仍是历史诊断留出评估。

[结果报告](zookeeper26_fold2/overview_luna_v5/report.md)、
[39 项定义 JSONL](zookeeper26_fold2/overview_luna_v5/factors.jsonl)、
[12 组结果 JSONL](zookeeper26_fold2/overview_luna_v5/results.jsonl)、
[搜索与成本 JSONL](zookeeper26_fold2/overview_luna_v5/search_runs.jsonl)、
[配置来源核对](zookeeper26_fold2/overview_luna_v5/configuration_audit.json)、
[冻结与评估核验](zookeeper26_fold2/overview_luna_v5/evaluation_audit.json)。
模型、完整 Test、版本及 scope 同目录保存；复跑入口见
[实验目录](../../configs/experiments/zk26_seeded_fold2_overview_300_v3/README.md)。
逐行预测保存在被 Git 排除的 `data/comparisons/zk26_seeded_fold2_overview_luna_300_v3/`。

本次 `pwsh -File scripts/check.ps1` 的 Ruff、格式、算子目录及 1,298 项离线测试通过，
pytest 耗时 122.25 秒；测试不调用真实模型或供应商。

## 2026-11-03：逐方法 B26 增益统一使用 v5 模型配置

当前比较仅保留 AlphaProbe、MCTS-LLM、AlphaAtlas 和两次独立 AlphaSchema 搜索。
各组原冻结因子、方向、准入顺序和搜索版本保持不变，统一以
`rq8_zk26_seeded_fold2_overview_luna_v5.model` 重训 Linear 与 LightGBM。
全部当前 scope 设置 `include_union=false`；没有重新调用 LLM 或消耗搜索预算。

来源运行分别为 `b448e9fb48`、`d8d6f22a47`、`5f77947f20`、`fbac3f9a28`、`3a4f640d02`。
原三方法与总览两方法分成两个 scope，使用同一评估协议，保留 200/300 两个节点。
有限文法 atlas 按用户指定移出当前总表；已有重训 fit 保留，没有执行本次对照的 Test。
原协议、搜索版本和历史结果保留，当前复跑入口只运行上述五次 LLM 搜索的冻结库。

24 个模型全部保存后才执行本次 Test。两个 scope 的 B26 完整拟合状态完全一致，
逐行 Test row_id、标签和两种模型预测完全一致；fit 文件在 Test 前后逐字节未变。
共同 Train+Val 为 5,296,605 行；共同 Test 为 2,549,205 / 2,558,829 行。
IC 使用相同 80 个品种、2,401,248 行；WMSE 使用 86 个品种、2,429,676 行。
共同 B26 的 Linear IC 为 0.01270556，LightGBM IC 为 0.01064594。

原三方法的因子身份、所有预处理状态和 Linear 模型与旧拟合逐项一致，LightGBM 使用
50 轮、leaf2000/depth5。AlphaAtlas 与最新 AlphaSchema 的全部拟合状态精确复现原 v5
单方法模型。各单方法保留全部列，Linear 满列秩。

| 方法 / 运行 | 模型 | 扩充 IC | ΔIC | 95% 区间 | WMSE 下降 |
| --- | --- | ---: | ---: | --- | ---: |
| AlphaProbe `b448e9fb48` | linear | +0.015913 | +0.003207 | [−0.003003, +0.011290] | −0.014780% |
| AlphaProbe `b448e9fb48` | lightgbm | +0.011419 | +0.000773 | [−0.009379, +0.014002] | −0.030897% |
| AlphaSchema `fbac3f9a28` | linear | +0.013270 | +0.000565 | [−0.003266, +0.005162] | −0.003987% |
| AlphaSchema `fbac3f9a28` | lightgbm | +0.014071 | +0.003425 | [−0.005023, +0.014533] | −0.026347% |
| MCTS-LLM `d8d6f22a47` | linear | +0.015378 | +0.002673 | [−0.000722, +0.007564] | +0.001860% |
| MCTS-LLM `d8d6f22a47` | lightgbm | +0.018358 | +0.007712 | [+0.002702, +0.013721] | −0.016405% |
| AlphaAtlas `5f77947f20` | linear | +0.018637 | +0.005932 | [+0.000010, +0.014157] | −0.031854% |
| AlphaAtlas `5f77947f20` | lightgbm | +0.020027 | +0.009381 | [+0.000176, +0.020036] | −0.020182% |
| AlphaSchema `3a4f640d02` | linear | +0.012497 | −0.000208 | [−0.005320, +0.007359] | −0.014557% |
| AlphaSchema `3a4f640d02` | lightgbm | +0.014807 | +0.004161 | [−0.000461, +0.011909] | −0.015537% |

MCTS-LLM 的 LightGBM，以及 AlphaAtlas 的两个模型，其 300 次 ΔIC 区间为正；
AlphaAtlas 的区间下限接近零。所有 300 次 WMSE 下降区间均未完全高于零，因此不能
把 IC 增益解释为稳定降低误差。两个节点都保留，不根据历史 Test 改选。
区间为固定预测、按年分层的全市场同步 20 交易日块、2,000 次重采样，全部重复有效；
不包含搜索随机性，不作多重比较后的赢家声明。

配对入口增加显式关闭并集及跨版本模型重训。跨版本必须保持数据、字段、算子、目标、
fold 和初始 B26 一致；原搜索仍按保存版本与库身份校验，不修改冻结来源。
相关合成测试 20 项通过；`pwsh -File scripts/check.ps1` 全部通过，pytest 为
1,307 passed。数值结果来自上述真实历史数据评估，测试本身没有调用真实服务。

[配置与复跑命令](../../configs/experiments/zk26_single_methods_v5_20261103/README.md)、
[完整报告](zookeeper26_fold2/single_methods_v5/report.md)、
[20 组结果 JSONL](zookeeper26_fold2/single_methods_v5/results.jsonl)、
[因子定义 JSONL](zookeeper26_fold2/single_methods_v5/factors.jsonl)、
[五次搜索与成本](zookeeper26_fold2/single_methods_v5/search_runs.jsonl)、
[拟合冻结与配对核验](zookeeper26_fold2/single_methods_v5/evaluation_audit.json)。

## 2026-11-03 AlphaSchema DeepSeek：前500次截面评估

按用户预定的 500 次节点，运行 `futures_kernel_5m_full-all-fold2-alphaschema-42-69020461a6`
于北京时间 22:10 提交第 500 次候选后，建立独立、不可恢复的 `69020461a6-prefix500`
冻结截面，保留当时已入库的全部 23 个因子。原 `deepseek-flash` 搜索继续执行 1000 次预算，
截面评估不调用模型服务，不向搜索回传 Test。22:18 完成单因子、组合模型及 Val 相似度评估。

前 500 次结果为入库 23、质量阈值拒绝 432、常数因子 21、覆盖不足 12、行为重复 9、
编译失败 3。500 个原子 trial 逐字节复制并核对；冻结原定义、顺序、训练方向和裁剪边界。
第 500 次的精确方法状态及模型用量不可得，因此不复制后续检查点，也不报告截面模型成本。

沿用 `rq8_fold2_alphaschema_deepseek_v1` 的数据快照、八字段、60 个算子、fold2、seed 42、
规则及模型配置。全部 23 列有效，Train+Val 拟合使用 5,308,104 行；模型保存后才加载 Test。
LightGBM 为 50 轮、15 叶、min_data_in_leaf=2000、max_depth=5，预处理沿用 P1–P99
和品种去均值。两个组合模型均预测 2,558,829 / 2,558,829 行，Test 覆盖率为 100%。

| 指标 | 前 500 次截面结果 |
| --- | ---: |
| 入库因子数 | 23 |
| Test 单因子加权 Pearson 均值 / 中位数 | 0.006462 / 0.006366 |
| Test 正 Pearson IC 因子 | 23 / 23 |
| Test 单因子加权 Spearman 均值 | 0.003874 |
| Linear Test 加权 Pearson | 0.012882 |
| LightGBM Test 加权 Pearson | 0.014689 |
| Val 有效 / 总配对数 | 253 / 253 |
| Val 绝对 Spearman 均值 / 中位数 | 0.167630 / 0.095491 |
| Val 绝对 Spearman P90 / 最大值 | 0.459133 / 0.695945 |
| Val 绝对 Spearman ≥ 0.7 的配对 | 0 |

冻结库、单因子 OOS、组合模型与相似度的因子身份和顺序一致，模型参数与保存版本一致。
拟合如实记录 `source_matches_search=false`，未改写搜索来源或绕过配置、数据和冻结校验。
结果及相似度已填入总表 Fold2 的 DeepSeek 分组；沿用原运行已披露的 Kernel 聚合数据、
历史留出集和 AlphaSchema 共享诊断差异，不作为新的盲测证据。

准备脚本通过 Ruff 和格式检查；完整 `pwsh -File scripts/check.ps1` 通过，
1,330 项离线测试全部通过（138.78 秒）。

[截面准备脚本](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/prepare_prefix500.py)、
[运行报告](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-69020461a6-prefix500/report.md)、
[组合模型报告](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-69020461a6-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/prefix500.log)、
[完整检查](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/prefix500-checks.log)。

## 2026-11-04 AlphaSchema DeepSeek：1000次完整评估

运行 `futures_kernel_5m_full-all-fold2-alphaschema-42-69020461a6` 已提交完整 1000 次，
冻结 34 个因子；单因子 Test、Linear／LightGBM Test 和 Val 全库相似度全部完成，
收尾报告于北京时间 08:06 保存。完整结果与前 500 次截面在总表 DeepSeek 分组相邻列出。

沿用 `rq8_fold2_alphaschema_deepseek_v1`、原数据快照、八字段、60 个算子、fold2 和
seed 42。组合模型按保存版本拟合，LightGBM 为 50 轮、15 叶、leaf2000/depth5；
Train+Val 为 5,308,104 行，Test 为 2,558,829 / 2,558,829 行，覆盖率均为 100%。
搜索、冻结库、OOS、模型和相似度记录的因子身份、顺序及配置来源已核对一致。

| 指标 | 前 500 次 | 完整 1000 次 |
| --- | ---: | ---: |
| 入库因子数 | 23 | 34 |
| Test 单因子加权 Pearson 均值 / 中位数 | 0.006462 / 0.006366 | 0.006122 / 0.006035 |
| Test 正 Pearson IC 因子 | 23 / 23 | 34 / 34 |
| Test 单因子加权 Spearman 均值 | 0.003874 | 0.002672 |
| Linear Test 加权 Pearson | 0.012882 | 0.013154 |
| LightGBM Test 加权 Pearson | 0.014689 | 0.015982 |
| Val 有效 / 总配对数 | 253 / 253 | 561 / 561 |
| Val 绝对 Spearman 均值 / 中位数 | 0.167630 / 0.095491 | 0.188888 / 0.118472 |
| Val 绝对 Spearman P90 / 最大值 | 0.459133 / 0.695945 | 0.487110 / 0.695945 |
| Val 绝对 Spearman ≥ 0.7 的配对 | 0 | 0 |

后 500 次新增 11 个成员。相较前 500 次，Linear 和 LightGBM 的 Test 加权 Pearson
点估计分别提高 0.000272 和 0.001292，单因子均值略降。前 23 项冻结定义及单因子 Test
结果与截面完全一致，其 253 对 Val 相似度也完全一致；未重算或改写已有产物。
完整模型如实保留 `source_matches_search=false` 的来源记录，因子身份和配置校验未绕过。
两节点均保留，不根据 Test 改选；沿用原运行的 Kernel 聚合数据、历史留出集与共享诊断限制。

[完整运行报告](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-69020461a6/report.md)、
[组合模型报告](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-69020461a6/model/report.md)、
[Val 配对结果](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-69020461a6/library_similarity.json)、
[运行与评估日志](../../artifacts/comparisons/alphaschema_deepseek_fold2_20261103/run.log)。

## 2026-11-04 V2 Sync Haiku：尺度统计版前500次截面评估

按用户指定的 `rq8_fold2_v2sync_haiku_v2` 和运行
`futures_kernel_5m_full-all-fold2-v2_sync-42-630b551ad5`，从前 500 个已提交原子 trial
建立独立、不可恢复的 `630b551ad5-prefix500` 截面，冻结全部 35 个已入库因子。
主运行继续原 1000 次预算；本次不调用 Haiku，不向搜索回传 Test。
截面于北京时间 11:09 保存，11:15 完成单因子、组合模型与 Val 相似度评估。

500 次提交为入库 35、质量阈值拒绝 350、行为重复 60、覆盖不足 41、常数因子 11、
表达式重复 3。原子 trial 逐字节复制并核对，因子定义、顺序、训练方向及裁剪边界保留。
第 500 次的精确方法状态和模型用量不可得，没有复制后续检查点或报告截面模型调用成本。

保留原版本的 Kernel 八字段、60 个算子、空初始库、fold2、seed 42、数据快照和评估规则。
该版本启用 `library_residual=residual_ic_v1` 与 `scale_statistics=true`，搜索反馈包含
按特征跨度分档的残差信息，与既有 V1 的搜索协议不同，不能把差异单独归因于 LLM。
下游模型读取保存版本的配置：Linear OLS；LightGBM 50 轮、15 叶、leaf2000/depth5；
预处理为 Train+Val 全局 P1–P99 和品种去均值。没有采用工作区当前 TOML 的 leaf100 默认值。

35 个输入因子全部可用，拟合使用 5,307,829 行 Train+Val 数据。模型保存后才计算 Test；
两个模型均预测 2,558,697 / 2,558,829 行，覆盖率为 99.994841%，按统一规则排除
132 条全因子缺失行。

| 指标 | 前 500 次截面结果 |
| --- | ---: |
| 入库因子数 | 35 |
| Test 单因子加权 Pearson 均值 / 中位数 | 0.004042 / 0.003891 |
| Test 正 Pearson IC 因子 | 31 / 35（88.57%） |
| Test 单因子加权 Spearman 均值 | 0.001888 |
| Linear Test 加权 Pearson | 0.011080 |
| LightGBM Test 加权 Pearson | 0.013874 |
| Val 有效 / 总配对数 | 595 / 595 |
| Val 绝对 Spearman 均值 / 中位数 | 0.105328 / 0.025537 |
| Val 绝对 Spearman P90 / 最大值 | 0.375251 / 0.690604 |
| Val 绝对 Spearman ≥ 0.7 的配对 | 0 |

冻结库、OOS、模型和相似度中的 35 项身份及顺序一致，模型参数与保存版本一致。
拟合如实记录 `source_matches_search=false`，配置、数据和冻结校验保留。
结果与相似度已写入总表 Haiku 分组，以“尺度统计，630b551ad5”标识此次运行。
沿用原版本披露的 Kernel 聚合数据和历史留出限制，不作为新的盲测证据。

本次未修改搜索或评估源码。截面准备脚本通过 Ruff 和格式检查；
`pwsh -File scripts/check.ps1` 完整通过，1,330 项离线测试通过（133.87 秒）。

[截面准备脚本](../../artifacts/comparisons/v2sync_scales_haiku_fold2_20261103/prepare_prefix500.py)、
[固定版本](../../artifacts/comparisons/v2sync_scales_haiku_fold2_20261103/workspace/configs/versions/rq8_fold2_v2sync_haiku_v2.json)、
[运行报告](../../artifacts/comparisons/v2sync_scales_haiku_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-630b551ad5-prefix500/report.md)、
[组合模型报告](../../artifacts/comparisons/v2sync_scales_haiku_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-630b551ad5-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/v2sync_scales_haiku_fold2_20261103/prefix500.log)、
[完整检查](../../artifacts/comparisons/v2sync_scales_haiku_fold2_20261103/prefix500-checks.log)。

## 2026-11-04 V2 Sync Haiku：命名机制版前500次截面评估

按用户指定的 `rq8_fold2_v2sync_haiku_v3` 和运行
`futures_kernel_5m_full-all-fold2-v2_sync-42-a95ded14a5`，从前 500 个原子 trial 建立
独立、不可恢复的 `a95ded14a5-prefix500` 截面，冻结当时全部 33 个已入库因子。
北京时间 15:53 保存截面，15:59 完成单因子 Test、组合模型和 Val 相似度评估。
主运行继续原 1000 次预算，本次不调用 Haiku，不向搜索回传 Test。

500 次提交为入库 33、质量阈值拒绝 410、行为重复 24、覆盖不足 17、常数因子 14、
表达式重复 2。原子 trial 逐字节复制并核对，保留原定义、顺序、训练方向和裁剪边界。
截面不复制方法检查点，也不将主运行的实时状态或用量归入前 500 次成本。

保存版本为 Kernel 八字段、60 个算子、空初始库、fold2、seed 42；开启
`library_residual=residual_ic_v1`、`scale_statistics=true` 和 `mechanism_regions=true`。
v3 只允许命名的机制区域，区别于此前 v2 的尺度统计版，搜索反馈和研究区域约束均须披露。
下游模型使用保存版本中的 Linear OLS 与 LightGBM 50 轮、15 叶、leaf2000/depth5，
预处理沿用 Train+Val 全局 P1–P99 与品种去均值。

33 个输入因子全部可用，模型在 5,308,073 行 Train+Val 数据上拟合并保存后计算 Test。
两个组合模型均预测 2,558,825 / 2,558,829 行，覆盖率为 99.999844%，排除 4 条
全因子缺失行。

| 指标 | 前 500 次截面结果 |
| --- | ---: |
| 入库因子数 | 33 |
| Test 单因子加权 Pearson 均值 / 中位数 | 0.003766 / 0.003772 |
| Test 正 Pearson IC 因子 | 30 / 33（90.91%） |
| Test 单因子加权 Spearman 均值 | 0.005095 |
| Linear Test 加权 Pearson | 0.013271 |
| LightGBM Test 加权 Pearson | 0.008715 |
| Val 有效 / 总配对数 | 528 / 528 |
| Val 绝对 Spearman 均值 / 中位数 | 0.118200 / 0.017427 |
| Val 绝对 Spearman P90 / 最大值 | 0.446327 / 0.695408 |
| Val 绝对 Spearman ≥ 0.7 的配对 | 0 |

相较原 `a95ded14a5-early336` 临时截面新增 17 个成员；原 16 个因子的冻结定义、
单因子 Test 结果及 120 对 Val 相似度均完全一致。组合模型使用全部 33 列独立拟合，
保留原 336 次产物，不按 Test 改选成员、参数或预算节点。
冻结库、OOS、模型和相似度的身份、顺序及配置已核对一致，拟合如实记录
`source_matches_search=false`。沿用 Kernel 聚合数据及历史留出限制，不作为新的盲测证据。

按用户要求，前 500 次结果、相似度及对应说明已从总表撤下；完整评估产物与本记录保留。
本次未修改搜索或评估源码；准备脚本通过 Ruff 和格式检查，
完整 `pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（182.58 秒）。

[截面准备脚本](../../artifacts/comparisons/v2sync_mechanism_haiku_fold2_20261103/prepare_prefix500.py)、
[固定版本](../../artifacts/comparisons/v2sync_mechanism_haiku_fold2_20261103/workspace/configs/versions/rq8_fold2_v2sync_haiku_v3.json)、
[运行报告](../../artifacts/comparisons/v2sync_mechanism_haiku_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-a95ded14a5-prefix500/report.md)、
[组合模型报告](../../artifacts/comparisons/v2sync_mechanism_haiku_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-a95ded14a5-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/v2sync_mechanism_haiku_fold2_20261103/prefix500.log)、
[完整检查](../../artifacts/comparisons/v2sync_mechanism_haiku_fold2_20261103/prefix500-checks.log)。

## 2026-11-04 V2 Sync Luna v4：前500次截面评估

按用户指定的 `rq8_fold2_v2sync_luna_v4` 和运行
`futures_kernel_5m_full-all-fold2-v2_sync-42-57ed99c496`，沿用此前比较的 500 次节点，
从原 1000 次预算搜索的前 500 个原子 trial 建立独立、不可恢复的
`57ed99c496-prefix500` 截面，冻结全部 14 个已入库因子。北京时间 16:14 保存截面，
16:17 完成单因子 Test、组合模型和 Val 相似度评估。主搜索继续原预算；
本次不调用模型服务，不回传 Test，也没有重新运行一个 500 次预算的搜索。

500 次提交为入库 14、质量阈值拒绝 458、行为重复 17、常数因子 6、表达式重复 4、
覆盖不足 1。原子记录逐字节复制并核对，保留原定义、顺序、训练方向及裁剪边界。
截面不复制方法检查点，也不将主运行的实时状态或用量归入前 500 次成本。

保存版本使用 `gpt-6-luna`、Kernel 八字段、60 个算子、空初始库、fold2、seed 42，
开启库残差、尺度统计和命名机制区域。v4 将退火温度设为 0.45→0.08，启用
`adaptive_depth=true`，单区域预算占比为 0.15；这些搜索设置保持原样。
下游模型沿用保存版本中的 Linear OLS 与 LightGBM 50 轮、15 叶、leaf2000/depth5，
预处理为 Train+Val 全局 P1–P99 和品种去均值。

14 列全部可用，Train+Val 拟合使用 5,308,104 行。模型保存后才加载 Test，
两个组合模型均预测 2,558,829 / 2,558,829 行，覆盖率为 100%。

| 指标 | 前 500 次截面结果 |
| --- | ---: |
| 入库因子数 | 14 |
| Test 单因子加权 Pearson 均值 / 中位数 | 0.004687 / 0.003730 |
| Test 正 Pearson IC 因子 | 14 / 14 |
| Test 单因子加权 Spearman 均值 | 0.001691 |
| Linear Test 加权 Pearson | 0.010182 |
| LightGBM Test 加权 Pearson | 0.008575 |
| Val 有效 / 总配对数 | 91 / 91 |
| Val 绝对 Spearman 均值 / 中位数 | 0.126699 / 0.022577 |
| Val 绝对 Spearman P90 / 最大值 | 0.435124 / 0.672502 |
| Val 绝对 Spearman ≥ 0.7 的配对 | 0 |

冻结库、OOS、模型和相似度的身份与顺序一致，模型配置与保存版本一致。
拟合如实记录 `source_matches_search=false`，数据、配置及冻结校验保留。
结果保存在独立运行产物和本记录中，未发布到仓库根目录的实验总表。
沿用原版本披露的 Kernel 聚合数据、供应商历史版本及历史留出限制，不作为新的盲测证据。

本次未修改搜索或评估源码；准备与评估脚本通过 Ruff 和格式检查，完整
`pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（136.10 秒）。

[截面准备脚本](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/prepare_prefix500.py)、
[独立评估入口](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/evaluate_prefix500.py)、
[固定版本](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/workspace/configs/versions/rq8_fold2_v2sync_luna_v4.json)、
[运行报告](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-57ed99c496-prefix500/report.md)、
[组合模型报告](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-57ed99c496-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/prefix500.log)、
[完整检查](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/prefix500-checks.log)。

## 2026-11-04 V2 Sync DeepSeek v3：前500次截面评估

按用户指定的 `rq8_fold2_v2sync_deepseek_v3` 和运行
`futures_kernel_5m_full-all-fold2-v2_sync-42-482d8fe0a1`，沿用此前的 500 次节点，
从原 1000 次预算搜索的前 500 个原子 trial 建立独立、不可恢复的
`482d8fe0a1-prefix500` 截面，冻结全部 33 个已入库因子。北京时间 16:27 保存截面，
16:33 完成单因子 Test、组合模型与 Val 相似度评估。主搜索继续原预算；
本次不调用模型服务，不回传 Test，也没有重新运行一个 500 次预算的搜索。

500 次提交为入库 33、质量阈值拒绝 421、行为重复 41、覆盖不足 2、常数因子 2、
训练指标未定义 1。原子记录逐字节复制并核对，保留原定义、顺序、训练方向及裁剪边界。
截面不复制方法检查点，也不将主运行的实时状态或用量归入前 500 次成本。

保存版本使用 `deepseek-flash`、Kernel 八字段、60 个算子、空初始库、fold2、seed 42，
开启库残差、尺度统计和命名机制区域；除模型连接外，版本配置与 Haiku v3 一致。
退火温度沿用 0.25→0.02，下游模型沿用保存版本中的 Linear OLS 与 LightGBM
50 轮、15 叶、leaf2000/depth5，预处理为 Train+Val 全局 P1–P99 和品种去均值。

33 列全部可用，Train+Val 拟合使用 5,308,097 行。模型保存后才加载 Test，
两个组合模型均预测 2,558,825 / 2,558,829 行，覆盖率为 99.999844%，排除 4 条
全因子缺失行。

| 指标 | 前 500 次截面结果 |
| --- | ---: |
| 入库因子数 | 33 |
| Test 单因子加权 Pearson 均值 / 中位数 | 0.004736 / 0.005335 |
| Test 正 Pearson IC 因子 | 30 / 33（90.91%） |
| Test 单因子加权 Spearman 均值 | 0.003105 |
| Linear Test 加权 Pearson | 0.016330 |
| LightGBM Test 加权 Pearson | 0.010640 |
| Val 有效 / 总配对数 | 528 / 528 |
| Val 绝对 Spearman 均值 / 中位数 | 0.074472 / 0.008281 |
| Val 绝对 Spearman P90 / 最大值 | 0.281889 / 0.671051 |
| Val 绝对 Spearman ≥ 0.7 的配对 | 0 |

冻结库、OOS、模型和相似度的身份与顺序一致，模型配置与保存版本一致。
拟合如实记录 `source_matches_search=false`，数据、配置及冻结校验保留。
结果保存在独立运行产物和本记录中，未发布到仓库根目录的实验总表。
沿用原版本披露的 Kernel 聚合数据、供应商历史版本及历史留出限制，不作为新的盲测证据。

本次未修改搜索或评估源码；准备与评估脚本通过 Ruff 和格式检查，完整
`pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（168.49 秒）。

[截面准备脚本](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/prepare_prefix500.py)、
[独立评估入口](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/evaluate_prefix500.py)、
[固定版本](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/workspace/configs/versions/rq8_fold2_v2sync_deepseek_v3.json)、
[运行报告](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-482d8fe0a1-prefix500/report.md)、
[组合模型报告](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-482d8fe0a1-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/prefix500.log)、
[完整检查](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/prefix500-checks.log)。

## 2026-11-04 V2 Async Haiku v4：当前359次截面评估

按用户要求评估 `rq8_fold2_v2async_haiku_v4` 的当前情况，不写入结果总表。检查时运行
`futures_kernel_5m_full-all-fold2-v2_async-42-315881ff68` 已提交 359 次候选评估，
入库 7 个因子；据此固定 `315881ff68-prefix359` 独立、不可恢复的截面。北京时间
17:21 保存截面，17:25 完成评估。这是原 1000 次预算搜索的中途截面，非 500 次节点，
也没有重新进行 359 次预算的搜索。主搜索继续原预算，Test 不回传方法。

359 次提交包括质量阈值拒绝 275、覆盖不足 59、常数因子 9、行为重复 8、入库 7、
表达式重复 1。7 次准入分别位于第 84、136、216、240、266、289、345 次评估。
截面保留原子 trial、因子定义、顺序、训练方向和裁剪边界，逐字节核对复制记录；
不复制方法检查点，也不将实时并行请求状态或模型用量归入截面成本。

保存版本使用 `claude-haiku-5-5`、`v2_async`、5 个并行计划、Kernel 八字段、
60 个算子、空初始库、fold2、seed 42。库残差、尺度统计和命名机制区域开启，
退火温度为 0.45→0.08，开启 `adaptive_depth`，`region_budget_share=0.15`。
下游沿用 Linear OLS 与 LightGBM 50 轮、15 叶、leaf2000/depth5；预处理为
Train+Val 全局 P1–P99 与品种去均值，不按 Test 筛选因子或调整参数。

7 列全部可用，在 5,076,427 行 Train+Val 数据上拟合并保存后才加载 Test。
单因子 Test 加权 Pearson 均值为 0.002989、中位数为 0.000305，5 / 7 为正
（71.43%）；加权 Spearman 均值为 0.005358，6 / 7 为正。
单因子覆盖率范围为 86.770394%–91.474811%。

Linear 与 LightGBM 的 Test 加权 Pearson IC 分别为 0.008336、0.011564；
这里的 IC 使用期货 √成交额权重。两个模型均预测 2,403,290 / 2,558,829 行，
覆盖率为 93.921477%，155,539 条全因子缺失行不预测。
Val 的 21 / 21 对相似度均有效，绝对 Spearman 均值 / 中位数为
0.165671 / 0.008147，P90 / 最大值为 0.670958 / 0.699624，没有 ≥ 0.7 的配对。

冻结库、OOS、模型及相似度的身份、顺序、数据快照与配置一致，拟合来源记录为
`source_matches_search=false`，保留全部既有校验。本次通过公共单因子和组合模型入口
以及既有 Val 相似度计算函数评估，不调用自动填表入口；仓库总表逐字节未变，
工作区总表也未创建。结果仅保存在独立运行产物与本记录。
沿用原版本披露的 Kernel 聚合数据、供应商历史版本及历史留出限制，不作为新的盲测证据。

本次未修改搜索或评估源码，也未发起模型服务请求。准备与评估脚本通过 Ruff 和格式检查，
完整 `pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（152.11 秒）。

[截面准备脚本](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/prepare_prefix359.py)、
[独立评估入口](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/evaluate_prefix359.py)、
[固定版本](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/workspace/configs/versions/rq8_fold2_v2async_haiku_v4.json)、
[运行报告](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_async-42-315881ff68-prefix359/report.md)、
[组合模型报告](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_async-42-315881ff68-prefix359/model/report.md)、
[评估日志](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/prefix359.log)、
[完整检查](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/prefix359-checks.log)。

## 2026-11-04 V2 Sync DeepSeek v3：最新546次截面的模型预测

按用户要求再次评估 `rq8_fold2_v2sync_deepseek_v3`，检查时运行
`futures_kernel_5m_full-all-fold2-v2_sync-42-482d8fe0a1` 已提交 546 次候选评估，
入库 36 个因子。北京时间 17:32 保存独立、不可恢复的 `482d8fe0a1-prefix546`，
17:41 保存组合模型 Test 结果。原 1000 次预算搜索继续运行，Test 不回传方法；
保留此前 500 次截面的完整产物。

相较前 500 次新增 3 个因子，分别在第 509、537、541 次评估入库；原 33 个成员的
定义、顺序、训练方向及裁剪边界完全相同。546 次提交包括质量阈值拒绝 456、入库 36、
行为重复 47、覆盖不足 4、常数因子 2、训练指标未定义 1。截面保留原子 trial，
不复制方法检查点，不将实时方法状态或模型用量归入截面成本。

数据快照、方法配置、fold2、seed 42 和下游模型配置均与 500 次截面一致。模型使用全部
36 列，Train+Val 可用行从 5,308,097 增至 5,308,104。继续使用 Linear OLS、LightGBM
50 轮、15 叶、leaf2000/depth5，以及 Train+Val 全局 P1–P99 和品种去均值。
模型在加载 Test 前已保存，不按 Test 筛选因子或调参。

Test 的 √成交额加权 Pearson IC：Linear 从 0.016330 变为 0.016279，差值为
−0.000051；LightGBM 从 0.010640 变为 0.012949，差值为 +0.002309。
本次两个模型的加权 Spearman IC 分别为 0.012306、0.007433。
两个模型均预测 2,558,829 / 2,558,829 行，覆盖率为 100%；500 次截面为
2,558,825 / 2,558,829 行，因此本次额外覆盖 4 行，以上是各自实际覆盖样本上的结果。

本次以模型预测结果为报告范围，已生成的单因子及 Val 相似度产物保留。
原 33 个单因子的 Test 覆盖与指标元数据一致，指标数值在绝对误差 `1e-12` 内一致。
模型、冻结库及配置的身份和顺序核对一致，拟合如实记录 `source_matches_search=false`。
仓库根目录及工作区的结果总表逐字节未变，未加入本次 546 次截面。
沿用原版本披露的 Kernel 聚合数据、供应商历史版本及历史留出限制，不作为新的盲测证据。

本次未修改搜索或评估源码，也未发起模型服务请求。准备与评估脚本通过 Ruff 和格式检查，
完整 `pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（145.74 秒）。

[截面准备脚本](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/prepare_prefix546.py)、
[独立评估入口](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/evaluate_prefix546.py)、
[固定版本](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/workspace/configs/versions/rq8_fold2_v2sync_deepseek_v3.json)、
[运行报告](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-482d8fe0a1-prefix546/report.md)、
[组合模型报告](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-482d8fe0a1-prefix546/model/report.md)、
[评估日志](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/prefix546.log)、
[完整检查](../../artifacts/comparisons/v2sync_deepseek_fold2_20261103/prefix546-checks.log)。

## 2026-11-04 V2 Async Haiku v4：最新463次截面的模型预测

按用户要求评估 `rq8_fold2_v2async_haiku_v4` 的最新模型结果，检查时运行
`futures_kernel_5m_full-all-fold2-v2_async-42-315881ff68` 已提交 463 次候选评估，
入库 10 个因子。北京时间 17:57 保存独立、不可恢复的 `315881ff68-prefix463`，
17:59 完成两个组合模型的 Test 评估。只调用公共 `run_model` 拟合和 Test 入口，
没有计算单因子 OOS 或 Val 配对相关性，也没有调用自动填表流程。
主搜索继续原 1000 次预算，Test 不回传方法，保留此前 359 次截面的完整产物。

新增 3 个因子分别在第 401、440、459 次评估入库，原 7 个因子的定义、顺序、训练方向
及裁剪边界完全相同。463 次提交包括质量阈值拒绝 338、覆盖不足 88、行为重复 17、
入库 10、常数因子 9、表达式重复 1。原子 trial 逐字节复制并核对；不复制方法检查点，
不将实时并行请求状态或模型用量归入截面成本。

数据快照、方法配置、fold2、seed 42 和下游模型配置与 359 次截面一致。10 列全部可用，
Train+Val 可用样本仍为 5,076,427 行。模型沿用 Linear OLS、LightGBM 50 轮、15 叶、
leaf2000/depth5，以及 Train+Val 全局 P1–P99 和品种去均值；保存模型后才加载 Test，
不按 Test 筛选因子或调整参数。

Test 的 √成交额加权 Pearson IC：Linear 从 0.008336 降至 0.007717，差值为
−0.000619；LightGBM 从 0.011564 降至 0.009666，差值为 −0.001898。
本次加权 Spearman IC 分别为 0.015021、0.017633。
两个模型仍预测 2,403,290 / 2,558,829 行，覆盖率为 93.921477%，
155,539 条全因子缺失行不预测；训练和预测样本数均与上次相同。

模型与冻结库的身份、顺序、数据快照及配置核对一致，拟合如实记录
`source_matches_search=false`，保留全部既有校验。本截面没有 `oos.json` 或
`library_similarity.json`，符合仅评估模型预测的范围。仓库总表逐字节未变，
工作区总表也未创建；结果仅保存在独立产物与本记录。
沿用原版本披露的 Kernel 聚合数据、供应商历史版本及历史留出限制，不作为新的盲测证据。

本次未修改搜索或评估源码，也未发起模型服务请求。准备与评估脚本通过 Ruff 和格式检查，
完整 `pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（135.02 秒）。

[截面准备脚本](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/prepare_prefix463.py)、
[模型评估入口](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/evaluate_models_prefix463.py)、
[固定版本](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/workspace/configs/versions/rq8_fold2_v2async_haiku_v4.json)、
[运行报告](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_async-42-315881ff68-prefix463/report.md)、
[组合模型报告](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_async-42-315881ff68-prefix463/model/report.md)、
[评估日志](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/prefix463-models.log)、
[完整检查](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/prefix463-checks.log)。

## 2026-11-04 V2 Async Haiku v4：前500次截面的模型预测

按用户指定的 500 次节点评估 `rq8_fold2_v2async_haiku_v4`，从运行
`futures_kernel_5m_full-all-fold2-v2_async-42-315881ff68` 的前 500 个原子 trial 建立
独立、不可恢复的 `315881ff68-prefix500`，冻结全部 11 个入库因子。北京时间 18:25
保存截面，18:28 完成两个组合模型的 Test 评估。只评估模型预测，没有计算单因子 OOS
或 Val 配对相关性，也没有写入结果总表。主搜索继续原 1000 次预算，Test 不回传方法。

相较 463 次截面新增第 497 次入库的 `stop_sweep_range_position_36_control`；
原 10 个成员的定义、顺序、训练方向和裁剪边界完全相同。500 次提交包括质量阈值
拒绝 365、覆盖不足 97、行为重复 17、入库 11、常数因子 9、表达式重复 1。
原子 trial 逐字节复制并核对，不复制方法检查点或实时模型用量，保留此前截面产物。

数据快照、方法配置、fold2、seed 42 和下游模型配置与 463 次截面一致。11 列全部可用，
Train+Val 样本从 5,076,427 增至 5,115,130 行，增加 38,703 行。继续使用 Linear OLS、
LightGBM 50 轮、15 叶、leaf2000/depth5，以及 Train+Val 全局 P1–P99 和品种去均值。
模型保存后才加载 Test，不按 Test 筛选因子或调整参数。

Test 的 √成交额加权 Pearson IC：Linear 从 0.007717 变为 0.007729，差值为
+0.000011；LightGBM 从 0.009666 降至 0.006626，差值为 −0.003040。
本次加权 Spearman IC 分别为 0.014970、0.016480。
两个模型均预测 2,411,365 / 2,558,829 行，覆盖率为 94.237051%，
147,464 条全因子缺失行不预测。相较 463 次截面增加 8,075 条预测行，原覆盖率为
93.921477%；以上是各自实际覆盖样本上的指标，不是共同样本上的增量对照。

模型与冻结库的身份、顺序、数据快照及配置核对一致，拟合如实记录
`source_matches_search=false`，保留全部既有校验。本截面没有 `oos.json` 或
`library_similarity.json`，仓库总表逐字节未变，工作区总表也未创建。
沿用原版本披露的 Kernel 聚合数据、供应商历史版本及历史留出限制，不作为新的盲测证据。

本次未修改搜索或评估源码，也未发起模型服务请求。准备与评估脚本通过 Ruff 和格式检查，
完整 `pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（255.38 秒）。

[截面准备脚本](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/prepare_prefix500.py)、
[模型评估入口](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/evaluate_models_prefix500.py)、
[固定版本](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/workspace/configs/versions/rq8_fold2_v2async_haiku_v4.json)、
[运行报告](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_async-42-315881ff68-prefix500/report.md)、
[组合模型报告](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_async-42-315881ff68-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/prefix500-models.log)、
[完整检查](../../artifacts/comparisons/v2async_haiku_v4_fold2_20261104/prefix500-checks.log)。

## 2026-11-04 V2 Sync Luna v4：完整1000次的模型预测

`rq8_fold2_v2sync_luna_v4` 的运行
`futures_kernel_5m_full-all-fold2-v2_sync-42-57ed99c496` 已完成全部 1000 次尝试，
冻结 17 个入库因子。原启动脚本于北京时间 20:56 完成模型 Test 评估，并已自动将
完整运行填入仓库和工作区总表。本次通过公共 `run_model` 入口核验并复用已有拟合与
Test 缓存，没有重新训练、计算单因子 OOS 或配对相关性，也没有改写两份总表。

相较前 500 次截面的 14 个因子新增 3 个成员；原 14 个成员的定义、顺序、训练方向和
裁剪边界完全相同。数据快照、fold2、seed 42、方法配置与下游模型配置均相同。
17 列全部可用，没有移除列；Train+Val 训练行数仍为 5,308,104。
沿用 Linear OLS、LightGBM 50 轮、15 叶、leaf2000/depth5，以及 Train+Val 全局
P1–P99 和品种去均值，不根据 Test 重新选因子或调参。

Test 的 √成交额加权 Pearson IC：Linear 从 0.010182 升至 0.015134，差值为
+0.004952；LightGBM 从 0.008575 升至 0.010102，差值为 +0.001528。
本次加权 Spearman IC 分别为 0.016597、0.016273。
两模型均预测 2,558,829 / 2,558,829 行，覆盖率均为 100%，与 500 次截面一致。

公共入口的冻结库、配置、数据快照及依赖校验通过；拟合与 Test 的规格相同，
17 个模型因子的身份和顺序与冻结库一致。完整运行保存的
`source_matches_search=true`，沿用原拟合来源记录；500 次截面保留原来的 false。
核验前后，运行与冻结库、模型拟合与 Test、模型及运行报告、两份总表均逐字节未变。
本次未修改搜索或评估源码，也未发起模型服务请求。沿用原版本披露的 Kernel 聚合数据、
供应商历史版本及历史留出限制，不作为新的盲测证据。

完整 `pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（119.51 秒）。

[固定版本](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/workspace/configs/versions/rq8_fold2_v2sync_luna_v4.json)、
[1000次模型报告](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-57ed99c496/model/report.md)、
[500次模型报告](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v2_sync-42-57ed99c496-prefix500/model/report.md)、
[原运行日志](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/run.log)、
[模型核验日志](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/verify_full1000_models.log)、
[完整检查](../../artifacts/comparisons/v2sync_luna_v4_fold2_20261104/full1000-checks.log)。

## 2026-11-04 V3 Async：三模型 fold2 后台运行

V2 的诊断见本日此前各节：每次尝试的平均 Val 残差由噪声主导（Luna v4 的 733 个有测量
trial 正负各半），入库因子的 `residual_ic`、Val IC 与 Test 的相关均约 −0.06；地图状态
“5 次内最佳残差低于 0.005 即海床”使 V2 Async Haiku v4 停机时 90 个已测 Region 中 57 个
已关闭（尝试数中位数 5），且 24 个 Region 在 Region 层关闭时仍有未测完分档；该运行在
第 851 次因 Explorer 只剩一个方向、提案下限为二而以 unresolved 提前结束。这些诊断部分
使用了已观察的 Test 结果，因此 V3 不是对 Test 的盲测设计。

新方法 `v3_async`（[方法说明](../methods/v3_async.md)）在 `feature/v3-async` 分支实现，
未提交，运行均从该工作树启动（基础提交 9786b6c）。相对 V2 Async：Region 得分改为入库因子残差
之和／尝试；地图状态按 Region×特征跨度分档以准入判定，`probe_attempts=10`（按档计）；
Region 状态由分档推导，活动 Region 有开放档时继续深挖；Explorer 可提交一至四个方向；
提示词写明 5m 每小时 12 根、每交易日 48 根 bar。退火温度回到 0.25→0.02，不设
`region_budget_share`。V1、V1 Async、V2 的提案下限仍为二。完整 `pwsh -File scripts/check.ps1`
通过，1,345 项离线测试通过（396.79 秒）；新增 7 项 V3 测试覆盖分档边界、得分、按档深挖、
固定配置、单方向批次和中断恢复。

三组共用主表 fold2 设置（Kernel 八字段、all、1%–99% 裁剪、seed 42、空初始库、
`library_residual=residual_ic_v1`、统一下游模型），`parallel_plans=5`，预算 1000，
仅模型连接不同：

| 模型 | 版本 | 运行 | PID |
| --- | --- | --- | ---: |
| claude-haiku-5-5 | `rq8_fold2_v3async_haiku_v1` | `futures_kernel_5m_full-all-fold2-v3_async-42-600bd5bbd1` | 370303 |
| gpt-6-luna | `rq8_fold2_v3async_luna_v1` | `futures_kernel_5m_full-all-fold2-v3_async-42-8d95766cb7` | 370302 |
| deepseek-flash | `rq8_fold2_v3async_deepseek_v1` | `futures_kernel_5m_full-all-fold2-v3_async-42-4e2a92c781` | 377799 |

DeepSeek 首次启动时 API 密钥误带引号，首个 Explorer 请求返回 HTTP 401，运行在提交任何
trial 前失败；去掉引号后以原脚本恢复同一运行，失败日志保留为 `run-401.log`。三组均已
提交首批候选，运行结束后由原脚本冻结、评估 Test 并填表。

[Haiku 日志](../../artifacts/comparisons/v3async_haiku_fold2_20261104/run.log)、
[Luna 日志](../../artifacts/comparisons/v3async_luna_fold2_20261104/run.log)、
[DeepSeek 日志](../../artifacts/comparisons/v3async_deepseek_fold2_20261104/run.log)、
[检查日志](../../artifacts/comparisons/v3async_haiku_fold2_20261104/checks.log)。

## 2026-11-04 V2 Sync DeepSeek v3：按用户要求停止

按用户要求，V3 Async 三组启动后停止 `futures_kernel_5m_full-all-fold2-v2_sync-42-482d8fe0a1`
（PID 2235504）。以 SIGINT 中断，运行状态为 `interrupted`，已提交 698 个原子 trial；
未冻结，未评估完整 Test。此前的 500、546 次截面及其评估保留不变。

## 2026-11-04 V3 Async：Luna／Haiku／DeepSeek 最新截面的模型预测

按用户要求评估新启动的 `rq8_fold2_v3async_{luna,haiku,deepseek}_v1`。
北京时间 23:48:52 固定三组当时已提交的前缀，分别从 `8d95766cb7`、`600bd5bbd1`、
`4e2a92c781` 建立独立、不可恢复的 `prefix317`、`prefix183`、`prefix110`；
23:51 至 23:52 完成模型 Test。原搜索继续 1000 次预算，Test 不回传方法。
只通过公共 `run_model` 拟合与评估 Linear、LightGBM，不计算单因子 OOS 或配对相关性。

三组数据快照、Kernel 八字段、fold2、seed 42、评价规则与下游模型配置相同。
使用全部冻结因子，没有按 Test 筛选；三组的 7、7、9 列均可用，没有移除列。
Train+Val 训练行数依次为 5,293,626、5,307,576、5,307,801。沿用 Linear OLS、
LightGBM 50 轮、15 叶、leaf2000/depth5，以及 Train+Val 全局 P1–P99 和品种去均值。

以下为 Test 的 √成交额加权 Pearson IC，覆盖率对每组的两个模型均相同：

| 生成模型 | 尝试次数 | 入库因子 | Linear | LightGBM | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| gpt-6-luna | 317 | 7 | 0.018719 | 0.016601 | 99.938292% |
| claude-haiku-5-5 | 183 | 7 | 0.005619 | 0.009691 | 99.988745% |
| deepseek-flash | 110 | 9 | 0.014673 | 0.017910 | 99.991637% |

三组均有 2,558,829 条可评估行，实际预测行数依次为 2,557,250、2,558,541、2,558,615；
分别有 1,579、288、214 条全因子缺失行不预测。加权 Spearman IC（Linear／LightGBM）
依次为 0.018531／0.016923、0.007437／0.008757、0.005288／0.011240。
目前 Linear 以 Luna 截面较高，LightGBM 以 DeepSeek 截面较高；三组尝试次数不同，
以上指标采用各自实际覆盖样本，不能视为相同预算、共同样本上的最终方法对照。

原子 trial 逐字节复制并核对，保留准入时的定义、顺序、训练方向和裁剪边界；不复制
方法检查点或实时模型用量。三组冻结库、运行配置、数据快照及模型因子顺序校验通过，
`source_matches_search=false` 如实保留拟合来源。组合模型、评价与表达式模块与搜索工作树
逐字节相同。运行与冻结库未被模型评估改写，未生成 `oos.json` 或 `library_similarity.json`，
仓库与各工作区总表均未改变。本次未修改平台源码，也未发起模型服务请求。
沿用版本披露的 Kernel 聚合数据、供应商历史版本及已观察 Test 的历史留出限制，
不作为新的盲测证据。

截面与模型评估脚本通过 Ruff 和格式检查；当前评估工作区的完整
`pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（145.42 秒）。

[截面准备脚本](../../artifacts/comparisons/v3async_models_latest_20261104/prepare_snapshots.py)、
[模型评估入口](../../artifacts/comparisons/v3async_models_latest_20261104/evaluate_models.py)、
[截面与准入记录](../../artifacts/comparisons/v3async_models_latest_20261104/prepare.log)、
[Luna 模型报告](../../artifacts/comparisons/v3async_luna_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-8d95766cb7-prefix317/model/report.md)、
[Haiku 模型报告](../../artifacts/comparisons/v3async_haiku_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-600bd5bbd1-prefix183/model/report.md)、
[DeepSeek 模型报告](../../artifacts/comparisons/v3async_deepseek_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-4e2a92c781-prefix110/model/report.md)、
[Luna 评估日志](../../artifacts/comparisons/v3async_models_latest_20261104/luna-prefix317-models.log)、
[Haiku 评估日志](../../artifacts/comparisons/v3async_models_latest_20261104/haiku-prefix183-models.log)、
[DeepSeek 评估日志](../../artifacts/comparisons/v3async_models_latest_20261104/deepseek-prefix110-models.log)、
[完整检查](../../artifacts/comparisons/v3async_models_latest_20261104/checks.log)。

## 2026-11-05 V3 Async：9／8／12 因子的模型预测复测

按用户要求继续评估同一批 V3 Async 的最新结果。北京时间 11 月 4 日 23:59:32
固定 Luna／Haiku／DeepSeek 的 380／210／115 次截面，分别冻结 9／8／12 个因子；
11 月 5 日 00:02 至 00:03 完成两个组合模型的 Test。对应独立目录为
`8d95766cb7-prefix380`、`600bd5bbd1-prefix210`、`4e2a92c781-prefix115`。
原搜索继续，Test 不回传方法；只评估模型预测，不计算单因子 OOS 或配对相关性。

相比此前 317／183／110 次截面，分别新增 2／1／3 个因子，准入尝试编号为
Luna 339、380，Haiku 185，DeepSeek 111、112、115。旧成员的完整冻结记录、顺序、
训练方向和裁剪边界相同，数据快照、fold、方法与模型配置均未改变。
所有 9／8／12 列都可用，没有移除列。Train+Val 训练行数依次为
5,308,086、5,307,576、5,307,801；Luna 增加 14,460 行，其余两组不变。
沿用固定的 Linear OLS、LightGBM 50 轮、15 叶、leaf2000/depth5，以及
Train+Val 全局 P1–P99 和品种去均值。

Test 的 √成交额加权 Pearson IC，括号内为相对此前截面的变化：

| 生成模型 | 尝试次数 | 因子数 | Linear | LightGBM | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| gpt-6-luna | 380 | 9 | 0.019577（+0.000858） | 0.018813（+0.002212） | 99.999844% |
| claude-haiku-5-5 | 210 | 8 | 0.003758（−0.001861） | 0.007227（−0.002464） | 99.988745% |
| deepseek-flash | 115 | 12 | 0.014821（+0.000148） | 0.015435（−0.002475） | 99.991637% |

两模型在各组内覆盖相同。三组实际预测行数依次为 2,558,825、2,558,541、2,558,615，
可评估行数均为 2,558,829；分别有 4、288、214 条全因子缺失行不预测。
Luna 比上次多预测 1,575 行，其余两组的预测覆盖不变。加权 Spearman IC
（Linear／LightGBM）依次为 0.018555／0.018729、0.005672／0.006593、0.004661／0.005088。
本次两个预测模型均以 Luna 截面较高；三组尝试次数不同，指标按各自实际覆盖样本计算，
尤其 Luna 新旧样本覆盖有变化，不是共同样本上的增量对照。

复用已有截面准备与模型评估脚本，保留旧截面及日志；原子 trial 逐字节复制并核对，
不复制方法检查点或实时模型用量。三组通过公共冻结、配置、数据与模型身份校验，
`source_matches_search=false` 如实记录拟合来源。模型评估未改写运行或冻结库；
没有生成单因子 OOS、库内相关性文件，也没有改变仓库或工作区总表。
本次未修改平台源码或发起模型服务请求；原版本的数据与历史留出限制继续适用。

完整 `pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（191.13 秒）。

[截面与准入记录](../../artifacts/comparisons/v3async_models_latest_20261104/prepare-9-8-12.log)、
[Luna 模型报告](../../artifacts/comparisons/v3async_luna_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-8d95766cb7-prefix380/model/report.md)、
[Haiku 模型报告](../../artifacts/comparisons/v3async_haiku_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-600bd5bbd1-prefix210/model/report.md)、
[DeepSeek 模型报告](../../artifacts/comparisons/v3async_deepseek_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-4e2a92c781-prefix115/model/report.md)、
[Luna 评估日志](../../artifacts/comparisons/v3async_models_latest_20261104/luna-prefix380-models.log)、
[Haiku 评估日志](../../artifacts/comparisons/v3async_models_latest_20261104/haiku-prefix210-models.log)、
[DeepSeek 评估日志](../../artifacts/comparisons/v3async_models_latest_20261104/deepseek-prefix115-models.log)、
[完整检查](../../artifacts/comparisons/v3async_models_latest_20261104/checks-9-8-12.log)。

## 2026-11-05 V3 Async：Luna／Haiku 各10因子的模型预测

按用户要求只复测 Luna、Haiku。北京时间 00:15:59 固定各自最新已提交的
457／233 次截面，生成 `8d95766cb7-prefix457`、`600bd5bbd1-prefix233`，
两组均冻结 10 个因子，00:19 完成模型 Test。本次不评估 DeepSeek，原搜索继续，
Test 不回传方法；只调用公共模型拟合与 Test，不计算单因子 OOS 或配对相关性。

相较此前 380／210 次截面，Luna 新增第 393 次入库的
`peer_leadership_move_magnitude_interaction`；Haiku 新增第 221、228 次入库的
`cross_product_dispersion_rank_gated_move_5m_12_r78` 与
`cross_product_dispersion_tail_gate_5m_12_z78`。旧成员的完整冻结记录、顺序、方向和
裁剪边界相同，数据快照、fold、方法及模型配置未变。两组各 10 列全部可用，没有移除列；
Train+Val 训练行数仍分别为 5,308,086、5,307,576。沿用固定 Linear OLS、
LightGBM 50 轮、15 叶、leaf2000/depth5，以及 Train+Val 全局 P1–P99 和品种去均值。

Test 的 √成交额加权 Pearson IC，括号内为相对此前 9／8 因子截面的变化：

| 生成模型 | 尝试次数 | 因子数 | Linear | LightGBM | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| gpt-6-luna | 457 | 10 | 0.020118（+0.000540） | 0.018723（−0.000090） | 99.999844% |
| claude-haiku-5-5 | 233 | 10 | 0.003803（+0.000045） | 0.006619（−0.000608） | 99.988745% |

两组各自的两模型覆盖率相同。实际预测行数仍为 2,558,825、2,558,541，
可评估行数均为 2,558,829，分别有 4、288 条全因子缺失行不预测。
训练行数与预测覆盖均未变化。加权 Spearman IC（Linear／LightGBM）分别为
0.018633／0.018324、0.006118／0.006358。两组的 Linear 均提高，LightGBM 均下降；
尝试次数仍不同，结果属于各自搜索阶段的历史留出诊断。

复用已有脚本，仅选择 Luna、Haiku 准备截面；保留旧产物。原子 trial 逐字节复制并核对，
不复制方法检查点或实时模型用量。公共冻结、配置、数据及模型因子顺序校验通过，
`source_matches_search=false` 如实记录拟合来源。运行与冻结库未被评估改写，
未生成单因子 OOS 或库内相关性文件，也没有改变仓库或工作区总表。
本次未修改平台源码或发起模型服务请求，沿用原版本的数据与历史留出限制。
完整 `pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（146.14 秒）。

[截面与准入记录](../../artifacts/comparisons/v3async_models_latest_20261104/prepare-10-10.log)、
[Luna 模型报告](../../artifacts/comparisons/v3async_luna_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-8d95766cb7-prefix457/model/report.md)、
[Haiku 模型报告](../../artifacts/comparisons/v3async_haiku_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-600bd5bbd1-prefix233/model/report.md)、
[Luna 评估日志](../../artifacts/comparisons/v3async_models_latest_20261104/luna-prefix457-models.log)、
[Haiku 评估日志](../../artifacts/comparisons/v3async_models_latest_20261104/haiku-prefix233-models.log)、
[完整检查](../../artifacts/comparisons/v3async_models_latest_20261104/checks-10-10.log)。

## 2026-11-05 V3 Async Luna：前500次模型评估与填表

按用户要求评估并填表 `rq8_fold2_v3async_luna_v1` 的精确前 500 次截面。
北京时间 00:25:27 从 `futures_kernel_5m_full-all-fold2-v3_async-42-8d95766cb7`
建立独立、不可恢复的 `8d95766cb7-prefix500`，冻结全部 11 个入库因子。
原搜索继续 1000 次预算，Test 不回传方法；本次只评估 Linear、LightGBM 预测，
没有计算单因子 OOS 或配对相关性。

相较 457 次截面新增第 473 次入库的 `persistent_hourly_failed_extreme_retreat`，
原 10 个成员的完整冻结记录、顺序、训练方向和裁剪边界相同。前 500 次包括质量阈值
拒绝 434、入库 11、行为重复 36、常数因子 10、编译失败 4、覆盖不足 2、表达式重复 3。
原子 trial 逐字节复制并核对，不复制方法检查点或实时模型用量；此前截面保持不变。

数据快照、fold2、seed 42、方法与模型配置均未改变。11 列全部可用，没有移除列，
Train+Val 训练行数仍为 5,308,086。沿用固定 Linear OLS、LightGBM 50 轮、15 叶、
leaf2000/depth5，以及 Train+Val 全局 P1–P99 和品种去均值。

Test √成交额加权 Pearson IC：Linear 为 0.019912，较 457 次的 0.020118 下降
0.000206；LightGBM 为 0.017605，较此前 0.018723 下降 0.001119。
加权 Spearman IC 分别为 0.018712、0.017736。两个模型均预测
2,558,825 / 2,558,829 行，覆盖率为 99.999844%，4 条全因子缺失行不预测。
训练行数与预测覆盖均与上次相同。

公共冻结、配置、数据快照及模型因子顺序校验通过，`source_matches_search=false`
如实记录拟合来源。模型评估未改写运行或冻结库，未生成 `oos.json` 或
`library_similarity.json`。按用户此次授权，已将 500 次的两项模型 Pearson 写入
[实验总览](../../experiment_overview.md#期货因子搜索fold2)，关联运行与模型报告，
并注明 11 个因子、实际覆盖及仅评估模型的范围。三个未评估的单因子指标栏以“—”表示，
没有填零或借用其他截面的结果；相似度表未增加行。填表使用既有 Markdown 行渲染和
共享文件锁，移除新增行与说明后逐字节等于填表前内容，其余历史结果保留。
工作区总表未创建。

本次未修改平台源码或发起模型服务请求，沿用原版本的数据与历史留出限制。
准备脚本通过 Ruff 和格式检查；完整 `pwsh -File scripts/check.ps1` 通过，
1,330 项离线测试通过（198.91 秒）。

[500次准备脚本](../../artifacts/comparisons/v3async_luna_fold2_20261104/prepare_prefix500.py)、
[截面与准入记录](../../artifacts/comparisons/v3async_luna_fold2_20261104/prefix500-prepare.log)、
[模型报告](../../artifacts/comparisons/v3async_luna_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-8d95766cb7-prefix500/model/report.md)、
[模型评估日志](../../artifacts/comparisons/v3async_luna_fold2_20261104/prefix500-models.log)、
[填表核验日志](../../artifacts/comparisons/v3async_luna_fold2_20261104/prefix500-publish.log)、
[完整检查](../../artifacts/comparisons/v3async_luna_fold2_20261104/prefix500-checks.log)。

## 2026-11-05 V3 Async DeepSeek：思考强度 low 对照

平台此前不发送思考参数，`deepseek-flash`（API 返回的模型名；底层版本未经官方确认）默认开启
思考。小请求实测：默认与 `reasoning_effort` 为 low／high／max 时均产生推理 token，
`thinking: disabled` 不产生；一道因子设计题上推理 token 为默认 2,223、high 1,872、low 836。
V3 DeepSeek 运行平均每次请求输出 5,305 token，速度约为 Luna 的三分之一。

`v3_async` 新增可选参数 `reasoning_effort`（low／medium／high／max）：设置时随 Chat 请求发送并
写入运行配置；未设置时请求体与记录配置均不变，已运行的 V3 组恢复不受影响。新增测试覆盖发送、
记录与非法值；V3 测试 8 项通过。

对照运行 `futures_kernel_5m_full-all-fold2-v3_async-42-debb1b1dfd`（PID 1100521），版本
`rq8_fold2_v3async_deepseek_low_v1`，与 `rq8_fold2_v3async_deepseek_v1` 仅差
`reasoning_effort=low`；已提交首批方向。降低思考强度会改变模型能力，结果与既有 DeepSeek 组
分开报告。

[运行日志](../../artifacts/comparisons/v3async_deepseek_low_fold2_20261105/run.log)。

## 2026-11-05 V3 Async Luna：前500次单因子补评

用户要求补测其他指标，复用已有 `8d95766cb7-prefix500` 的 500 次截面和全部 11 个
冻结因子，通过公共 `test_frozen` 完成单因子 Test。沿用冻结的 Train 方向与裁剪边界，
不在 Test 上拟合或筛选，11 个因子全部计算成功。

Test √成交额加权 Pearson IC 均值为 0.007264，中位数为 0.006719；
11 / 11 个因子的加权 Pearson IC 为正，比例为 100.00%。加权 Spearman IC 均值为
0.003325。各单因子预测覆盖率为 78.984606%–99.979366%；逐因子的加权、等权
Pearson／Spearman 和覆盖率见运行报告及 `oos.json`。

运行与冻结库、既有模型拟合和 Test 产物及模型报告逐字节保持不变，未重新拟合组合模型。
Linear／LightGBM 的 Test 加权 Pearson 仍为 0.019912／0.017605，
两者预测覆盖率仍为 99.999844%（2,558,825 / 2,558,829 行）。
新增单因子 `oos.json`，由公共流程刷新运行报告；没有计算库内配对相关性。

[实验总览](../../experiment_overview.md#期货因子搜索fold2)中该 500 次行的三个单因子
指标栏已补齐，范围说明同步更新。两项模型单元格、其他行与相似度表保持不变，
工作区总表未创建。填表复用既有行渲染和共享文件锁，核对其他内容未变。
此前仅评估模型的记录保留为当时事实。

本次未修改平台源码或发起模型服务请求，沿用该版本已记录的数据与历史留出诊断限制。
完整 `pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（153.58 秒）。

[运行报告](../../artifacts/comparisons/v3async_luna_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-8d95766cb7-prefix500/report.md)、
[单因子原始结果](../../artifacts/comparisons/v3async_luna_fold2_20261104/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-8d95766cb7-prefix500/oos.json)、
[评估日志](../../artifacts/comparisons/v3async_luna_fold2_20261104/prefix500-single-factors.log)、
[填表核验日志](../../artifacts/comparisons/v3async_luna_fold2_20261104/prefix500-single-factors-publish.log)、
[完整检查](../../artifacts/comparisons/v3async_luna_fold2_20261104/prefix500-single-factor-checks.log)。

## 2026-11-05 V3 Async 四组 1000 次完成

Luna、Haiku、DeepSeek（服务默认思考）与 DeepSeek（`reasoning_effort = "low"`）四组 fold2 / seed 42
均完成 1000 次尝试，状态为 frozen，并由公共流程完成冻结、Test 评估和填表。

| 模型 | 入库 | Test 单因子加权 Pearson 均值／中位数 | 正 IC | 加权 Spearman 均值 | Linear | LightGBM |
| --- | --- | --- | --- | --- | --- | --- |
| gpt-6-luna | 21 | 0.005970 / 0.006418 | 21 / 21 | 0.002725 | 0.019660 | 0.017815 |
| claude-haiku-5-5 | 43 | 0.003259 / 0.002384 | 34 / 43 | 0.000900 | 0.011279 | 0.016154 |
| deepseek-flash | 95 | 0.003619 / 0.003513 | 79 / 95 | 0.003187 | 0.015633 | 0.009349 |
| deepseek-flash（low） | 83 | 0.004421 / 0.004357 | 73 / 83 | 0.004879 | 0.019145 | 0.005648 |

DeepSeek 默认思考组在第 846 次后失败一次：Analyst 请求在给出正文前达到服务端默认输出上限
（`chat model output limit reached before any message content`）。该配置未设 `max_output_tokens`。
失败请求不形成 trial；用原 `run.py` 恢复同一运行后跑完剩余预算，未修改源码或配置。
原日志保留为 `run-length846.log`。其余三组无异常退出。

## 2026-11-05 V3 Async 新分档规则：五组启动

依据四组 V3 运行的分析，`feature/v3-async` 改为：特征跨度分为日内（≤1 交易日）、1–20 日、20 日以上；
每档 `probe_attempts = 20`；Region 状态只由已测分档推导（`scale_bands = "intraday_1d_20d"`）。
依据：六组 fold2 运行中按新分档的入库因子同档平均 |相关| 普遍高于跨档（V1 Haiku 531d 为 0.134／0.070，
旧分档为 0.089／0.103），20 日以上因子 Test 衰减最大；V3 Haiku 的成交量、持仓量方向在每档 10 次未入库后
即被判海床。旧运行配置无 `scale_bands`，观测台按旧规则展示。完整检查 1,347 项通过。

启动五组（fold2 / seed 42 / 1000 次，其余设置同各自 v1 版本）：

| 目录 | 版本 | 模型 | parallel_plans |
| --- | --- | --- | --- |
| `v3async2_luna_fold2_20261105` | `rq8_fold2_v3async_luna_v2` | gpt-6-luna | 5 |
| `v3async2_luna_p16_fold2_20261105` | `rq8_fold2_v3async_luna_p16_v2` | gpt-6-luna | 16 |
| `v3async2_haiku_fold2_20261105` | `rq8_fold2_v3async_haiku_v2` | claude-haiku-5-5 | 5 |
| `v3async2_haiku_p10_fold2_20261105` | `rq8_fold2_v3async_haiku_p10_v2` | claude-haiku-5-5 | 10 |
| `v3async2_deepseek_low_fold2_20261105` | `rq8_fold2_v3async_deepseek_low_v2` | deepseek-flash（low） | 5 |

并发组与 5 并发组仅差 `parallel_plans`，用于比较最终效果与耗时。

## 2026-11-05 高并发两组改用并行评估重跑

按用户要求停止 `v3async2_haiku_p10_fold2_20261105`（109 次）与 `v3async2_luna_p16_fold2_20261105`（98 次），
产物保留并以 `STOPPED` 标注。两组的瓶颈是串行评估（约 88% 墙钟时间），提高 `parallel_plans` 不提速。
`feature/parallel-eval`（工作区 `AlphaAtlas-parallel`）照 ChaoticTrader_Future Miner 的 `max_parallel`
加入 `parallel_evaluations`：不同研究组的候选在线程池中同时计算 Train／Valid，准入、残差、原子 trial
与 `tell` 仍按 trial 编号串行；在算候选写入检查点，恢复时复用已提交 trial。

| 目录 | 版本 | 模型 | parallel_plans | parallel_evaluations |
| --- | --- | --- | --- | --- |
| `v3async3_haiku_p10_fold2_20261105` | `rq8_fold2_v3async_haiku_p10_pe_v3` | claude-haiku-5-5 | 10 | 10 |
| `v3async3_luna_p16_fold2_20261105` | `rq8_fold2_v3async_luna_p16_pe_v3` | gpt-6-luna | 16 | 16 |

生成候选时的地图可能落后于在算候选，故与串行组不是同一轨迹；与 5 并发组比较时一并披露。

## 2026-11-05 V3 Async 新分档 Luna：11／10 因子模型预测

按用户要求评估正在运行的两组 Luna，在北京时间 11:39:34 读取各自已提交检查点并冻结
独立截面。`rq8_fold2_v3async_luna_p16_pe_v3`（`bf0c21a38d`）取前 259 次、11 个因子，
16 个并发计划、16 路并行评估；`rq8_fold2_v3async_luna_v2`（`510cffd920`）取前 271 次、
10 个因子，5 个并发计划、串行评估。原搜索继续运行，Test 不回传方法。

两组数据快照、八字段、fold2、seed 42、准入规则与模型配置相同，均采用新分档与每档
20 次探测。方法配置仅有 `parallel_plans`、`parallel_evaluations` 差异；并行候选生成时
可能尚未收到在算候选的准入反馈，搜索轨迹不同。模型沿用 Linear OLS、LightGBM 50 轮、
15 叶、leaf2000/depth5，以及 Train+Val 全局 P1–P99 裁剪和品种去均值。

Test √成交额加权 Pearson IC：

| 实验 | 尝试次数 | 入库因子 | Linear | LightGBM | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Luna，16 计划／16 路评估（bf0c21a38d） | 259 | 11 | 0.014802 | 0.018808 | 99.976943% |
| Luna，5 计划／串行评估（510cffd920） | 271 | 10 | 0.009459 | 0.012289 | 100.000000% |

对应加权 Spearman IC（Linear／LightGBM）分别为 0.018436／0.018493、
0.015513／0.016501。每组两模型覆盖率相同：11 因子组预测 2,558,239 / 2,558,829 行，
590 条全因子缺失行不预测；10 因子组预测全部 2,558,829 行。
Train+Val 训练行数分别为 5,307,056、5,308,104，全部 11／10 列可用，没有移除列。
当前 11 因子组的两项 Pearson 较高；尝试次数和覆盖样本不同，属于进度截面诊断，
不能据此将差异归因于并发设置。

原子 trial 逐字节复制并核对，全部已入库定义、顺序、Train 方向和裁剪记录保持不变；
不复制方法检查点或实时模型用量。公共配置、数据、冻结及因子顺序校验通过，
`source_matches_search=false` 如实记录拟合来源。模型评估未改写截面的运行与冻结库。
仅评估组合预测，没有计算单因子 OOS 或库内配对相关性，仓库和工作区总表均未改写。

本次没有修改平台源码或发起模型服务请求。沿用版本中已披露的 Kernel 聚合数据、
未认证完整供应商历史版本 PIT、Test 已观察的历史留出诊断限制。
准备脚本通过 Ruff 与格式检查；完整 `pwsh -File scripts/check.ps1` 通过，
1,330 项离线测试通过（305.20 秒）。

[截面准备脚本](../../artifacts/comparisons/v3async_luna_latest_20261105/prepare_snapshots.py)、
[截面与准入记录](../../artifacts/comparisons/v3async_luna_latest_20261105/prepare.log)、
[11因子模型报告](../../artifacts/comparisons/v3async3_luna_p16_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-bf0c21a38d-prefix259/model/report.md)、
[10因子模型报告](../../artifacts/comparisons/v3async2_luna_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-510cffd920-prefix271/model/report.md)、
[11因子评估日志](../../artifacts/comparisons/v3async_luna_latest_20261105/luna-p16-prefix259-models.log)、
[10因子评估日志](../../artifacts/comparisons/v3async_luna_latest_20261105/luna-prefix271-models.log)、
[完整检查](../../artifacts/comparisons/v3async_luna_latest_20261105/checks.log)。

## 2026-11-05 V3 Async 新分档 Haiku：8／12 因子模型预测

按用户更新后的 8／12 个入库因子评估两组运行，在北京时间 12:01:58 冻结各自最新已提交
截面。`rq8_fold2_v3async_haiku_p10_pe_v3`（`a13803e3f9`）取前 233 次、8 个因子，
10 个并发计划、10 路并行评估；`rq8_fold2_v3async_haiku_v2`（`20a1012038`）取前
267 次、12 个因子，5 个并发计划、串行评估，包含第 266 次新入库成员。

两组模型均为 `claude-haiku-5-5`，方法配置只差 `parallel_plans`、`parallel_evaluations`。
同一数据快照、八字段、fold2、seed 42 和评估规则通过核对；模型沿用固定 Linear OLS、
LightGBM 50 轮、15 叶、leaf2000/depth5，以及 Train+Val 全局 P1–P99 裁剪和品种去均值。

Test √成交额加权 Pearson IC：

| 实验 | 尝试次数 | 入库因子 | Linear | LightGBM | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Haiku，10 计划／10 路评估（a13803e3f9） | 233 | 8 | 0.009662 | 0.012167 | 100.000000% |
| Haiku，5 计划／串行评估（20a1012038） | 267 | 12 | 0.015280 | 0.019963 | 99.999844% |

加权 Spearman IC（Linear／LightGBM）分别为 0.017824／0.017073、0.020712／0.024876。
8 因子组预测全部 2,558,829 行；12 因子组预测 2,558,825 / 2,558,829 行，
4 条全因子缺失行不预测。Train+Val 训练行数分别为 5,308,104、5,308,046，
全部 8／12 列可用，没有移除列。当前 12 因子组两种模型的 Pearson 较高；预算节点不同，
并行候选收到的地图反馈也可能不同，本次不将结果差异归因于并发设置。

原子 trial 逐字节复制并核对，保留全部成员定义、顺序、Train 方向和裁剪记录，
不复制方法检查点或实时模型用量。公共冻结、配置、数据和因子顺序校验通过，
`source_matches_search=false` 如实记录拟合来源。评估未改写截面的运行与冻结库；
原搜索继续运行，Test 不回传方法。仅评估组合预测，不计算单因子 OOS 或库内配对相关性，
仓库和工作区总表均未改写。

本次未修改平台源码或发起模型服务请求，沿用版本中 Kernel 聚合数据、未认证完整供应商
历史版本 PIT 和 Test 已观察的历史留出诊断限制。准备脚本通过 Ruff 和格式检查；
完整 `pwsh -File scripts/check.ps1` 通过，1,330 项离线测试通过（166.46 秒）。

[截面准备脚本](../../artifacts/comparisons/v3async_haiku_latest_20261105/prepare_snapshots.py)、
[截面与准入记录](../../artifacts/comparisons/v3async_haiku_latest_20261105/prepare.log)、
[8因子模型报告](../../artifacts/comparisons/v3async3_haiku_p10_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-a13803e3f9-prefix233/model/report.md)、
[12因子模型报告](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-prefix267/model/report.md)、
[8因子评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-p10-prefix233-models.log)、
[12因子评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-prefix267-models.log)、
[完整检查](../../artifacts/comparisons/v3async_haiku_latest_20261105/checks.log)。

## 2026-11-05 V3 Async 新分档 DeepSeek low：9 因子模型预测与快捷入口

按用户指定的 9 个入库因子评估 `rq8_fold2_v3async_deepseek_low_v2`，来源为
`futures_kernel_5m_full-all-fold2-v3_async-42-bfabcc7663`。第 248 次入库第 9 个成员，
第 251 次入库第 10 个成员，因此取最后仍为 9 个成员的前 250 次截面。
北京时间 12:13:46 建立独立 `bfabcc7663-prefix250`；原搜索继续运行，Test 不回传方法。

生成模型为 `deepseek-flash`，`reasoning_effort=low`，5 个并发计划、串行评估。
数据快照、八字段、fold2、seed 42、准入和预测模型设置与同日两组 Haiku 一致，
沿用固定 Linear OLS、LightGBM 50 轮、15 叶、leaf2000/depth5，
以及 Train+Val 全局 P1–P99 裁剪和品种去均值。

Test √成交额加权 Pearson IC：Linear 为 0.007325，LightGBM 为 0.010550；
加权 Spearman IC 分别为 0.016166、0.018138。两个模型均预测
2,413,625 / 2,558,829 行，覆盖率为 94.325373%，145,204 条全因子缺失行不预测。
Train+Val 训练行数为 5,161,642，9 列全部可用，没有移除列。与其他截面对比时，
尝试次数和实际预测覆盖不同，不能直接将指标差异归因于生成模型或并发设置。

公共配置、数据、冻结库和因子身份校验通过；保留原子 trial、全部成员定义、顺序、
Train 方向与裁剪记录，`source_matches_search=false` 如实记录拟合来源。
只评估组合预测，未计算单因子 OOS 或库内配对相关性，实验总表未改写。
最初 245 次／8 因子截面的已生成产物保留，本记录的结论仅对应 250 次／9 因子。
沿用版本已披露的 Kernel 聚合数据、未认证完整供应商历史版本 PIT、Test 已观察的限制。

按用户要求新增 [`scripts/evaluate_snapshot.py`](../../scripts/evaluate_snapshot.py)，
接受运行目录、完整或短 ID、实验版本或 comparisons 目录名，一次完成截面冻结、固定模型
拟合及 Test。默认取最新已提交记录，也可指定因子数或尝试次数；同一截面复用既有结果。
复用公共模型入口，不新增评估规则、模型调用、配对相关性或自动填表流程。
命令说明见[快捷预测评估](../model.md#运行中截面的快捷预测评估)。
8 项合成数据专项测试通过，覆盖已提交边界、精确因子／尝试数、冻结记录保留、
已有截面复用与篡改拒绝、不可用数量、检查点不一致及来源歧义。
完整 `pwsh -File scripts/check.ps1` 通过，1,338 项离线测试通过（259.79 秒）。
同一 9 因子截面通过快捷命令复测耗时 3.10 秒，运行、冻结库、模型拟合、Test、模型报告
及总表逐字节保持不变，确认公共模型缓存复用生效。

[9因子模型报告](../../artifacts/comparisons/v3async2_deepseek_low_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-bfabcc7663-prefix250/model/report.md)、
[9因子评估日志](../../artifacts/comparisons/v3async_deepseek_low_latest_20261105/deepseek-9factors-models.log)、
[9因子缓存复测](../../artifacts/comparisons/v3async_deepseek_low_latest_20261105/deepseek-9factors-cache.log)、
[快捷入口检查](../../artifacts/comparisons/v3async_deepseek_low_latest_20261105/quick-path-checks.log)。

## 2026-11-05 V3 Async 新分档 DeepSeek low：快捷评估11因子

按用户要求直接执行 `scripts/evaluate_snapshot.py bfabcc7663 --factors 11`，
北京时间 12:20:21 冻结前 256 次、11 个因子的截面。来源仍为
`rq8_fold2_v3async_deepseek_low_v2`，原搜索继续运行，评估配置与此前 9 因子截面相同。

Test √成交额加权 Pearson IC：Linear 为 0.007362，较 9 因子的 0.007325 提高
0.000037；LightGBM 为 0.012489，较此前 0.010550 提高 0.001939。
加权 Spearman IC 分别为 0.016630、0.018184。两模型预测覆盖率仍为 94.325373%，
预测 2,413,625 / 2,558,829 行，145,204 条全因子缺失行不预测。
Train+Val 训练行数为 5,171,810，较此前增加 10,168 行；11 列全部可用，没有移除列。

复用快捷入口的冻结与公共模型校验，未调整模型参数或依据 Test 筛选成员。
原 9 个成员的完整冻结记录和顺序保持不变，保留历史截面；仅计算组合预测，
没有单因子 OOS、库内配对相关性或总表写入。结果仍属于该版本已有数据限制下的历史
留出诊断。快捷入口沿用本轮已通过的 8 项专项测试和 1,338 项完整离线检查，未再修改实现。

[11因子模型报告](../../artifacts/comparisons/v3async2_deepseek_low_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-bfabcc7663-prefix256/model/report.md)、
[11因子评估日志](../../artifacts/comparisons/v3async_deepseek_low_latest_20261105/deepseek-11factors-models.log)。

## 2026-11-05 V3 Async 新分档 Haiku：快捷评估15／13因子

直接使用 `scripts/evaluate_snapshot.py` 评估两个 Haiku 的最新已提交截面。
北京时间 12:28:22，`a13803e3f9`（10 计划／10 路评估）冻结前 285 次、15 个因子；
12:28:34，`20a1012038`（5 计划／串行评估）冻结前 337 次、13 个因子。
相较此前 233／267 次截面分别新增 7／1 个成员，原有成员的完整冻结记录与顺序未变。

Test √成交额加权 Pearson IC，括号为相较各自上次 8／12 因子结果的变化：

| 实验 | 尝试次数 | 入库因子 | Linear | LightGBM | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Haiku，10 计划／10 路评估 | 285 | 15 | 0.011963（+0.002302） | 0.012571（+0.000405） | 100.000000% |
| Haiku，5 计划／串行评估 | 337 | 13 | 0.016725（+0.001445） | 0.018745（−0.001218） | 99.999844% |

加权 Spearman IC（Linear／LightGBM）分别为 0.020355／0.016041、0.020907／0.022190。
预测行数仍分别为 2,558,829 和 2,558,825，可评估行数均为 2,558,829；
串行组仍有 4 条全因子缺失行不预测。Train+Val 训练行数分别为 5,308,104、5,308,073，
15／13 列全部可用，没有移除列。串行组两种模型的 Pearson 仍较高，但其 LightGBM
较上次下降；尝试次数不同，不能将组间差异直接归因于并发设置。

沿用相同数据、fold2、seed 42、冻结方向及固定模型参数，通过公共配置、冻结与因子身份
校验，`source_matches_search=false` 如实记录拟合来源。原搜索继续运行，Test 不回传；
保留已有数据及历史留出诊断限制。只测组合预测，未计算单因子 OOS 或配对相关性，
未写实验总表。本次未修改实现，复用已通过专项与完整检查的快捷入口，并核对本次产物。

[15因子模型报告](../../artifacts/comparisons/v3async3_haiku_p10_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-a13803e3f9-prefix285/model/report.md)、
[13因子模型报告](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-prefix337/model/report.md)、
[15因子评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-p10-latest-models-20261105-2.log)、
[13因子评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-latest-models-20261105-2.log)。

## 2026-11-05 V3 Async 新分档 Luna：两组前500次快捷评估

按用户要求，对 `bf0c21a38d`、`510cffd920` 分别执行
`scripts/evaluate_snapshot.py <run_id> --attempts 500`。北京时间 12:34:09／12:34:15
冻结精确前 500 次截面，16 计划／16 路评估组入库 16 个，5 计划／串行评估组入库 20 个。
相较此前 259 次／11 因子、271 次／10 因子分别新增 5／10 个成员，原成员的完整冻结记录
和顺序未变。两组数据、八字段、fold2、seed 42 与预测模型参数相同。

Test √成交额加权 Pearson IC，括号为相较各自上次早期截面的变化：

| 实验 | 尝试次数 | 入库因子 | Linear | LightGBM | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Luna，16 计划／16 路评估（bf0c21a38d） | 500 | 16 | 0.011054（−0.003748） | 0.016333（−0.002475） | 100.000000% |
| Luna，5 计划／串行评估（510cffd920） | 500 | 20 | 0.005626（−0.003833） | 0.011488（−0.000801） | 100.000000% |

加权 Spearman IC（Linear／LightGBM）分别为 0.014000／0.016793、0.011336／0.016092。
两组的两模型均预测全部 2,558,829 条可评估行，Train+Val 训练行数均为 5,308,104，
16／20 列全部可用，没有移除列。500 次预算下并行组两种模型的 Pearson 较高，
但两组均低于各自此前较早截面的结果；没有根据 Test 筛选因子或调整模型参数。

公共配置、数据、冻结库和因子身份校验通过，`source_matches_search=false` 如实记录
拟合来源。原搜索继续运行，Test 不回传；仅评估组合预测，未计算单因子 OOS 或库内
配对相关性，未写实验总表。沿用版本已有数据与历史留出诊断限制；本次未修改实现，
复用已经验证的快捷入口，并核对本次冻结成员、模型产物、指标及报告链接。

[16因子模型报告](../../artifacts/comparisons/v3async3_luna_p16_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-bf0c21a38d-prefix500/model/report.md)、
[20因子模型报告](../../artifacts/comparisons/v3async2_luna_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-510cffd920-prefix500/model/report.md)、
[16因子评估日志](../../artifacts/comparisons/v3async_luna_latest_20261105/luna-p16-prefix500-models.log)、
[20因子评估日志](../../artifacts/comparisons/v3async_luna_latest_20261105/luna-prefix500-models.log)。

## 2026-11-05 V3 Async 新分档 DeepSeek low：快捷评估13因子

按用户要求执行 `scripts/evaluate_snapshot.py bfabcc7663 --factors 13`，
北京时间 12:46:05 冻结前 295 次、13 个因子的截面，来源仍为
`rq8_fold2_v3async_deepseek_low_v2`。较此前 256 次／11 因子新增 2 个成员，
原 11 个成员的完整冻结记录和顺序保持不变，数据及模型参数相同。

Test √成交额加权 Pearson IC：Linear 为 0.010654，较 11 因子的 0.007362 提高
0.003291；LightGBM 为 0.011547，较此前 0.012489 下降 0.000942。
加权 Spearman IC 分别为 0.017518、0.018057。两个模型均预测
2,413,690 / 2,558,829 行，覆盖率为 94.327913%，145,139 条全因子缺失行不预测；
较此前多预测 65 行。Train+Val 训练行数为 5,172,397，较此前增加 587 行，
13 列全部可用，没有移除列。

公共配置、数据、冻结库及因子身份校验通过，`source_matches_search=false` 如实记录
拟合来源。原搜索继续运行，Test 不回传；沿用版本已有数据与历史留出诊断限制。
仅评估组合预测，没有计算单因子 OOS 或配对相关性，未写实验总表。
本次复用已验证的快捷入口，未修改实现，并核对冻结成员、模型产物和报告指标。

[13因子模型报告](../../artifacts/comparisons/v3async2_deepseek_low_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-bfabcc7663-prefix295/model/report.md)、
[13因子评估日志](../../artifacts/comparisons/v3async_deepseek_low_latest_20261105/deepseek-13factors-models.log)。

## 2026-11-05 Haiku 10 并发改用并发 Explorer 重跑

`v3async3_haiku_p10_fold2_20261105` 的瓶颈在候选供应：仅 3／10 个计划位在用，47 个计划由 Miner 提前结束
（blocked），平均每计划 3.6 次尝试，单个 Explorer 补位跟不上，评估有效并发仅 0.16。
`feature/parallel-explorers`（工作区 `AlphaAtlas-explorers`）加入 `parallel_explorers`：多条 Explorer
对话同时补位，启动时扣除其他对话已认领的计划位与额度，提交时另查其他对话已开计划的重复。
完整检查 1,357 项通过。按用户要求停止该组（299 次，状态记为 interrupted，产物保留），启动
`v3async4_haiku_p10_fold2_20261105`（`rq8_fold2_v3async_haiku_p10_pe_px3_v4`：parallel_plans=10、
parallel_evaluations=10、parallel_explorers=3，其余同前）。五组运行均以 POLARS_MAX_THREADS=48、
OPENBLAS_NUM_THREADS=8 启动或恢复。

## 2026-11-05 并行评估缺陷：准入时观测值已被缓存淘汰

`v3async3_luna_p16_fold2_20261105` 在第 613 次后失败：`observation reference is not available in this run`。
并行评估的计算线程只把因子值留在评估器的有界缓存（内存与 512 MB 磁盘预算）中，16 个候选在算时，
队头候选准入前其观测已被后续结果淘汰；串行评估算完即取，不会触发。修复：`EvaluationService.measure`
返回报告及其 Valid 观测，在算条目持有该值直至准入，`SearchSession.evaluate(measured=...)` 直接使用；
新增回归测试（观测查询始终失败时并行运行仍完成）。修复同步到 `AlphaAtlas-parallel` 与
`AlphaAtlas-explorers`。失败请求未形成 trial，Luna 16 组以原目录恢复；Haiku Explorer 并发组尚未出错，
也中断后恢复以加载修复。

## 2026-11-05 V3 Async 新分档 Haiku：快捷评估21因子

按用户要求执行 `scripts/evaluate_snapshot.py 20a1012038 --factors 21`，
北京时间 12:54:58 冻结前 402 次、21 个因子的截面，来源为
`rq8_fold2_v3async_haiku_v2`（5 计划、串行评估）。较此前 337 次／13 因子新增
8 个成员，原 13 个成员的完整冻结记录和顺序保持不变，数据及模型参数相同。

Test √成交额加权 Pearson IC：Linear 为 0.022957，较此前 0.016725 提高
0.006232；LightGBM 为 0.028174，较此前 0.018745 提高 0.009429。
加权 Spearman IC 分别为 0.021097、0.022277。两个模型均预测
2,558,825 / 2,558,829 行，覆盖率为 99.999844%，4 条全因子缺失行不预测。
Train+Val 训练行数为 5,308,073，与 13 因子截面一致；21 列全部可用，没有移除列。

公共配置、数据、冻结库及因子身份校验通过，`source_matches_search=false` 如实记录
拟合来源。原搜索继续运行，Test 不回传；沿用版本已有数据与历史留出诊断限制。
仅评估组合预测，没有计算单因子 OOS 或配对相关性，未写实验总表。
本次复用已验证的快捷入口，未修改实现，并核对冻结成员、模型产物和报告指标。

[21因子模型报告](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-prefix402/model/report.md)、
[21因子评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-21factors-models.log)。

## 2026-11-05 V3 Async 新分档 DeepSeek low：快捷评估15因子

按用户追加要求，在 Haiku 21 因子评估完成后执行
`scripts/evaluate_snapshot.py bfabcc7663`，北京时间 12:58:29 冻结最新已提交的
前 336 次、15 个因子截面，来源仍为 `rq8_fold2_v3async_deepseek_low_v2`。
较此前 295 次／13 因子新增 2 个成员，原 13 个成员的完整冻结记录和顺序保持不变，
数据及模型参数相同。

Test √成交额加权 Pearson IC：Linear 为 0.010863，较此前 0.010654 提高
0.000210；LightGBM 为 0.013846，较此前 0.011547 提高 0.002299。
加权 Spearman IC 分别为 0.017424、0.017347。两个模型均预测
2,413,726 / 2,558,829 行，覆盖率为 94.329320%，145,103 条全因子缺失行不预测；
较此前多预测 36 行。Train+Val 训练行数为 5,172,415，较此前增加 18 行，
15 列全部可用，没有移除列。

公共配置、数据、冻结库及因子身份校验通过，`source_matches_search=false` 如实记录
拟合来源。原搜索继续运行，Test 不回传；沿用版本已有数据与历史留出诊断限制。
仅评估组合预测，没有计算单因子 OOS 或配对相关性，未写实验总表。
本次复用已验证的快捷入口，未修改实现，并核对冻结成员、模型产物和报告指标。

[15因子模型报告](../../artifacts/comparisons/v3async2_deepseek_low_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-bfabcc7663-prefix336/model/report.md)、
[15因子评估日志](../../artifacts/comparisons/v3async_deepseek_low_latest_20261105/deepseek-latest-models-20261105-2.log)。

## 2026-11-05 DeepSeek low 高并发组启动

`v3async2_deepseek_low_fold2_20261105` 的瓶颈在 Explorer：计划均做满（5 次／计划，0 个提前结束），但
Explorer 每批多只提交 1 个方向、需 3 轮左右对话；连续 2 分钟采样中 0 个计划在跑，最近 150 次尝试的
67 分钟墙钟中有 31 分钟（47%）无任何 trial。启动 `v3async4_deepseek_low_p10_fold2_20261105`
（`rq8_fold2_v3async_deepseek_low_p10_pe_px3_v4`：parallel_plans=10、parallel_evaluations=10、
parallel_explorers=3，代码 `AlphaAtlas-explorers`，POLARS_MAX_THREADS=40），与 5 并发组仅差并发设置。
同期 Haiku 新组（3 条 Explorer）15 分钟窗口速率 10.5 次／分，旧 Haiku 10 并发组为 2.0 次／分。

## 2026-11-05 V3 Async 新分档 Luna：500／1000次结果入表

按用户要求核对已完成 1000 次的 `rq8_fold2_v3async_luna_v2`（`510cffd920`，
5 计划、串行评估），并将同一运行此前测过的前 500 次结果相邻写入实验总表。
完整运行已自动完成冻结与评估，入库 24 个因子；前 500 次冻结 20 个因子。
两目录分别执行快捷入口，公共配置、数据、冻结库和模型缓存校验通过，复用各自保存的
Linear／LightGBM 拟合及 Test，没有重复训练。前 500 次成员的完整冻结记录与最终库
前 20 个成员逐项一致，两个截面的数据、模型参数和依赖版本相同。

Test √成交额加权 Pearson IC：

| 尝试次数 | 因子数 | Linear | LightGBM | 预测覆盖率 |
| --- | --- | --- | --- | --- |
| 500 | 20 | 0.005626 | 0.011488 | 100.000000% |
| 1000 | 24 | 0.005159 | 0.011148 | 100.000000% |

1000 次较 500 次分别下降 0.000467、0.000340。加权 Spearman IC（Linear／LightGBM）
分别为 500 次的 0.011336／0.016092、1000 次的 0.011181／0.016044。
两截面均使用 5,308,104 条 Train+Val 训练行，并预测全部 2,558,829 条 Test 可评估行；
20／24 列全部可用，没有移除列。保存的 `source_matches_search` 分别为 false／true，
如实保留前缀主工作区评估与完整运行自动收尾的来源记录。

完整运行已有 24 个成功的单因子 OOS 结果。核对原 OOS 指纹、成员身份、冻结定义、方向、
裁剪边界及数据配置后，按前缀成员取其中 20 个结果汇总 500 次的单因子栏，不新建或
伪造前缀 OOS 缓存：500 次加权 Pearson 均值／中位数为 0.004204／0.005208，正 IC
15／20（75.00%），加权 Spearman 均值为 0.002541；1000 次对应为
0.003705／0.004106、17／24（70.83%）、0.002768。

此次只复用已有评估并填表，未计算配对相关性；完整运行此前自动收尾生成的相关产物保留。
没有根据 Test 选择因子或调参，沿用本版本的数据与历史留出诊断限制。未修改实现；
核对表格数值、行位置和报告链接，并执行差异格式检查。

[500次模型报告](../../artifacts/comparisons/v3async2_luna_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-510cffd920-prefix500/model/report.md)、
[1000次模型报告](../../artifacts/comparisons/v3async2_luna_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-510cffd920/model/report.md)、
[单因子OOS来源](../../artifacts/comparisons/v3async2_luna_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-510cffd920/oos.json)、
[500次快捷校验日志](../../artifacts/comparisons/v3async_luna_latest_20261105/luna-prefix500-models-verified.log)、
[1000次快捷校验日志](../../artifacts/comparisons/v3async_luna_latest_20261105/luna-1000-models-verified.log)。

随后按用户要求，从实验总览主结果表撤下 `510cffd920` 的 500／1000 次两行及对应说明。
按用户追加要求，相似度表中的同一运行记录也已撤下。
上述历史评估记录和原始产物保留；撤表不改变已经完成的评估事实。

## 2026-11-05 V3 Async DeepSeek low：两组前500次快捷评估

按用户要求，分别以 `scripts/evaluate_snapshot.py <run_id> --attempts 500` 冻结并评估
两个运行的前 500 次已提交记录。北京时间 15:13:08，5 计划／串行评估组 `bfabcc7663`
截面入库 28 个因子；15:14:03，10 计划／10 路评估／3 路 Explorer 组 `3f8261a9ad`
截面入库 17 个因子。两组均为 DeepSeek low，使用相同数据、fold2、seed 和固定预测模型参数。

Test √成交额加权 IC：

| 运行 | 因子数 | Linear Pearson | LightGBM Pearson | Linear Spearman | LightGBM Spearman | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 计划／串行评估 | 28 | 0.011093 | 0.015482 | 0.014950 | 0.014534 | 94.434212% |
| 10 计划／10 路评估／3 路 Explorer | 17 | 0.017090 | 0.012079 | 0.015518 | 0.011271 | 99.994372% |

两组分别预测 2,416,410／2,558,829 和 2,558,685／2,558,829 行，训练行数为
5,203,172 和 5,307,840，所有因子列均保留。预测覆盖范围不同，且搜索并发设置不同，
指标差异不能直接归因于并发。公共配置、数据、冻结库与因子身份校验通过；拟合来源与
搜索启动源码记录不同，`source_matches_search=false`。原搜索继续运行，Test 不回传。
仅评估组合预测，未计算单因子 OOS 或库内配对相关性，未写实验总表。沿用已有数据与
历史留出诊断限制，Test 已被观察，不能视为最终盲测。

[串行组模型报告](../../artifacts/comparisons/v3async2_deepseek_low_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-bfabcc7663-prefix500/model/report.md)、
[高并发组模型报告](../../artifacts/comparisons/v3async4_deepseek_low_p10_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-3f8261a9ad-prefix500/model/report.md)、
[串行组日志](../../artifacts/comparisons/v3async_deepseek_low_latest_20261105/deepseek-serial-prefix500-models.log)、
[高并发组日志](../../artifacts/comparisons/v3async_deepseek_low_latest_20261105/deepseek-parallel-prefix500-models.log)。

## 2026-11-05 V3 Async 16路并行 Luna：500／1000次结果入表

按用户要求评估 `rq8_fold2_v3async_luna_p16_pe_v3`（`bf0c21a38d`，16 计划、
16 路并行评估）的完整 1000 次结果，并与此前前 500 次结果相邻写入实验总表。
完整运行冻结 45 个因子，其原运行脚本在此次核对期间完成了自动收尾；前 500 次有
16 个因子。两目录均通过快捷入口的公共配置、数据、冻结库和模型缓存校验，复用保存的
各自模型。最终库的前 16 个完整冻结成员与 500 次逐项一致，数据、模型参数和依赖版本相同。

Test √成交额加权 Pearson IC：

| 尝试次数 | 因子数 | Linear | LightGBM | 预测覆盖率 |
| --- | --- | --- | --- | --- |
| 500 | 16 | 0.011054 | 0.016333 | 100.000000% |
| 1000 | 45 | 0.007330 | 0.014097 | 100.000000% |

1000 次较 500 次分别下降 0.003723、0.002236。加权 Spearman IC（Linear／LightGBM）
分别为 500 次的 0.014000／0.016793、1000 次的 0.009642／0.015438。
两截面均使用 5,308,104 条 Train+Val 训练行，并预测全部 2,558,829 条 Test 可评估行；
16／45 列全部可用，没有移除列。两者 `source_matches_search=false` 均按保存的模型来源
如实保留，冻结配置、数据和因子身份校验通过。

核对完整运行单因子 OOS 的指纹、成员身份及全部成功状态，再按与前缀完全一致的冻结定义、
方向和裁剪边界提取前 16 个结果汇总 500 次单因子栏，未新建前缀 OOS 缓存。
500 次单因子加权 Pearson 均值／中位数为 0.006369／0.005350，正 IC 为
16／16（100.00%），加权 Spearman 均值为 0.002274；1000 次对应为
0.005481／0.005366、41／45（91.11%）、0.005203。

未额外发起配对相关性计算；原运行脚本自动收尾生成的 1000 次相似度结果保留。
没有根据 Test 选择因子或调参，沿用本版本的数据与历史留出诊断限制。未修改实现；
核对两行表格数值、位置、报告链接及文档锚点，并执行差异格式检查。

[500次模型报告](../../artifacts/comparisons/v3async3_luna_p16_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-bf0c21a38d-prefix500/model/report.md)、
[1000次模型报告](../../artifacts/comparisons/v3async3_luna_p16_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-bf0c21a38d/model/report.md)、
[单因子OOS来源](../../artifacts/comparisons/v3async3_luna_p16_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-bf0c21a38d/oos.json)、
[500次快捷校验日志](../../artifacts/comparisons/v3async_luna_latest_20261105/luna-p16-prefix500-models-verified.log)、
[1000次快捷校验日志](../../artifacts/comparisons/v3async_luna_latest_20261105/luna-p16-1000-models-verified.log)。

## 2026-11-05 DeepSeek low：两组500次组合模型结果入表

按用户要求，将 `bfabcc7663` 与 `3f8261a9ad` 的前 500 次组合模型结果写入
[实验总览](../../experiment_overview.md#期货因子搜索fold2)。分别入库 28／17 个因子，
Linear 加权 Pearson 为 0.011093／0.017090，LightGBM 为 0.015482／0.012079。
两组预测覆盖率分别为 94.434212%／99.994372%；单因子 Test 未评估，表中标记“—”。
本次复用已有评估结果，未增加 Test 计算或改变原搜索。

## 2026-11-05 V3 Async Haiku：最新30／34因子模型预测

按用户要求用快捷脚本评估两个在跑 Haiku 的最新已提交截面。北京时间 15:21:40，
`rq8_fold2_v3async_haiku_v2`（`20a1012038`，5 计划、串行评估）冻结前 779 次、
30 个因子；15:21:41，`rq8_fold2_v3async_haiku_p10_pe_px3_v4`（`67a4cb9f79`，
10 计划、10 路评估、3 个并行 Explorer）冻结前 671 次、34 个因子。
两组均执行 `scripts/evaluate_snapshot.py <短ID>`，使用相同固定数据、模型配置及依赖版本。

Test √成交额加权 Pearson IC：

| 运行 | 尝试次数 | 因子数 | Linear | LightGBM | 预测覆盖率 |
| --- | --- | --- | --- | --- | --- |
| 5 计划／串行评估（20a1012038） | 779 | 30 | 0.018476 | 0.026646 | 99.999844% |
| 10 计划／10 路评估／3 Explorer（67a4cb9f79） | 671 | 34 | 0.009381 | 0.000455 | 100.000000% |

加权 Spearman IC（Linear／LightGBM）分别为 0.017638／0.016217、0.011140／−0.000214。
串行组较此前 402 次／21 因子的加权 Pearson 分别下降 0.004481、0.001527；原 21 个
完整冻结成员及顺序保持不变，新增 9 个成员。3 Explorer 组为本次首次预测评估，
不与已停止的旧 10 路评估运行合并。两组尝试数与因子集合不同，不是相同预算的比较。

两组 Train+Val 训练行数分别为 5,308,073、5,308,104，30／34 列全部可用，没有移除列。
Test 可评估行均为 2,558,829；串行组预测 2,558,825 行，4 条全因子缺失行不预测，
3 Explorer 组预测全部行。公共配置、数据、冻结库、模型输入身份和保存结果校验通过，
两组 `source_matches_search=false` 如实保留来源差异。

仅评估组合预测，未计算单因子 OOS 或库内配对相关性，未写实验总表。原搜索继续运行，
Test 不回传，沿用版本已有数据与历史留出诊断限制。未修改实现；核对冻结截面、
模型指标、覆盖率和报告链接，并执行差异格式检查。

[30因子模型报告](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-prefix779/model/report.md)、
[34因子模型报告](../../artifacts/comparisons/v3async4_haiku_p10_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-67a4cb9f79-prefix671/model/report.md)、
[串行组评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-serial-latest-models-20261105-3.log)、
[3 Explorer组评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-px3-latest-models-20261105.log)。

## 2026-11-05 Haiku 两组模型差距诊断：冻结模型敏感性

诊断固定使用此前已测的串行组 `20a1012038-prefix779`（30 因子）及 3 Explorer 组
`67a4cb9f79-prefix671`（34 因子），不追随源运行后续新增成员。最明确的发现是：并行组
LightGBM 对市场流动性状态的使用拖累了本段 Test，串行组的日收益公共分量则帮助了
保存模型；该结果解释模型输入依赖，尚不能把差距归因为并发设置本身。

两组资产、快照、目标、fold、seed、字段、规则、运行时及下游模型配置一致。两组模型的
训练行分别为 5,308,073／5,308,104，Test 预测行仅相差 4 行；所有输入列均保留。
方法配置的差异为 `parallel_plans=5/10`、`parallel_evaluations=1/10`、
`parallel_explorers=1/3`。因子 ID 完全重合的只有 2 个，这两个因子的 Train／Val 指标、
方向和覆盖率也一致；表达式 ID 不同不代表经济机制必然不同。

入库因子的定向 Train IC 均值为 0.008019／0.008688，Val IC 均值为
0.010416／0.009814，未显示与组合 Test 差距同量级的验证期差异。串行组 30 个成员来自
21 个 Region，最多一个区域 4 个；并行组 34 个成员来自 15 个 Region，其中慢趋势区域
12 个、价格限制锁定区域 6 个，合计 18／34。区域计数是研究分类，不等价于因子间相关性。
两个截面的有效尝试数不同，且都只有一轮，不能据此证明并发降低泛化。

从已保存 LightGBM 提取训练分裂增益，分别选各自前三个特征做固定模型敏感性检查。
将被检查列的标准化输入设为 0，即保存的训练均值；其他输入、原始有效预测行和模型参数
均保持不变，不重新训练、不计算单因子 OOS 或配对相关性。先重现两个模型的原 Test 四项
指标，与已保存报告逐项核对（绝对容差 1e-12）。所有敏感性结果只作事后诊断，不回传搜索，
不据此更新因子库、模型或实验总表；均值遮蔽的结果不等于删列重训的结果。

下表为 LightGBM 的 Test √成交额加权 Pearson IC：

| 组别 | 被替换为训练均值的输入 | IC |
| --- | --- | --- |
| 串行 | 无，原模型 | 0.026646 |
| 串行 | 日收益公共分量 `common_component_r1d_1` | 0.014690 |
| 串行 | 日线突破失败公共分量 `failed_daily_sweep_common_component_1d_20_2` | 0.030075 |
| 串行 | 市场持仓变化状态 `market_positioning_build_state_only_5m_48` | 0.022721 |
| 串行 | 上述三个输入一起 | 0.022884 |
| 3 Explorer | 无，原模型 | 0.000455 |
| 3 Explorer | 市场流动性状态 `market_liquidity_gate_alone_control` | 0.014861 |
| 3 Explorer | 偏离上一日收盘 `prior_settlement_dev_1d_5m` | −0.007093 |
| 3 Explorer | 价格停滞条件信号 `staleness_gate_060_reversal_48_12_5m` | −0.001188 |
| 3 Explorer | 上述三个输入一起 | 0.008948 |

并行组市场流动性状态取横截面日成交额对数均值的 60 日标准分数，占其训练分裂增益的
22.996%，是训练增益最高的列。单独遮蔽后 IC 改善 0.014406，2024／2025 年均改善，
指向该特征在当前树模型中的使用方式为主要拖累之一；价格停滞信号的遮蔽反而更差，
不能将其与流动性信号一并判为失效。串行组日收益公共分量占训练增益 20.369%，遮蔽后
IC 下降 0.011957；新增突破失败公共分量虽占 19.453% 训练增益，遮蔽却改善 Test。
这些结果也说明训练增益不等于 Test 贡献，且不同特征的遮蔽效应不能相加。

按 Test 起点交易年份切片，保留完整 Test 的固定品种权重，原模型加权 Pearson 为：

| 组别／模型 | 2024 | 2025 |
| --- | --- | --- |
| 串行 Linear | 0.022660 | 0.014221 |
| 串行 LightGBM | 0.042980 | 0.007086 |
| 3 Explorer Linear | 0.019591 | 0.005010 |
| 3 Explorer LightGBM | 0.010113 | −0.008785 |

两组按年份可评估行均为 1,238,337／1,320,492，合计 2,558,829。串行 LightGBM 的优势
在 2024 更突出，2025 同样衰减；并行组流动性状态遮蔽后的两年 IC 为
0.025093／0.007492。年度 IC 不能直接平均还原完整 Test IC。

搜索实现中每个 Miner 仍先收到本组反馈再生成下一候选；多路评估的准入、反馈、地图更新
仍按 trial 顺序执行。多个 Explorer 和多个在算候选会改变生成时可见的地图、活动计划及
后续选择，因此是不同搜索轨迹。该机制是可能影响因子集合的途径，不是此次差距的独立
因果证明。要隔离并发作用，需要固定预算并多轮复跑，分别改变计划数、评估并发和
Explorer 数；当前 Test 继续按历史留出诊断理解。

诊断脚本通过 Ruff 检查与格式检查；数值校验以原模型指标重现为准，未修改平台实现。

[保存状态与特征重要性](../../artifacts/comparisons/haiku_diagnosis_20261105/saved_state_analysis.json)、
[串行组敏感性结果](../../artifacts/comparisons/haiku_diagnosis_20261105/serial_sensitivity.json)、
[并行组敏感性结果](../../artifacts/comparisons/haiku_diagnosis_20261105/parallel_sensitivity.json)、
[可复现诊断脚本](../../artifacts/comparisons/haiku_diagnosis_20261105/model_sensitivity.py)。

## 2026-11-05 V3 Region 可用性：集中问题与三模型重跑

**问题**：V3 入库因子高度集中在单个 Region。DeepSeek low 串行组 62 个因子中 66% 来自
Overnight gap order-flow persistence；前 500 次已有 10 个 Region 产生入库，第 501–650 次中
145 次仍在该 Region，新增 31 个因子全部来自这里。DeepSeek low 3-Explorer 组 53% 来自 Daily
settlement close-location pressure，Luna 16 路组 62% 来自 Close-VWAP price dislocation，Haiku
3-Explorer 组 38%。集中 Region 的入库残差 IC 后半段普遍下降（如 DeepSeek 0.0033→0.0018）。
Haiku 两组的 779／671 次快照中，3-Explorer 组单因子 Test IC 更高（均值 0.0072 vs 0.0051），
但 Linear／LightGBM 分别为 0.0094／0.0005 vs 0.0185／0.0266；线性系数反号 12/34 vs 3/30，
LightGBM 最大增益因子为测试 IC -0.0152 的市场状态对照因子。

**原因**：退火进入 depth 后只围绕活动 Region；持续入库使它保持 land，不会关闭；Metropolis
用累计得分比较，新 Region 难以接任；并发计划和多个 Explorer 同时拿到同一深挖目标。提示词要求
depth 测试活动 Region 的 probing／land 档，与上述规则一致地推动继续深挖。

**修改**（feature/region-availability，仅 V3 方法，平台准入不变）：新增可选
`region_admission_limit` 与 `region_plan_limit`。平台用同一判断给出 Region 可用性：入库达上限为
full，运行计划达上限为 busy。地图标注 `available`／`closed_reason`，提案校验拒绝不可用 Region 及
同批次重复 Region，depth 仅在活动 Region 可用且未被其他 Explorer 占用时保留，关闭或 full 的活动
Region 在计划结束时释放退火活动位。提示词只说明上述平台规则。未设置时旧运行不受影响。
完整检查 1364 项通过。

**运行**：三组均为 10 计划、10 路评估、3 个 Explorer，`region_admission_limit=5`、
`region_plan_limit=1`：`v3async5_haiku_avail_fold2_20261105`、
`v3async5_deepseek_low_avail_fold2_20261105`、`v3async5_luna_avail_fold2_20261105`
（版本 `rq8_fold2_v3async_{model}_p10_pe_px3_avail_v5`）。Luna 对照组原为 16 计划、单 Explorer，
并发设置与本组不同。

## 2026-11-05 V3 Async 并行 Haiku：快捷评估46因子

按用户要求再次评估此前的并行 Haiku `67a4cb9f79`，版本
`rq8_fold2_v3async_haiku_p10_pe_px3_v4`（10 计划、10 路评估、3 个 Explorer）。
执行 `scripts/evaluate_snapshot.py 67a4cb9f79`，北京时间 16:11:54 固定前 810 次、
46 个入库因子。此前 671 次／34 因子的完整冻结成员与顺序保持不变，新增 12 个成员；
数据快照、fold、模型参数及依赖版本与上次一致。

| 模型 | Test 加权 Pearson IC | 较34因子变化 | Test 加权 Spearman IC | 预测覆盖率 |
| --- | --- | --- | --- | --- |
| Linear | 0.009408 | +0.000026 | 0.011433 | 100.000000% |
| LightGBM | −0.001066 | −0.001521 | −0.000017 | 100.000000% |

加权口径为 √成交额；品种等权 Pearson IC（Linear／LightGBM）为
0.008455／0.000404，等权 Spearman IC 为 0.012801／0.005260。
Linear 主指标基本持平，LightGBM 从 0.000455 降至 −0.001066；新增因子后本段
Test 的组合主指标没有明显改善，不能仅凭因子数量增加推断预测增强。

Train+Val 训练行数 5,308,104，46 列全部保留；两个模型均预测 Test 全部
2,558,829 行。公共评估入口完成配置、数据与冻结校验；另核对冻结 SHA、配置指纹、
模型输入身份、fit／test 规格、四项指标和覆盖率，`source_matches_search=false`
如实保留来源差异。仅评估组合模型，未计算单因子 OOS 或配对相关性，未写实验总表。
原搜索保持独立，Test 不回传，沿用版本已有数据与历史留出诊断限制。未修改平台实现。

[46因子模型报告](../../artifacts/comparisons/v3async4_haiku_p10_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-67a4cb9f79-prefix810/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-px3-latest-models-20261105-2.log)。

随后按用户要求停止源运行 `67a4cb9f79`，最终保存 833 次尝试、48 个入库因子。
SIGINT 后进程仍继续搜索，随后仅对该进程发送 SIGTERM 并确认退出。取得原运行锁后，
使用运行所属工作树的检查点接口将状态标为 `interrupted`，记录用户主动停止并刷新报告；
`resumable=true`，检查点字节及配置指纹保持不变，保留原子 trial 和此前评估产物。

## 2026-11-05 无深挖：可用性组改为无深挖，串行 Haiku 第 500 次分叉

**依据**：退火温度从 0.25 等比降到 0.02，以几何中点为界，预算后半程全部是 depth，Explorer
只能围绕活动 Region。旧运行后半程中，单个 Region 占 22%–76% 的尝试；11 组运行中有 8 组后半程
入库因子的 Test IC 低于前半程（如 V1 Haiku 531d 0.0096→0.0052、V3 DeepSeek low
0.0061→0.0034）。这一对比混有因子库变大的影响，所以另做下面的对照。

**修改**：V3 支持沿用 V1 的 `selection_policy="greedy"`：每轮 breadth，Explorer 输入不含
mode／活动 Region，退火不再移动活动 Region。提示词中的 depth 规则替换为只做 breadth 的说明；
默认（退火）提示词逐字节不变。完整检查 1365 项通过。

**运行**：
- 可用性组（v3async5，跑到 160–209 次）已停止，标记 interrupted，保留产物。改为
  `v3async6_{haiku,deepseek_low,luna}_avail_nodepth_fold2_20261105`（版本
  `rq8_fold2_v3async_{model}_p10_pe_px3_avail_nodepth_v6`），与 v5 仅差 `selection_policy`。
- `v3async6_haiku_nodepth_fork500_fold2_20261105`：由 `fork.py` 复制串行 Haiku（20a1012038）
  前 500 个 trial（库 26 个），按截至第 500 次的记录重建方法状态（无在途计划，token 用量从 0 计），
  从第 501 次起 `selection_policy="greedy"`，其余配置不变（版本
  `rq8_fold2_v3async_haiku_nodepth_fork500_v1`）。与原运行的后 500 次对照，只差有无深挖。
  第 500 次时在途的计划不延续，原运行同一时点的 5 个在途计划会继续。

## 2026-11-05 V3 Async 串行 Haiku：快捷复测902次截面

按用户要求运行 `scripts/evaluate_snapshot.py 20a1012038`，北京时间 16:33:44 固定
`rq8_fold2_v3async_haiku_v2` 前 902 次、30 个因子，重新拟合两个模型并评估 Test。
该截面与此前 779 次截面的完整冻结库字节相同，期间没有新增入库因子；数据快照、
fold、模型配置及依赖版本一致，重新保存的拟合内容和全部 Test 报告也完全一致。

| 模型 | Test 加权 Pearson IC | Test 加权 Spearman IC | 预测覆盖率 |
| --- | --- | --- | --- |
| Linear | 0.018476 | 0.017638 | 99.999844% |
| LightGBM | 0.026646 | 0.016217 | 99.999844% |

加权口径为 √成交额。品种等权 Pearson IC（Linear／LightGBM）为
0.021436／0.021605，等权 Spearman IC 为 0.022372／0.021572。
Train+Val 训练 5,308,073 行，30 列全部保留；Test 预测 2,558,825／2,558,829 行。
已核对配置指纹、冻结 SHA、模型输入身份、fit／test 规格、四项指标及覆盖率；
`source_matches_search=false` 如实保留来源差异。仅评估组合预测，未计算单因子 OOS
或配对相关性，未写实验总表，Test 不回传搜索，沿用已有数据与历史留出诊断限制。

[902次模型报告](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-prefix902/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-serial-latest-models-20261105-4.log)。

## 2026-11-05 停止 DeepSeek low 串行组；观测台增加停止按钮

- `v3async2_deepseek_low_fold2_20261105`（`rq8_fold2_v3async_deepseek_low_v2`，bfabcc7663）在
  718 次时按用户要求停止，标记 interrupted：入库因子 66% 集中在 Overnight gap order-flow
  persistence。产物保留，可 resume。
- 观测台运行标题栏增加“停止运行”按钮（仅 running 状态显示，需确认）。后端
  `POST /api/stop?id=…` 从 /proc 找到持有该运行 `.run.lock` 的进程并发 SIGINT，效果等同 Ctrl+C，
  运行自行记录 interrupted；不保存 PID，没有进程持锁时返回 409。请求须带 `X-Atlas-Action: stop`
  头，跨站页面无法伪造。改动应用于主仓库、AlphaAtlas-scales（当前观测台进程）与
  AlphaAtlas-regions，测试 `tests/test_dashboard_stop.py`。

## 2026-11-05 Haiku 10 并发组误恢复后停止；停止按钮改为 SIGINT→SIGTERM

- `v3async4_haiku_p10_fold2_20261105`（67a4cb9f79）16:28 在 833 次无 traceback 退出、状态仍为
  running，当时按异常退出处理并从检查点恢复。用户本意是停止该组，现于 875 次停止并记为
  interrupted。
- 恢复后的该进程对 SIGINT 两次都无响应（信号已送达、Python 已注册处理器，仍继续提交 trial；
  ptrace 受限，未能取栈，原因未明）。停止按钮因此改为：先 SIGINT，20 秒内未退出则 SIGTERM；
  进程放开运行锁后，若 run.json 仍为 running，观测台在运行锁内记为 interrupted。测试覆盖
  正常处理 SIGINT 与忽略 SIGINT 两种进程。
- DeepSeek low 3-Explorer 组（3f8261a9ad）于 780 次由停止按钮停止，进程自行记为 interrupted。

## 2026-11-05 V3 Async Luna avail nodepth：前500次快捷评估

按用户要求执行 `scripts/evaluate_snapshot.py 90706b3755 --attempts 500`，北京时间
17:44:11 冻结 `rq8_fold2_v3async_luna_p10_pe_px3_avail_nodepth_v6` 的前 500 次
已提交截面，入库 14 个因子。原搜索继续运行，Test 不回传方法。

Test √成交额加权 Pearson IC：Linear 为 0.008455，LightGBM 为 0.006432；加权
Spearman IC 分别为 0.016645、0.017685。两模型均预测 2,558,825／2,558,829 行，
覆盖率为 99.999844%，4 条全因子缺失行不预测。Train+Val 训练行数为 5,308,099，
14 列全部可用。公共配置、数据、冻结库和因子身份校验通过；`source_matches_search=false`
记录拟合与搜索启动源码来源不同。只评估组合预测，未计算单因子 OOS 或库内配对相关性，
未写实验总表。Test 已被观察，属于历史留出诊断。

[模型报告](../../artifacts/comparisons/v3async6_luna_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-90706b3755-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async6_luna_avail_nodepth_fold2_20261105/prefix500-models.log)。

## 2026-11-05 V3 Async Luna avail nodepth：16因子快捷评估

按用户要求执行 `scripts/evaluate_snapshot.py 90706b3755 --factors 16`，北京时间
17:47:43 冻结前 546 次、16 个因子的截面。较此前 500 次／14 因子新增 2 个成员，
原 14 个成员的完整冻结记录和顺序保持一致。

Test √成交额加权 Pearson IC：Linear 为 0.008074，较 14 因子的 0.008455 下降
0.000381；LightGBM 为 0.007072，较此前 0.006432 上升 0.000641。加权 Spearman IC
分别为 0.016467、0.017217。两个模型均预测 2,558,825／2,558,829 行，覆盖率为
99.999844%；Train+Val 训练行数为 5,308,099，16 列全部可用。公共配置、数据、
冻结库和因子身份校验通过，`source_matches_search=false` 记录源码来源差异。
原搜索继续运行，Test 不回传；只评估组合预测，未计算单因子 OOS 或库内相关性。
Test 已被观察，属于历史留出诊断。

[16因子模型报告](../../artifacts/comparisons/v3async6_luna_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-90706b3755-prefix546/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async6_luna_avail_nodepth_fold2_20261105/16factors-models.log)。

## 2026-11-05 V3 Async Haiku 无深挖分支：645次与500次预测对比

按用户要求使用 `scripts/evaluate_snapshot.py` 评估
`rq8_fold2_v3async_haiku_nodepth_fork500_v1`（`20a1012038-nodepth500`）最新截面，
北京时间 17:49:26 固定前 645 次、30 个因子；同时用 `20a1012038 --attempts 500`
评估原串行运行的精确 500 次截面，共 26 个因子。分支在第 501 次起采用
`selection_policy=greedy`，本次比基础截面新增 145 次尝试、4 个因子。

两组原 26 个成员的完整冻结记录、顺序、方向和裁剪边界一致；数据快照、fold、字段、
规则、运行时、下游模型参数及依赖版本相同。Test √成交额加权 IC 为：

| 模型／指标 | 500次／26因子 | 无深挖645次／30因子 | 变化 |
| --- | --- | --- | --- |
| Linear Pearson | 0.024497 | 0.023496 | −0.001002 |
| LightGBM Pearson | 0.028821 | 0.030248 | +0.001427 |
| Linear Spearman | 0.021668 | 0.020990 | −0.000679 |
| LightGBM Spearman | 0.022225 | 0.022917 | +0.000691 |

品种等权 Pearson IC（Linear／LightGBM）从 0.026013／0.023997 变为
0.024698／0.025855；等权 Spearman IC 从 0.027057／0.026753 变为
0.025979／0.028591。新增因子后 LightGBM 改善，Linear 略降。这属于续搜前后比较，
预算和因子集合不同，尚不能单独归因于 `greedy` 策略。

两组 Train+Val 均训练 5,308,073 行，26／30 列全部保留；Test 均预测
2,558,825／2,558,829 行，覆盖率 99.999844%。公共评估入口与额外核对确认配置指纹、
冻结 SHA、模型输入身份、fit／test 规格、全部指标及覆盖率一致有效；两组
`source_matches_search=false` 如实记录来源差异。仅评估组合预测，未计算单因子 OOS
或配对相关性，未写实验总表。Test 不回传搜索，沿用已有数据与历史留出诊断限制。

[500次模型报告](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-prefix500/model/report.md)、
[无深挖645次模型报告](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-nodepth500-prefix645/model/report.md)、
[500次评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-serial-prefix500-models-20261105.log)、
[无深挖分支评估日志](../../artifacts/comparisons/v3async_haiku_latest_20261105/haiku-nodepth500-latest-models-20261105.log)。

## 2026-11-05 V3 Async avail nodepth：DeepSeek／Haiku最新预测

按用户要求同时执行快捷脚本 `scripts/evaluate_snapshot.py e78d475f38` 和
`scripts/evaluate_snapshot.py 132e72e79a`。北京时间 17:59:19 分别固定
`rq8_fold2_v3async_deepseek_low_p10_pe_px3_avail_nodepth_v6` 前 437 次、16 个因子，
`rq8_fold2_v3async_haiku_p10_pe_px3_avail_nodepth_v6` 前 351 次、20 个因子。
两组均为 10 计划、10 路评估、3 个 Explorer，`selection_policy=greedy`、
`region_admission_limit=5`、`region_plan_limit=1`。数据与下游模型配置相同；
这是各自最新截面，尝试数不同。

Test √成交额加权 Pearson IC：

| 运行 | 尝试次数 | 因子数 | Linear | LightGBM | 预测覆盖率 |
| --- | --- | --- | --- | --- | --- |
| DeepSeek low（e78d475f38） | 437 | 16 | 0.009954 | 0.016424 | 100.000000% |
| Haiku（132e72e79a） | 351 | 20 | 0.014111 | 0.012811 | 100.000000% |

加权 Spearman IC（Linear／LightGBM）分别为 DeepSeek 0.016394／0.021873、
Haiku 0.013852／0.012652。品种等权 Pearson IC 分别为
0.011327／0.016516、0.018143／0.009754；等权 Spearman IC 分别为
0.013411／0.021368、0.017573／0.013802。

两组 Train+Val 均训练 5,308,104 行，16／20 列全部可用；Test 均预测全部
2,558,829 行。公共评估入口及额外核对确认配置指纹、冻结 SHA、模型输入身份、
fit／test 规格、依赖版本、全部指标和覆盖率有效；两组 `source_matches_search=false`
如实保留来源差异。仅评估组合预测，未计算单因子 OOS 或配对相关性，未写实验总表。
原搜索继续运行，Test 不回传，沿用已有数据与历史留出诊断限制。

[DeepSeek模型报告](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-e78d475f38-prefix437/model/report.md)、
[Haiku模型报告](../../artifacts/comparisons/v3async6_haiku_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-132e72e79a-prefix351/model/report.md)、
[DeepSeek评估日志](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/latest-models-20261105.log)、
[Haiku评估日志](../../artifacts/comparisons/v3async6_haiku_avail_nodepth_fold2_20261105/latest-models-20261105.log)。

## 2026-11-05 V3 Async Haiku nodepth 续跑：最新截面快捷评估

按用户要求，以 `scripts/evaluate_snapshot.py` 评估
`rq8_fold2_v3async_haiku_nodepth_fork500_v1` 的最新已提交截面。北京时间 18:07:11
冻结前 670 次、30 个入库因子；该运行由 `20a1012038` 的前 500 次续跑，截面尝试数
包含继承部分。原搜索继续运行，Test 不回传方法。

Test √成交额加权 Pearson IC：Linear 为 0.023496，LightGBM 为 0.030248；加权
Spearman IC 分别为 0.020990、0.022917。两模型均预测 2,558,825／2,558,829 行，
覆盖率为 99.999844%；Train+Val 训练行数为 5,308,073，30 列全部可用。公共配置、
数据、冻结库和因子身份校验通过，模型拟合与搜索启动的源码记录不同。只评估组合预测，
未计算单因子 OOS 或库内配对相关性。Test 已被观察，属于历史留出诊断。

[模型报告](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-nodepth500-prefix670/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/latest-models.log)。

## 2026-11-05 V3 Async DeepSeek low avail nodepth：前500次快捷评估

按用户要求执行 `scripts/evaluate_snapshot.py e78d475f38 --attempts 500`，北京时间
18:19:52 冻结 `rq8_fold2_v3async_deepseek_low_p10_pe_px3_avail_nodepth_v6` 的前
500 次截面，入库 19 个因子。原搜索继续运行，Test 不回传方法。

Test √成交额加权 Pearson IC：Linear 为 0.011632，LightGBM 为 0.016788；加权
Spearman IC 分别为 0.019499、0.022164。两个模型均预测全部 2,558,829 条可评估行，
覆盖率 100%；Train+Val 训练行数为 5,308,104，19 列全部可用。公共配置、数据、
冻结库和因子身份校验通过；模型拟合与搜索启动的源码记录不同。只评估组合预测，
未计算单因子 OOS 或库内配对相关性。Test 已被观察，属于历史留出诊断。

[模型报告](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-e78d475f38-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/prefix500-models.log)。

随后按用户要求再次调用同一快捷命令，公共入口校验通过并复用已有 fit／test 缓存。
相较此前 437 次／16 因子截面新增 3 个成员，原 16 个完整冻结记录保持不变，模型配置、
数据、fold 及依赖版本一致；Linear／LightGBM 加权 Pearson 分别提高 0.001678／0.000364。
本次未写实验总表。[快捷校验日志](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/prefix500-models-verified.log)。

## 2026-11-05 DeepSeek low avail nodepth：500次组合模型结果入表

按用户要求，将 `e78d475f38` 前 500 次的组合模型结果写入
[实验总览](../../experiment_overview.md#期货因子搜索fold2)：19 个因子，Linear／LightGBM
加权 Pearson 分别为 0.011632／0.016788，预测覆盖率均为 100%。单因子 Test 尚未评估，
表中标记“—”；本次复用已有结果，未增加 Test 计算。

## 2026-11-05 V3 Async Luna 与 Haiku：最新截面快捷评估

按用户要求分别执行快捷评估。北京时间 18:26:44，`90706b3755` 冻结前 758 次、
21 个因子；Haiku 续跑 `20a1012038-nodepth500` 冻结前 716 次、33 个因子，次数
包含继承的前 500 次。此前分别评估的 546 次／16 因子与 670 次／30 因子完整成员
记录及顺序均与各自新截面的前序成员一致。

Test √成交额加权 Pearson IC：

| 截面 | 因子 | Linear | LightGBM |
| --- | ---: | ---: | ---: |
| Luna 758 次 | 21 | 0.010273 | 0.006219 |
| Haiku 716 次 | 33 | 0.024171 | 0.027623 |

相较此前各自截面，Luna 的 Linear／LightGBM 分别变化 +0.002198／−0.000853；
Haiku 分别变化 +0.000676／−0.002625。加权 Spearman IC 分别为 Luna 的
0.016112／0.016699 和 Haiku 的 0.019596／0.021720。两截面的两个模型均预测
2,558,825／2,558,829 行，覆盖率为 99.999844%；Train+Val 训练行数分别为
5,308,099／5,308,074，所有因子列保留。

公共配置、数据、冻结库和因子身份校验通过；模型拟合与搜索启动的源码记录不同。
原搜索继续运行，Test 不回传。只评估组合预测，未计算单因子 OOS 或库内配对相关性；
Test 已被观察，属于历史留出诊断。

[Luna 模型报告](../../artifacts/comparisons/v3async6_luna_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-90706b3755-prefix758/model/report.md)、
[Haiku 模型报告](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-nodepth500-prefix716/model/report.md)、
[Luna 评估日志](../../artifacts/comparisons/v3async6_luna_avail_nodepth_fold2_20261105/latest-models-758.log)、
[Haiku 评估日志](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/latest-models-716.log)。

## 2026-11-05 V3 Async 串行 Haiku：最终结果主表核对

按用户要求，将已评估的串行 Haiku `20a1012038` 结果落实到主表。原运行已完成
1000 次并自动写入主表，仍为 30 个因子；核对 `prefix902` 与最终运行的完整冻结库、
数据快照、模型配置及依赖版本一致，全部模型 Test 报告完全相同。
保留最终 1000 次这一行，补充串行标识、模型报告链接及 902 次后无新增因子的说明。

Test √成交额加权 Pearson IC（Linear／LightGBM）为 0.018476／0.026646，
加权 Spearman IC 为 0.017638／0.016217，两模型预测覆盖率均为 99.999844%
（2,558,825／2,558,829 行）。单因子栏沿用并核对最终运行已有 OOS：加权 Pearson
均值／中位数为 0.005100／0.005174，正 IC 26／30（86.67%），加权 Spearman
均值为 0.002107。配置指纹、冻结 SHA、因子身份、fit／test 规格与 OOS 指纹校验通过。
本次仅核对已有产物并整理表格，未重新训练、评估 Test 或计算配对相关性。

[最终模型报告](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038/model/report.md)、
[902次模型报告](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-prefix902/model/report.md)、
[最终单因子结果](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038/oos.json)。

## 2026-11-05 V3 Async Haiku avail nodepth：最新截面快捷评估及 Luna 停止

按用户要求，快捷评估 `rq8_fold2_v3async_haiku_p10_pe_px3_avail_nodepth_v6`
（`132e72e79a`）最新已提交截面。北京时间 18:32:11 冻结前 405 次、25 个因子。
Test √成交额加权 Pearson IC：Linear 为 0.014278，LightGBM 为 0.012310；加权
Spearman IC 分别为 0.014020、0.012761。两模型均预测全部 2,558,829 条可评估行；
Train+Val 训练行数为 5,308,104，25 列全部可用。公共配置、数据、冻结库和因子身份
校验通过，模型拟合与搜索启动的源码记录不同。原 Haiku 搜索继续运行，Test 不回传；
只评估组合预测，未计算单因子 OOS 或库内配对相关性。Test 已被观察，属于历史留出诊断。

按用户要求停止 Luna `90706b3755`：项目停止入口发出中断后，运行锁释放时出现竞争，
入口未能立即写状态；确认无锁持有者后，在获取运行锁的情况下按同一停止流程将状态记为
`interrupted`。检查点保留 790 次已提交尝试和 21 个入库因子，可用于后续恢复。

[Haiku 模型报告](../../artifacts/comparisons/v3async6_haiku_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-132e72e79a-prefix405/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async6_haiku_avail_nodepth_fold2_20261105/latest-models-405.log)。

- 2026-11-05：Luna「可用性＋无深挖」v6（`90706b3755`）效果差，用户在第 790 次手动停止（状态 interrupted，不续跑）。
  以完全相同的配置另开重复运行 `rq8_fold2_v3async_luna_p10_pe_px3_avail_nodepth_r2_v7`
  （`v3async7_luna_avail_nodepth_r2_fold2_20261105`，run `0e0d7ab291`，seed 42），用于区分配置问题与单次运行波动。

## 2026-11-05 Haiku 两组无深挖：437次最终库／763次截面预测

按用户要求对 `rq8_fold2_v3async_haiku_p10_pe_px3_avail_nodepth_v6`（`132e72e79a`）
与 `rq8_fold2_v3async_haiku_nodepth_fork500_v1`（`20a1012038-nodepth500`）调用快捷脚本。
前者在本次调用期间因 `V1 Explorer unresolved` 提前冻结：Explorer 未提出可执行的新方向，
回复认为需要当前 context 未提供的远月价格或时段／日历字段。原子 trial 和检查点确认实际
完成 437 次、入库 26 个；`run.json` 的 1000 是预算，不能视为已完成次数。

首次快捷调用遇到原进程收尾锁；等待其自动收尾完成后，再次调用快捷脚本，公共入口核对
并复用已有 fit／test 缓存。原脚本自身已完成单因子审计、库内相关性并自动写入总表，
本次未另外启动这些计算或手动填表。500 次分支于北京时间 18:48:34 固定前 763 次、
35 个因子，快捷脚本完成模型拟合与 Test；次数包含继承的前 500 次，源分支继续搜索。

Test √成交额加权 Pearson IC：

| 运行 | 实际尝试次数 | 因子数 | Linear | LightGBM | 预测覆盖率 |
| --- | --- | --- | --- | --- | --- |
| 可用性＋无深挖（132e72e79a） | 437，提前结束 | 26 | 0.016715 | 0.013289 | 100.000000% |
| 串行500次分支（20a1012038-nodepth500） | 763 | 35 | 0.025362 | 0.028509 | 99.999844% |

加权 Spearman IC（Linear／LightGBM）分别为 0.014545／0.011906、0.020367／0.021887。
品种等权 Pearson IC 分别为 0.021374／0.010900、0.024448／0.025055；等权 Spearman IC
分别为 0.018750／0.014401、0.024428／0.028053。

较各自此前 405 次／25 因子、716 次／33 因子，完整前序冻结成员及顺序保持不变，
分别新增 1／2 个成员；加权 Pearson 的 Linear／LightGBM 分别变化
+0.002438／+0.000979、+0.001191／+0.000886。两组预算、搜索方式和因子集合不同，
此处汇报各自进展。

两组 Train+Val 训练行分别为 5,308,104／5,308,074，26／35 列全部保留；Test 预测行
分别为 2,558,829／2,558,825，可评估行均为 2,558,829。公共入口及额外核对验证实际
次数、配置指纹、冻结 SHA、因子身份、fit／test 规格、数据、下游模型参数、依赖版本、
全部指标和覆盖率；两组 `source_matches_search=false` 如实保留来源差异。Test 不回传方法，
沿用已有数据与历史留出诊断限制。

[437次模型报告](../../artifacts/comparisons/v3async6_haiku_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-132e72e79a/model/report.md)、
[437次快捷校验日志](../../artifacts/comparisons/v3async6_haiku_avail_nodepth_fold2_20261105/latest-models-20261105-2-verified.log)、
[763次模型报告](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-nodepth500-prefix763/model/report.md)、
[763次评估日志](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/latest-models-20261105-2.log)。

## 2026-11-05 V3 Async 无深挖：Luna重复组／DeepSeek最新预测

按用户要求同时执行快捷脚本，分别评估
`rq8_fold2_v3async_luna_p10_pe_px3_avail_nodepth_r2_v7`（`0e0d7ab291`）与
`rq8_fold2_v3async_deepseek_low_p10_pe_px3_avail_nodepth_v6`（`e78d475f38`）。
北京时间 19:03:33／19:03:34 固定前 281 次／9 因子及前 600 次／24 因子的截面。
两组数据快照、fold、字段、规则、下游模型配置及依赖版本相同，尝试数和预测覆盖不同。

Test √成交额加权 Pearson IC：

| 运行 | 尝试次数 | 因子数 | Linear | LightGBM | 预测覆盖率 |
| --- | --- | --- | --- | --- | --- |
| Luna重复组（0e0d7ab291） | 281 | 9 | 0.009307 | 0.006410 | 94.304231% |
| DeepSeek low（e78d475f38） | 600 | 24 | 0.014673 | 0.026595 | 100.000000% |

加权 Spearman IC（Linear／LightGBM）分别为 Luna 0.004586／0.004148、
DeepSeek 0.019122／0.027194。品种等权 Pearson IC 分别为
0.005813／0.003830、0.015734／0.025790；等权 Spearman IC 分别为
0.000359／0.002484、0.021393／0.026936。

DeepSeek 较前 500 次／19 因子新增 5 个成员，原 19 个完整冻结记录和顺序保持不变；
Linear／LightGBM 加权 Pearson 分别提高 0.003041／0.009807，后者由 0.016788
升至 0.026595。该比较保持数据、配置、训练行数和 Test 覆盖一致。

Luna／DeepSeek 的 Train+Val 训练行数分别为 5,152,899／5,308,104，9／24 列全部保留；
Test 可评估行均为 2,558,829，分别预测 2,413,084／2,558,829 行，Luna 有 145,745 条
可评估行未预测。公共入口及额外核对验证配置指纹、冻结 SHA、因子身份、fit／test 规格、
全部指标及覆盖率；两组 `source_matches_search=false` 如实保留来源差异。仅评估组合预测，
未计算单因子 OOS 或配对相关性，未写实验总表。Test 不回传搜索，沿用已有数据与历史
留出诊断限制。

[Luna模型报告](../../artifacts/comparisons/v3async7_luna_avail_nodepth_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-0e0d7ab291-prefix281/model/report.md)、
[DeepSeek模型报告](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-e78d475f38-prefix600/model/report.md)、
[Luna评估日志](../../artifacts/comparisons/v3async7_luna_avail_nodepth_r2_fold2_20261105/latest-models-20261105.log)、
[DeepSeek评估日志](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/latest-models-20261105-2.log)。

- 2026-11-05：Haiku「可用性＋无深挖」v6（`132e72e79a`）在第 437 次正常冻结：Explorer 判断现有字段下地图已无可执行
  新机制并返回 unresolved。入库 26 个，单因子 Test IC 均值／中位数 0.0063／0.0047（20/26 为正），
  Linear 0.016715，LightGBM 0.013289。
- 2026-11-05：DeepSeek low「可用性＋无深挖」v6（`e78d475f38`）在第 643 次因一次结构不完整的模型回复
  （`chat model response has no message content`，各运行中首次出现）失败，按原配置续跑。
- 2026-11-05：提示词精简与一致化（分支 `feature/prompt-cleanup`）：Miner 可用 `done` 按计划自身规则正常结束计划，
  `blocked` 只表示缺失能力（Haiku v6 的 138 条 `blocked_plans` 大多是正常提前结束）；改正并发 Explorer 的
  `available_slots` 说明（原替换句已失效）；Region 选择规则只出现一次；分档状态按判定顺序描述。
  以此启动 Haiku、Luna 两组 v8（`..._p8_pe8_px3_avail_nodepth_v8`，run `1b154fb482`／`22d7cdbc4f`）：
  可用性 5／1、无深挖、8 计划、8 路评估、3 Explorer。与 v6 同时改了提示词和并发，二者不可分开归因。
- 2026-11-05：Luna v8（`22d7cdbc4f`）在第 142 次因模型网关返回 HTTP 520 失败；该类网关错误原不重试。
  公共客户端将 HTTP 520–524 加入可重试分类（prompts 与 regions 两个工作树），随后续跑。

## 2026-11-05 Luna 无深挖 v7／v8：最新截面快捷评估

按用户要求同时执行 `scripts/evaluate_snapshot.py 0e0d7ab291` 和
`scripts/evaluate_snapshot.py 22d7cdbc4f`。北京时间 19:46:29，分别固定
`rq8_fold2_v3async_luna_p10_pe_px3_avail_nodepth_r2_v7` 前 534 次、14 因子，
`rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_v8` 前 165 次、7 因子。
两组数据快照、fold、字段、规则和下游模型参数相同；v7 为 10 计划／10 路评估，
v8 为 8 计划／8 路评估，两组均为 3 Explorer、可用性限制 5／1、greedy 无深挖。
v8 另有此前记录的提示词调整，当前预算也不同，不能将差异单独归因于并发数。

Test √成交额加权 Pearson IC：

| 运行 | 尝试次数 | 因子数 | Linear | LightGBM | 预测覆盖率 |
| --- | --- | --- | --- | --- | --- |
| Luna v7（0e0d7ab291） | 534 | 14 | 0.006363 | 0.010175 | 99.995271% |
| Luna v8（22d7cdbc4f） | 165 | 7 | 0.010870 | 0.007531 | 100.000000% |

加权 Spearman IC（Linear／LightGBM）分别为 v7 0.003427／0.005887、
v8 0.018530／0.013501。品种等权 Pearson IC 分别为
0.002707／0.006482、0.014546／0.016630；等权 Spearman IC 分别为
−0.000013／0.005375、0.022428／0.018393。

v7 较此前 281 次／9 因子新增 5 个成员，原 9 个完整冻结成员及顺序保持不变。
Linear／LightGBM 加权 Pearson 分别变化 −0.002944／+0.003764；预测覆盖率由
94.304231% 升至 99.995271%，预测样本也发生变化，不能把该差值全部视为同一样本上的改善。

两组 Train+Val 训练行数分别为 5,307,922／5,308,104，14／7 列全部保留；Test
可评估行均为 2,558,829，分别预测 2,558,708／2,558,829 行，v7 有 121 条可评估行
未预测。公共入口及额外核对验证配置指纹、冻结 SHA、因子身份、fit／test 规格、
依赖版本、全部指标及覆盖率；两组 `source_matches_search=false` 如实保留来源差异。
仅评估组合预测，未计算单因子 OOS 或配对相关性，未写实验总表。Test 不回传搜索，
沿用已有数据与历史留出诊断限制。

[v7模型报告](../../artifacts/comparisons/v3async7_luna_avail_nodepth_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-0e0d7ab291-prefix534/model/report.md)、
[v8模型报告](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-22d7cdbc4f-prefix165/model/report.md)、
[v7评估日志](../../artifacts/comparisons/v3async7_luna_avail_nodepth_r2_fold2_20261105/latest-models-20261105-2.log)、
[v8评估日志](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/latest-models-20261105.log)。

- 2026-11-05：按用户要求停止 Luna v7（`0e0d7ab291`）。通过现有停止入口向该运行锁
  持有进程发送 SIGINT，进程正常退出并自行记录 `interrupted`；确认没有运行锁持有者。
  检查点与最后原子 trial 均为 606 次尝试、14 个入库因子，配置指纹一致，已有结果及
  可恢复检查点保留。本次是用户主动停止，不自动续跑；Luna v8（`22d7cdbc4f`）继续运行。

## 2026-11-05 Luna v8：282次／14因子快捷预测

用户要求复测 Luna v8（`rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_v8`，
`22d7cdbc4f`）当前结果。请求时提及 13 个因子，实际调用快捷脚本时已增至 14 个；
于北京时间 20:03:21 固定前 282 次尝试，保存在 `-prefix282`，源搜索继续运行。

Test 模型指标：

| 模型 | √成交额加权 Pearson IC | √成交额加权 Spearman IC | 品种等权 Pearson IC | 品种等权 Spearman IC |
| --- | --- | --- | --- | --- |
| Linear | 0.015513 | 0.019604 | 0.019038 | 0.022810 |
| LightGBM | 0.014500 | 0.016770 | 0.019365 | 0.021360 |

较此前 165 次／7 因子，Linear／LightGBM 加权 Pearson 分别提高
0.004643／0.006969（此前为 0.010870／0.007531）。原 7 个完整冻结成员和顺序保持不变；
数据快照、fold、下游模型参数、依赖版本、训练行数和 Test 覆盖一致。

Train+Val 训练 5,308,104 行，14 列全部保留；两个模型均预测全部 2,558,829 条 Test
可评估行，覆盖率 100%。公共入口与额外核对确认配置指纹、冻结 SHA、因子身份、
fit／test 规格、四项指标与覆盖率；`source_matches_search=false` 如实保留来源差异。
本次仅评估组合预测，不计算单因子 OOS 或配对相关性，不写实验总表。Test 不回传搜索，
结果沿用已有数据与历史留出诊断限制。

[模型报告](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-22d7cdbc4f-prefix282/model/report.md)、
[快捷评估日志](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/latest-models-20261105-2.log)。
- 2026-11-05：深挖／无深挖对照（同一串行 Haiku 前 500 次，`20a1012038` 对 `20a1012038-nodepth500`）：
  分叉组在第 912 次因 Explorer 判断无可执行新方向而正常冻结（unresolved）。第 501 次起：

  | 第 501 次起 | 带深挖（串行原组，至 1000） | 无深挖（分叉，至 912） |
  | --- | --- | --- |
  | 尝试 / 涉及区域 / 最大区域占比 | 500 / 17 / 45% | 412 / 44 / 8% |
  | 新入库（来自区域数） | 4（3） | 15（11） |
  | 总入库 | 30 | 41 |
  | 单因子 Test IC 均值／中位数（为正） | 0.0051／0.0052（26/30） | 0.0064／0.0065（37/41） |
  | Linear／LightGBM | 0.018476／0.026646 | 0.026102／0.022128 |

  无深挖用更少尝试多入库 11 个，分散到 11 个区域，单因子 Test IC 与 Linear 更高，LightGBM 略低。
  单一 seed、单一模型，结论仅支持“后半程深挖在该运行中无益”。
- 2026-11-05：Haiku v8（`1b154fb482`，新提示词、8 计划／8 评估）在第 515 次因 Explorer unresolved 正常冻结。
  入库 32 个，来自 25 个区域（单区域最多 3 个，最大区域尝试占比 4%）；单因子 Test IC 0.0077／0.0064（28/32 为正），
  Linear 0.019176，LightGBM 0.006097。Miner 用 `done` 结束计划 45 次，`blocked` 仅 2 次。
  Haiku 三组无深挖运行均因 unresolved 提前停止（437、515、912 次）。

## 2026-11-05 Luna v8：500次与871次截面快捷预测

按用户要求对 `rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_v8`（`22d7cdbc4f`）
调用快捷脚本，分别使用 `--attempts 500` 和 `--attempts 871`。北京时间
21:42:34／21:42:35 固定指定截面，分别有 20／29 个入库因子；原搜索继续运行。

Test √成交额加权 IC：

| 尝试次数 | 因子数 | Linear Pearson | LightGBM Pearson | Linear Spearman | LightGBM Spearman |
| --- | --- | --- | --- | --- | --- |
| 500 | 20 | 0.011798 | 0.015331 | 0.016976 | 0.017250 |
| 871 | 29 | 0.010497 | 0.012807 | 0.015846 | 0.015809 |

品种等权 Pearson IC（Linear／LightGBM）分别为 0.011795／0.018287、
0.011072／0.014726；等权 Spearman IC 分别为 0.018829／0.020996、
0.018195／0.019898。871 次较 500 次的 Linear／LightGBM 加权 Pearson
分别下降 0.001301／0.002525；新增 9 个因子后，这两个固定模型的历史 Test 表现降低。

前 500 次的全部 20 个冻结成员、顺序、方向和裁剪边界在 871 次截面中保持不变。
两组数据快照、fold、模型配置和依赖版本一致；Train+Val 均训练 5,308,104 行，
20／29 列全部保留，两个模型均预测全部 2,558,829 条 Test 可评估行，覆盖率 100%。
公共入口及额外核对确认配置指纹、冻结 SHA、因子身份、fit／test 规格、四项指标和
覆盖率；两组 `source_matches_search=false` 如实保留来源差异。本次仅评估模型预测，
不计算单因子 OOS 或配对相关性，不更新实验总表。Test 不回传搜索，沿用已有数据与
历史留出诊断限制。

[500次模型报告](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-22d7cdbc4f-prefix500/model/report.md)、
[871次模型报告](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-22d7cdbc4f-prefix871/model/report.md)、
[500次评估日志](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/prefix500-models.log)、
[871次评估日志](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/prefix871-models.log)。

## 2026-11-05 Haiku v8：500次／31因子快捷预测

按用户要求对 `rq8_fold2_v3async_haiku_p8_pe8_px3_avail_nodepth_v8`（`1b154fb482`）
执行快捷脚本 `--attempts 500`，于北京时间 21:54:10 固定前 500 次尝试、31 个入库因子。
原运行已在 515 次／32 因子冻结，本次明确评估前 500 次截面；其全部冻结成员与顺序
均保持原记录，未包含最后新增的第 32 个成员。

Test 模型指标：

| 模型 | √成交额加权 Pearson IC | √成交额加权 Spearman IC | 品种等权 Pearson IC | 品种等权 Spearman IC |
| --- | --- | --- | --- | --- |
| Linear | 0.017342 | 0.015984 | 0.017401 | 0.018160 |
| LightGBM | 0.002950 | 0.008422 | 0.008006 | 0.013896 |

Train+Val 训练 5,308,104 行，31 列全部保留。两个模型均预测 2,558,827 条 Test
可评估行，总可评估行 2,558,829，缺失 2 行，覆盖率 99.999922%。公共入口及额外核对
确认实际次数、配置指纹、冻结 SHA、因子身份、fit／test 规格、全部指标和覆盖率；
`source_matches_search=false` 如实保留来源差异。本次只评估模型预测，不计算单因子 OOS
或配对相关性，不更新实验总表；Test 不回传方法，沿用已有数据与历史留出诊断限制。

[500次模型报告](../../artifacts/comparisons/v3async8_haiku_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-1b154fb482-prefix500/model/report.md)、
[快捷评估日志](../../artifacts/comparisons/v3async8_haiku_avail_nodepth_p8_fold2_20261105/prefix500-models.log)。
- 2026-11-05：V3 Explorer 不再允许 unresolved（`feature/prompt-cleanup`）：提示词写明当前方向受阻（区域 busy／full／关闭，
  或缺数据、工具）时搜索新方向——换一个可用 Region 或提出新机制；仍回复 unresolved 时平台在对话内退回。
  动机：三组 Haiku 无深挖运行在 437、515、912 次因 unresolved 停止，理由含候选区域暂时 busy、可用区域未筛完。
  - 无深挖分叉从 912 次续跑至 1000（`cont912.py`，run `…-20a1012038-nodepth500-cont912`）：复制 912 个 trial 与方法状态，
    把停机 Explorer 的两次 unresolved 改写为新代码的退回，配置不变；第 913 次起使用新代码（含提示词精简与 Miner `done`）。
    原 912 次冻结运行保留不动。
  - Haiku v9（`rq8_fold2_v3async_haiku_p8_pe8_px3_avail_nodepth_v9`）：与 v8 仅差此项，从头运行。

## 2026-11-05 Luna v8／DeepSeek v6：966次与975次最新预测

按用户要求执行快捷脚本，分别评估
`rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_v8`（`22d7cdbc4f`）与
`rq8_fold2_v3async_deepseek_low_p10_pe_px3_avail_nodepth_v6`（`e78d475f38`）。
北京时间 22:05:35／22:05:37 固定前 966 次／32 因子及前 975 次／43 因子的截面。

Test √成交额加权 IC：

| 运行 | 尝试次数 | 因子数 | Linear Pearson | LightGBM Pearson | Linear Spearman | LightGBM Spearman |
| --- | --- | --- | --- | --- | --- | --- |
| Luna v8 | 966 | 32 | 0.009837 | 0.011657 | 0.014413 | 0.015594 |
| DeepSeek v6 | 975 | 43 | 0.016252 | 0.025573 | 0.018896 | 0.024048 |

品种等权 Pearson IC（Linear／LightGBM）分别为 Luna 0.010537／0.013097、
DeepSeek 0.015504／0.025687；等权 Spearman IC 分别为 0.016695／0.020037、
0.021188／0.023674。

较各自前次评估，Luna 从 871 次／29 因子新增 3 个，Linear／LightGBM 加权 Pearson
分别下降 0.000660／0.001149；DeepSeek 从 600 次／24 因子新增 19 个，分别变化
+0.001578／−0.001022。两组各自的前序完整冻结成员和顺序保持不变。当前两个截面及
各自此前截面的数据快照、fold、下游模型配置和依赖版本一致。

两组 Train+Val 均训练 5,308,104 行，32／43 列全部保留；两个模型均预测全部
2,558,829 条 Test 可评估行，覆盖率 100%。公共入口及额外核对确认配置指纹、冻结
SHA、因子身份、fit／test 规格、四项指标及覆盖率；两组 `source_matches_search=false`
如实保留来源差异。本次仅评估组合预测，不计算单因子 OOS 或配对相关性，不更新
实验总表。Test 不回传搜索，沿用已有数据与历史留出诊断限制。

[Luna模型报告](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-22d7cdbc4f-prefix966/model/report.md)、
[DeepSeek模型报告](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-e78d475f38-prefix975/model/report.md)、
[Luna评估日志](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/latest-models-20261105-3.log)、
[DeepSeek评估日志](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/latest-models-20261105-3.log)。

## 2026-11-05 DeepSeek v6：最终1000次结果入表

用户要求将 `rq8_fold2_v3async_deepseek_low_p10_pe_px3_avail_nodepth_v6`（`e78d475f38`）
按 1000 次结果写入总表。最终原运行状态为 `frozen`，检查点确认完成 1000 次、入库
43 个因子；第 976–1000 次原子 trial 均未入库，库版本持续为 43。原运行与已评估的
`-prefix975` 完整冻结库逐字节一致，研究配置及下游模型配置相同；双方配置指纹、冻结
SHA，以及既有 fit／test 的来源与规格核对通过。

按用户授权在 `experiment_overview.md` 的同组 500 次行后新增 1000 次行，复用已有
975 次截面的模型预测结果：Test √成交额加权 Pearson IC 为 Linear 0.016252、
LightGBM 0.025573，覆盖率均为 100%。行身份链接最终 1000 次运行报告，指标链接保留
实际的 975 次模型报告，并在表下说明复用依据。
单因子指标维持“—”，本次未重训模型、计算单因子 OOS 或配对相关性。

[最终1000次运行报告](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-e78d475f38/report.md)、
[复用的模型报告](../../artifacts/comparisons/v3async6_deepseek_low_avail_nodepth_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-e78d475f38-prefix975/model/report.md)。
- 2026-11-05：Luna v8（`22d7cdbc4f`，新提示词但仍允许 unresolved，8 计划／8 评估）跑满 1000 次冻结：入库 35 个，
  单因子 Test IC 0.0047／0.0056（28/35 为正），Linear 0.010046，LightGBM 0.011573；尝试分布于 62 个区域，
  最大区域占比 7%。不如串行 Luna（1000 次：21 个，Linear 0.019660，LightGBM 0.017815），与 16 路并行 Luna
  （45 个，0.007330／0.014097）相近：Luna 在并发运行中入库更多但组合 OOS 更差，分散度改善未带来组合收益。
- 2026-11-05：DeepSeek low「可用性＋无深挖」v6（`e78d475f38`）跑满 1000 次冻结（643 次处续跑一次）：入库 43 个，来自
  20 个区域（单区域最多 6 个：达到上限时已在运行的计划仍可入库），最大区域尝试占比 6%；单因子 Test IC
  0.0063／0.0053（41/43 为正），Linear 0.016252，LightGBM 0.025573。此前两组 DeepSeek V3 1000 次运行入库 83／95 个，
  Linear 0.019145／0.015633，LightGBM 0.005648／0.009349：可用性＋无深挖后入库数减半，单因子质量与 LightGBM 明显提高。

## 2026-11-05 Haiku cont912：938次快捷评估；Luna v8最终结果核对

按用户要求对 `futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-nodepth500-cont912`
执行快捷脚本。北京时间 22:21:39 固定前 938 次、41 个因子，次数包含继承的前 912 次。
续跑尚未新增入库，完整冻结库与原 912 次运行一致。两个模型重新拟合后，全部 Test IC
与 912 次结果的差异不超过 4.1×10⁻¹¹，LightGBM 四项 IC 完全一致。

评估期间用户询问 Luna v8（`22d7cdbc4f`）是否已测；核对发现原运行已完成最终
1000 次／35 因子的自动评估并写入总表，因此读取和验证既有结果，无需重复拟合。
其完整冻结库保留此前 966 次的 32 个成员，另新增 3 个；最终结果区别于此前 966 次截面。

Test √成交额加权 IC：

| 运行 | 尝试次数 | 因子数 | Linear Pearson | LightGBM Pearson | Linear Spearman | LightGBM Spearman |
| --- | --- | --- | --- | --- | --- | --- |
| Haiku cont912 | 938 | 41 | 0.026102 | 0.022128 | 0.021008 | 0.016947 |
| Luna v8 | 1000 | 35 | 0.010046 | 0.011573 | 0.014729 | 0.015340 |

品种等权 Pearson IC（Linear／LightGBM）分别为 Haiku 0.024890／0.021921、
Luna 0.011889／0.012396；等权 Spearman IC 分别为 0.024874／0.024476、
0.017545／0.018941。Luna 较 966 次的 Linear／LightGBM 加权 Pearson 分别变化
+0.000210／−0.000084。

Train+Val 训练行分别为 5,308,074／5,308,104，41／35 列全部保留；Test 可评估行
均为 2,558,829，Haiku 预测 2,558,825 行，覆盖率 99.999844%，Luna 预测全部行。
配置指纹、冻结 SHA、因子身份、fit／test 规格、指标及覆盖率核对通过；各自与前序
评估的数据、fold、下游模型配置和依赖版本一致，均保留 `source_matches_search=false`。
Haiku 本次只评估组合预测，不追加单因子 OOS 或配对相关性，不写其新截面到总表。
Luna 的既有自动收尾结果与表格保留；Test 均不回传搜索。

[Haiku938次模型报告](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-nodepth500-cont912-prefix938/model/report.md)、
[Haiku快捷评估日志](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/cont912-latest-models-20261105.log)、
[Luna1000次模型报告](../../artifacts/comparisons/v3async8_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-22d7cdbc4f/model/report.md)。

## 2026-11-05 Haiku v9：272次／12因子快捷预测

按用户要求对 `rq8_fold2_v3async_haiku_p8_pe8_px3_avail_nodepth_v9`（`f7fec0f4f2`）
执行快捷脚本，于北京时间 22:47:31 固定前 272 次尝试、12 个入库因子，源搜索继续运行。

Test 模型指标：

| 模型 | √成交额加权 Pearson IC | √成交额加权 Spearman IC | 品种等权 Pearson IC | 品种等权 Spearman IC |
| --- | --- | --- | --- | --- |
| Linear | 0.012945 | 0.015897 | 0.011990 | 0.017602 |
| LightGBM | 0.015588 | 0.013818 | 0.017161 | 0.017636 |

Train+Val 训练 5,307,761 行，12 列全部保留。两个模型均预测 2,558,551 条 Test
可评估行，总可评估行 2,558,829，缺失 278 行，覆盖率 99.989136%。公共入口及额外
核对确认实际次数、配置指纹、冻结 SHA、因子身份、fit／test 规格、全部指标和覆盖率；
`source_matches_search=false` 如实保留来源差异。本次仅评估组合预测，不计算单因子 OOS
或配对相关性，不更新实验总表。Test 不回传搜索，沿用已有数据与历史留出诊断限制。

[模型报告](../../artifacts/comparisons/v3async9_haiku_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-f7fec0f4f2-prefix272/model/report.md)、
[快捷评估日志](../../artifacts/comparisons/v3async9_haiku_avail_nodepth_p8_fold2_20261105/latest-models-20261105.log)。
- 2026-11-05：Luna 组合 OOS 偏差的归因（fold2，V3 Async 四组 Luna 与 Haiku／DeepSeek 对照）：
  - 不是并发特有：两组串行 Luna 分别为 Linear 0.0197（`8d95766cb7`）与 0.0052（`510cffd920`），四组中三组偏差。
  - Luna 的 5m 因子各组都稳定（Test 0.0060–0.0069，几乎无负值）；差组的问题集中在日线（`@1d`）因子：
    `510cffd920` 18/24、16 路 24/45、v8 26/35 为日线因子，Test 仅 0.0027–0.0042，负值 4–7 个；好组 `8d95766cb7`
    只有 2/21 个日线因子、无负值。各模型日线尝试的入库率相近（4.6%–6.9%），不是 Luna 筛选更宽。
  - Luna 的日线因子多为阈值／事件型（失败突破、边界拒绝、日内 VWAP 不对称等，16/26），且几乎不结合 5m 信息；
    此类条件型日线因子 Test 0.0015–0.0037。Haiku 日线因子中连续型 Test 0.0079–0.0081；DeepSeek 一半日线因子混合 5m
    信息（Test 0.0099）。
  - 时间上，好组使用旧分档（1h／1d），差组均为三分档（日内／1–20 日／20 日以上）后：Luna 日线尝试占比从 7% 升至
    32%–42%，与“在未测分档测试同一机制算新方向”的提示一致。样本仅四组，属相关性证据。

## 2026-11-05 Haiku cont912：最终1000次／42因子模型结果

按用户要求跟踪 `futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-nodepth500-cont912`
至 1000 次提交。运行已正常冻结，最终入库 42 个因子；此前 938 次的全部 41 个冻结成员
及顺序保留，新增 1 个。本次复用并核对原脚本自动收尾生成的模型结果，没有另起重复拟合。

Test 模型指标：

| 模型 | √成交额加权 Pearson IC | √成交额加权 Spearman IC | 品种等权 Pearson IC | 品种等权 Spearman IC |
| --- | --- | --- | --- | --- |
| Linear | 0.025755 | 0.020874 | 0.024001 | 0.024276 |
| LightGBM | 0.022685 | 0.016496 | 0.020622 | 0.023093 |

较 938 次／41 因子，Linear／LightGBM 加权 Pearson 分别变化 −0.000347／+0.000557。
数据快照、fold、下游模型配置与依赖版本一致；Train+Val 训练 5,308,074 行，42 列全部
保留。两个模型均预测 2,558,825／2,558,829 条 Test 可评估行，覆盖率 99.999844%。
配置指纹、冻结 SHA、因子身份、fit／test 规格、全部指标和覆盖率核对通过；
`source_matches_search=false` 如实保留来源差异，沿用历史留出诊断限制。

原脚本已完成自动收尾并将该续跑分支的 1000 次结果写入总表；原 912 次运行保持独立。
本次未另行启动单因子 OOS 或配对相关性计算，Test 不回传方法。

[最终模型报告](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-nodepth500-cont912/model/report.md)、
[最终运行报告](../../artifacts/comparisons/v3async6_haiku_nodepth_fork500_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-nodepth500-cont912/report.md)。
- 2026-11-05：无深挖分叉续跑（`…-nodepth500-cont912`）跑满 1000 次冻结，未再停机：第 913–1000 次新增 1 个因子
  （共 42 个），单因子 Test IC 0.0065／0.0067（38/42 为正），Linear 0.025755，LightGBM 0.022685，与 912 次快照
  （0.026102／0.022128）基本相同。
- 2026-11-05：Luna v9（`rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_v9`，run `af75f77310`）：与 Haiku v9 同配置
  （不允许 unresolved、可用性 5／1、无深挖、8 计划／8 评估／3 Explorer），用于复查 Luna 结果的波动。
  - 重复运行 `..._avail_nodepth_v9_r2`（run `ea03683b78`）：配置与 Luna v9 完全相同（seed 42），两组并行，用于估计运行间随机性。

## 2026-11-05 Haiku无深挖分支：500／1000次模型的年度稳定性

用户要求检查模型在 Test 按年的表现。比较共享前 500 次搜索的两个冻结模型输入：
原串行 `20a1012038-prefix500`（500 次／26 因子）与无深挖续跑
`20a1012038-nodepth500-cont912`（1000 次／42 因子）。复用各自保存的模型与预处理，
不重训或筛选因子。42 个冻结因子计算一次，其中前 26 个完整记录与 500 次库一致。

以完整 Test 的合格样本为基础，按 `trading_day` 切分 2024／2025 年和各自然月，
保留原 Test 目标边界及 2024–2025 全期固定品种 √成交额权重。两组可预测行逐行相同；
2024 年预测 1,238,337／1,238,337 行，2025 年预测 1,320,488／1,320,492 行。
四个模型的全期四项 IC 复现已存结果（绝对容差 1e-10、相对容差 1e-9），预测覆盖亦一致。

年度 √成交额加权 Pearson IC：

| 截面 | 模型 | 2024 | 2025 | 2025较2024下降 | 正IC月数（全24月） |
| --- | --- | --- | --- | --- | --- |
| 500次／26因子 | Linear | 0.029338 | 0.020063 | 31.6% | 21/24 |
| 500次／26因子 | LightGBM | 0.048659 | 0.011103 | 77.2% | 20/24 |
| 1000次／42因子 | Linear | 0.030558 | 0.020494 | 32.9% | 18/24 |
| 1000次／42因子 | LightGBM | 0.034210 | 0.011242 | 67.1% | 18/24 |

年度加权 Spearman 同样是 2025 较弱：500 次 Linear 为 0.027304→0.017746，
LightGBM 为 0.026147→0.017182；1000 次分别为 0.025425→0.015820、
0.020103→0.011591。年度／全期 IC 不是月度 IC 的简单平均。

描述性判断：Linear 的年度落差小于 LightGBM，但两个年份的表现并不均衡。
500 次 LightGBM 的高全期 Pearson 主要伴随较强的 2024 表现，2025 仅约 0.0111；
扩到 1000 次后，2025 仅增加 0.000139，年度落差缩小主要来自 2024 下降 0.014449。
Linear 两年 Pearson 小幅提高，正 IC 月数却由 21 降至 18；LightGBM 由 20 降至 18。
月度 Pearson 标准差在两年、两个模型中都增大，未观察到扩库后更好的月度稳定性。
这里只描述已观察的两个历史年份，不作长期稳定或因果归因判断。

预测与评分复用 MarketData、冻结 DSL、`transform`、`predict`、`metrics`、`product_weights`
和 `split_panel`。配置指纹、冻结 SHA、输入顺序、数据快照和模型规格核对通过；脚本
Ruff 检查通过，各年度／月份指标有限、覆盖率及样本数核对通过。未修改原模型、
搜索状态或实验总表，不计算因子配对相关性，诊断不回传搜索。

[年度／月度报告](../../artifacts/comparisons/haiku_nodepth_yearly_20261105/report.md)、
[完整指标](../../artifacts/comparisons/haiku_nodepth_yearly_20261105/results.json)、
[复跑脚本](../../artifacts/comparisons/haiku_nodepth_yearly_20261105/evaluate_yearly.py)。

## 2026-11-05 Haiku v9：500次／24因子快捷预测

按用户要求评估 `rq8_fold2_v3async_haiku_p8_pe8_px3_avail_nodepth_v9`（`f7fec0f4f2`）
前 500 次。请求时检查点为 495 次；等待提交达到 500 后，使用快捷脚本
`--attempts 500` 于北京时间 23:43:01 固定 24 个入库因子，源搜索继续运行。

Test 模型指标：

| 模型 | √成交额加权 Pearson IC | √成交额加权 Spearman IC | 品种等权 Pearson IC | 品种等权 Spearman IC |
| --- | --- | --- | --- | --- |
| Linear | 0.013542 | 0.016441 | 0.017930 | 0.021550 |
| LightGBM | 0.013391 | 0.016203 | 0.017432 | 0.021956 |

此前 272 次的 12 个完整冻结成员及顺序保留，新增 12 个。较此前截面，Linear／LightGBM
加权 Pearson 分别变化 +0.000596／−0.002198；预测覆盖率由 99.989136% 升至 100%，
多覆盖 278 行，上述差值使用各自可预测样本。数据快照、fold、模型配置与依赖版本一致。

Train+Val 训练 5,308,104 行，24 列全部保留；两个模型均预测全部 2,558,829 条 Test
可评估行。配置指纹、冻结 SHA、因子身份、fit／test 规格、四项指标与覆盖率核对通过；
`source_matches_search=false` 如实保留来源差异。本次仅评估模型预测，不计算单因子 OOS
或配对相关性，不更新实验总表，Test 不回传搜索，沿用已有历史留出诊断限制。

[500次模型报告](../../artifacts/comparisons/v3async9_haiku_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-f7fec0f4f2-prefix500/model/report.md)、
[快捷评估日志](../../artifacts/comparisons/v3async9_haiku_avail_nodepth_p8_fold2_20261105/prefix500-models.log)。

## 2026-11-05 Luna v9／v9 r2：当前376次与333次快捷预测

按用户要求，用快捷脚本分别固定两组当前已提交截面，源搜索继续运行：
`rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_v9`（`af75f77310`）于北京时间
23:54:19 捕获 376 次／15 因子；同配置重复运行 `..._v9_r2`（`ea03683b78`）于
23:54:20 捕获 333 次／15 因子。各自保存到 `-prefix376`／`-prefix333`。

Test 模型指标：

| 截面 | 模型 | √成交额加权 Pearson IC | √成交额加权 Spearman IC | 品种等权 Pearson IC | 品种等权 Spearman IC |
| --- | --- | --- | --- | --- | --- |
| v9：376次／15因子 | Linear | 0.014912 | 0.020047 | 0.018610 | 0.024695 |
| v9：376次／15因子 | LightGBM | 0.013972 | 0.020585 | 0.017417 | 0.025122 |
| v9 r2：333次／15因子 | Linear | 0.008747 | 0.017208 | 0.011420 | 0.019638 |
| v9 r2：333次／15因子 | LightGBM | 0.012930 | 0.015052 | 0.016592 | 0.021446 |

两组数据快照、fold、下游模型配置与依赖版本一致，15 列均全部保留。v9 的 Train+Val
训练 5,308,076 行，两个模型均预测 2,558,827／2,558,829 条 Test 可评估行，覆盖率
99.999922%；r2 训练 5,308,002 行，预测 2,558,795／2,558,829 行，覆盖率 99.998671%。
当前 v9 的两个模型加权 Pearson／Spearman 均较高；这里使用各自可预测样本，尝试数
不同，不能解释为固定预算下的重复实验优劣结论。

两组配置指纹、冻结 SHA、因子身份与顺序、fit／test 规格、四项指标和覆盖率均核对通过；
`source_matches_search=false` 如实保留来源差异。仅评估模型预测，不计算单因子 OOS
或配对相关性，不更新实验总表，Test 不回传搜索，沿用历史留出诊断限制。

[v9模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-af75f77310-prefix376/model/report.md)、
[v9 r2模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78-prefix333/model/report.md)。

## 2026-11-05 V3 Async Luna v9 与 v9 r2：当前截面快捷评估

按用户要求运行 `scripts/evaluate_snapshot.py af75f77310` 和
`scripts/evaluate_snapshot.py ea03683b78`，北京时间 23:53:10 分别冻结检查点已提交的
前 369 次和前 323 次；两组均入库 15 个因子。两组使用 Luna、fold2、seed 42、8 计划、
8 路评估和 3 路 Explorer；下游模型配置相同。

Test √成交额加权 IC：

| 运行 | 尝试次数 | 因子数 | Linear Pearson | LightGBM Pearson | Linear Spearman | LightGBM Spearman | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v9（af75f77310） | 369 | 15 | 0.014912 | 0.013972 | 0.020047 | 0.020585 | 99.999922% |
| v9 r2（ea03683b78） | 323 | 15 | 0.008747 | 0.012930 | 0.017208 | 0.015052 | 99.998671% |

两组分别预测 2,558,827／2,558,829 和 2,558,795／2,558,829 条 Test 可评估行；
Train+Val 训练行数为 5,308,076 和 5,308,002，所有因子列均保留。两组尝试次数不同，
不能将当前指标差异直接归因于单一因素。公共配置、数据、冻结库及因子身份校验通过；
原搜索继续运行，Test 不回传。仅评估组合预测，没有单因子 OOS、库内配对相关性或总表写入；
Test 已被观察，属于历史留出诊断，不能视为最终盲测。

[v9 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-af75f77310-prefix369/model/report.md)、
[v9 r2 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78-prefix323/model/report.md)、
[v9 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261105/v9-models.log)、
[v9 r2 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261105/v9-r2-models.log)。

## 2026-11-06 V3 Async Luna v9 与 v9 r2：最新截面快捷评估

按用户要求再次运行 `scripts/evaluate_snapshot.py af75f77310` 和
`scripts/evaluate_snapshot.py ea03683b78`，北京时间 00:42:55 分别冻结检查点已提交的
前 677 次／24 因子和前 625 次／27 因子。两组原搜索继续运行，配置沿用此前截面。

Test √成交额加权 IC：

| 运行 | 尝试次数 | 因子数 | Linear Pearson | LightGBM Pearson | Linear Spearman | LightGBM Spearman | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v9（af75f77310） | 677 | 24 | 0.014744 | 0.013806 | 0.018897 | 0.019497 | 99.999922% |
| v9 r2（ea03683b78） | 625 | 27 | 0.013646 | 0.016707 | 0.021010 | 0.018343 | 100.000000% |

与此前 369 次／15 因子和 323 次／15 因子截面相比，两组分别增加 9 和 12 个成员；
此前成员的完整冻结记录与顺序均保持一致。Linear／LightGBM 的 Pearson 分别变化
−0.000167／−0.000167 和 +0.004899／+0.003777。预测覆盖分别为
2,558,827／2,558,829 和 2,558,829／2,558,829；Train+Val 训练行数为
5,308,076 和 5,308,104，所有因子列均保留。两组尝试次数和成员不同，指标差异不能
直接归因于单一因素。

公共配置、数据、冻结库及因子身份校验通过；Test 不回传搜索。仅评估组合预测，未计算
单因子 OOS 或库内配对相关性，未写实验总表；Test 已被观察，属于历史留出诊断。

[v9 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-af75f77310-prefix677/model/report.md)、
[v9 r2 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78-prefix625/model/report.md)、
[v9 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-models.log)、
[v9 r2 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-r2-models.log)。

## 2026-11-06 V3 Async Luna v9 与 v9 r2：前500次快捷评估

按用户要求运行 `scripts/evaluate_snapshot.py af75f77310 --attempts 500` 和
`scripts/evaluate_snapshot.py ea03683b78 --attempts 500`，北京时间 00:50:05 分别冻结
前 500 次已提交记录，入库 18 和 22 个因子。两组原搜索继续运行，配置沿用此前截面。

Test √成交额加权 IC：

| 运行 | 尝试次数 | 因子数 | Linear Pearson | LightGBM Pearson | Linear Spearman | LightGBM Spearman | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v9（af75f77310） | 500 | 18 | 0.015366 | 0.013423 | 0.019510 | 0.019552 | 99.999922% |
| v9 r2（ea03683b78） | 500 | 22 | 0.013794 | 0.015852 | 0.020765 | 0.018932 | 100.000000% |

分别预测 2,558,827／2,558,829 和 2,558,829／2,558,829 条 Test 可评估行；
Train+Val 训练行数为 5,308,076 和 5,308,101，所有因子列均保留。核对前 500 次的
完整成员记录与顺序，分别与此前 677 次和 625 次截面的前 18 和 22 个成员一致。
公共配置、数据、冻结库及因子身份校验通过；Test 不回传搜索。仅评估组合预测，未计算
单因子 OOS 或库内配对相关性，未写实验总表；Test 已被观察，属于历史留出诊断。

[v9 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-af75f77310-prefix500/model/report.md)、
[v9 r2 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78-prefix500/model/report.md)、
[v9 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-prefix500-models.log)、
[v9 r2 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-r2-prefix500-models.log)。

- 2026-11-06：用户要求再跑一组 Luna v9 重复，合计 3 组，最后汇报 3 组在 500 和 1000 trial 时的平均结果。新增 `v3async9_luna_avail_nodepth_p8_r3_fold2_20261105`（版本 `..._v9_r3`，run 23979d14af），配置与 v9（af75f77310）、r2（ea03683b78）完全相同。500 处用 `scripts/evaluate_snapshot.py --attempts 500` 做前缀快照。

## 2026-11-06 V3 Async Luna v9 与 v9 r2：26／29因子快捷评估

按用户要求再次运行 `scripts/evaluate_snapshot.py af75f77310` 和
`scripts/evaluate_snapshot.py ea03683b78`，北京时间 00:56:49 分别冻结检查点已提交的
前 743 次／26 因子和前 695 次／29 因子。两组原搜索继续运行，配置沿用此前截面。

Test √成交额加权 IC：

| 运行 | 尝试次数 | 因子数 | Linear Pearson | LightGBM Pearson | Linear Spearman | LightGBM Spearman | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v9（af75f77310） | 743 | 26 | 0.012589 | 0.011004 | 0.018057 | 0.018472 | 99.999922% |
| v9 r2（ea03683b78） | 695 | 29 | 0.014337 | 0.016470 | 0.020781 | 0.018419 | 100.000000% |

与此前 677 次／24 因子和 625 次／27 因子截面相比，两组各增加 2 个成员，此前成员
完整冻结记录及顺序均保持一致。Linear／LightGBM 的 Pearson 分别变化
−0.002155／−0.002802 和 +0.000691／−0.000236。分别预测
2,558,827／2,558,829 和 2,558,829／2,558,829 条 Test 可评估行；Train+Val 训练行数
为 5,308,076 和 5,308,104，所有因子列均保留。

公共配置、数据、冻结库及因子身份校验通过；Test 不回传搜索。仅评估组合预测，未计算
单因子 OOS 或库内配对相关性，未写实验总表；Test 已被观察，属于历史留出诊断。

[v9 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-af75f77310-prefix743/model/report.md)、
[v9 r2 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78-prefix695/model/report.md)、
[v9 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-26f-latest-models.log)、
[v9 r2 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-r2-29f-latest-models.log)。
- 2026-11-06：Luna v9 三组同时失败，报 HTTP 401（本地 codex 网关返回 token_expired，即上游登录过期，不是代码问题）。停止位置：v9 在 801（27 个因子），r2 在 747（29 个），r3 在 174（7 个）。Haiku v9 不受影响。用户重新登录网关后，用 `run.py <run_dir>` 续跑。
- 2026-11-06：Haiku v9（f7fec0f4f2，不接受 unresolved）正常冻结在 1000，没有再提前停。共 34 个因子；Linear 0.014474，LGBM 0.012566；单因子测试 IC 中位数 0.0040（验证 0.0081，测试/验证 0.53）。按跨度分：intraday 7 个，1d_to_20d 11 个，over_20d 16 个，各组测试 IC 均值在 0.005 左右。对比 Haiku v8（515 次停止）：32 个因子，0.019176/0.006097，单因子测试 IC 0.0064。对比旧提示词续跑组 cont912（1000）：42 个因子，0.025755/0.022685，单因子测试 IC 0.0067。

## 2026-11-06 V3 Async Luna v9 与 v9 r2：27／29因子快捷评估

按用户要求再次运行 `scripts/evaluate_snapshot.py af75f77310` 和
`scripts/evaluate_snapshot.py ea03683b78`，北京时间 10:04:38 分别冻结检查点已提交的
前 801 次／27 因子和前 747 次／29 因子。两组原搜索继续运行，配置沿用此前截面。

Test √成交额加权 IC：

| 运行 | 尝试次数 | 因子数 | Linear Pearson | LightGBM Pearson | Linear Spearman | LightGBM Spearman | 预测覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v9（af75f77310） | 801 | 27 | 0.013296 | 0.011163 | 0.017680 | 0.018150 | 99.999922% |
| v9 r2（ea03683b78） | 747 | 29 | 0.014337 | 0.016470 | 0.020781 | 0.018419 | 100.000000% |

相较此前 743 次／26 因子截面，v9 增加 1 个成员，原成员的完整冻结记录和顺序一致；
Linear／LightGBM Pearson 分别提高 0.000706／0.000160。r2 相较 695 次／29 因子截面，
全部成员完整冻结记录及顺序一致，两模型的 Test 指标一致。分别预测
2,558,827／2,558,829 和 2,558,829／2,558,829 条 Test 可评估行；Train+Val 训练行数
为 5,308,079 和 5,308,104，所有因子列均保留。

公共配置、数据、冻结库及因子身份校验通过；Test 不回传搜索。仅评估组合预测，未计算
单因子 OOS 或库内配对相关性，未写实验总表；Test 已被观察，属于历史留出诊断。

[v9 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-af75f77310-prefix801/model/report.md)、
[v9 r2 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78-prefix747/model/report.md)、
[v9 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-latest-repeat-2-models.log)、
[v9 r2 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-r2-latest-repeat-2-models.log)。

## 2026-11-06 V3 Async Luna v9 r3：当前截面快捷评估

按用户要求运行 `scripts/evaluate_snapshot.py 23979d14af`，北京时间 10:18:28 冻结
`rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_v9_r3` 检查点已提交的前 280 次，
入库 15 个因子。Test √成交额加权 Pearson IC：Linear 为 0.014280，LightGBM 为
0.013703；加权 Spearman IC 分别为 0.018664 和 0.017539。

两模型均预测 2,558,823／2,558,829 条 Test 可评估行，覆盖率 99.999766%；
Train+Val 训练行数为 5,308,085，所有因子列均保留。公共配置、数据、冻结库及因子身份
校验通过；原搜索继续运行，Test 不回传。仅评估组合预测，未计算单因子 OOS 或库内配对
相关性，未写实验总表；Test 已被观察，属于历史留出诊断。

[r3 模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r3_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-23979d14af-prefix280/model/report.md)、
[r3 日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-r3-latest-models.log)。

## 2026-11-06 V3 Async Luna v9 r3：21因子快捷评估

按用户要求再次运行 `scripts/evaluate_snapshot.py 23979d14af`，北京时间 10:38:26 冻结
检查点已提交的前 396 次／21 因子。Test √成交额加权 Pearson IC：Linear 为 0.014094，
LightGBM 为 0.012094；加权 Spearman IC 分别为 0.019151 和 0.015648。

相较此前 280 次／15 因子截面，新增 6 个成员，原成员完整冻结记录及顺序保持一致；
Linear／LightGBM Pearson 分别下降 0.000186／0.001609。两模型均预测
2,558,823／2,558,829 条 Test 可评估行，覆盖率 99.999766%，与此前相同；Train+Val
训练行数为 5,308,085，所有因子列均保留。公共配置、数据、冻结库及因子身份校验通过；
原搜索继续运行，Test 不回传。仅评估组合预测，未计算单因子 OOS 或库内配对相关性，
未写实验总表；Test 已被观察，属于历史留出诊断。

[21因子模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r3_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-23979d14af-prefix396/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-r3-21f-models.log)。

## 2026-11-06 V3 Async Luna v9 r3：25因子快捷评估

按用户要求再次运行 `scripts/evaluate_snapshot.py 23979d14af`，北京时间 11:03:02 冻结
检查点已提交的前 470 次／25 因子。Test √成交额加权 Pearson IC：Linear 为 0.012377，
LightGBM 为 0.010776；加权 Spearman IC 分别为 0.015101 和 0.013465。

相较此前 396 次／21 因子截面，新增 4 个成员，原成员完整冻结记录及顺序保持一致；
Linear／LightGBM Pearson 分别下降 0.001717／0.001318。两模型均预测
2,558,823／2,558,829 条 Test 可评估行，覆盖率 99.999766%，与此前相同；Train+Val
训练行数为 5,308,085，所有因子列均保留。公共配置、数据、冻结库及因子身份校验通过；
原搜索继续运行，Test 不回传。仅评估组合预测，未计算单因子 OOS 或库内配对相关性，
未写实验总表；Test 已被观察，属于历史留出诊断。

[25因子模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r3_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-23979d14af-prefix470/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-r3-25f-models.log)。

## 2026-11-06 Luna v9：最终1000次／27因子，复用相同模型的Test证据

用户通知 `rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_v9`（`af75f77310`）
已完成 1000 次。核对原运行与检查点，实际提交 1000 次、冻结 27 个因子；冻结库文件
与已评估的 `-prefix801` 逐字节一致。原脚本已自动完成最终模型训练；两个模型保存的
全部拟合结果（含预处理）与 801 次模型逐项一致，因子身份、顺序、数据快照、fold、
模型配置、实现标识和依赖版本亦一致，因此直接复用 801 次已保存的 Test 证据。
记录时原脚本仍在自动收尾，最终目录尚未写出 `model/test.json`；以下不冒充新执行的 Test。

| 模型 | √成交额加权 Pearson IC | √成交额加权 Spearman IC | 品种等权 Pearson IC | 品种等权 Spearman IC |
| --- | --- | --- | --- | --- |
| Linear | 0.013296 | 0.017680 | 0.013706 | 0.019884 |
| LightGBM | 0.011163 | 0.018150 | 0.011497 | 0.021200 |

相较 500 次／18 因子，新增 9 个成员，原成员完整冻结记录和顺序保留；Linear／LightGBM
加权 Pearson 分别下降 0.002071／0.002260，Spearman 分别下降 0.001830／0.001402。
801 次之后的 199 次没有新增成员。最终训练 5,308,079 行、全部 27 列保留；复用的
Test 结果预测 2,558,827／2,558,829 行，覆盖率 99.999922%，与 500 次覆盖行数相同。

最终配置指纹、冻结 SHA、fit 规格及缓存 Test 规格、四项指标与覆盖率核对通过；
`source_matches_search=false` 如实保留。未启动重复评估、单因子 OOS 或配对相关性，
未手动更新实验总表；原脚本已有的自动收尾继续运行。Test 不回传搜索，沿用历史留出
诊断限制。

[复用的801次Test报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-af75f77310-prefix801/model/report.md)、
[最终模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-af75f77310/model/report.md)。

## 2026-11-06 V3 Async Luna v9 r3：前500次快捷评估

按用户要求运行 `scripts/evaluate_snapshot.py 23979d14af --attempts 500`，北京时间
11:12:13 冻结精确前 500 次／26 因子。Test √成交额加权 Pearson IC：Linear 为
0.012439，LightGBM 为 0.010776；加权 Spearman IC 分别为 0.015132 和 0.013465。

相较此前 470 次／25 因子截面，新增 1 个成员，原成员完整冻结记录及顺序保持一致；
Linear Pearson 提高 0.000062，LightGBM 的 Test 指标一致。两模型均预测
2,558,823／2,558,829 条 Test 可评估行，覆盖率 99.999766%，与此前相同；Train+Val
训练行数为 5,308,085，所有因子列均保留。公共配置、数据、冻结库及因子身份校验通过；
原搜索继续运行，Test 不回传。仅评估组合预测，未计算单因子 OOS 或库内配对相关性，
未写实验总表；Test 已被观察，属于历史留出诊断。

[500次模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r3_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-23979d14af-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async_luna_v9_latest_20261106/v9-r3-prefix500-models.log)。

## 2026-11-06 V3 Async Luna v9、r2、r3：前500次单因子补评

按用户要求，对 `af75f77310`、`ea03683b78` 和 `23979d14af` 的既有前 500 次冻结截面
分别调用公共 `test_frozen`，沿用冻结的训练方向及裁剪边界，计算全部入库因子的单因子
Test 指标。三组成员身份与冻结库顺序一致，18／22／26 个因子全部计算成功，所列两个
指标均无缺失。表中均值对各入库因子的对应指标等权平均；各因子指标本身按品种 √成交额加权。

| 运行 | 因子数 | Test 单因子加权 Pearson 均值 | Test 单因子加权 Spearman 均值 |
| --- | ---: | ---: | ---: |
| v9（af75f77310） | 18 | 0.004228 | 0.003599 |
| v9 r2（ea03683b78） | 22 | 0.006834 | 0.006407 |
| v9 r3（23979d14af） | 26 | 0.005926 | 0.005100 |

公共配置、数据、冻结库及因子身份校验通过；原搜索不接收 Test 反馈。新增截面的
`oos.json` 并刷新运行报告，未重新拟合组合模型，未计算库内配对相关性或写入实验总表。
Test 已被观察，属于历史留出诊断，不是最终盲测。

[v9 单因子结果](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-af75f77310-prefix500/oos.json)、
[v9 r2 单因子结果](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78-prefix500/oos.json)、
[v9 r3 单因子结果](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r3_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-23979d14af-prefix500/oos.json)。

## 2026-11-06 Haiku V3 串行与无深挖续跑：共享500次结果入表

按用户要求，将 `20a1012038` 与 `20a1012038-nodepth500-cont912` 共享的前 500 次
截面放在两条 1000 次主表行之前。核对两组前 500 个原子 trial 逐字节一致，500 次冻结库
的 26 个成员完整记录与顺序均与两组最终库前 26 个成员一致；两组数据快照及 fold 一致。

单因子结果从已保存的最终 OOS 按 26 个冻结成员身份提取，核对冻结库 SHA、OOS 指纹和
结果身份；两组提取结果一致。Test √成交额加权 Pearson 均值／中位数为
0.006393／0.006366，正 IC 25／26（96.15%），加权 Spearman 均值为 0.002584。
500 次截面的既有 Linear／LightGBM Test 加权 Pearson 分别为 0.024497／0.028821，
预测覆盖率为 99.999844%。两条 1000 次行保留，主表只列一条共享截面，避免重复计数。

此次复用已有结果，未重新计算 Test 或创建单因子 OOS 缓存；在主表说明了单因子结果来源。
核对行位置、数值及链接，`git diff --check` 通过。Test 属于已观察的历史留出诊断。

[500次模型报告](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038-prefix500/model/report.md)、
[单因子 OOS 来源](../../artifacts/comparisons/v3async2_haiku_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-20a1012038/oos.json)、
[实验总览](../../experiment_overview.md#期货因子搜索fold2)。

## 2026-11-06 V1 与 V3 主表差距诊断（fold2，1000 次）

用户问为什么主表里很多 V1 比 V3 好。对主表全部 fold2 V1 运行（DS 958979b287／4e3f0e7900，Haiku ef96a68059／531d4676f6，Luna 382bb38611）和 V3 运行（DS 4e2a92c781／debb1b1dfd／e78d475f38，Haiku 600bd5bbd1／cont912／f7fec0f4f2，Luna 8d95766cb7／bf0c21a38d／22d7cdbc4f／af75f77310）做逐因子诊断。脚本见会话草稿，只读运行产物，不回传搜索。组合模型数值取自主表（统一的 leaf2000／depth5 参数）。

- **差距不是一致的**。500 次截面上 V3 不比 V1 差：Luna V3 为 0.0199／0.0111（Linear），V1 为 0.0121；Haiku V3 为 0.0245，V1 为 0.0163／0.0246。到 1000 次，平均 Linear 是 V1 0.0192、V3 0.0153，平均 LGBM 是 V1 0.0171、V3 0.0147。差距主要来自 Luna：V1 为 0.0182，V3 四组为 0.0197／0.0073／0.0100／0.0133。Haiku 和 DS 的区间相互重叠，最好的 Haiku Linear（0.0258）和最好的 DS LGBM（0.0256）都是 V3。
- **单次运行的波动很大**。V1 Luna 只有一组；V3 的四组 Luna 版本各不相同，同配置组之间的差距与 V1 和 V3 的差距同量级。之前的敏感性诊断也显示，单个特征就能让 LGBM 的 Test 变化约 0.014。
- **单因子质量和冗余度不是主因**。V3 入库因子的测试/验证比（0.41–0.94）不低于 V1（0.22–1.00）；有效因子数／因子数，V3 为 0.24–0.68，V1 为 0.17–0.65。无深挖版本的 Region 集中度也更低，最大的单个 Region 只占 10–15%。
- **一致的结构差异是 V3 偏向日线因子**。入库因子中含 `@1d` 的比例，V1 为 4%／12%／32%／52%／25%，V3 多为 51–74%（Luna 串行组 8d95766cb7 只有 10%）。Luna 与 Haiku 的 V3 日线因子测试 IC 偏低：Luna 0.0028–0.0042，同组 5m 因子 0.0068–0.0070，与 V1 Luna 的 5m 因子 0.0073 相当；Haiku f7f 为 0.0046，5m 为 0.0063。DS 是例外，它的日线因子测试 IC 不低（e78 为 0.0080）。可能的原因：V3 的三档时间跨度和"未测跨度算作新方向"的提示，再加上每档 20 次试探，让 Explorer 把预算投向日线和长周期，而目标只有 12 根 bar（1 小时）。
- 结论：现有证据不足以说明 V3 整体劣于 V1。可以确认的是 Luna（以及部分 Haiku 运行）的 V3 库日线占比过高，而这部分因子泛化较弱。要定论，需要同配置多轮复跑（Luna v9 三组正在跑；V1 Luna 也应补重复）。要检验日线偏向，可以把"未测跨度算作新方向"改为中性表述，或对比去掉时间跨度分档的版本。

## 2026-11-06 V3 Async Luna v9 r2：500／1000次评估结果

`rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_v9_r2`（`ea03683b78`）完成 1000 次
搜索并冻结 32 个因子。按用户要求核对与前 500 次截面的结果：500 次入库 22 个因子，
其完整冻结记录和顺序与最终库前 22 个成员一致。

Test √成交额加权 IC：

| 尝试次数 | 因子数 | 单因子 Pearson 均值／中位数 | 正 IC | 单因子 Spearman 均值 | Linear Pearson | LightGBM Pearson |
| --- | ---: | --- | --- | ---: | ---: | ---: |
| 500 | 22 | 0.006834／0.008631 | 19／22（86.36%） | 0.006407 | 0.013794 | 0.015852 |
| 1000 | 32 | 0.006489／0.006844 | 28／32（87.50%） | 0.005579 | 0.014437 | 0.016280 |

单因子结果全部成功且指标无缺失；模型输入无被移除因子列。两截面的两模型均预测
全部 2,558,829 条 Test 可评估行，Train+Val 训练行数分别为 5,308,101／5,308,104。
核对配置指纹、冻结库 SHA、单因子 OOS 指纹和成员身份、模型拟合及 Test 规格一致。
最终单因子与模型 Test 结果由运行的自动收尾生成；写本记录时库内相似度仍在计算。
Test 已被观察，属于历史留出诊断。

[500次模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78-prefix500/model/report.md)、
[500次单因子结果](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78-prefix500/oos.json)、
[1000次模型 Test](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78/model/test.json)、
[1000次单因子结果](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78/oos.json)。

## 2026-11-06 V3 Async Luna v9 r2：500／1000次结果入表

按用户要求，将 `ea03683b78` 已评估的前 500 次和最终 1000 次结果相邻写入主表；
复用各自已保存的单因子和模型 Test，不重新计算。两截面分别入库 22／32 个因子，
单因子 Pearson 均值／中位数为 0.006834／0.008631、0.006489／0.006844，
Spearman 均值／中位数为 0.006407／0.004732、0.005579／0.005149；
Linear／LightGBM Pearson 为 0.013794／0.015852、0.014437／0.016280。

自动收尾完成最终 496 对 Val 相似度，但原运行脚本使用旧表头写主工作区总表时失败；
本次使用主工作区当前填表逻辑复用完整结果补入 1000 次及其相似度行，再按相同表头
补入 500 次行。两截面成员关系、数据及产物校验见前一节；核对行顺序、数值、相似度
和链接，`git diff --check` 通过。Test 已被观察，属于历史留出诊断。

[实验总览](../../experiment_overview.md#期货因子搜索fold2)、
[1000次相似度](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r2_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-ea03683b78/library_similarity.json)。
- 补充（用户指出）：V3 的三档按整个表达式的特征回看跨度计算（`feature_span_minutes`，一个交易日按 240 分钟），不看字段用的是 `@1d` 还是 `@15m`。`@1d` 是另一个维度。两者的关系是实际观察到的：over_20d 档的尝试几乎全部用了 `@1d`（如 Luna af7 为 0.00 不用／0.16 用，Haiku f7f 为 0.06／0.22），1d_to_20d 档两种写法都有。V1 Luna 的 over_20d 尝试只占 3%，V3 Luna 无深挖、并行组为 16–29%（串行组 8d9 为 2%）。因此上面"分档把预算推向日线"应理解为：分档把预算推向长回看，Miner 实现长回看时几乎都用 `@1d`。
- 2026-11-06 提示词修改（prompts 工作区，未提交）：Explorer 对分档的描述从 "feature span, the approximate trading time…" 改为 "lookback span"，说明按整个表达式回看窗口的时间跨度计算，嵌套窗口和 DELAY 累加（例：5m 上 `DELAY(TS_MEAN($volume,48),48)` 约两天，平台算出 475 分钟，属 1d_to_20d），并写明字段频率不决定所属档。"未测档算新方向"一句不变。Luna v9 r3（23979d14af）在修改前启动，进程内仍用旧提示词；若 r3 中途崩溃恢复，会混入新提示词，需要另行记录。新提示词用于之后新起的组。


## 2026-11-06 V3 Async Luna v9 r3：最新709次／32因子快捷评估

按用户要求，用 `scripts/evaluate_snapshot.py 23979d14af` 评估当前已提交截面；
2026-11-06 12:20:05（北京时间）固定前 709 次尝试、32 个入库因子，保存为
`futures_kernel_5m_full-all-fold2-v3_async-42-23979d14af-prefix709`。
沿用原数据、fold 和模型配置，Train+Val 拟合后评估 Test。

| 模型 | Test 加权 Pearson | Test 加权 Spearman | Test 等权 Pearson | Test 等权 Spearman |
| --- | ---: | ---: | ---: | ---: |
| Linear | 0.012457 | 0.015354 | 0.010322 | 0.015521 |
| LightGBM | 0.011352 | 0.014747 | 0.013985 | 0.017382 |

相较同运行前 500 次／26 因子，Linear／LightGBM 加权 Pearson 分别提高
0.000018／0.000576，加权 Spearman 分别提高 0.000222／0.001281。
前 500 次的 26 个完整冻结成员及顺序与当前库前 26 个成员一致。Train+Val 共
5,308,089 行，32 个因子列全部保留；两模型均预测全部 2,558,829 条 Test 可评估行，
覆盖率 100%（500 次为 2,558,823／2,558,829，99.999766%）。

公共配置、冻结库 SHA、因子身份及模型 fit/test 规格校验通过，四项指标均为有限值。
只评估组合模型预测，未计算单因子 OOS 或配对相关性，未写入实验总表；原搜索不接收
Test 反馈。Test 已被观察，本结果属于历史留出诊断。

[709次模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r3_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-23979d14af-prefix709/model/report.md)、
[500次模型报告](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r3_fold2_20261105/workspace/artifacts/runs/futures_kernel_5m_full-all-fold2-v3_async-42-23979d14af-prefix500/model/report.md)、
[评估日志](../../artifacts/comparisons/v3async9_luna_avail_nodepth_p8_r3_fold2_20261105/latest-models-20261106.log)。


## 2026-11-06 V3 Async Luna v9 r3：按用户要求停止

按用户要求，通过现有 `stop_run` 入口对 `23979d14af` 的运行锁持有进程发送 SIGINT；
进程已退出，确认没有运行锁持有者，运行状态为 `interrupted`。最终保留 752 次已提交
尝试、34 个入库因子；原子 trial 数与检查点 completed 一致。原始产物、可恢复检查点
及既有 500／709 次模型评估保留。本次为用户主动停止，不自动续跑，未新增评估或写入总表。


## 2026-11-06 Haiku 5.5：五方法 B26 初始库增量实验启动

按用户指定，仅运行 AlphaPROBE、AlphaSchema、MCTS-LLM、ReAct、AlphaAtlas V3，
不追加有限文法基线或 AlphaAtlas 其他版本。五个独立进程并行启动，各 300 次新增尝试，
合计上限 1,500；Fold2、seed 42、26 项初始 B26、八字段＋26 个发布特征保持不变。

四个对照方法沿用 B26 v5；V3 沿用本日 B26 Luna V3 的完整配置与保存源码。
各方法仅修改 `chat_model`、`chat_base_url`、`chat_key_env`，统一使用
`claude-haiku-5-5` 和既有本机 Claude CLI 桥接。AlphaPROBE 的 Qwen3 embedding 不变。
V3 保留既有残差反馈、8 并行计划／8 路评估／3 Explorer、greedy 选题、每 Region
5 项入库及 1 个并发计划上限；其他方法不新增残差配置。数据源、字段、算子身份、
初始因子身份、准入与模型配置已逐项核对。

| 方法 | Run ID |
| --- | --- |
| AlphaPROBE | `futures_kernel_5m_full-all-fold2-alphaprobe-42-a99c2b066d` |
| AlphaSchema | `futures_kernel_5m_full-all-fold2-alphaschema-42-8cc5576eb7` |
| MCTS-LLM | `futures_kernel_5m_full-all-fold2-mcts_llm-42-64f65a23b9` |
| ReAct | `futures_kernel_5m_full-all-fold2-react-42-db9096e73d` |
| AlphaAtlas V3 | `futures_kernel_5m_full-all-fold2-v3_async-42-61c42b9f7c` |

启动时五个 `run.json` 均为 `running`，模型均为 `claude-haiku-5-5`，日志确认只加载
截至 2023-12-31 的搜索数据。四方法和 V3 分为两个 scope，以保留各自配置；五组全部
冻结后拟合两个 scope 的所有模型，再统一进入历史 Test。保留 200／300 前缀，
Linear OLS、LightGBM 50 轮／leaf2000／depth5、共同训练与预测行及 20 交易日块
2,000 次配对重采样均不变；不计算并集、不筛选准入成员、不回传 Test。

启动前 `pwsh -File scripts/check.ps1` 通过：1341 项测试，154.02 秒；版本加载、
方法配置与 B26 身份校验通过。真实服务健康检查与实验启动独立于自动化测试。
源码副本只作本轮执行来源，复用原有来源指纹、原子 trial、恢复与冻结校验。
本条记录启动事实，增量结果尚未产生；2024–2025 仍属于已观察的历史诊断留出集。

[配置与入口](../../configs/experiments/zk26_seeded_fold2_haiku55_all_300_20261106/README.md)、
[总控日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/run.log)、
[检查日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/check.log)。


### Haiku B26 本轮 ReAct 初始化兼容修复与恢复

ReAct `db9096e73d` 首次完成 26 项初始库测量后，在构造系统提示词时因
`KeyError: initial_library.members` 停止；当时已提交候选 0、Chat 请求 0。
原因是 ReAct 的提示词字段说明未覆盖公共 context 的 `initial_library.members` 和
`initial_library.semantics`。已补齐成员数说明及初始库规则段落，加入使用真实平台初始库、
合成行情及模拟模型的回归测试，验证首次候选可提交、初始因子重复正确拒绝且不额外扣预算。

修复同步保存在本轮独立 `source/react/` 副本，原 `source/methods/` 与 `source/v3/`
保留。源代码变化由本记录及保存副本说明，恢复沿用原配置与冻结校验；版本、模型和研究参数没有改动。
2026-11-06 重新执行 `pwsh -File scripts/check.ps1`：1342 项全部通过，144.83 秒。
ReAct 沿原 Run ID 恢复，保留已测量的 26 项初始库和 300 次新增预算。

其他四个搜索持续运行，已收到真实 Haiku 回复。恢复脚本负责在 ReAct 与其他四组都冻结后，
先拟合两个 scope 的全部模型，再评估历史 Test；原总控保存首个 ReAct 子进程的失败退出码，
因此不执行配对评估。任一运行失败或被用户停止时，恢复脚本不会继续进入 Test。

[修复后检查](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/check-react-fix.log)、
[ReAct 日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/react.log)、
[恢复与收尾日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/recovery.log)。


ReAct 完整 B26 上下文补充验证：首次恢复仍在模型调用前因
`prepared_fields.fields.zk_mom1.description` 缺少说明停止。最终修复同时覆盖初始库和
全部发布特征的 description、calculation_frequency、publication、calculation_expression，
保留公共准备语义，说明准备公式不开放额外原始字段，方法窗口仍按基础频率计数。
新增完整 34 字段／26 项初始库的离线提示词回归，验证全部 26 个发布字段元数据、
信息边界与不扣预算；两项定向回归通过。最终完整检查 1343 项通过，141.67 秒。

最终修复另存于本轮 `source/react/`，前一修复副本保留为 `source/react-initial-fix/`。
两次初始化兼容失败都发生在首个 Chat 请求和候选提交之前；再次恢复沿用原运行。
[最终检查日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/check-react-b26-final.log)。


## 2026-11-06 AlphaAtlas V3 Luna：B26 初始库增量补充

补齐实验总览的 B26 初始库比较。运行
`futures_kernel_5m_full-all-fold2-v3_async-42-8142c2c849` 完成 300 次并冻结，
新增 16 项、最终 42 项成员；同一条轨迹的 200 次前缀新增 7 项、共 33 项。
300 次 Linear 的加权 Pearson 增量为 +0.008258，95% 区间完全为正；
LightGBM 增量接近零，区间含零。全部新增成员参与对应节点的组合，没有按 Test 筛选。

版本为 `rq8_zk26_seeded_fold2_v3async_luna_20261106`。数据、Fold2、seed 42、
34 字段、初始 B26、准入规则与下游模型沿用原表 v5。V3 的完整方法参数复制 Luna v9
`af75f77310`：8 个并行计划、8 路评估、3 个 Explorer，每 Region 最多 5 项入库、同时
1 个计划，`selection_policy=greedy`（无深挖）。搜索保留 B26 的 lag1 诊断并开启
`library_residual=residual_ic_v1`。执行源码保存于本轮产物目录的 `source/`。
V3 的残差反馈、提示词和并发配置与旧方法不同，本轮属于固定数据与候选预算下的系统比较。

初始 26 项重新在 Train/Val 测量，不占新增预算；其身份、方向、数据快照及 Train/Val
报告均与 V1 的 `5f77947f20` 一致。数值核对采用相对容差 1e-12、绝对容差 1e-15；
仅运行特有的评估引用、ID 和耗时不同。新增准入均经过包含 B26 的公共查重。

六个配对模型全部保存后才评估历史 Test。Linear OLS、LightGBM 50 轮、leaf2000/depth5
及预处理与原表相同；B26 新拟合的完整状态与原 v5 完全一致。共同 Train+Val 为
5,296,605 行，Test 为 2,549,205／2,558,829 行，覆盖率 99.6239%。逐行 row_id、
日期、品种、标签及两种 B26 预测与原 v5 完全一致。IC 使用相同 80 个品种、2,401,248 行，
WMSE 使用 86 个品种、2,429,676 行。B26 的 Linear／LightGBM IC 仍为
0.01270556／0.01064594；33／42 列全部保留，Linear 均满列秩，fit 在 Test 期间逐字节未变。

| 节点 | 新增 / 总成员 / 入模列数 | 模型 | 扩充加权 Pearson IC | ΔIC [95% 区间] | WMSE 下降 |
| --- | ---: | --- | ---: | --- | ---: |
| 200 | 7 / 33 / 33 | Linear | +0.016271 | +0.003566 [−0.002951, +0.012324] | −0.002027% |
| 200 | 7 / 33 / 33 | LightGBM | +0.008428 | −0.002218 [−0.011971, +0.011070] | −0.023961% |
| 300 | 16 / 42 / 42 | Linear | +0.020964 | +0.008258 [+0.001682, +0.018117] | +0.008025% |
| 300 | 16 / 42 / 42 | LightGBM | +0.010659 | +0.000013 [−0.011466, +0.014836] | −0.021602% |

300 次 Linear 的 2024／2025 年 ΔIC 分别为 +0.003614／+0.011103；LightGBM 分别为
−0.002028／+0.005908。300 次 WMSE 下降区间为 Linear
[−0.030869%, +0.034929%]、LightGBM [−0.070433%, +0.048304%]，均含零。
全部区间使用固定预测、按年分层、全市场同步的 20 交易日块、2,000 次重采样，全部重复有效。
主节点保持 300；200 次是同一轨迹的前缀。2024–2025 已查看，结论属于历史诊断留出评估；
区间不包含搜索随机性，不作多重比较后的方法优胜声明。

搜索含准备共 51.21 分钟，其中初始库测量 257.40 秒；Chat 请求 541 次，输入 token
23,632,768，输出 token 250,722，unknown usage 为 0。提交结果为：准入 16、质量拒绝
251、数值重复 13、定义重复 1、覆盖不足 10、常量 3、Train 指标未定义 3、编译拒绝 3。
模型成本与并发设置单列，墙钟耗时受共享主机负载影响。

本轮结果与文档补齐后执行 `pwsh -File scripts/check.ps1`：Ruff、格式、算子目录及
1,343 项离线测试全部通过，pytest 128.71 秒。测试使用合成数据和模拟服务；允许本地
socket 后，观测台 HTTP 与 asyncio 测试正常通过。真实搜索和历史数据评价独立执行。

[配置及复跑命令](../../configs/experiments/zk26_seeded_fold2_v3_luna_300_20261106/README.md)、
[四组配对结果](zookeeper26_fold2/v3_luna_20261106/results.jsonl)、
[42 项冻结定义与来源](zookeeper26_fold2/v3_luna_20261106/factors.jsonl)、
[搜索与成本](zookeeper26_fold2/v3_luna_20261106/search_runs.jsonl)、
[样本、模型与初始化核验](zookeeper26_fold2/v3_luna_20261106/evaluation_audit.json)。
同目录保存 fit、test、版本、scope 和 plan；逐行预测留在 Git 排除的
`data/comparisons/zk26_seeded_fold2_v3_luna_300_20261106/predictions.parq`。

恢复后核对（北京时间 2026-11-06 12:59:07）：五组均为 `running`，均收到 Haiku 回复并已提交候选；alphaprobe 60/300、alphaschema 30/300、mcts_llm 22/300、react 2/300、v3_async 126/300。ReAct 的短暂模型服务错误经原有有界重试恢复，已产生正式原子 trial；26 项初始成员及完整配置保持一致。后续进度以运行产物为准。

## 2026-11-06 Haiku 5.5 B26：AlphaPROBE 与 V3 提前配对评估

按用户后续指示，先评估已经完成 300 次并冻结的 AlphaPROBE（`a99c2b066d`）和
AlphaAtlas V3（`61c42b9f7c`）。AlphaPROBE 使用单方法 scope `alphaprobe_ready.json`
显式引用原运行，V3 复用原 `v3.json`。两个 scope 的全部配对模型先并行拟合，均在
15:23:20（北京时间）前保存，再并行评估历史 Test；15:26:12 全部完成。
原五方法批次继续保留，其他方法的搜索不接收本次 Test 结果。

Fold2、seed 42、初始 B26、200／300 次前缀、全部新增准入成员和模型配置均沿用原实验。
Linear OLS；LightGBM 50 轮、leaf2000/depth5；不计算并集、不按 Test 筛选成员。
两个 scope 使用相同的 5,296,605 行 Train+Val 和 2,549,205 行共同 Test，
Test eligible 共 2,558,829 行、覆盖率 99.6239%。IC 使用 80 个品种、2,401,248 行；
WMSE 使用 86 个品种、2,429,676 行。两个节点全部列均保留，Linear 均满列秩。

与原表配对 B26 核对：逐行 row_id、日期、品种和标签完全一致；预处理与 LightGBM
模型、预测完全一致。Linear 仅有浮点求解差异，系数最大绝对差 1.90e-19，
Test 预测最大绝对差 1.47e-18。配对基线 IC 仍为 Linear 0.01270556、
LightGBM 0.01064594。早期 `model_overview/fit.json` 属于普通单库拟合；本轮数值核对
采用原表 `single_methods_v5` 及 `v3_luna_20261106` 的配对基线。

| 方法 / 节点 | 新增 / 总成员 / 入模列数 | Linear ΔIC [95% 区间] | LightGBM ΔIC [95% 区间] | Linear WMSE 下降 | LightGBM WMSE 下降 |
| --- | ---: | --- | --- | ---: | ---: |
| AlphaPROBE / 200 | 5 / 31 / 31 | +0.003715 [−0.002029, +0.011490] | +0.006018 [−0.000218, +0.014806] | +0.004399% | +0.012792% |
| AlphaPROBE / 300 | 6 / 32 / 32 | +0.004741 [−0.000859, +0.012629] | +0.004747 [−0.001829, +0.013994] | −0.006472% | +0.008585% |
| AlphaAtlas V3 / 200 | 13 / 39 / 39 | +0.000563 [−0.009620, +0.010620] | +0.002703 [−0.012072, +0.020014] | −0.048722% | −0.033679% |
| AlphaAtlas V3 / 300 | 20 / 46 / 46 | +0.007474 [−0.003441, +0.017650] | +0.004601 [−0.008339, +0.019551] | −0.038815% | −0.043389% |

300 次 AlphaPROBE 的 WMSE 下降区间为 Linear [−0.026465%, +0.022650%]、
LightGBM [−0.017994%, +0.043458%]；V3 为 Linear [−0.058655%, +0.057157%]、
LightGBM [−0.081171%, +0.102244%]。两个节点、两种模型的 ΔIC 和 WMSE 下降区间
均含零，尚无稳定改善的证据。区间采用按年分层、全市场同步的 20 交易日块、
2,000 次重采样，全部重复有效。2024–2025 属于已查看的历史诊断留出集；区间不包含
搜索随机性，不能据此作多重比较后的方法优胜声明。

本次核对时，AlphaSchema 为 273/300、MCTS-LLM 为 229/300，仍在搜索；ReAct 完成
3 次评估后因 `AgentScope ReAct made no evaluation request` 失败，其恢复总控也已退出。
上述未完成方法未进入本次配对评估。

结果回填后执行 `pwsh -File scripts/check.ps1`：Ruff、格式、算子目录检查及
1,343 项合成数据／模拟服务测试全部通过，pytest 179.96 秒。
表格数值另与两个 `test.json` 逐项核对，配置、两个节点、列数及区间均一致。
[检查日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/check-ready-evaluation.log)。

[配置与复跑入口](../../configs/experiments/zk26_seeded_fold2_haiku55_all_300_20261106/README.md)、
[执行日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/ready-evaluation.log)、
[AlphaPROBE 配对结果](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/alphaprobe/results.jsonl)、
[V3 配对结果](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/v3/results.jsonl)。
两个结果目录同时保留 `fit.json`、`test.json` 和 `factors.jsonl`；逐行预测保存在
Git 排除的 `data/comparisons/` 对应 scope 目录。

## 2026-11-06 Haiku B26：变换后标签 WMSE 重算

用户确认需要评价变换后的标签。本次复用 AlphaPROBE 和 AlphaAtlas V3 已保存的
200／300 次预测，只重算 WMSE；原始标签 IC、模型、预测、因子和历史误差结果均保留。

从原 MarketData 快照与 B26 定义重建共同 Train+Val 样本，得到 5,296,605 行、79 个品种。
核对原预处理和样本数一致，重新计算的标签分位边界与模型保存值完全相同：
`[-0.019304276275104854, 0.019190049003133047]`。对每个品种计算训练期裁剪后标签均值。
Test 标签先用上述边界裁剪，再减这个训练均值；不使用 Test 分位点或均值。
`product_offsets` 仍只作为预测的特征偏移使用，没有拿它代替标签均值。

主结果保持原 86 个正权重品种与 2,429,676 行评分样本。12 个品种没有训练期样本：
`AD`、`BZ`、`LG`、`L_F`、`OP`、`PD`、`PL`、`PP_F`、`PR`、`PS`、`PT`、`V_F`。
本次诊断对它们明确采用零中心，只做冻结边界裁剪；这不是从 Test 估计的均值。
另外报告原 IC 的固定 80 品种、2,401,248 行对照，其中 7 个品种也使用零中心。
完整预测集合仍为 2,549,205 行，评分只使用原来有正权重的品种。

公式为 `1 − WMSE(B26＋新增, 变换后标签) / WMSE(B26, 同一变换后标签)`。
品种 √成交额权重沿用原结果，两个模型与基线使用同一标签变换。86 品种的 B26
变换后 WMSE 为 Linear `2.6654542226772497e-05`、LightGBM `2.662291388275881e-05`。
区间继续使用按年分层、全市场同步的 20 交易日块和 2,000 次重采样；16 组均有
2,000 个有效重复。所有数值均为百分比，方括号为 95% 区间。

| 方法 / 节点 | Linear：86 品种 | LightGBM：86 品种 | Linear：原 IC 80 品种 | LightGBM：原 IC 80 品种 |
| --- | --- | --- | --- | --- |
| AlphaPROBE / 200 | +0.012878% [−0.007629%, +0.040082%] | +0.029449% [+0.003366%, +0.063537%] | +0.016950% [−0.006646%, +0.041143%] | +0.032988% [+0.005230%, +0.065122%] |
| AlphaPROBE / 300 | −0.011305% [−0.036378%, +0.019000%] | +0.018235% [−0.011208%, +0.050809%] | −0.005226% [−0.032283%, +0.021838%] | +0.020426% [−0.009940%, +0.052927%] |
| AlphaAtlas V3 / 200 | −0.027373% [−0.058145%, +0.040152%] | −0.022325% [−0.089019%, +0.075176%] | −0.005589% [−0.052479%, +0.042681%] | −0.010696% [−0.086776%, +0.079406%] |
| AlphaAtlas V3 / 300 | −0.013456% [−0.051998%, +0.054828%] | −0.022602% [−0.073499%, +0.077867%] | +0.014464% [−0.039397%, +0.060610%] | +0.005390% [−0.062932%, +0.085192%] |

300 次主节点的四个全品种区间均含零。200 次 AlphaPROBE＋LightGBM 的变换后误差下降
区间为正，但这是查看原始 Test 后追加的标签口径；200 次仍是补充节点，不据此选择
预算、调整模型或宣称方法稳定优胜。原始标签与变换后标签的 WMSE 不混作同一指标。

核验：用合成输入检查先裁剪再减训练均值、未见品种零中心，以及预测不变；
再用 NumPy 按逐行 `品种权重 / 品种样本数` 独立重算，16 个误差比值均与公共评估函数一致。
同时核对模型与 Test 规格相同，变换前后的预测列和 row_id 均一致。

本次执行 `pwsh -File scripts/check.ps1`：Ruff、格式、算子目录检查及
1,343 项合成数据／模拟服务测试全部通过，pytest 119.61 秒。
[检查日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/check-transformed-wmse.log)。

[复跑脚本](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/recompute_transformed_wmse.py)、
[训练期标签统计](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/transformed_target.json)、
[全部变换后误差及区间](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/transformed_wmse.json)、
[执行日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/transformed-wmse.log)。


## 2026-11-06 B26 Luna：LightGBM 叶样本数与默认 L2 对照

按用户要求重训 B26 Luna 表中六个冻结组合及配对 B26。第一轮仅将
`min_data_in_leaf=2000` 改为 200，固定 `lambda_l2=1`；用户随后要求采用较好的
叶样本数组，将 L2 改回 LightGBM 默认值。本机 LightGBM 4.7.0 的
`LGBMRegressor.reg_lambda` 默认值核对为 0.0，第二轮固定 leaf200、改用 L2=0。
50 轮、学习率 0.05、15 叶、深度 5、seed 42、4 线程及预处理保持不变。
项目全局模型配置保持原值，两轮分别使用独立实验版本和输出目录。

第一轮结束前说明的选择口径为六个 300 次组合的平均加权 Pearson IC，固定原 B26
共同可评分的 80 个品种，不按单个方法最高值或相对不同 B26 的 ΔIC 选择。
200 次前缀全部保留，不依据 Test 选择预算或因子。两个 AlphaSchema 是独立搜索，
六行平均仅作本轮参数选择，不将它们视为独立统计重复。

| 参数组 | 六行平均组合 IC | 相对上一组变化 | 同库 IC 上升 | 同库 WMSE 改善 |
| --- | ---: | ---: | ---: | ---: |
| leaf2000 / L2=1 | 0.014889916 | — | — | — |
| leaf200 / L2=1 | 0.015362915 | +0.000472998 | 5/6 | 4/6 |
| leaf200 / L2=0 | 0.015590073 | +0.000227159 | 5/6 | 2/6 |

两轮各个库的参数 ΔIC 95% 区间均含零。第一轮 V3 的 IC 提升最多，AlphaProbe 回落；
第二轮提升主要来自 V3 和 AlphaSchema（`fbac3f9a28`），V1 略降，V3 的 WMSE 同时变差。
因此 leaf200/L2=0 的平均 IC 点估计最高，但尚无全面或稳定改善证据。
最终六行 300 次 LightGBM 相对配对 B26 的 ΔIC 仅 MCTS-LLM 区间完全高于 0；
六行 WMSE 点估计均未优于 B26，区间都含零。Linear 拟合和逐行预测与原表精确一致。

每轮三个 scope 的全部 30 个配对模型在 Test 前保存，并记录文件摘要；读取 Test 后
逐字节未变。独立 B26 普通拟合只提供配置来源，表内基准使用各 incremental scope 的
配对 B26。各组的配对 B26 拟合和预测完全一致，因子身份、准入顺序、方向、预处理、
Train+Val 行数和 Test 的 row_id／日期／品种／标签均与来源一致。Train+Val 为
5,296,605 行，Test 共同样本为 2,549,205 / 2,558,829 行，覆盖率 99.623890%；
IC 使用 80 品种、2,401,248 行，WMSE 使用 86 品种、2,429,676 行。
固定预测使用按年分层、全市场同步的 20 交易日块，2000 次重采样全部有效。

两轮均复用原冻结搜索，没有新增搜索或模型服务请求，结果不回传方法。第二轮叶样本数
由第一轮已查看的历史 Test 点估计选定，因此两轮均为历史留出敏感性分析，不能作为
新的盲测。V3 的残差反馈、提示词和并发差异沿用原实验的比较边界。

已回填实验总览的 B26 Luna 表，保留 leaf2000 和第一轮 leaf200/L2=1 产物。
第一轮单独报告包含 Linear／LightGBM、200／300 次的全部 24 行指标和区间。
本轮执行 `pwsh -File scripts/check.ps1`：Ruff、格式、算子目录检查及 1343 项
合成数据／模拟服务测试全部通过，pytest 135.37 秒；另核对参数、冻结模型、逐行预测
及表格数值。测试需要的本地 loopback socket 已获执行权限。

[第一轮完整结果](zookeeper26_fold2/luna_lgbm_sensitivity_20261106/leaf200_l2_1/report.md)、
[两轮参数对照](zookeeper26_fold2/luna_lgbm_sensitivity_20261106/report.md)、
[第一轮配置与复跑](../../configs/experiments/zk26_luna_leaf200_20261106/README.md)、
[第二轮配置与复跑](../../configs/experiments/zk26_luna_l2zero_20261106/README.md)、
[检查日志](../../artifacts/comparisons/zk26_luna_l2zero_20261106/check.log)。
两轮的 scope、plan、版本、模型、因子、Test 结果和核验存于报告同目录的对应参数子目录；
逐行预测保留于 Git 排除的 `data/comparisons/zk26_luna_*_20261106_*/predictions.parq`。

## 2026-11-06 Haiku B26：AlphaSchema 配对评估与变换后 WMSE

AlphaSchema（`8cc5576eb7`）完成 300 次并冻结后，按用户要求补充评估。
单方法 scope `alphaschema_ready.json` 显式复用原运行、原版本与原配对计划；不新增搜索。
200／300 次前缀分别新增 6／9 项，总库 32／35 项，全部列保留，Linear 满列秩。
六个配对模型于 16:03:55（北京时间）前保存，随后读取历史 Test，于 16:06:37 完成。
Linear OLS；LightGBM 50 轮、leaf2000/depth5、L2=1；其余配置保持原值。

配对 B26 的完整拟合状态和模型配置与 AlphaPROBE 完全相同；共同 Train+Val 为
5,296,605 行，Test 为 2,549,205／2,558,829 行，覆盖率 99.623890%。逐行 row_id、
交易日、品种、原始标签和两种 B26 预测完全一致。IC 仍使用原始标签，固定 80 个品种、
2,401,248 行；WMSE 为原 86 个正权重品种、2,429,676 行。各节点品种权重也一致。

| 节点 | 模型 | 扩充加权 Pearson IC | ΔIC [95% 区间] | 原始标签 WMSE 下降 [95% 区间] |
| --- | --- | ---: | --- | --- |
| 200 | Linear | +0.015596 | +0.002890 [−0.003311, +0.007445] | +0.022289% [−0.006102%, +0.024864%] |
| 200 | LightGBM | +0.015208 | +0.004562 [−0.001399, +0.009623] | +0.009949% [−0.015845%, +0.028786%] |
| 300 | Linear | +0.017080 | +0.004374 [−0.002031, +0.009303] | +0.026488% [−0.001943%, +0.031655%] |
| 300 | LightGBM | +0.015670 | +0.005024 [−0.000412, +0.010235] | +0.008404% [−0.013799%, +0.028515%] |

变换后 WMSE 复用前两组已经恢复的 `transformed_target.json`：以原 Train+Val 的
1%／99% 边界裁剪 Test 标签，再减去各品种训练期裁剪后标签均值。12 个训练期未见
品种保持同一零中心规则；不使用 Test 统计，不改预测。下面均为相对配对 B26 的
WMSE 下降百分比；正数表示误差更低，方括号为 95% 区间。

| 节点 | Linear：86 品种 | LightGBM：86 品种 | Linear：原 IC 80 品种 | LightGBM：原 IC 80 品种 |
| --- | --- | --- | --- | --- |
| 200 | +0.019479% [−0.008484%, +0.023393%] | +0.015822% [−0.012348%, +0.036715%] | +0.012598% [−0.005740%, +0.019866%] | +0.014280% [−0.011840%, +0.037333%] |
| 300 | +0.025227% [−0.004569%, +0.031184%] | +0.016515% [−0.007938%, +0.035621%] | +0.019832% [−0.000758%, +0.029294%] | +0.015695% [−0.007604%, +0.036811%] |

两个节点的 ΔIC、原始及变换后 WMSE 下降区间均含零，尚无稳定改善证据。
区间继续使用按年分层、全市场同步的 20 交易日块、2,000 次重采样，所有重复有效。
AlphaSchema 的 8 项变换后结果追加到已有结果文件，前两组的 16 项保留；三组共 24 项
均用 NumPy 按逐行 `品种权重 / 品种样本数` 独立重算，与公共评估函数一致。
标签统计仍为原 79 个训练品种，不重新估计；预测列和 row_id 在变换前后不变。

300 次继续作为主节点。历史 Test 已查看，标签变换后的 WMSE 是补充诊断，不用于
选择因子、预算或参数；本次结果不回传仍在运行的方法。

回填后执行 `pwsh -File scripts/check.ps1`：Ruff、格式、算子目录检查及
1,349 项合成数据／模拟服务测试全部通过，pytest 131.00 秒。另核对 Haiku 表三行的
300 次 IC、区间、变换后 WMSE 和列数，与保存结果一致；本次文档链接和锚点均有效。
[检查日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/check-alphaschema-evaluation.log)。

[配置与复跑](../../configs/experiments/zk26_seeded_fold2_haiku55_all_300_20261106/README.md)、
[阶段日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/alphaschema-evaluation.log)、
[原始配对结果](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/alphaschema/results.jsonl)、
[变换后误差及区间](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/transformed_wmse.json)、
[变换后评分日志](../../artifacts/comparisons/zk26_seeded_fold2_haiku55_all_300_20261106/alphaschema-transformed-wmse.log)。


## 2026-11-06 Haiku B26：默认 leaf20／L2=0 结果核对

按用户最终更正，Haiku 下游 LightGBM 使用默认叶节点最少样本数 20、L2=0。
本机 LightGBM 4.7.0 的原生模型文本确认 `min_data_in_leaf: 20`、`lambda_l2: 0`；
配置省略 `min_data_in_leaf`，实际保存的每个 LightGBM 模型均核对为 20。
其余仍为 50 轮、学习率 0.05、15 叶、深度 5、seed 42、4 线程及原预处理。
此前误按 200 新启动的三组在保存 fit/Test 前中断；日志与配置留在该次产物目录。

复用工作区统一默认参数重训产物，核对 AlphaPROBE（`a99c2b066d`）、
AlphaSchema（`8cc5576eb7`）和 V3（`61c42b9f7c`），并补齐刚完成冻结的
MCTS-LLM（`64f65a23b9`）。全部使用原搜索的 200／300 次前缀和全部准入因子。
MCTS 分别新增 3／5 项，29／31 列全部保留、Linear 满列秩；其余三组因子与列数不变。
ReAct 在 3 次后失败，没有符合本表预算的冻结库。

各组拟合保存并校验后才读取历史 Test。共同 Train+Val 为 5,296,605 行，Test 为
2,549,205／2,558,829 行；IC 固定 80 品种、2,401,248 行，WMSE 固定 86 个正权重
品种、2,429,676 行。配对 B26 LightGBM 模型与预测完全一致；Linear 和旧参数结果
在相对 1e-12、绝对 1e-15 容差内一致。新 B26 的 Linear／LightGBM IC 为
0.012705561／0.011675215，变换后 WMSE 为 2.6654542226772497e-05／
2.6622061337248276e-05。

下表为 300 次主节点的 LightGBM 结果，增量相对新参数下的配对 B26。

| 方法 | 组合 IC | ΔIC [95% 区间] | 变换后 WMSE 下降 [95% 区间] |
| --- | ---: | --- | --- |
| AlphaPROBE | +0.015216 | +0.003541 [−0.002803, +0.012391] | +0.016936% [−0.015499%, +0.052332%] |
| AlphaSchema | +0.014416 | +0.002740 [−0.002869, +0.008341] | +0.007224% [−0.021734%, +0.025757%] |
| MCTS-LLM | +0.015497 | +0.003822 [−0.001657, +0.010988] | +0.009035% [−0.018295%, +0.036159%] |
| AlphaAtlas V3 | +0.016040 | +0.004365 [−0.008760, +0.019353] | −0.022965% [−0.078095%, +0.077070%] |

MCTS 的 300 次 Linear ΔIC 为 +0.001258 [−0.005839, +0.008628]，变换后 WMSE 下降
为 −0.007583% [−0.027577%, +0.011036%]；原三组 Linear 表内数值保持不变。
四组 300 次的 ΔIC 和变换后 WMSE 下降区间均含零。200 次前缀和两个模型的完整结果
保存在统一结果文件，`cohort=Haiku` 共 16 行。

WMSE 继续使用原共同 Train+Val 的 1%／99% 标签裁剪边界，再减训练期裁剪后品种
均值；12 个未见评分品种使用零中心，不用 Test 统计。全部 16 项变换后 WMSE 均用
NumPy 按逐行 `品种权重 / 品种样本数` 独立重算，与保存值一致。区间保持按年分层、
全市场同步的 20 交易日块、2,000 次重采样，全部重复有效。历史 Test 已查看，本次
为用户指定参数的诊断重训，不反馈搜索、不重选预算或成员。

本次执行 `pwsh -File scripts/check.ps1`：Ruff、格式、算子目录检查及 1,349 项
合成数据／模拟服务测试全部通过，pytest 132.92 秒。

[配置与复跑](../../configs/experiments/zk26_lgbm_defaults_20261106/README.md)、
[全部结果](../../artifacts/comparisons/zk26_lgbm_defaults_20261106/results.jsonl)、
[训练与 Test 核验](../../artifacts/comparisons/zk26_lgbm_defaults_20261106/evaluation_audit.json)、
[训练期标签统计](../../artifacts/comparisons/zk26_lgbm_defaults_20261106/transformed_target.json)、
[检查日志](../../artifacts/comparisons/zk26_lgbm_defaults_20261106/check-haiku-table-update.log)。


## 2026-11-06 B26 Luna / Haiku：默认叶样本数与基准统一重训

按用户指定，B26 Luna、Haiku 两张表统一省略 `min_data_in_leaf`，使用锁定
LightGBM 4.7.0 的默认值 20；`lambda_l2=0`。其余参数保持 50 轮、学习率 0.05、
15 叶、最大深度 5、seed 42、4 线程及原预处理。公共模型校验只增加允许省略
`min_data_in_leaf`，不向配置补值；显式值仍须为正整数，原有参数校验保留。
实际保存模型的原生参数均确认是 leaf20／L2=0。

实验版本为 `rq8_zk26_lgbm_defaults_20261106`。复用原冻结来源：Luna 六行与 Haiku
四行；Haiku MCTS（`64f65a23b9`）在本轮期间完成原有 300 次搜索，新增 5 项、总计
31 项，冻结后补入相同参数的评估。ReAct（`db9096e73d`）停在失败的 3/300，
没有伪造对应 300 次结果。重训流程不新增搜索或模型服务请求。

首批五个 scope 的 46 个配对模型全部保存并核验后才评估 Test；随后完成 Haiku MCTS
scope 的 6 个模型，再读取该组 Test。共 52 个配对模型，40 行模型／200 或 300 次
节点结果。每组保留原冻结因子、方向、顺序和全部准入成员，不按 Test 筛选。
独立 B26 普通拟合提供配置和因子顺序；下表及所有增量使用同样本重拟合的配对 B26。

| B26 模型 | 加权 Pearson IC | 原始目标 WMSE | 变换后目标 WMSE |
| --- | ---: | ---: | ---: |
| Linear | 0.012705561 | 4.0499655076e-05 | 2.6654542227e-05 |
| LightGBM | 0.011675215 | 4.0473190501e-05 | 2.6622061337e-05 |

WMSE 是绝对误差值，扩充库的下降百分比则使用同参数 B26 作分母。Luna 表继续采用
原始目标 WMSE；Haiku 表继续采用变换后目标 WMSE，两种误差的绝对值与区间全部保留。
标签变换复用已核验的共同 Train+Val 裁剪边界和 79 个训练品种的裁剪后标签均值，
12 个训练期未见评分品种采用零中心，只裁剪；不使用 Test 的分位点或均值。
IC 始终以原始目标评分。

Luna 六个 300 次组合的平均 LightGBM IC 从 leaf200/L2=0 的 0.015590073 变为
默认 leaf20/L2=0 的 0.015842580。3/6 行 IC 上升，3/6 行原始目标 WMSE 改善；
各库参数 ΔIC 的 95% 区间均包含零，不能据此认定稳定改善。Haiku MCTS 的 300 次
LightGBM IC 为 0.015497033，ΔIC 为 +0.003822，变换后 WMSE 下降 +0.009035%；
区间均含零，完整数值见报告。

各组 Train+Val 为 5,296,605 行，Test 为 2,549,205 / 2,558,829 行，覆盖率
99.623890%。IC 固定为 80 品种、2,401,248 行，WMSE 为 86 品种、2,429,676 行。
各组配对 B26 LightGBM 模型与逐行预测一致；已有旧评估的行，其 Linear 和预处理
按 rtol=1e-12、atol=1e-15 核对。所有 fit 在 Test 前后逐字节未变，逐行身份、日期、
品种、标签和权重保持一致。区间使用按年分层、全市场同步的 20 交易日块、2000 次
重采样，全部重复有效。

执行 `pwsh -File scripts/check.ps1`：Ruff、格式、算子目录及 1349 项离线测试通过，
pytest 143.00 秒。新增覆盖省略叶样本数与显式默认 20 的拟合一致性，以及无效显式值。
全部 40 行 IC、区间、原始 WMSE 与列数逐项核对保存结果；对新增 Haiku MCTS 另外用
NumPy 按逐行「品种权重 / 品种行数」独立重算两种 WMSE，绝对误差差异至多
1.44e-18，下降率差异至多 6.76e-14，均在约定数值容差内。表格链接及归档副本核验通过。

本轮是用户指定参数的历史 Test 敏感性分析；此前已查看 2024–2025，不作为新的盲测，
也不向仍在运行的方法反馈。旧参数结果与原搜索产物保留；执行源采用原 V3 Luna 保存源
的独立副本，只同步允许省略叶样本数的公共校验改动。

[默认参数完整报告](zookeeper26_fold2/lgbm_defaults_20261106/report.md)、
[B26 绝对基准](zookeeper26_fold2/lgbm_defaults_20261106/summary.json)、
[全部 40 行结果及区间](zookeeper26_fold2/lgbm_defaults_20261106/results.jsonl)、
[模型与数值核验](zookeeper26_fold2/lgbm_defaults_20261106/evaluation_audit.json)、
[配置及复跑](../../configs/experiments/zk26_lgbm_defaults_20261106/README.md)、
[检查日志](../../artifacts/comparisons/zk26_lgbm_defaults_20261106/check.log)。


## 2026-11-06 Haiku V3：固定 Val 末品种重算 WMSE

按用户指定，以 Fold2 Val 截止日 2023-12-31 前最后交易日 2023-12-29 的 MarketData
观测固定 79 个品种；不按 Test 值或截止日成交额筛选。名单先保存，再读取已存 Test 预测。
Test 中 74 个品种有正权重，WMSE 为 2,348,430 行；73 个品种可计算配对 IC，
为 2,326,761 行。所有评分品种均有原 Train+Val 标签中心，排除的 12 个有权重品种
均不在 Val 末名单，包含此前误差诊断中的 PT、PD。

复用 Haiku V3 `61c42b9f7c` 的 200／300 次冻结预测，Linear OLS，LightGBM
原生 leaf20／L2=0；不重训模型。B26 与扩充库在相同保留样本上重算，继续使用原 Test
品种 √成交额权重并重新归一化。标签沿用训练期 1%／99% 裁剪及品种均值，不拟合 Test 统计。

300 次时，Linear 变换后 WMSE 下降从 −0.013456% 变为 +0.019690%，增加
0.033146 个百分点；LightGBM 从 −0.022965% 变为 +0.011283%，增加 0.034248
个百分点。对应 ΔIC 为 +0.007513／+0.005212。WMSE 与 ΔIC 的 95% 区间均跨零；
LightGBM 分年误差增益为 2024 年 −0.011283%、2025 年 +0.039054%，不支持稳定改善结论。

区间沿用按年分层、全市场同步 20 交易日块、2,000 次重采样，全部重复有效。
四行结果的两种 WMSE 以 NumPy 逐行权重独立复核，最大绝对差 6.78e-21；原 86 品种
结果复现，原 fit.json／test.json 字节不变。总览在 Haiku 表下新增独立口径对照，原表保留。
`pwsh -File scripts/check.ps1` 的 Ruff、格式、算子目录和 1,349 项离线测试通过，
pytest 用时 132.94 秒。
本轮是在已查看 Test 后提出的评分范围敏感性分析，不作为新的盲测或模型重训收益。

[完整结果与复跑](zookeeper26_fold2/haiku_v3_val_end_20261106/report.md)、
[Val 末品种名单](../../artifacts/comparisons/zk26_haiku_v3_val_end_products_20261106/cohort.json)、
[数值与配置核验](../../artifacts/comparisons/zk26_haiku_v3_val_end_products_20261106/summary.json)。


## 2026-11-06 B26 Luna / Haiku：全部方法统一固定 Val 末品种

按用户要求，将上一轮 Haiku V3 的 Val 末品种规则应用于 B26 两张表全部已完成方法。
Luna 六个、Haiku 四个独立运行，共 40 行结果（200／300 次节点 × Linear／LightGBM）
重新评分。ReAct 仍未完成 300 次冻结，未补造结果。复用同一份 2023-12-29 的 79 品种名单，
各来源搜索的数据快照一致；Test 中 74 品种、2,348,430 行参与 WMSE，73 品种、
2,326,761 行参与 IC。各方法使用相同样本和原 Test √成交额权重，在保留品种内归一化。

52 个配对模型沿用原保存版本，实际 LightGBM leaf20／L2=0，其他配置及 5,296,605 行
共同 Train+Val 不变。本次仅重算评分范围，原 fit.json／test.json 字节不变，未重新搜索、
重训或调用模型服务。Luna 总表继续报告原始目标 WMSE，Haiku 继续报告变换后目标 WMSE；
完整产物同时保留两种误差及区间。变换后标签沿用训练期裁剪和品种中心，所有评分品种均有中心。

新口径的配对 B26 Linear／LightGBM IC 为 0.013115483／0.011616349，变换后 WMSE
为 2.5432135635e-05／2.5398461117e-05。Haiku 300 次的变换后 WMSE 下降如下：

| 方法 | Linear | LightGBM |
| --- | ---: | ---: |
| AlphaProbe | −0.005760% | +0.019564% |
| AlphaSchema | +0.016298% | +0.004283% |
| MCTS-LLM | −0.005445% | +0.012947% |
| AlphaAtlas V3 | +0.019690% | +0.011283% |

四种方法 LightGBM 的 WMSE 点估计均为正，区间均跨零；不能据此判定稳定优胜。
Luna 六组平均 LightGBM IC 为 0.015761575。两张总表、配对基准、样本说明与结果链接
均替换为新口径；此前 V3 单独对照移入历史报告，不再与统一表重复展示。

40 行两种 WMSE 均以 NumPy 逐行「品种权重 / 品种行数」独立核验，最大绝对差
1.36e-20。原预测行身份、标签、权重与配对 B26 预测逐组核对；全部区间沿用按年分层、
全市场同步 20 交易日块、2,000 次重采样，全部重复有效。上一轮 Haiku V3 四行结果和
区间全部复现。本轮仍为已查看历史 Test 后指定评分范围的敏感性分析，原 86 品种结果保留。
执行 `pwsh -File scripts/check.ps1`：Ruff、格式、算子目录及 1,354 项离线测试通过，
pytest 用时 141.00 秒，见[检查日志](../../artifacts/comparisons/zk26_val_end_products_20261106/check.log)。

[统一结果与复跑](zookeeper26_fold2/val_end_products_20261106/report.md)、
[40 行完整结果](zookeeper26_fold2/val_end_products_20261106/results.jsonl)、
[名单与基准](zookeeper26_fold2/val_end_products_20261106/summary.json)、
[逐组核验](zookeeper26_fold2/val_end_products_20261106/evaluation_audit.json)。


## 2026-11-06 B26 Luna / Haiku：73 品种加权 Pearson／Spearman ΔIC 与区间

按用户确认，B26 Luna、Haiku 两张表的 Pearson IC 与 Spearman IC 统一采用当前
Val 末固定名单中的 73 个可评分品种、2,326,761 行；两种增量都减去相同品种范围和
模型配置下的配对 B26。保留原 WMSE 下降列及全部数值：WMSE 仍使用 74 个正权重
品种，包含原始目标恒定、无法计算 IC 的 WH。Luna 继续报告原始目标 WMSE，
Haiku 继续报告变换后目标 WMSE。

沿用原 300 次冻结因子、Linear OLS、LightGBM 原生 leaf20／L2=0 以及固定预测。
Luna 六行、Haiku 四行的两个预测模型共 20 行全部补齐 ΔSpearman IC 的 95% 区间，
原 ΔIC 列明确标为 ΔPearson IC；B26 基准表同时列出两种绝对 IC。
B26 Linear 的 Pearson／Spearman IC 为 0.013115483／0.007821336，
LightGBM 为 0.011616349／0.006326984。

公共 `paired_block_bootstrap` 新增显式 `correlation="spearman"` 选项；原默认
Pearson 行为保留。两种区间使用同一 seed 20261102、按年分层的全市场同步
20 交易日块、2,000 次配对重采样。Spearman 每次在品种内对抽中的全期样本重新排名，
并列值取平均秩；通过观测被抽中的次数累计秩，数值等价于展开重复行后重新排名。
每次计算配对 IC 差，再按固定原 Test √总成交额权重聚合；样本不足或相关性未定义
的品种从该次两组同时排除。未复用原样本的固定秩或 Pearson 区间。

20 行原 Pearson 点估计与区间全部复现；新增 Spearman 区间均有 2,000 次有效重复。
40 行 200／300 次节点结果补入 Spearman 点估计，新增区间仅覆盖 300 次主节点。
总览与详细报告的表格逐项核对结果文件，原 WMSE 字段与表内数值保持不变。
六个来源 scope 的 fit 和 test 产物保持原字节；本次没有模型重训、重新搜索或模型服务调用。

此前聊天的 80 品种 IC 也已完成相同重采样并单独归档到原默认参数报告；当前总览只
使用用户确认的 73 品种口径。两套基准和区间分别保存，不混用。区间描述冻结模型在
已查看历史 Test 上的时间不确定性，不包含独立搜索 seed 的随机性。

合成测试独立展开抽样原始行并重新排名，核对有／无并列值时的区间，并覆盖品种缺席、
重采样后常量、相同预测的零增量。`pwsh -File scripts/check.ps1` 的 Ruff、格式、
算子目录和 1,354 项离线测试全部通过，pytest 134.75 秒。

[73 品种完整结果](zookeeper26_fold2/val_end_products_20261106/report.md)、
[73 品种重采样结果](zookeeper26_fold2/val_end_products_20261106/spearman_intervals.json)、
[原 80 品种结果](zookeeper26_fold2/lgbm_defaults_20261106/report.md)、
[复算入口](../../configs/experiments/zk26_lgbm_defaults_20261106/spearman_intervals.py)、
[检查日志](../../artifacts/comparisons/zk26_val_end_products_20261106/check-spearman.log)。

## 2026-11-06 B26 AlphaAtlas V3：Haiku 与 Luna 第二次运行启动

用户要求两组同时各增加一次独立运行，2026-11-06 17:20（Asia/Shanghai）已启动。
均从初始 B26 开始，Fold2、seed 42、300 次新增尝试，保留 200 次前缀；沿用各自
首轮的搜索版本、保存的 V3 源码、提示词和并发设置，不导入旧运行的新增成员。

- Haiku 5.5：`futures_kernel_5m_full-all-fold2-v3_async-42-27dcf84074`。
- Luna：`futures_kernel_5m_full-all-fold2-v3_async-42-5602cf6a24`。

后台入口同时执行两组搜索；两组均冻结、全部配对模型保存后才读取历史 Test。
下游沿用当前默认叶样本数 20、L2=0 的 LightGBM 与 Linear OLS，计算 73 品种
Pearson／Spearman ΔIC 和 95% 区间，保留 74 品种 WMSE。完成后各追加至原 B26 表。
这是用户指定的同 seed 历史诊断重复，结果不反馈搜索；当前尚无本轮最终结果。

启动检查：两组均完成 26 个初始成员的 Train/Val 初始化并开始候选评估。
`pwsh -File scripts/check.ps1` 在限制计算线程后通过，Ruff、格式、算子目录和
1,354 项离线测试全部通过，pytest 197.93 秒；日志为
`artifacts/comparisons/zk26_seeded_fold2_v3_repeat2_20261106/check-limited.log`。

配置、恢复命令与日志位置见
[运行说明](../../configs/experiments/zk26_seeded_fold2_v3_repeat2_20261106/README.md)。



## 2026-11-06 B26 AlphaAtlas V3：Luna 第二次运行完成

完成时间：2026-11-06T18:54:24+08:00。已评估 Luna，各 300 次，沿用 seed 42、初始 B26、原搜索配置和源码。
各组在自身搜索和配对模型冻结后读取历史 Test；Luna 按用户要求先评估，结果不回传 Haiku。
分别报告 73 品种 Pearson／Spearman ΔIC 及区间，保留 74 品种 WMSE。两种 WMSE 已用逐行权重独立复核；模型与原始 Test 产物保持不变。
属于用户指定的历史诊断重复，不据此筛选因子或调整参数。

[两组完整结果](zookeeper26_fold2/v3_repeat2_20261106/report.md)。


## 2026-11-06 B26 AlphaAtlas V3：Luna、Haiku 第二次运行完成

完成时间：2026-11-06T18:58:44+08:00。已评估 Luna、Haiku，各 300 次，沿用 seed 42、初始 B26、原搜索配置和源码。
各组在自身搜索和配对模型冻结后读取历史 Test；Luna 按用户要求先评估，结果不回传 Haiku。
分别报告 73 品种 Pearson／Spearman ΔIC 及区间，保留 74 品种 WMSE。两种 WMSE 已用逐行权重独立复核；模型与原始 Test 产物保持不变。
属于用户指定的历史诊断重复，不据此筛选因子或调整参数。

[两组完整结果](zookeeper26_fold2/v3_repeat2_20261106/report.md)。


## 2026-11-06 B26 AlphaAtlas V3：Luna 第三次运行启动

启动时间：2026-11-06T19:05:25+08:00。按用户要求，再从 B26 独立运行一次 Luna V3。
运行：`futures_kernel_5m_full-all-fold2-v3_async-42-35fc379af6`。
Fold2、seed 42、300 次尝试，保留 200 次前缀；计划／评估并发均为 8，Explorer 并发 3。
搜索版本、提示词、保存源码和准入配置与前两次相同；自身搜索冻结、模型保存后立即评估，
自动归档并向原 B26 Luna 表追加一行。下游仍为 Linear OLS 与默认叶样本数 20、L2=0 的 LightGBM，
统一 73 品种 Pearson／Spearman ΔIC 及 95% 区间，保留 74 品种 WMSE。

前两次分别新增 16、5 个成员；质量门槛拒绝 251、256 次，行为重复 13、12 次，
低覆盖 10、14 次，常量 3、8 次。保持原门槛观察重复结果，既有 Test 不反馈搜索。
同 seed 的在线模型调用及异步顺序可变；区块区间不包含搜索随机性。

[配置、执行与恢复](../../configs/experiments/zk26_seeded_fold2_v3_luna_repeat3_20261106/README.md)。

验证：新 scope 仅更换运行身份、计划引用和说明；方法配置与前两次保存值完全一致。
填表入口使用临时目录和既有结果验证，保留全部旧行，重复执行不重复追加。
`pwsh -File scripts/check.ps1` 在主机环境通过，1,354 项测试，149.28 秒；
日志为 `artifacts/comparisons/zk26_seeded_fold2_v3_luna_repeat3_20261106/check-host.log`。
此前沙箱内验证已中断，以这次完整检查为准。


## 2026-11-06 B26 AlphaAtlas V3：Luna 第三次运行完成

完成时间：2026-11-06T20:16:05+08:00。运行 `futures_kernel_5m_full-all-fold2-v3_async-42-35fc379af6`，300 次尝试，新增 17 个因子。
沿用原搜索配置、B26 和 seed 42；自身搜索与配对模型冻结后评估并追加原 Luna 表。
73 品种加权 Pearson／Spearman ΔIC、95% 区间；保留 74 品种 WMSE。
两种 WMSE 已独立数值复核。属于历史诊断重复，不据此选择因子或调整参数。

[完整结果](zookeeper26_fold2/v3_luna_repeat3_20261106/report.md)。

## 2026-11-07 V3 取消回看跨度分档

用户决定 V3 不再区分 intraday／1d_to_20d／over_20d（prompts 工作区，未提交）。

- **证据**：fold2 全部 V1／V3 运行中，回看超过 5 日的尝试占 43%，入库率 7.0%，入库因子 Test IC 0.0039、Test/Val 中位数 0.43；回看不到 1 日的分别为 30%、3.9%、0.0059、0.82。三档各有试探预算，加上"未测档算新方向"，V3 Luna 无深挖／并行组 16–29% 的尝试回看超过 20 日，V1 Luna 只有 3%。用户提出的"统一建议回看大于 5 日"与此相反，暂不加入，可另设对照组。
- **改动**：`scale_bands` 默认改为 `"none"`。Region 用全部尝试判定 probing／seabed／saturated／land（复用 `band_status`），`probe_attempts`=20 按 Region 计。Explorer 提示词删去 by_scale 段、"只由已测档推导 Region"和"未测档算新方向"两句。深挖规则改为"活动 Region 仍为 probing 或 land 时继续，关闭后转 breadth"。观测台对新运行显示 `v3_region` 规则，不显示分档表。
- **旧运行**：记录了 `intraday_1d_20d` 的运行（v9、r2、r3 等）恢复时沿用分档和原提示词 `BAND_PROMPT`。前一条"回看跨度"措辞修改已在 `BAND_PROMPT` 中撤回，恢复为这些运行启动时的原文，因此 r3 如果续跑，提示词与 v9、r2 一致。

## 2026-11-07 B26 AlphaAtlas V3 不分档：Luna 两组启动

用户要求参考 `experiment_overview.md` 的 B26 Luna 表，再跑两组 V3。用当前 V3 不分档版本：新搜索版本 `rq8_zk26_seeded_fold2_v3async_luna_noband_20261107`，与前三次 V3（8142c2c849／5602cf6a24／35fc379af6）只差 `scale_bands = "none"`。搜索源码是 prompts 工作区快照 `artifacts/comparisons/zk26_seeded_fold2_v3_luna_noband_20261107_source/src`。Fold2、seed 42、300 次，保留 200 次前缀。下游配对评估、评分口径和自动填表复用第三次运行的流程。

- 第一组：`configs/experiments/zk26_seeded_fold2_v3_luna_noband_r1_20261107/`，运行 `futures_kernel_5m_full-all-fold2-v3_async-42-60f742d873`。
- 第二组：`configs/experiments/zk26_seeded_fold2_v3_luna_noband_r2_20261107/`，运行 `futures_kernel_5m_full-all-fold2-v3_async-42-34227b615a`。
- 两组均不设代理启动。完成后各自向 B26 Luna 表追加"AlphaAtlas V3 不分档"和"AlphaAtlas V3 不分档重复"两行。
- 不分档第二组（34227b615a）在第 126 次失败：Luna 网关返回带 error 字段的响应，错误码不在可重试列表里，平台按"chat model returned an error response"停止。为了不泄露内容，报错有意不带服务原文，因此不知道具体错误码。随后用最简请求探测网关，返回 200，判断为一次性错误。代码未改（曾尝试在报错中附带错误码，但违反现有脱敏测试，已撤回），用同一命令续跑同一 Run ID，旧日志另存为 `luna-search.log.fail126`。


## 2026-11-07 B26 AlphaAtlas V3 不分档：Luna 第二次运行完成

完成时间：2026-11-07T11:39:56+08:00。运行 `futures_kernel_5m_full-all-fold2-v3_async-42-34227b615a`，300 次尝试，新增 7 个因子。
取消回看跨度分档，其余沿用原搜索配置、B26 和 seed 42；自身搜索与配对模型冻结后评估并追加原 Luna 表。
73 品种加权 Pearson／Spearman ΔIC、95% 区间；保留 74 品种 WMSE。
两种 WMSE 已独立数值复核。属于历史诊断重复，不据此选择因子或调整参数。

[完整结果](zookeeper26_fold2/v3_luna_noband_r2_20261107/report.md)。


## 2026-11-07 B26 AlphaAtlas V3 不分档：Luna 第一次运行完成

完成时间：2026-11-07T11:46:38+08:00。运行 `futures_kernel_5m_full-all-fold2-v3_async-42-60f742d873`，300 次尝试，新增 6 个因子。
取消回看跨度分档，其余沿用原搜索配置、B26 和 seed 42；自身搜索与配对模型冻结后评估并追加原 Luna 表。
73 品种加权 Pearson／Spearman ΔIC、95% 区间；保留 74 品种 WMSE。
两种 WMSE 已独立数值复核。属于历史诊断重复，不据此选择因子或调整参数。

[完整结果](zookeeper26_fold2/v3_luna_noband_r1_20261107/report.md)。

## 2026-11-07 B26 Luna：V3 不分档两组与分档三组对比

两组不分档均完成 300 次并自动评估、填表：60f742d873 新增 6 个，34227b615a 新增 7 个。总表中两行已按第一次、第二次排序（第二组先完成，自动填表时追加在前）。相对 B26 的 ΔIC（300 次）均值：

| 组 | 新增因子均值 | Linear ΔPearson | Linear ΔSpearman | LightGBM ΔPearson | LightGBM ΔSpearman |
| --- | ---: | ---: | ---: | ---: | ---: |
| V3 分档 3 组（8142c2c849／5602cf6a24／35fc379af6） | 12.7 | +0.003974 | +0.011153 | +0.002252 | +0.013335 |
| V3 不分档 2 组（60f742d873／34227b615a） | 6.5 | +0.001332 | +0.007363 | +0.005370 | +0.010236 |

尝试的回看构成（<1 日／1–5 日／>5 日）：分档三组为 0.57–0.64／0.24–0.31／0.05–0.18；不分档两组为 0.72／0.24／0.03 和 0.80／0.14／0.06。不分档两组的 13 个新增因子全部回看不到 1 日；分档三组的新增中，1 日以上占 18/38。

描述性判断：取消分档后，预算明显转向短回看，新增因子减半。LightGBM ΔPearson 均值升高、方差缩小（+0.0049／+0.0059，分档组为 −0.0016 到 +0.0081）；Linear ΔPearson 与两项 Spearman 均值下降。每组只有 2–3 次运行，单次区间大多跨 0，不足以判断优劣。


## 2026-11-07 B26 AlphaAtlas V3 不分档：Luna 第三次运行完成

完成时间：2026-11-07T15:04:08+08:00。运行 `futures_kernel_5m_full-all-fold2-v3_async-42-c8c6a17d46`，300 次尝试，新增 4 个因子。
取消回看跨度分档，其余沿用原搜索配置、B26 和 seed 42；自身搜索与配对模型冻结后评估并追加原 Luna 表。
73 品种加权 Pearson／Spearman ΔIC、95% 区间；保留 74 品种 WMSE。
两种 WMSE 已独立数值复核。属于历史诊断重复，不据此选择因子或调整参数。

[完整结果](zookeeper26_fold2/v3_luna_noband_r3_20261107/report.md)。

## 2026-11-07 B26 Luna：V3 不分档与分档各三组对比

按用户要求补跑不分档第三组 `c8c6a17d46`（配置同前两组，300 次）：新增 4 个，Linear ΔPearson +0.004204、
ΔSpearman +0.011601，LightGBM ΔPearson +0.009347、ΔSpearman +0.015689，原始目标 WMSE 下降
+0.008033%／+0.036632%。三组对三组的均值（括号为组间标准差）：

| 组 | 新增 | Linear ΔPearson | Linear ΔSpearman | LightGBM ΔPearson | LightGBM ΔSpearman | Linear WMSE 下降 | LightGBM WMSE 下降 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 分档 3 组 | 12.7 (6.7) | +0.003974 (0.0036) | +0.011153 (0.0025) | +0.002252 (0.0052) | +0.013335 (0.0038) | −0.0065% | −0.0025% |
| 不分档 3 组 | 5.7 (1.5) | +0.002289 (0.0027) | +0.008776 (0.0040) | +0.006696 (0.0023) | +0.012053 (0.0039) | −0.0016% | +0.0155% |

第三组尝试回看 <1d／1–5d／>5d 为 0.60／0.15／0.25，但入库 4 个仍全部 <1d；不分档三组共 17 个新增
因子全部回看不足 1 天。不分档在 LightGBM ΔPearson 与两项 WMSE 上均值更好、组间更稳；Linear ΔIC 与
两项 Spearman 均值略低，差距均小于组间标准差。每组 3 次，属于描述性对比，不足以判定优劣。

## 2026-11-09 fold2 规模曲线：后期入库因子诊断（诊断性，已看 Test）

按每 100 次尝试截取 fold2 1000 次运行的库前缀，用主表统一模型参数（50 轮、15 叶、`min_data_in_leaf=2000`、
深度 5、`lambda_l2=1`）在 Train+Val 重拟合后评估 Test；23/24 个已完成运行的 1000 次端点与主表一致，
Haiku V1 `531d4676f6` 的 LightGBM 为 0.010923，主表 0.012112（该行原用参数不同）。脚本与结果在会话临时目录，未入库。

Luna V3 五组的 Linear 在 12–16 个因子（200–400 次）达到峰值，三组随后下降（`bf0c21a38d` 14.8→7.3、
`22d7cdbc4f` 15.5→10.0、`af75f77310` 16.4→13.3，×1e-3），`8d95766cb7` 持平，`ea03683b78` 上升。
下降对应两类后期入库：

- 冗余簇：`bf0c21a38d` 第 15–23 个因子单因子 Test IC 多为 +5–10e-3，但与已有成员最大 |Spearman| 为 0.64–0.70，
  紧贴 0.7 去重门槛；单因子有效，组合 Linear 仍从 14.8 降至 9.9，属于共线簇在 Train+Val 拟合的权重到 Test 不稳。
- 新方向的 Val 假阳性：`22d7cdbc4f`、`af75f77310` 后期多个与库低相关（≤0.05）、回看 20–125 日的因子，
  Val IC 5–8e-3，Test 接近 0 或为负。

全部方法按入库时点分组（≤500 次 vs >500 次）：16 个方法×模型组中 13 组后期因子与已有库的最大相关上升，
10 组 Test/Val 比下降。Luna V3 前后期 Test/Val 0.77→0.62，负 Test 占比 8%→16%，最大相关 0.39→0.50，
回看 >5 日 40%→45%；Luna V1 后期反而更好（Test/Val 0.76、最大相关 0.38、无 >5 日因子），曲线持续上升。
Luna AlphaPROBE、MCTS-LLM 500 次后仅新增 2、4 个因子，曲线走平源于不再入库。
结论限于描述：准入只看 Val 单因子门槛与 0.7 相关门槛，后期入库的边际组合价值可为负。

## 2026-11-09 V3 v10：库残差准入门槛，Luna 两组启动

按用户要求，先将 `feature/prompt-cleanup` 当前版本提交为 `ce1ad8c`，再加入平台准入规则 `min_residual_ic`
（须开启 `library_residual`）：候选库残差 IC 须严格大于该值，否则以 `residual_threshold` 拒绝，残差未测得
同样拒绝；未设置时准入不变。v10 版本 `rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_residual_v10`（及 `_r2`）
只在 v9 基础上设 `min_residual_ic=0`，仍为分档提示词，使对照只差这一条准入规则。
按既有 Val 记录回放，五组 Luna V3 的入库因子中 19%（全部 V3 为 95/499）库残差 IC ≤ 0。
动机来自已看 Test 的规模曲线诊断，属于看过 Test 后提出的改动；两组结果只能作为该规则的探索性对照，
基线方法仍为原准入规则。目录 `artifacts/comparisons/v3async10_luna_residual_gate{,_r2}_fold2_20261109/`。

按用户要求补第三组 `rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_residual_v10_r3`（配置同 v10），
目录 `artifacts/comparisons/v3async10_luna_residual_gate_r3_fold2_20261109/`；三组完成后报告 500／1000 次均值。

v10 r2 在 830/1000 次时进程被直接终止（无 traceback，run.json 仍为 running，非 interrupted），
按同一命令从检查点恢复，日志另存 `run.log.killed830`，续跑从第 831 次开始。

## 2026-11-09 V3 v10（库残差准入）Luna 三组结果

三组 v10 均完成 1000 次：`1aa909d2ce`、`7a860211ae`、`63de96c0fc`，入库 31／21／25 个，
Test Linear 0.007215／0.019512／0.012414，LightGBM 0.018351／0.012723／0.020969，已写入 fold2 主表。
r1、r3 进程启动早于填表代码修正，填表在最后一步失败；修正后用同一 run.py 对冻结运行补填，工作区总览重置。
填表代码改为当前表头（单因子 Spearman 均值／中位数位于“正 IC”前，方法名 AlphaAtlas V1／V2／V3）。

按每 100 次截点统一重拟合（×1e-3，三组均值；V3 对照为 v8、v9、v9 r2 三组 `22d7cdbc4f`、`af75f77310`、`ea03683b78`）：

| 组 | 截点 | 因子 | Linear P | Linear S | LightGBM P | LightGBM S | 累计入库残差（Val） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V3 | 500 | 20.0 | 13.65 | 19.08 | 14.87 | 18.58 | 84.9 |
| V3 | 1000 | 31.3 | 12.59 | 17.69 | 13.01 | 17.48 | 105.7 |
| V3 + 残差准入 | 500 | 17.0 | 12.80 | 14.45 | 14.59 | 14.16 | 78.5 |
| V3 + 残差准入 | 1000 | 25.7 | 13.05 | 15.58 | 17.35 | 16.63 | 111.4 |

残差准入组 500→1000 次 Linear P、LightGBM P 均上升（V3 组均下降），1000 次 LightGBM P 高 4.3e-3；
两项 Spearman 低于 V3 组。组内差异大（Linear P 7.2–19.5），三组不足以下结论。基线 Luna 残差由同一评估器
事后重算，V3 运行重算值与记录值一致到 1e-16。页面 `artifacts/comparisons/scaling_curves_fold2_20261109/luna.html`。

## 2026-11-09 AlphaSchema + GPT6-luna fold2 三组重复启动

按用户要求，为与 V3 三组对比，AlphaSchema Luna 1000 次另跑三组独立重复，配置文件逐字复制自
`alphaschema_luna_fold2_20261102`（`7ce881f635`），使用当前 `feature/prompt-cleanup` 源码；
目录 `artifacts/comparisons/alphaschema_luna_fold2_repeat{1,2,3}_20261109/`。
用户改为只补两组（与原 `7ce881f635` 合计三组）：repeat3（`0776ed36e2`）在 22/1000 次时按用户要求以 SIGINT 停止，
状态 interrupted，不续跑、不计入比较；repeat1（`a6956445a7`）、repeat2（`c671886153`）继续。

AlphaSchema Luna 两组重复完成：`a6956445a7` 入库 17，Test Linear 0.011475、LightGBM 0.013346；
`c671886153` 入库 15，0.014310、0.016211；500 次截点分别为 9／8 个因子，0.009257／0.009925 与
0.010258／0.012536。主表与相似度表按方法分组归位并补 500 次行。与原 `7ce881f635` 合计三组均值：
1000 次 Linear 0.011802、LightGBM 0.014504，入库 17.3 个，Val 累计残差 0.054674（V3 两组 0.105723／0.111410）。
规模曲线图与总览“规模曲线：fold2 GPT6-luna”一节已改为 AlphaSchema 三组均值 ± 1 std。

## 2026-11-09 AlphaPROBE、MCTS-LLM + GPT6-luna fold2 各补两组重复

按用户要求补全 Luna 三组对比。配置取自 `mcts_llm_gpt6_luna_fold2_20261030` 工作区，使用当前
`feature/prompt-cleanup` 源码，启动后逐项对照原运行 run.json（除 run_id、指纹、状态外）：
MCTS-LLM `95df5036b7`、`bfc98b0bf4` 与原 `35c026f3fe` 一致。AlphaPROBE 首次启动的 `5a4af1a78d`、`77b8020d3e`
误用该工作区的 Haiku 模型配置，发现后在约 5 次时停止（interrupted，不计入）；改回与原 `3656be6b77` 相同的
Luna 模型、网关与密钥变量后重启为 `2e915baa0a`、`efb9a27cd6`，配置与原运行一致。
目录 `artifacts/comparisons/{alphaprobe,mcts_llm}_luna_fold2_repeat{1,2}_20261109/`。

AlphaPROBE、MCTS-LLM Luna 补跑完成：1000 次 AlphaPROBE `2e915baa0a` 8 个、`efb9a27cd6` 9 个；
MCTS-LLM `95df5036b7` 8 个、`bfc98b0bf4` 7 个。四组自身模型配置沿用旧工作区（LightGBM `min_data_in_leaf=100`、
无深度限制），与 fold2 主表统一参数不同；主表 500／1000 次 LightGBM 列改为统一参数重拟合值
（`2e915baa0a` 0.010905／0.013387，`efb9a27cd6` 0.017780／0.016883，`95df5036b7` 0.008721／0.010060，
`bfc98b0bf4` 0.008546／0.005873），Linear 列一致。AlphaSchema 与 v10 重复运行的自身模型配置即统一参数，结果一致。
规模曲线五个方法均为 3 组均值 ± std；截点曲线文件移至 `artifacts/comparisons/scaling_curves_fold2_20261109/curves/`。

## 2026-11-10 fold2 GPT6-luna 表格按方法合并

按用户要求，`experiment_overview.md` 的“期货因子搜索：fold2”与“期货库内因子相似度：fold2”两表中，GPT6-luna
各方法合并为一行：1000 次运行均值 ± 样本标准差（AlphaPROBE、MCTS-LLM、AlphaSchema、V3、V3 + 残差准入各 3 组，
V2 2 组，V1 1 组）。V3 取 v8／v9／v9 r2，与规模曲线一致；V3 早期 `8d95766cb7`、`bf0c21a38d` 不入均值。
原单次运行行（含 500 次截面）原样移至 `docs/records/scaling_fold2_luna_20261109/luna_runs.md`，链接已改为相对该文件。
合并脚本 `artifacts/comparisons/scaling_curves_fold2_20261109/aggregate_luna_rows.py`。其他模型尚未合并。
同日按用户要求，fold2 主表增加“Linear 加权 Spearman”“LightGBM 加权 Spearman”两列（组合预测 Test 品种 √成交额加权
Spearman IC）；GPT6-luna 五个方法按统一参数截点结果填均值 ± std，其他模型暂以“—”表示；主表 GPT6-luna 暂去掉
AlphaAtlas V1、V2 两行（单次运行仍在明细文件）。自动填表代码同步增加两列，未显示该两列的旧表（如 fold1）自动跳过。

## 2026-11-05 至 11-10 入库指标能否预测 Test 表现（诊断性，已看 Test）

问题：入库时已知的单因子指标（残差、Val IC、Train ICIR 等）能否预测因子在 Test 上的单因子 IC 和组合增量。
样本为 fold2 上 15 个开启 `residual_ic_v1` 的 V2/V3 运行（Haiku、Luna、DeepSeek；截面只取每个主运行库最大的一版），
共 573 个入库因子（单因子部分去重后 589 个）。组合增量用 Linear（主表规格，Train+Val 拟合），按入库顺序逐个加入
（顺序增量）和留一两种口径；全库复算 Test IC 与 `model/test.json` 一致。统计为控制入库位置的偏秩相关，区间按运行聚类
bootstrap。全部结果用了 Test 标签，不作为准入或奖励的设计依据。脚本在会话临时目录，未入库。

- **残差 vs 单因子 Test IC**：秩相关 −0.06，控制 Val IC 和 library_r2 后 −0.09；残差五分位 Test IC>0 比例从 93%
  降到 81%。残差高的因子在 Val 上的“独特信息”更多是噪声或时期特有成分。
- **残差 vs 组合增量**：顺序 +0.089 [+0.01, +0.17]，留一 +0.083 [−0.00, +0.18]；效应集中在最高一档，区分度不足以
  逐个挑因子。Val IC 对增量基本无预测力。
- **Train ICIR**：带符号的 ICIR 无预测力。|ICIR| 在日内因子上有 +0.13–0.17，主要来自 ICIR < 0 的一侧，但这是从约
  17 个指标 × 多种口径中事后挑出的结果。对 ChaoticTrader 因子库（998 个因子，2024–2025 检验）复现后，|ICIR| 只预测
  单因子 Test IC（+0.28），不预测组合增量（+0.02 / +0.06）。
- **ICIR 的机械效应**：按“交易日 × 品种”切块的 ICIR 只反映整期 IC 的日内成分。一天之内，“当前价格相对当天均价”与未来
  收益天然负相关（随机游走 −0.54，真实期货 −0.48），因子日内 IC ≈ 0.0045 − 0.45 ×（因子与当天价格位置的日内相关），
  R² 0.97。跟随价格的因子 ICIR 被压负，反转类被抬正；负 ICIR 的“预测力”是日间成分强的代理，控制日间成分后消失。
  分解与模拟见 ChaoticTrader_Future `docs/factor_stability_screen_review_zh.md`。
- **日频因子 ICIR 缺失**：日内常数的 `@1d` 因子日 IC 为 1e-19 量级浮点残差，通过有限值检查（`icir_days` 仍为 730）
  但标准差 < 1e-12，ICIR 为 null（268 个日频常数因子中 212 个）。混合因子中日内恰为常数的“品种 × 日”也以约 0 的 IC
  进入当日平均。两周切块可覆盖日频因子（与按日口径在日内因子上秩相关 0.90），但对增量同样无可靠预测力，块内去均值还
  会把日频因子的块 IC 放大约 3 倍。未改代码。
- **30m / 60m 逐年稳定性**（ChaoticTrader 库）：主要是开发期 IC 大小的代理，控制 IC 后对 Test 的偏相关 +0.02，对增量
  在 ±0.06 内；按此剔除 176 个因子的回测与随机剔除相同数量没有区别。

结论：现有单因子入库指标都不能可靠预测组合增量；残差最多有 +0.08 量级的弱信号，不应解读为“更可能外推”。日 ICIR 不宜当
稳定性指标使用，如需日内稳定性诊断，应先扣除与当天价格位置相关的部分，或改为整段中心化后按块分解（需新增诊断版本，
待定）。要把任何指标用进准入或奖励，须在开发期内切分（如 Val 2022 / 2023）重新验证。

## 2026-11-10 V3 v10 主源码接入与冻结模型复核

将 V3 Async、并发执行及 `min_residual_ic=0` 准入接入主源码，保留三组 v10 原版本配置。
三组 `1aa909d2ce`、`7a860211ae`、`63de96c0fc` 的配置指纹、冻结库哈希、全部因子和算子
身份及方法检查点通过核对；本机 MarketData 快照与运行记录一致。

在临时副本中复用各组已保存的 Linear 和 LightGBM 拟合模型，重新计算 Test 评分，不重新
搜索、拟合或请求 LLM。三组两种模型的加权／等权 Pearson 和 Spearman 均与原报告一致，
最大绝对差为 `1.05e-17`；有效行、预测覆盖和各指标样本数一致。该复核不重算单因子 OOS，
也不能保证在线 LLM 重新搜索得到相同候选；日志为 `logs/checks/v3-replay-20261110.log`。
