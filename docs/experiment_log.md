# 实验记录

这份记录面向多人协作，登记可复核的实验结论。每次正式实验一行；未冻结、未完成或只
有运行日志的实验不填写结果。原始行情、运行目录、逐次 trial、checkpoint 和 log 保留在
本地 `data/`、`artifacts/`、`logs/`，不提交到 Git。共享文档只保留复现实验所需的配置摘要
与汇总指标。

## 口径

- 记录人填写实际运行人；历史记录无法确认时写“未记录”，不要根据 Git 作者推断。
- 方法名和模型名按实际运行配置填写。无 LLM 的基线模型填“—”。
- Train、val、test 区间和 fold 按冻结运行记录填写。TEST 只在运行冻结后评估，不用于调参、
  选因子或改方向。
- 期货主指标为按品种计算、按区间 √成交额加权的 Pearson IC；同时记录品种等权 Pearson、
  加权 Spearman 等补充指标。A 股按对应资产配置的主指标记录。
- 本页的 train/val 因子均值是该冻结因子库成员的平均 IC；train 按 train 方向定向，val 沿用
  train 方向。TEST 因子统计也沿用冻结方向。不同因子库的共同有效样本可能不同，均值只作
  描述，不代表显著性检验。
- 下游模型的 TEST IC 单独记录。模型在 train+val 拟合，TEST 不参与拟合或模型选择；它和
  单因子 IC 是不同实验阶段的指标。
- 缺值用 `—` 表示“未记录/不适用”，不能写成 0。每行应包含可追溯的 `run_id` 或结果文档。

## 已完成实验

### 运行信息

“结果登记日”是对照结果汇总日期，不一定等于搜索实际结束日期。AlphaPROBE 的聊天模型和
embedding 模型分开记；MCTS-LLM 未使用 embedding 模型。

| 结果登记日 | 实验人 | 资产 | Universe | 方法 | 聊天模型 / embedding 模型 | Fold | Train 区间 | Val 区间 | Test 区间 | Seed | 尝试预算 | 入库因子 | Run ID |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | --- |
| 2026-09-18 | 未记录 | futures | all | AlphaPROBE | gpt-5.5 / Qwen3-Embedding-0.6B | fold1 | 2016-01-01–2018-12-31 | 2019-01-01–2020-12-31 | 2021-01-01–2022-12-31 | 42 | 500 | 37 | `futures-all-fold1-alphaprobe-42-1e4331978d` |
| 2026-09-18 | 未记录 | futures | all | MCTS-LLM | gpt-5.5 / — | fold1 | 2016-01-01–2018-12-31 | 2019-01-01–2020-12-31 | 2021-01-01–2022-12-31 | 42 | 500 | 44 | `futures-all-fold1-mcts_llm-42-1ec3984a4b` |

### Train / val 因子指标

以下为冻结因子库中已入库成员的 IC 算术均值，每个因子等权，不是把全体样本合并后重新计算
的 pooled IC。train IC 乘以该因子的训练方向；val 指标沿用同一训练方向。指标为品种 Pearson
后按区间 √成交额加权汇总。

| Run ID | 方法 | Fold | Seed | 因子数 | Train 加权 Pearson IC | Val 加权 Pearson IC | Train 加权 Spearman IC | Val 加权 Spearman IC |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `futures-all-fold1-alphaprobe-42-1e4331978d` | AlphaPROBE | fold1 | 42 | 37 | 0.008818 | 0.008132 | 0.005275 | 0.006559 |
| `futures-all-fold1-mcts_llm-42-1ec3984a4b` | MCTS-LLM | fold1 | 42 | 44 | 0.008190 | 0.008206 | 0.000865 | 0.003055 |

### TEST 单因子指标

下面统计各因子在冻结方向下的独立 TEST IC。报告没有对它们作 TEST 选因子或重定方向。

