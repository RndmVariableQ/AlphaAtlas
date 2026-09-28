# 因子库预测模型

`atlas model` 是公共的下游预测评估入口，支持 `linear` 和 `lightgbm`，适用于所有方法的
非空冻结因子库。实现位于 `src/alpha_atlas/model.py`，配置为 `configs/model.toml`。
它评估组合预测的 IC，不是交易持仓、收益或 Sharpe 回测，不参与搜索和因子准入。

## 使用

```powershell
# 完成搜索并冻结后，使用 train+val 合并训练两个模型：
uv run atlas model artifacts/runs/<run_id>

# 使用已经保存的模型评估 test，不重新拟合：
uv run atlas model artifacts/runs/<run_id> --test
```

支持 `--quiet`。首次命令合并 train 和 val 后拟合模型，不生成 train/val 指标；没有保存的
拟合结果时 `--test` 会拒绝执行。两种模型都使用整个冻结因子库，不做 top-k、自动调参、
early stopping 或模型择优。Linear 直接求解析最小二乘解；LightGBM 固定 50 个 boosting
rounds。参数在首次训练前固定；重复命令复用保存结果，不发起 LLM 请求。

## 模型和数据规则

- `linear`：带截距的普通最小二乘回归，使用
  [NumPy lstsq](https://numpy.org/doc/stable/reference/generated/numpy.linalg.lstsq.html)。
  `rcond` 是奇异值截断阈值，不是 Ridge 正则强度。共线列允许秩亏最小二乘解，保存有效秩。
- `lightgbm`：[LightGBM 回归](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.train.html)，
  固定树数、叶数、学习率、最小叶样本数、L2、线程数和 seed。CPU deterministic 与
  force_col_wise 打开；版本固定在依赖锁中，不承诺跨版本或硬件逐位一致。
- 监督目标沿用资产配置的未来对数收益；在真实合约／连续段内生成，目标终点不能跨 split。
  PIT eligibility、字段、高周期特征、预热和缺失规则复用公共 MarketData／DSL。
- 因子列顺序采用冻结库顺序；每列乘原训练方向。通过 row_id 对齐计算结果，不按数组位置拼接。
  输入仅包含因子列，不把 instrument_id、product、日期或 target 当作模型特征。
- 仅对符合 train+val 标签和 eligibility 规则的合并观测拟合各列均值、总体标准差。
  全缺失、非有限统计或标准差不超过 `1e-12` 的列移除；移除规则不看 test。
  所有列移除、有效训练行不足两行或目标为常量时明确失败。
- 两模型使用相同的训练均值填补与标准化；标准化后的缺失值为零。某行所有保留因子
  都缺失时不训练、不预测；部分缺失行仍可预测。因此组合覆盖率与单因子覆盖率不同。
  不使用未来值或测试统计填补特征，不填补目标。
- 模型按训练样本等权拟合。报告复用资产主 IC 及其其他相关指标，期货仍是按品种计算后
  等权／成交额权重聚合；模型训练损失权重和最终 IC 聚合权重不是同一口径。
  组合预测不根据 val/test 再翻转方向。

模型不汇报 train/val 指标；val 已参与上游因子筛选，和 train 一起用于最终模型拟合。
只有预处理、因子集合和模型固定后才可查看 test；test 结果不能反馈给搜索。

## 产物与旧运行

```text
artifacts/runs/<run_id>/model/
  fit.json     # 配置、来源、因子列顺序、预处理和两个拟合模型
  test.json    # 显式 --test 后生成的测试指标
  report.md    # 组合 IC、覆盖率、有效行数和因子列映射
```

线性系数以 JSON 保存，LightGBM 以官方文本模型保存，不加载 pickle。两模型在同一次
训练中固定；`fit.json` 原子写入。失败后尚未保存 fit 时可重跑；已保存时不重新训练。
每个搜索运行的 `model/` 对应一份固定配置。配置、所记录依赖版本或输入身份改变时，
已有模型结果拒绝复用，不静默覆盖，也不自动迁移。

旧搜索源码与当前源码可以不同：这里是**导入冻结表达式的新模型实验**，不是恢复旧搜索，
也不冒充原源码下的单因子 OOS。保留原 run.json、trial、冻结清单和 oos.json；不修改
它们的指纹或状态。验证原有配置指纹、冻结库校验值、数据快照、算子运行环境，并重新编译
核对已有因子 ID，语义不兼容则拒绝执行。日期、资产配置等研究条件来自该运行的保存记录。
fit.json 分别记录搜索启动与模型拟合时的既有源码指纹、模型配置与依赖版本。
源码指纹仅用于来源记录：`atlas resume`、`atlas test` 和已保存模型的复用不再比较当前
源码或当前 `uv.lock` 内容。模型复用和测试保留拟合时来源记录，不重写 fit.json；
报告中的同源仅指两个保存记录一致，不代表后续执行始终使用相同源码。

测试仅用合成行情和模型数值，不访问真实凭据或供应商。覆盖线性手算、非线性学习、
序列化一致性、训练预处理隔离、缺失和常量、split／合约边界、旧库导入、缓存校验及
原搜索产物不变。运行 `pwsh -File scripts/check.ps1`。
