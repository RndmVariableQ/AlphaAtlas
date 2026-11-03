# 各方法对 B26 的增益：统一 v5 模型配置

本比较只拟合 B26 及每次独立搜索的 B26＋新增因子。全部 scope 设置
`include_union=false`，保留 300 次主节点与同条轨迹的 200 次前缀。
模型参数统一取 `rq8_zk26_seeded_fold2_overview_luna_v5.model`：Linear OLS，
LightGBM 50 轮、`min_data_in_leaf=2000`、`max_depth=5`。

搜索定义、方向、准入顺序和原搜索版本保留；不重新请求模型服务或搜索因子。
`experiment_version` 是本次评估版本，`source_experiment_version` 是冻结搜索的来源版本。
跨版本重训先核对数据快照、字段、算子、目标、fold 和初始 B26；只换下游模型配置。
原搜索版本文件、scope、plan 和已经保存的模型均不改写。

| Scope | 冻结搜索来源 | 来源版本 |
| --- | --- | --- |
| [earlier_methods.json](earlier_methods.json) | AlphaProbe `b448e9fb48`、AlphaSchema `fbac3f9a28`、MCTS-LLM `d8d6f22a47` | v4 |
| [overview_methods.json](overview_methods.json) | AlphaAtlas `5f77947f20`、AlphaSchema `3a4f640d02` | v5 |

分成两个 scope 是为了保留原运行身份，包括两次独立的 AlphaSchema；它们使用相同的
[配对协议](plan.json)。每个 scope 的 B26 都在相同 Train+Val 行上重拟合，报告核对各组
B26 的预处理、预测和评分样本一致。先完成所有 scope 的拟合，再打开历史 Test。

在仓库根目录执行：

```bash
for group in earlier_methods overview_methods; do
  uv run atlas experiment configs/experiments/zk26_single_methods_v5_20261103/$group.json \
    --output artifacts/comparisons/zk26_single_methods_v5_20261103/$group
done
# 两组都拟合完成后，对每组重复上述命令并追加 --test。
```

每组保存 `fit.json`、`test.json`、`results.jsonl`、`factors.jsonl`。
已完成的相同输入会复用保存结果；更改输入必须另建 scope 和输出目录。
逐行预测保存在 `data/comparisons/<scope.id>/predictions.parq`，排除 Git。
汇总公开于 [single_methods_v5](../../../docs/records/zookeeper26_fold2/single_methods_v5/)。
2024–2025 已查看，评价属于历史诊断留出集；结果不反馈给搜索。