| Run ID | 方法 | Fold | Seed | 因子数 | 加权 Pearson IC 均值 | 加权 Pearson IC 中位数 | 加权 Spearman IC 均值 | Pearson IC > 0 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `futures-all-fold1-alphaprobe-42-1e4331978d` | AlphaPROBE | fold1 | 42 | 37 | 0.002393 | 0.001559 | 0.003421 | 27 / 37（73.0%） |
| `futures-all-fold1-mcts_llm-42-1ec3984a4b` | MCTS-LLM | fold1 | 42 | 44 | 0.002785 | 0.003165 | 0.002229 | 32 / 44（72.7%） |

### 下游模型 TEST 指标

这是独立的因子库模型实验：模型和预处理在 train+val 拟合，TEST 仅作冻结后的最终评估。两组
实验分别使用其完整冻结因子库，不是相同特征集合上的比较。表内 IC 分别为 √成交额加权与品种
等权汇总；覆盖率是模型 TEST 预测覆盖率。

| Run ID | 搜索方法 | Fold | Seed | 下游模型 | 输入因子数 | Train+val 行数 | TEST 覆盖率 | 加权 Pearson IC | 等权 Pearson IC | 加权 Spearman IC | 等权 Spearman IC |
| --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `futures-all-fold1-alphaprobe-42-1e4331978d` | AlphaPROBE | fold1 | 42 | Linear | 37 | 4,361,033 | 99.28% | 0.006413 | 0.031862 | 0.011148 | 0.022558 |
| `futures-all-fold1-alphaprobe-42-1e4331978d` | AlphaPROBE | fold1 | 42 | LightGBM（50 轮） | 37 | 4,361,033 | 99.28% | 0.011228 | 0.032380 | 0.014437 | 0.024152 |
| `futures-all-fold1-mcts_llm-42-1ec3984a4b` | MCTS-LLM | fold1 | 42 | Linear | 44 | 4,374,809 | 99.56% | 0.008493 | 0.016936 | 0.007333 | 0.010124 |
| `futures-all-fold1-mcts_llm-42-1ec3984a4b` | MCTS-LLM | fold1 | 42 | LightGBM（50 轮） | 44 | 4,374,809 | 99.56% | 0.017879 | 0.023062 | 0.010101 | 0.016928 |

### 共同设置与比较限制

- 数据：RiceQuant 原生 futures 5m；目标为未来 12 Bar 对数收益；使用 close、open、high、low、
  volume、amount、open_interest 七个字段；公共 `futures_cta` 参考库开放。
- 两个搜索均为 fold1、seed 42、500 次尝试，使用同一数据快照
  `e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。
- 模型比较使用冻结因子库。Linear 为带截距 OLS；LightGBM 固定 50 轮。未按 TEST 选因子、
  调参或翻转预测方向。
- 两组模型 TEST 覆盖率为 99.28% 和 99.56%，有效行与特征集合略有差别，因此不是严格共同
  样本比较。单 seed、单 fold 的结果不代表统计显著性或方法的普遍优劣。
- 汇总来源为 2026-09-18 对照报告和冻结运行产物；原始产物、逐次 trial 与 log 未提交，表内
  保留了共享结论和 run_id。

## 新实验登记模板

每个新搜索运行先在“运行信息”追加一行；同一 run 的 train/val、单因子 TEST 指标分别追加到对应
结果表。该 run 的下游模型可能有多个，因此“下游模型 TEST 指标”按模型各占一行。表格间通过
`run_id` 关联。每个不同 fold 或 seed 都是独立记录，不要覆盖已有行。

## 建议填写顺序

1. 从冻结的 `run.json` 复制资产、Universe、方法、模型、seed、预算、fold 的完整日期区间、
   数据快照和完整 run_id。
2. 从冻结因子库汇总 train/val；检查 train 和 val 都使用训练阶段确定的方向。
3. 只从冻结后的 OOS 报告填写单因子 TEST；若未运行下游模型，不在下游模型表添加行。
4. 每个下游模型单独一行，并记录输入因子数、train+val 拟合行数和 TEST 覆盖率。
5. 写明覆盖率差异、数据缺口或与其他实验不完全可比之处。
6. 不粘贴 token、URL 凭据、API key、原始 log 或大型逐次结果。
