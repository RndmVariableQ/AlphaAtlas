# AlphaAtlas 与 AlphaSchema：B26 增量实验

使用 `rq8_zk26_seeded_fold2_overview_luna_v5`。AlphaAtlas 的 CLI 方法名为 `v1_async`。
方法参数来自 [experiment_overview.md](../../../experiment_overview.md) 的 GPT6-luna 组，
来源运行和核对结果保存在版本的 `configuration_sources` 与
[配置核对](../../../docs/records/zookeeper26_fold2/overview_luna_v5/configuration_audit.json)。

本轮沿用此前指定的 Fold2、seed 42、B26 初始库及每方法 300 次预算。方法可查询初始
成员，并收到单因子与相似度反馈；搜索不接收组合增量或 Test。数据仍为八字段加上月
发布的 26 个特征，共 34 个字段。它是上述方法配置在本轮增量实验中的应用，不等于
总览八字段、空初始库、1000 次实验的逐轨迹重放。

## 搜索

在仓库根目录分别运行，可并行：

```bash
uv run --env-file .env atlas run \
  --version rq8_zk26_seeded_fold2_overview_luna_v5 \
  --method v1_async --seed 42 --attempts 300 --search-only

uv run --env-file .env atlas run \
  --version rq8_zk26_seeded_fold2_overview_luna_v5 \
  --method alphaschema --seed 42 --attempts 300 --search-only
```

AlphaAtlas 最多并行 5 个研究计划，每计划最多 5 个候选，Explorer 最多 40 轮。
AlphaSchema 的 150 个有效完整计划奖励阈值保持原值。两个周期各消耗一次候选预算，
因此本轮可能只覆盖代理启动前的阶段；不降低阈值来制造完整机制已经运行的表象。

## 冻结与配对评估

本轮实际输入见 [two_methods.json](two_methods.json)，配对规则见 [plan.json](plan.json)。
重新搜索时，复制 scope，更新 `id` 和两个 `runs`；使用新的输出目录，保留历史产物。
不要在拟合后修改 scope、plan 或版本。

两项搜索均冻结后，使用同一 scope 和运行目录先执行拟合，再显式增加 `--test`：

```bash
uv run python scripts/evaluate_incremental.py \
  --scope configs/experiments/zk26_seeded_fold2_overview_300_v3/two_methods.json \
  --runs artifacts/runs/futures_kernel_5m_full-all-fold2-v1_async-42-5f77947f20 \
         artifacts/runs/futures_kernel_5m_full-all-fold2-alphaschema-42-3a4f640d02 \
  --output artifacts/comparisons/zk26_seeded_fold2_overview_luna_300_v3
```

模型为 Linear 与 LightGBM。LightGBM 固定 50 轮、叶节点最少 2000 条、最大深度 5；
完整参数以版本为准。B26 和全部扩充组均在相同 Train+Val 行上重新拟合，不直接减去
旧模型报告的 IC。单方法及两方法并集的 200、300 节点全部报告，以 300 为主节点。

`model_overview/fit.json` 是旧冻结 B26 在总览模型配置下的独立训练产物，提供配对入口
的配置和因子顺序核验。配对模型另行统一拟合，正式增量以配对输出为准。
行情与逐行预测仍保存在被 Git 排除的 `data/` 下；公开产物只含配置、定义和汇总。
