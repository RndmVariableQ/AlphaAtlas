# AlphaSchema：作者代码在统一协议下的适配

入口 `--method alphaschema`，实现名 `alphaschema_atlas_v1`。
遵循[方法复现规范](../method_reproduction.md)，使用公共 SearchSession、DSL、评估、预算、
准入、冻结与 OOS。本实现复现语义搜索机制，不声称复现论文的市场收益。

## 来源

- 论文：Jingyang Yi 等，
  [AlphaSchema: Exploring the Space of Trading Semantics for LLM-Based Alpha Mining，v1](https://arxiv.org/abs/2607.26642v1)。
- 作者仓库：[JingyangYi/AlphaSchema，固定 commit](https://github.com/JingyangYi/AlphaSchema/tree/1206a094abfaad7cc53e6dff39f8fae43e851acb)。
  参考 `schema_space.py`、`selector.py`、`reward_model.py`、`pipeline.py`、`workflow.py`、
  `backend.py`、`configs/default_stock_search.json` 和实现／修复提示词。
- 五类语义词表原样保存于
  [alphaschema_schemas](../../src/alpha_atlas/methods/alphaschema_schemas/)，保留原名称、描述、
  terms、scope 及禁用状态；来源为该 commit 的 `schemas/stock_alpha/*.json`。
  该 commit 未提供 LICENSE；这里记录来源，不另行授予上游材料许可证。
- 搜索与平台集成由 [alphaschema.py](../../src/alpha_atlas/methods/alphaschema.py) 实现。
  未导入上游 pandas 执行器、供应商数据、任意 Python 执行或独立回测路径。

## 保留的搜索机制

每个计划包括 Event、Context、0–3 个不重复 Qualities、Direction、Output。
默认每轮采样 10,000 个未尝试计划，选出 16 个计划；质量组件数量分布为
`0/1/2/3 → 0.15/0.35/0.35/0.15`。计划键保留作者代码中的 quality 顺序；
代理模型的 quality 特征按集合编码，因此不同排列可能是不同计划键却共享模型特征。

每批开始时，根据有有限测量奖励的完整计划数决定配额，批内不更新选择：

| 计划奖励数 | 覆盖探索 | 代理利用 | 局部变异 |
| --- | --- | --- | --- |
| 0–149 | 16 | 0 | 0 |
| 150–299 | 8 | 6 | 2 |
| 300–699 | 5 | 8 | 3 |
| ≥700 | 3 | 8 | 5 |

覆盖新颖度为 `0.60/sqrt(1+n_event_context) + 0.25/sqrt(1+n_event)
+ 0.15/sqrt(1+n_context)`。计数包括已结束但失败的计划，避免失败区域无限免费重抽；
同批候选使用同一历史计数，并列保留带 seed 的采样顺序。

代理使用 8 个 LightGBM 回归器。每个回归器从全部有效历史中无放回抽取 70% 样本，
使用 450 棵树、学习率 0.035、原代码的叶子数序列、叶样本下限、行列采样与 L2 参数。
特征为组件 one-hot、quality 数量，以及 Event–Context、Event–Output、Event–Direction、
Event–Quality、Context–Quality 交互。利用分支按预测均值降序选择；标准差仅记录，不作为 UCB。
没有使用验证集、测试集或未来轮次拟合代理。

局部变异从历史奖励最高的 100 个计划轮流选父计划，每次只替换一个组件，或增删一个
quality。使用作者代码的操作概率，保留首批有效、未重复的子计划；不足时从随机候选池补齐。
这遵循作者 `selector.py` 的行为，与论文附录所述对变异候选再做代理排序有区别。
选中计划的预测、排名、父计划、变异操作和改变字段均写入既有方法状态。

## 实现、奖励与平台适配

LLM 收到固定公共 context 和选中计划的完整组件记录，一次生成默认 20、80 两个周期的
具体 DSL，要求同一金融机制、同一表达式结构，其他窗口／阈值从主周期派生。周期是输入
原生 Bar 数；在 5m 输入上不是 20／80 天，也不是标签长度或平台窗口上限。
两式的语义一致性由模型遵循提示词，不宣称编译器已证明这一点。

公共诊断提供原符号训练 RankIC、日 RankICIR 和一期滞后 RankIC。每个实现的奖励为：

```text
J = 10 × train RankIC + train RankICIR
    − 2 × max(0, train RankIC − train lag1 RankIC)
plan_reward = max(J of finite measured realizations)
```

各项原值、加权项、周期、factor ID 和 trial 编号均保留。负奖励仍是有效观测，
不取绝对值，也不使用平台训练方向翻转；这样保留语义 Direction 对搜索奖励的作用。
正式准入仍由平台按训练方向、验证质量、覆盖和相关性裁决，不能由计划奖励替代。
两个周期分别准入；相关性冲突仍先入库者保留，不按计划的最大训练奖励事后替换成员。
即使验证拒绝或表达式重复，只要训练诊断可定义，仍可成为该计划的测量证据。

| 项目 | 本地选择及与上游差异 |
| --- | --- |
| 词表 | 作者代码启用 Event 54、Context 43、Quality 59、Direction 3、Output 8 项，共 167；Context 原有 46 项中的 3 项保持禁用。不同于论文的 140 项词表 |
| 表达式 | 两个具体 DSL 替代一个带 period 参数的 Python 函数；复用编译、字段权限、数值与合约边界。缺失字段或算子能力显式失败，不静默替换计划 |
| 数据及标签 | 使用当前资产 profile 的 PIT、频率、对数收益目标与 split；不采用作者 backend 的简单收益或论文另一标签起点 |
| 奖励样本 | 仅训练期，统一 train 裁剪，每组至少 5 对有限样本（上游默认 20）。A 股按日横截面；期货按品种及固定训练成交额权重计算 RankIC，RankICIR 使用公共逐日聚合。期货和 5m 属于额外资产适配 |
| 延迟 | 一根原生输入 Bar，按因子 panel 及连续段滞后；不从预热带入 train 首笔信号、不跳过已知缺失。使用原因子的冻结训练裁剪边界 |
| 失败奖励 | 缺少任一核心统计时该实现奖励为 null。全计划失败时不加入代理训练；不用上游的失败零分。配额阈值因此按有效奖励数推进 |
| 双周期与预算 | 两个候选逐个提交、各扣一次；保留双方结果，包含重复、非法和被拒绝候选。不仅记录获胜周期 |
| 尾部预算 | 剩余一次仍评估一个候选，但计划记为 `budget_partial`，不以单周期结果填充双周期代理标签 |
| 实现失败 | 格式或能力失败耗尽修复后提交一个空 DSL 失败 trial；已生成的非法 DSL 则保留原式，逐个计费 |
| 修复 | 默认最多一次格式／静态 DSL 修复，仅预检不计算数值；不复现上游执行／回测失败后的免费修复。真实运行错误由公共 trial 记录 |
| LightGBM 接口 | 使用既有依赖的 native train API 和等价特征矩阵，避免新增 scikit-learn；预测分块以控制内存。保留无放回子采样和模型参数，不承诺与上游逐位相同 |
| 候选初始化 | 不预置因子或绩效库。公共示例、参考库权限沿用所有方法的统一设置 |
| 最终评估 | 不接入论文独立的因子池筛选、组合或交易回测；沿用平台冻结全库、统一下游模型与独立 OOS |

词表中的股票横截面语义在期货上并非都适用；本适配保留词表，要求显式报告不可实现的
计划，不能将期货结果称为原股票实验复现。与其他方法比较须固定字段、算子、模型、
参考库、共享诊断、准入和候选预算；不同语义空间仍属于系统比较。

## 配置与运行

[alphaschema.toml](../../configs/alphaschema.toml) 提供模型服务、生成、采样、配额与奖励参数。
使用公共 Chat 客户端；密钥只从配置指定的环境变量读取。默认 Luna 服务配置与当前
annealing／V1 一致，不需要 embedding 服务。检查配置时不会访问真实模型或供应商。

共享诊断需在 [benchmark.toml](../../configs/benchmark.toml) 中设为
`icir_turnover_lag1_v2`，对全部方法开放。原 `icir_turnover_v1` 继续受支持；
旧运行恢复和 OOS 必须还原为其保存的配置，不能在同一运行中切换诊断版本。
数值定义见[共享训练诊断](../research_protocol.md#共享训练诊断)。

```bash
# 搜索与开发期间不打开 Test；使用本项目已有数据快照。
uv run --env-file .env atlas run --asset futures_kernel_5m_full --universe all --fold fold1 --method alphaschema --seed 42 --attempts 1000 --search-only
uv run atlas resume artifacts/runs/<run_id> --search-only
uv run atlas report artifacts/runs/<run_id>
# 所有开发决策冻结后再统一 OOS。
uv run atlas finish artifacts/runs/<run_id>
```

默认批大小 16、两个周期且奖励都可定义时，要完成 160 个计划／320 次候选尝试，
下一批才启用代理。100 次预算只能验证覆盖探索，不能检验奖励引导。
失败会推迟这个阈值；不得为显得有效而静默缩短冷启动。
论文 80 轮 × 16 计划在无失败、双周期齐全时对应 2,560 次本地候选尝试；模型调用、
计划数与候选尝试数须分别记录。上面的 1,000 次是适配运行示例，不是论文完整实验预算。

## 恢复、报告与验证

沿用 `ask/tell` 和既有检查点，保存 seed／轮次、已选批次、计划及选中原因、模型原响应、
查询结果、静态验证、待提交候选、逐周期 trial 引用和奖励。随机数流由保存的 seed 和
轮次推导；代理模型仅在选择新批次时由历史重建，不另存模型账本或文件指纹。

模型响应在解析／查询前保存；中断后复用已完成阶段。已提交 trial 由 runner 重放反馈，
不重复数值评估、入库或扣费。tell 不调用外部模型。请求已发但回复未保存的中断可能重发，
该次用量记为 unknown。临时网络故障遵循公共重试规则，耗尽后停止并允许原配置恢复。
只读查询每次回复最多 16 项、每阶段最多 8 轮，格式／DSL 修复受独立次数上限约束。

报告展示计划数、有限奖励、失败、预算截断、各计划选择及奖励、模型请求／token，
以及统一因子积累、拒绝／重复、耗时和冻结库 OOS；不把语义计划数当作有效因子数。

[方法测试](../../tests/test_alphaschema.py) 使用模拟 Chat 和合成数据验证词表及采样、
手算新颖度／奖励、代理排序／并列、七种局部变异、真实 LightGBM 拟合、失败与预算尾部、
双周期最大值及重复身份、查询／修复边界、训练与验证隔离、模型响应／trial／tell／周期间
恢复、配置冻结和 OOS 不回写状态。
[诊断测试](../../tests/test_search_diagnostics.py) 独立验证股票滞后、PIT、缺失、期货连续段／
品种权重、冻结裁剪、旧指标不变及版本隔离。
另外对固定 commit 的 `schema_space.py` 直接进行源码对照：3 个 seed 下合计
3,000 个采样计划和 3,000 次变异的计划键／操作与本地实现一致；这不等于模型输出或
市场结果逐位复现。

完整检查命令为 `pwsh -File scripts/check.ps1`。这些测试验证算法和平台协议；真实 Luna
试跑的条件、查重入库与冻结后评估证据，以及 Fold2 比较配置见
[实验记录](../records/experiments.md#2026-11-02-alphaschema真实试跑与-fold2-luna-比较配置)。
