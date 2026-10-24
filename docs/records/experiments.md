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
