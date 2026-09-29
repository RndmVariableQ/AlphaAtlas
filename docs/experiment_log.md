# 实验记录

正式实验完成冻结和 TEST 评估后，在结果表追加一行。日志、逐次 trial、checkpoint、行情数据和
本地运行产物不提交到 Git；共享表只记录实验设置摘要和 TEST 结果。历史实验人无法确认时填
“未记录”，不根据 Git 作者推断。

## 已完成对照：期货因子搜索

2026-09-18 汇总的两组实验使用 RiceQuant 原生 futures 5m，同一数据快照，预测未来 12 Bar
对数收益，开放 `futures_cta` 参考库且不预置初始因子。共同设置为 fold1（train：2016-01-01
至 2018-12-31；
val：2019-01-01 至 2020-12-31；test：2021-01-01 至 2022-12-31）、seed 42、500 次尝试；
聊天模型均为 gpt-5.5，AlphaPROBE 另使用 Qwen3-Embedding-0.6B。两组使用同一数据快照
`e2e9e697fef4013c86e4be21a7841abc30ba8041c2b6d1087bcaa7f8d838a2e8`。

表中只列 TEST 指标。单因子 TEST IC 沿用训练阶段确定的方向；下游 Linear 和 LightGBM 使用
冻结全库，均在 train+val 拟合后评估 TEST，LightGBM 固定 50 轮。主指标为按品种计算后按
√成交额加权的 Pearson IC；单因子均值是对因子 IC 的算术平均，不是合并样本后重算的 IC。
两组下游模型 TEST 覆盖率分别为 99.28% 和 99.56%，并非严格共同样本比较。结果来自单一
fold 和 seed，不代表统计显著性或方法的普遍优劣。

| 实验人 | 方法 | 聊天模型 | Fold / seed | 入库因子 | TEST 单因子加权 Pearson（均值 / 中位数） | TEST 正 IC | TEST 单因子加权 Spearman 均值 | TEST Linear 加权 Pearson | TEST LightGBM 加权 Pearson | Run ID / 备注 |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 未记录 | AlphaPROBE | gpt-5.5 | fold1 / 42 | 37 | 0.002393 / 0.001559 | 27 / 37（73.0%） | 0.003421 | 0.006413 | 0.011228 | `futures-all-fold1-alphaprobe-42-1e4331978d` |
| 未记录 | MCTS-LLM | gpt-5.5 | fold1 / 42 | 44 | 0.002785 / 0.003165 | 32 / 44（72.7%） | 0.002229 | 0.008493 | 0.017879 | `futures-all-fold1-mcts_llm-42-1ec3984a4b` |

后续实验直接在上表追加一行；共享设置写在本节说明里，若日期区间、seed、数据快照或预算不同，
在新增行的备注中注明。未运行的下游模型填 `—`。
