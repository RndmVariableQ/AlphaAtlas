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

同一入口也支持 `scripts/evaluate_zookeeper.py` 生成的固定 26 项目录，路径为
`artifacts/reference-evaluations/zookeeper26-<fold>-<id>`。该目录使用
`ZookeeperMarketData` 读取已冻结来源、按完整可用历史准备月度特征，并校验定义版本和内容。
所有 26 项按冻结顺序进入模型，不以单因子准入或 Test 表现筛选；模型预处理与评分复用
本页协议。其辅助曲线不完整、代理定义和历史 OOS 边界仍然适用，见
[Zookeeper 复现说明](../src/alpha_atlas/factor_libraries/zookeeper/README.md)。

完整实验的 `atlas run`／`atlas resume` 默认在冻结后串联拟合、Test 和 Val 库内相似度，
三项成功后自动填表；`atlas finish <run_dir>` 可补跑收尾。
批量研究先用 `--search-only` 冻结全部运行，再统一执行 finish，见
[自动评估与填表](research_protocol.md#自动评估与填表)。

## 模型和数据规则

- `linear`：普通最小二乘回归；混合拟合带截距，品种去均值模式使用品种固定效应。使用
  [NumPy lstsq](https://numpy.org/doc/stable/reference/generated/numpy.linalg.lstsq.html)。
  `rcond` 是奇异值截断阈值，不是 Ridge 正则强度。共线列允许秩亏最小二乘解，保存有效秩。
- `lightgbm`：[LightGBM 回归](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.train.html)，
  固定树数、叶数、学习率、最小叶样本数、L2、线程数和 seed。CPU deterministic 与
  force_col_wise 打开；可选 `max_depth` 限制树深，未配置或为 `-1` 时不限制，
  正整数表示最大深度。版本固定在依赖锁中，不承诺跨版本或硬件逐位一致。
- 监督目标沿用资产配置的未来对数收益；在真实合约／连续段内生成，目标终点不能跨 split。
  PIT eligibility、字段、高周期特征、预热和缺失规则复用公共 MarketData／DSL。
- 因子列顺序采用冻结库顺序；每列乘原训练方向。通过 row_id 对齐计算结果，不按数组位置拼接。
  输入仅包含因子列，不把 instrument_id、product、日期或 target 当作模型特征。
- 模型预处理由 `[preprocessing]` 固定：当前 `clip_quantiles = [0.01, 0.99]`，
  `product_demean = true`。各特征和标签分别使用 train+val 合并拟合区间的全局分位边界；
  特征先裁剪，再拟合均值和标准差。标签边界独立于各因子的缺失，不按 test 重新估计。
  这是模型自身的预处理，区别于单因子评估只在 train 拟合的裁剪边界。
  Test 特征复用模型冻结边界；Test 标签和组合预测的 IC 仍使用原始目标。
- 仅对符合 train+val 标签和 eligibility 规则的合并观测拟合各列均值、总体标准差。
  全缺失、非有限统计或标准差不超过 `1e-12` 的列移除；移除规则不看 test。
  所有列移除、有效训练行不足两行或目标为常量时明确失败。
- 两模型使用相同的训练均值填补与标准化；标准化后的缺失值为零。某行所有保留因子
  都缺失时不训练、不预测；部分缺失行仍可预测。因此组合覆盖率与单因子覆盖率不同。
  不使用未来值或测试统计填补特征，不填补目标。
- 期货有 `product` 时，Linear 在整个模型拟合区间内分别对每个品种的特征和裁剪后标签
  去均值，再求最小二乘系数；保存各品种的特征均值投影作为预测偏移，不加回标签均值。
  LightGBM 使用全局预处理特征，训练标签按品种全期去均值。全期不包含 test，也不按年
  重置。Test 预测只使用保存的偏移，新品种偏移为零；没有 product 的资产保持混合样本拟合。
- 模型按训练样本等权拟合。报告复用资产主 IC 及其其他相关指标，期货仍是按品种计算后
  等权／成交额权重聚合；模型训练损失权重和最终 IC 聚合权重不是同一口径。
  组合预测不根据 val/test 再翻转方向。

模型不汇报 train/val 指标；val 已参与上游因子筛选，和 train 一起用于最终模型拟合。
只有预处理、因子集合和模型固定后才可查看 test；test 结果不能反馈给搜索。

该口径参考本地 ChaoticTrader_Future 的“全局预处理＋品种全期去均值”（模式 2）：
`materialized_training.py`、`materialized_models.py` 和 `panel_linear.py`，参考 HEAD 为
`cdef172c410a5eb2e130e261ca36bdb3179529df`，读取时上述模块含未提交改动。
本地实现不导入该项目；保留本平台最小二乘、样本等权和固定 LightGBM 超参，未同步对方
可配置的 Ridge、品种年波动率权重、观测标记列和其他预处理模式。

## 预测误差增量诊断

公共评估函数 `evaluation.prediction_error_summary(target, prediction, baseline, weights=None)` 在调用方
已对齐的共同样本上返回模型与基线的 SSE、MSE，以及 `1 - MSE_model / MSE_baseline`。
正值表示预测误差下降；分母为零时相对下降率未定义。输入必须非空、一维、等长且有限，
基线可为标量；函数不自动丢弃缺失行，以免不同模型使用不同评价样本。

不传 `weights` 时沿用逐行等权。显式权重须非负、有限、逐行对齐且总和为正；
返回的 SSE 使用该权重，MSE 除以权重总和，并另返回 `weight_sum`。
当前 Fold2 补充表采用与 IC 相同的完整 Test 品种 √成交额权重：先对每个品种求 MSE，
再按品种权重加权平均；等价于该品种每行权重 `w_product / n_product`。
模型和基线使用同一共同样本与权重，误差下降取两者加权 MSE 的比值，
不平均各品种的误差下降率，也不额外按目标方差标准化。均值偏差和预测幅度仍影响 MSE。
时间区块重采样保持完整 Test 品种权重固定，各次按抽中的品种行数重算 MSE；
无观测的品种从该次模型与基线同时排除，再归一化其余品种权重。

从空库开始的历史诊断可以使用 Train+Val 原始目标的全局均值作为无因子预测基线，
在 Test 上保持不变；同时可明确报告零预测基线的敏感性。已有库则比较相同预测流程下
旧库与扩充库的误差。基线、预算节点、模型配置和样本规则应在评估前确定；空库 IC
不能填为零。MSE 与 IC 含义不同，现有去均值模型的 MSE 也受输出尺度和校准影响，
不能将误差改善直接称为新机制或独立信息数量。

比较不同冻结库时先对齐共同可预测样本，另报各自覆盖率；若报告缺失预测回退到基线的
全样本结果，须单独注明这一评价规则。因子数与入库率作为产出报告，不将增益除以因子数
当作单因子质量。时间区块重采样区间只描述冻结模型在测试时期的不确定性，不替代多次
独立搜索。该诊断复用已保存模型，不自动重写历史 `model/test.json` 或参与搜索反馈。

## 固定 baseline 的配对增量实验

`atlas experiment <scope.json>` 按[固定预算协议](research_protocol.md#zookeeper-26-项组合的固定预算增量实验)
装配 B26 与 B26＋冻结新库。当前统一 v5、仅逐方法比较的入口见
[复跑配置](../configs/experiments/zk26_single_methods_v5_20261103/README.md)。
各 scope 设置 `include_union=false`，不会拟合或评分并集。省略此字段保留历史 scope 的语义。
`experiment_version` 指定评估配置；跨版本重训须显式声明 `source_experiment_version`，
并校验两版本的数据、字段、算子、目标、fold 和初始 B26 相同。搜索仍按其原版本通过冻结
校验，配对模型配置必须等于预先指定的 baseline 配置；不改写来源运行或筛选成员。

scope 列出 `runs` 时直接复用这些冻结运行；未列出时按方法 × seed 启动搜索，运行 ID
由 scope、方法和 seed 确定，中断后重复同一命令即续跑原运行。默认输出到
`artifacts/experiments/<scope.id>/`，历史输出目录用 `--output` 指定。
必须覆盖计划中的全部搜索运行。历史三方法首批使用
`configs/experiments/zk26_seeded_fold2_300_v2/three_methods.json`。
该 scope 固定 AlphaProbe、AlphaSchema、MCTS-LLM，seed 42，各 300 次；三个运行全部冻结后
才能拟合，分别保留 200/300 前缀以及三方法并集，不代表完整十一方法并集。
固定 B26 的原始冻结库已逐字节复制到
`docs/records/zookeeper26_fold2/frozen/library.json`，与相邻的 `run.json` 一起通过原有冻结
校验。v4 计划引用该可发布副本，避免基准定义只存在于 Git 排除的 `artifacts/` 目录。

```bash
uv run atlas experiment configs/experiments/zk26_seeded_fold2_300_v2/three_methods.json \
  --output artifacts/comparisons/zk26_seeded_fold2_three_methods_300_v2
# 同一命令追加 --test，才读取 Test；要求所有组的两个模型已经保存。
# --search-only 只启动或续跑搜索，不拟合。
```

实现位于 `incremental.py`，复用 MarketData、DSL、冻结因子身份及 `model.py`。
先在 Train+Val 上确定 B 的可用行，再让全部组使用这一训练集合和同一预处理规则。
主 Test 集合固定为 B 可预测且标签有效的行；额外覆盖单列。输入按 row_id 连接，
仅按已有 factor_id 去掉完全重复定义；相关但不同身份的因子保留。
空库或全部重复 B 时直接复用 B 预测；全缺失或常量新列仍按训练预处理删除。

v4 的搜索库本身包含初始 B26。冻结记录将这些成员标为 initial，装配时只从原子 trial
读取新增准入成员，并核对初始身份集合等于固定 B26；不把初始 26 项计为新发现。
没有新增成员时复用 B 预测，仍报告初始库存在。旧 v3 独立空库产物保持可读，不能与新
初始条件的运行混成同一组比较。

`evaluation.paired_prediction_metrics` 在共同有效品种上计算两组 IC，在共同有权重品种上
计算 MSE。恒定预测的 IC 未定义，不填零；MSE 仍可计算。年度结果沿用完整 Test 的固定
品种权重。`paired_block_bootstrap` 按年分层并同步重抽市场交易日块，使用每日充分统计量
恢复重抽后全期相关性与误差，不平均每日 IC。点估计和重采样都保留共同品种及有效样本数。
横向方法表另外给出同模型、同预算节点、所有可评分方法的共同有效品种结果。

输出目录保存 `fit.json`、`test.json`、`factors.jsonl` 和 `results.jsonl`；因子包含公式、
说明、方向和来源 run/trial。逐行预测保存到 `data/comparisons/<scope_id>/predictions.parq`，
排除 Git。保存的 fit 复用既有配置、库和数据身份，输入变化明确拒绝；来源源码指纹只作
记录，不新增校验体系。拟合全部成功后才原子保存 fit；Test 缓存不触发重训。
此入口是独立的配对下游实验，不改写原 baseline 或搜索运行的普通 `model/`。

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
命名实验版本的运行从保存版本中读取模型配置，并使用该版本的数据适配器及算子集合；
不读取后来修改的 `configs/model.toml`。普通旧运行仍沿用原配置入口。详情见
[数据与算子版本](research_protocol.md#数据与算子版本)。
缺少 `[preprocessing]` 的旧模型配置沿用原始输入／标签和混合样本拟合。变更预处理后，
通过导入原冻结库的新评估截面训练，保留原模型和搜索产物，不在原目录覆盖重训。

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
