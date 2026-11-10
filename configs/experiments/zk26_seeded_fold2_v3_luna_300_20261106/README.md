# AlphaAtlas V3：B26 初始库增量补充

补充 [B26 实验总表](../../../experiment_overview.md#b26-初始库增量补充2026-11-03)。
[scope.json](scope.json) 固定 Fold2、seed 42、300 次新候选提交，以及同条轨迹的
200／300 次评估节点；不计算并集，不按 Test 筛选成员。

本轮运行 `8142c2c849` 已完成 300 次并冻结，200／300 次分别新增 7／16 项。
结果、样本核验和成本见
[实验记录](../../../docs/records/experiments.md#2026-11-06-alphaatlas-v3-lunab26-初始库增量补充)，
机器可读产物保存于
[v3_luna_20261106](../../../docs/records/zookeeper26_fold2/v3_luna_20261106/)。

数据、34 个字段、B26 初始定义和准入规则沿用
`rq8_zk26_seeded_fold2_overview_luna_v5`。V3 开启 `library_residual=residual_ic_v1`，
完整方法参数复制 Luna v9 的 `af75f77310`：8 个并行计划、8 路评估、3 个 Explorer，
每 Region 最多 5 项入库、同时 1 个计划，`selection_policy=greedy`（无深挖）。
原 B26 的 `icir_turnover_lag1_v2` 诊断保持开放。初始化的 26 项不计为新发现或新提交。

搜索调用当前 V3 实现；执行源码从 `AlphaAtlas-prompts` 复制到本轮产物目录的
`source/`，避免其他运行的后续代码编辑影响本轮。主仓库的旧方法实现不变。
版本记录配置来源，运行沿用平台现有的来源指纹、配置与冻结校验。

复跑入口（从仓库根目录执行，需要本轮保存的 V3 执行源码）：

```bash
PYTHONPATH=artifacts/comparisons/zk26_seeded_fold2_v3_luna_300_20261106/source/src \
  uv run --env-file .env atlas experiment \
  configs/experiments/zk26_seeded_fold2_v3_luna_300_20261106/scope.json \
  --output artifacts/comparisons/zk26_seeded_fold2_v3_luna_300_20261106 \
  --search-only
```

同一命令去掉 `--search-only` 完成配对拟合，再增加 `--test` 评估历史 Test。
重复命令复用原运行、已提交 trial、方法状态与已完成模型；新重复实验须另建 scope。

配对规则见 [plan.json](plan.json)。Linear OLS 与 LightGBM
50 轮、leaf2000/depth5 均与原表 v5 相同。固定 B26 可用的共同训练行和 Test 行，
分别拟合 B26、B26＋200 次新增、B26＋300 次新增；全部模型保存后才读取 Test。
95% 区间沿用按年分层、全市场同步的 20 交易日块、2,000 次重采样。

2024–2025 已查看，结果属于历史诊断留出评估。V3 的残差反馈、提示词和并发配置
与旧方法不同；相同候选预算不表示相同模型成本，也不能将差异只归因于搜索机制。
