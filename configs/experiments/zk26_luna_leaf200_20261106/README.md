# B26 Luna：最小叶样本数 200 对照

复用 B26 Luna 表中的六次冻结搜索，只把 LightGBM 的 `min_data_in_leaf` 从 2000
改为 200。50 轮、学习率 0.05、15 叶、深度 5、L2=1、seed 42、4 线程及预处理规则
保持不变。B26 基线和全部扩充组合均按新参数拟合，保留 200／300 次两个节点。

六个 300 次组合平均 IC 从 0.014889916 升至 0.015362915，5/6 行上升；各行参数
差值区间均含零。[第一轮全部 24 行结果及区间](../../../docs/records/zookeeper26_fold2/luna_lgbm_sensitivity_20261106/leaf200_l2_1/report.md)。
用户随后指定固定较好的叶样本数组，继续将 L2 降至默认值 0；
[两轮完整对照](../../../docs/records/zookeeper26_fold2/luna_lgbm_sensitivity_20261106/report.md)。

配置以 [版本](../../versions/rq8_zk26_luna_leaf200_20261106.json) 和
[配对协议](plan.json) 为准；[model.toml](model.toml) 供独立 B26 基准拟合使用。
所有 scope 都显式指定既有 `runs` 并关闭并集，不调用模型服务或继续搜索。

| Scope | 冻结来源 |
| --- | --- |
| [earlier_methods.json](earlier_methods.json) | AlphaProbe `b448e9fb48`、AlphaSchema `fbac3f9a28`、MCTS-LLM `d8d6f22a47` |
| [overview_methods.json](overview_methods.json) | AlphaAtlas V1 `5f77947f20`、AlphaSchema `3a4f640d02` |
| [v3.json](v3.json) | AlphaAtlas V3 `8142c2c849` |

使用 V3 Luna 搜索时已保存的执行源码，在仓库根目录复跑：

```bash
export PYTHONPATH="$PWD/artifacts/comparisons/zk26_seeded_fold2_v3_luna_300_20261106/source/src"
export POLARS_MAX_THREADS=8 OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=4
b26_config=configs/experiments/zk26_luna_leaf200_20261106
b26_output=artifacts/comparisons/zk26_luna_leaf200_20261106
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
# 三组全部拟合保存后，才执行 Test。
for b26_group in earlier_methods overview_methods v3; do
  uv run atlas experiment "$b26_config/$b26_group.json" --output "$b26_output/$b26_group" --test
done
```

原 leaf2000 搜索、模型和结果保留。每个 scope 固定 B26 可用的共同训练与预测行；
重训后核对因子身份、预处理、训练行数、逐行 Test 标签与 Linear 预测。
ΔIC 与 WMSE 仍比较同一参数下的 B26 与扩充库；参数敏感性另比较相同冻结库的
leaf200 与 leaf2000 预测，防止把基线变化当作新增因子改善。

2024–2025 已查看，本次是用户指定参数的历史留出敏感性分析。结果不反馈给搜索，
也不筛选因子。后续 L2 对照按六个 300 次组合的平均加权 Pearson IC 选择叶样本数，
再将 `lambda_l2` 从 1 改为 LightGBM 默认值 0，并以独立版本保存；该选择仍属于历史
Test 上的参数敏感性分析。
