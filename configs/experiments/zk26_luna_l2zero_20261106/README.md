# B26 Luna：leaf200 与默认 L2=0

六个 300 次冻结组合在固定 B26 共同品种上的平均加权 Pearson IC，leaf2000 为
0.014889916，leaf200 为 0.015362915，后者有 5／6 行上升。因此固定
`min_data_in_leaf=200`，继续测试 `lambda_l2=0`，即当前 LightGBM 的默认 L2。

本机 LightGBM 4.7.0 的 `LGBMRegressor.reg_lambda` 默认值核对为 0.0。
第二轮平均 IC 为 0.015590073，比 L2=1 增加 0.000227159；5/6 行 IC 上升，2/6 行
WMSE 改善，各行参数 ΔIC 区间均含零。
[完整报告与冻结产物](../../../docs/records/zookeeper26_fold2/luna_lgbm_sensitivity_20261106/report.md)。

这一轮相对 leaf200/L2=1 只改变 L2；50 轮、学习率 0.05、15 叶、深度 5、seed 42、
4 线程、原冻结因子及预处理规则保持一致。B26 和全部扩充组合均按 L2=0 拟合，
三个 scope 全部保存后才查看 Test，保留 200／300 次节点。

[plan.json](plan.json) 保存选择标准与第一轮结果；
[版本](../../versions/rq8_zk26_luna_l2zero_20261106.json) 固定最终参数；
[model.toml](model.toml) 用于独立 B26 基准拟合。
[earlier_methods.json](earlier_methods.json)、[overview_methods.json](overview_methods.json)、
[v3.json](v3.json) 显式引用六次原冻结搜索，不重新调用 LLM。

从仓库根目录复跑：

```bash
export PYTHONPATH="$PWD/artifacts/comparisons/zk26_seeded_fold2_v3_luna_300_20261106/source/src"
export POLARS_MAX_THREADS=8 OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=4
b26_config=configs/experiments/zk26_luna_l2zero_20261106
b26_output=artifacts/comparisons/zk26_luna_l2zero_20261106
b26_workspace="$b26_output/baseline_workspace"
mkdir -p "$b26_workspace/configs" "$b26_workspace/baseline/frozen"
cp "$b26_config/model.toml" "$b26_workspace/configs/model.toml"
cp docs/records/zookeeper26_fold2/run.json "$b26_workspace/baseline/run.json"
cp docs/records/zookeeper26_fold2/frozen/library.json "$b26_workspace/baseline/frozen/library.json"
if [ ! -e "$b26_workspace/data" ]; then
  ln -s "$PWD/data" "$b26_workspace/data"
fi
uv run atlas --root "$b26_workspace" model "$b26_workspace/baseline"
for b26_group in earlier_methods overview_methods v3; do
  uv run atlas experiment "$b26_config/$b26_group.json" --output "$b26_output/$b26_group"
done
# 三组拟合全部保存后执行 Test。
for b26_group in earlier_methods overview_methods v3; do
  uv run atlas experiment "$b26_config/$b26_group.json" --output "$b26_output/$b26_group" --test
done
```

两轮都属于已查看的 2024–2025 历史 Test 上的参数敏感性分析；叶样本数按第一轮
平均 IC 选择，因此第二轮不能作为新的盲测证据。所有模型使用固定因子集合，结果不
反馈给搜索，也不据此调整其他参数。项目全局配置和历史模型保留。
