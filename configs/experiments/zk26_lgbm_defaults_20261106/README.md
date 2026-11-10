# B26 Luna / Haiku：默认叶样本数与 L2

当前总览已按用户指定的 **Val 末固定品种** 重新评分，见
[统一重测结果](../../../docs/records/zookeeper26_fold2/val_end_products_20261106/report.md)。
该评分复用本目录的冻结模型；下文记录的 80 品种 IC／86 品种 WMSE 为原始评估产物，历史数值保留。

按用户要求统一 B26 区域两张表。`min_data_in_leaf` 从配置中省略，由锁定的
LightGBM 4.7.0 使用默认值 20；`lambda_l2=0`。其余参数为 50 轮、学习率 0.05、
15 叶、最大深度 5、seed 42、4 线程，预处理和冻结因子保持原值。

该版本仅用于下游重训，不重新搜索，也不修改原搜索版本。Luna 六个冻结组合按三个
scope 装配；Haiku 使用已冻结的 AlphaPROBE、AlphaSchema、V3，以及本轮评估期间
完成 300 次并冻结的 MCTS。失败的 ReAct 不伪造 300 次结果。

共完成 52 个配对模型、40 行模型／预算节点结果。B26 LightGBM 的加权 Pearson IC 为
0.011675215，加权 Spearman IC 为 0.006848482；原始目标 WMSE 为
4.0473190501e-05，变换后目标 WMSE 为
2.6622061337e-05。[完整结果与基准核验](../../../docs/records/zookeeper26_fold2/lgbm_defaults_20261106/report.md)。

配置以 [版本](../../versions/rq8_zk26_lgbm_defaults_20261106.json)、
[plan.json](plan.json) 和 [model.toml](model.toml) 为准。
每个 scope 显式指定来源版本和运行，关闭并集；保留同次搜索 200／300 次节点。

B26 和全部扩充组合使用同参数、同样本重新拟合。表内 B26 基准以 incremental
scope 的配对拟合为准；独立 B26 普通拟合只为现有入口提供配置和因子顺序。
报告显式给出 B26 的绝对 Pearson／Spearman IC、原始目标 WMSE、变换后目标 WMSE，
以及扩充库的增量。原 80 品种报告的两张表分别列出 Linear、LightGBM 的 ΔPearson IC、ΔSpearman IC
和各自 95% 区间，并保留 WMSE 下降列。
Luna 表继续使用原始目标误差；Haiku 表保留现有的变换后目标误差，避免混淆两种指标。

执行源为此前 V3 Luna 保存源的独立副本，仅同步公共模型校验允许省略
`min_data_in_leaf` 的改动；原保存源保持不变。执行前确认该副本存在。

从仓库根目录复跑：

```bash
export PYTHONPATH="$PWD/artifacts/comparisons/zk26_lgbm_defaults_20261106/source/src"
export POLARS_MAX_THREADS=8 OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=4
b26_config=configs/experiments/zk26_lgbm_defaults_20261106
b26_output=artifacts/comparisons/zk26_lgbm_defaults_20261106
b26_workspace="$b26_output/baseline_workspace"
mkdir -p "$b26_workspace/configs" "$b26_workspace/baseline/frozen"
cp "$b26_config/model.toml" "$b26_workspace/configs/model.toml"
cp docs/records/zookeeper26_fold2/run.json "$b26_workspace/baseline/run.json"
cp docs/records/zookeeper26_fold2/frozen/library.json "$b26_workspace/baseline/frozen/library.json"
if [ ! -e "$b26_workspace/data" ]; then
  ln -s "$PWD/data" "$b26_workspace/data"
fi
uv run atlas --root "$b26_workspace" model "$b26_workspace/baseline"
b26_groups="luna_earlier_methods luna_overview_methods luna_v3 haiku_ready_methods haiku_v3 haiku_mcts"
for b26_group in $b26_groups; do
  uv run atlas experiment "$b26_config/$b26_group.json" --output "$b26_output/$b26_group"
done
# 全部所选组拟合保存后，统一读取历史 Test。
for b26_group in $b26_groups; do
  uv run atlas experiment "$b26_config/$b26_group.json" --output "$b26_output/$b26_group" --test
done
```

补算 300 次主节点的 Spearman 区间时，直接使用已保存预测和当前公共评估函数：

```bash
PYTHONPATH=src NUMBA_NUM_THREADS=8 POLARS_MAX_THREADS=4 OPENBLAS_NUM_THREADS=1 \
  .venv/bin/python configs/experiments/zk26_lgbm_defaults_20261106/spearman_intervals.py
```

输出为 `artifacts/comparisons/zk26_lgbm_defaults_20261106/spearman_intervals.json`。
先核对全部点估计及原 Pearson 区间，再按相同 seed 20261102、按年分层的同步
20 交易日块做 2,000 次配对重采样。每次在品种内重新排名，重复观测和并列值使用
平均秩；完整 Test 的品种 √成交额权重固定。原模型、预测和 `test.json` 不改写。
新增区间只覆盖 300 次主节点，200 次前缀的 Spearman 先保留点估计。

总览切换为 Val 末固定品种后，使用相同入口加 `--cohort val-end`：

```bash
PYTHONPATH=src NUMBA_NUM_THREADS=8 POLARS_MAX_THREADS=4 OPENBLAS_NUM_THREADS=1 \
  .venv/bin/python configs/experiments/zk26_lgbm_defaults_20261106/spearman_intervals.py \
  --cohort val-end
```

名单和原 Pearson 区间取自
[Val 末品种报告](../../../docs/records/zookeeper26_fold2/val_end_products_20261106/report.md)，
IC 为 73 品种、2,326,761 行；输出到
`artifacts/comparisons/zk26_val_end_products_20261106/spearman_intervals.json`。
两种品种口径分别保存，不能混用基准或区间。

本轮是用户指定参数的历史 Test 敏感性分析；此前参数对照已查看 2024–2025，
不能作为新的盲测。结果不反馈搜索，不据此筛选成员、预算节点或继续自动调参。
